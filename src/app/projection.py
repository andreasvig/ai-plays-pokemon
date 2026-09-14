"""Project a run folder's nested ``run_summary.json`` → flat :class:`RunSummary`.

The nested writer (``agent/turn.py:_write_run_summary``) is the source of truth;
this module reads it (plus ``config.json`` for the ladder pointer) and produces
the flat denormalized index entry. It must tolerate LEGACY runs that predate the
control-center fields — missing ``referee``, missing top-level ``kind``/``status``,
missing ``llm_alias``, missing ``agent_type`` — and still produce a valid entry,
inferring defensively.

Gate counting MUST agree with ``cli/report.py``'s "N/total cleared" header:
  - ``total_gates`` = number of ladder *nodes* (== ``len(referee["gates"])``).
  - ``gates_reached`` = count of scorecard gates with status in ("done", "auto").
The ladder used by a run is recorded in ``config.json["referee"]["checkpoints"]``;
we read node count from that file (falling back to the default v1 ladder), so we
never hardcode the integer (the ladder is WIP).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.app.models import RunKind, RunStatus, RunSummary
from src.referee.progress import OPEN_LEG_FRACTION_CAP

# Benchmark legacy official runs (a benchmark_version but no benchmark id) map to.
# They were scored on the full 20-gate ladder, so they belong to pokebench-full.
_LEGACY_OFFICIAL_BENCHMARK = "pokebench-full"

# Default ladder used when a run doesn't record which one it ran against, or the
# recorded one no longer exists. Read at runtime — never hardcode the count.
_DEFAULT_LADDER = Path("configs/checkpoints-firered-v1.yaml")

# Bump when project_run_dir() starts reading a new field out of run_summary.json.
# app boot re-projects every stored row whose projection_version is older, so a
# field added here reaches History without anyone deleting runs_index.json.
#   1 — 2026-09-09: error / crash (why a crashed run ended).
#   2 — 2026-09-09: record (the spec the run was recorded with, for continues).
#   3 — 2026-09-11: open-leg fraction capped at OPEN_LEG_FRACTION_CAP.
PROJECTION_VERSION = 6  # 6 (2026-09-14): avg_output_tokens_per_turn + thinking_share (Efficiency · output tokens per turn); 5: inputs per turn

# Status values the report treats as "cleared" for a gate (mirror report.py).
_CLEARED_STATUSES = ("done", "auto")

# Cache: ladder path (as a string) -> node count, so a scan over many runs
# sharing one ladder doesn't re-parse the YAML per run.
_ladder_node_count_cache: dict[str, int] = {}


def _num(v) -> float | None:
    """A JSON number as float, else None (bools are not numbers here)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v) -> int | None:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def _ladder_node_count(ladder_path: Path | None) -> int:
    """Number of ladder *nodes* for ``ladder_path`` (falls back to the default).

    Returns 0 only if neither the recorded ladder nor the default can be loaded.
    """
    candidates: list[Path] = []
    if ladder_path is not None:
        candidates.append(ladder_path)
    candidates.append(_DEFAULT_LADDER)

    for path in candidates:
        key = str(path)
        if key in _ladder_node_count_cache:
            return _ladder_node_count_cache[key]
        try:
            # Local import: load_ladder pulls in yaml; keep projection import-light.
            from src.referee.checkpoints import load_ladder

            count = len(load_ladder(path).nodes)
            _ladder_node_count_cache[key] = count
            return count
        except Exception:
            continue
    return 0


def _load_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _input_stats(run_dir: Path) -> tuple[float | None, dict[str, int] | None]:
    """Average game inputs per accepted turn and the button mix, from the
    ``turn_explanation`` events (one per settled turn, ``explanation.action`` is
    the input list the emulator ran). A continued run's events.jsonl carries the
    source's turns too, which is right here: the average is over the whole run,
    like ``session.total_turns``. A retried turn keeps its last explanation.
    (None, None) when the run has no events file or no explained turn — older
    harnesses — so the board leaves the run off that card rather than drawing 0.
    """
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return None, None
    per_turn: dict[int, list] = {}
    try:
        with path.open() as fh:
            for line in fh:
                if '"turn_explanation"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "turn_explanation":
                    continue
                action = (e.get("explanation") or {}).get("action")
                turn = e.get("turn")
                if isinstance(action, list) and isinstance(turn, int):
                    per_turn[turn] = action
    except OSError:
        return None, None
    if not per_turn:
        return None, None
    counts: dict[str, int] = {}
    total = 0
    for action in per_turn.values():
        total += len(action)
        for a in action:
            key = str(a).strip().lower()
            counts[key] = counts.get(key, 0) + 1
    return total / len(per_turn), dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _output_token_stats(run_dir: Path, turns: int, cost: dict) -> tuple[float | None, float | None]:
    """Mean output tokens per turn — thinking plus the reply, every call the run
    made (gameplay and compaction, retries included, like cost per turn) — and
    the share of them that was thinking. Summed from the ``llm_request_usage``
    events: ``response_tokens`` is the provider's completion count, which
    already contains ``reasoning_tokens`` on every route we play (checked over
    the 25 published runs, 2026-09-14). Divided by ``session.total_turns`` so a
    continued run averages over the whole run. Without usage events the
    summary's ``cost.total_output_tokens`` still gives the mean (share None);
    with neither, (None, None) and the board leaves the run off the card.
    """
    if not turns:
        return None, None
    out = 0
    reasoning = 0
    saw_reasoning = False
    path = run_dir / "events.jsonl"
    if path.is_file():
        try:
            with path.open() as fh:
                for line in fh:
                    if '"llm_request_usage"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("type") != "llm_request_usage":
                        continue
                    r = e.get("response_tokens")
                    if isinstance(r, (int, float)):
                        out += r
                    t = e.get("reasoning_tokens")
                    if isinstance(t, (int, float)):
                        reasoning += t
                        saw_reasoning = True
        except OSError:
            out = 0
    if out <= 0:
        total = cost.get("total_output_tokens")
        if not isinstance(total, (int, float)) or total <= 0:
            return None, None
        return total / turns, None
    return out / turns, (reasoning / out if saw_reasoning else None)


def _safe_div(numerator: float, denominator: float) -> float:
    """``numerator / denominator`` guarding divide-by-zero → 0.0."""
    if not denominator:
        return 0.0
    return numerator / denominator


def _infer_config_stem(run_dir_name: str) -> str | None:
    """Pull the config stem out of a run-dir name, or None if not derivable.

    Real run dirs are ``<date>_<time>_<stem>`` or ``<date>_<time>_<stem>__<model>``.
    """
    # Strip a trailing ``__<model-slug>`` segment if present.
    base = run_dir_name.split("__", 1)[0]
    parts = base.split("_")
    # parts[0]=date, parts[1]=time, parts[2:]=stem (stems may contain underscores
    # but here they're hyphenated, so rejoin defensively).
    if len(parts) >= 3:
        stem = "_".join(parts[2:])
        return stem or None
    return None


def _record_spec(config: dict) -> dict | None:
    """``config.json["_record"]`` normalised, or None (unrecorded / unreadable)."""
    raw = config.get("_record") if isinstance(config, dict) else None
    if raw is None:
        return None
    try:
        from src.dashboard.recorder import normalize_spec

        return normalize_spec(raw)
    except (ValueError, TypeError, KeyError):
        return None


def project_run_dir(run_dir: Path) -> RunSummary | None:
    """Project ``run_dir/run_summary.json`` into a flat :class:`RunSummary`.

    Returns ``None`` when the summary is absent or unreadable (so callers can
    drop it from the index). Explicit top-level fields (written by P3 +
    deliverable-6) win; legacy runs fall back to defensive inference.
    """
    run_dir = Path(run_dir)
    summary = _load_json(run_dir / "run_summary.json")
    if summary is None:
        return None

    session = summary.get("session") or {}
    cost = summary.get("cost") or {}
    referee = summary.get("referee") or None

    # --- nested → flat (always present in the nested writer) ---
    model = session.get("llm_alias") or session.get("llm_model") or "unknown"
    model_resolved = session.get("llm_model")
    turns = session.get("total_turns") or 0
    duration_s = session.get("duration_seconds") or 0.0
    started_at = session.get("started_at")
    total_cost_usd = cost.get("total_usd") or 0.0

    avg_cost_per_turn_usd = _safe_div(total_cost_usd, turns)
    avg_s_per_turn = _safe_div(duration_s, turns)
    avg_inputs_per_turn, input_counts = _input_stats(run_dir)
    avg_output_tokens_per_turn, thinking_share = _output_token_stats(run_dir, turns, cost)

    # --- explicit-or-inferred top-level fields ---
    run_id = summary.get("run_id") or run_dir.name

    benchmark_version = summary.get("benchmark_version")

    # kind: explicit, else official iff a benchmark_version is recorded.
    raw_kind = summary.get("kind")
    if raw_kind in (RunKind.official.value, RunKind.casual.value):
        kind = RunKind(raw_kind)
    else:
        kind = RunKind.official if benchmark_version else RunKind.casual

    # benchmark: which benchmark this run played (drives the per-benchmark
    # leaderboard filter). Explicit when stamped by the executor; for LEGACY
    # official runs predating the multi-benchmark split (a benchmark_version but
    # no benchmark id) fall back to the full ladder — those runs were scored on
    # the full 20-gate ladder, so they belong to pokebench-full.
    benchmark = summary.get("benchmark")
    if benchmark is None and kind == RunKind.official and benchmark_version:
        benchmark = _LEGACY_OFFICIAL_BENCHMARK

    # referee scorecard fields (only a meaningful referee block has gates).
    has_gates = bool(referee) and bool(referee.get("gates"))
    termination_reason = summary.get("termination_reason")
    if termination_reason is None and has_gates:
        termination_reason = referee.get("termination_reason")

    # status: explicit, else infer — terminated if a missed-gate termination was
    # latched, else completed (legacy runs on disk are finished).
    raw_status = summary.get("status")
    valid_statuses = {s.value for s in RunStatus}
    if raw_status in valid_statuses:
        status = RunStatus(raw_status)
    elif termination_reason:
        status = RunStatus.terminated
    else:
        status = RunStatus.completed

    # ladder node count for this run: read from the ladder the run used
    # (config.json["referee"]["checkpoints"]) or fall back to the default.
    config = _load_json(run_dir / "config.json") or {}
    ladder_path = None
    ref_cfg = config.get("referee")
    if isinstance(ref_cfg, dict):
        cp = ref_cfg.get("checkpoints")
        if isinstance(cp, str) and cp:
            ladder_path = Path(cp)
    total_gates = _ladder_node_count(ladder_path)

    # gates_reached / furthest derived the SAME way report.py counts.
    gates_reached = 0
    furthest_gate = None
    furthest_gate_turn = None
    gate_turns = None
    if has_gates:
        gates = referee["gates"]
        # Per-gate stamps in ladder order; only cleared gates carry a turn.
        gate_turns = {
            g["id"]: g["turn"] for g in gates
            if isinstance(g.get("id"), str) and g.get("status") in _CLEARED_STATUSES
            and isinstance(g.get("turn"), int) and not isinstance(g.get("turn"), bool)
        }
        gates_reached = sum(
            1 for g in gates if g.get("status") in _CLEARED_STATUSES
        )
        # total_gates == number of nodes == len(scorecard gates); prefer the
        # scorecard length when the ladder file is gone (keeps report agreement).
        if not total_gates:
            total_gates = len(gates)
        furthest_gate = referee.get("furthest")
        if furthest_gate is not None:
            for g in gates:
                if g.get("id") == furthest_gate:
                    furthest_gate_turn = g.get("turn")
                    break

    # Between-gate progress — ``referee.progress`` is the ProgressTracker's
    # summary (src/referee/progress.py). Absent on every run before 2026-09-09
    # and on runs without a walk graph; then ``progress`` stays None and the
    # row ranks on gates_reached (RunSummary.rank_score).
    progress = None
    leg_gate = leg_fraction = leg_dmin = leg_dopen = None
    prog = referee.get("progress") if has_gates else None
    if isinstance(prog, dict):
        progress = _num(prog.get("progress"))
        leg = prog.get("current_leg")
        if isinstance(leg, dict):
            leg_gate = leg.get("node_id") if isinstance(leg.get("node_id"), str) else None
            leg_fraction = _num(leg.get("fraction"))
            leg_dmin = _int(leg.get("d_min"))
            leg_dopen = _int(leg.get("d_open"))
            # Summaries written before 2026-09-11 let an OPEN leg reach 1.0 (the
            # run stood on the target tile without the stamp). The tracker now
            # caps that at OPEN_LEG_FRACTION_CAP; apply the same cap here so a
            # stored row never reads as a full clear it did not make. The
            # current leg is by definition unfinished, so no status check.
            if leg_fraction is not None and leg_fraction > OPEN_LEG_FRACTION_CAP:
                gates_n = _int(prog.get("gates_reached"))
                if gates_n is None:
                    gates_n = gates_reached
                leg_fraction = OPEN_LEG_FRACTION_CAP
                progress = float(gates_n) + OPEN_LEG_FRACTION_CAP

    return RunSummary(
        run_id=run_id,
        label=summary.get("label"),
        kind=kind,
        model=model,
        model_resolved=model_resolved,
        config_stem=summary.get("config_stem") or _infer_config_stem(run_dir.name),
        benchmark=benchmark,
        benchmark_version=benchmark_version,
        status=status,
        started_at=started_at,
        ended_at=summary.get("ended_at"),
        turns=turns,
        duration_s=duration_s,
        total_cost_usd=total_cost_usd,
        avg_cost_per_turn_usd=avg_cost_per_turn_usd,
        avg_s_per_turn=avg_s_per_turn,
        furthest_gate=furthest_gate,
        furthest_gate_turn=furthest_gate_turn,
        gates_reached=gates_reached,
        total_gates=total_gates,
        gate_turns=gate_turns,
        avg_inputs_per_turn=avg_inputs_per_turn,
        input_counts=input_counts,
        avg_output_tokens_per_turn=avg_output_tokens_per_turn,
        thinking_share=thinking_share,
        progress=progress,
        leg_gate=leg_gate,
        leg_fraction=leg_fraction,
        leg_distance_min=leg_dmin,
        leg_distance_open=leg_dopen,
        termination_reason=termination_reason,
        error=summary.get("error") if isinstance(summary.get("error"), str) else None,
        crash=summary.get("crash") if isinstance(summary.get("crash"), dict) else None,
        record=_record_spec(config),
        projection_version=PROJECTION_VERSION,
        continued_from=summary.get("continued_from"),
        # WHICH HARNESS ran. ``run_summary.json["agent_type"]`` is stamped only
        # when an AppendAgent was active (turn.py:2844), so its ABSENCE is the
        # legacy sliding-window agent — hence the "current" default rather than
        # None. First reader of a field that has been written since the append
        # work landed. A non-string value (a hand-edited summary) falls back the
        # same way: this row is a display + guard input, never worth a crash.
        harness=(
            summary["agent_type"]
            if isinstance(summary.get("agent_type"), str) and summary["agent_type"]
            else "current"
        ),
        # The turn cap the run ran under, as recorded by the writer. Absent on an
        # official run (pace is its only bound) and on any run that predates the
        # summary carrying it — both mean "no cap to show", which is None, NOT 0:
        # a 0 would render as a run that was allowed no turns.
        max_turns=(
            summary["max_turns"]
            if isinstance(summary.get("max_turns"), int)
            and not isinstance(summary.get("max_turns"), bool)
            else None
        ),
        # resumed: explicit when the writer stamped it; else infer from a
        # continued_from link so legacy continued runs still flag as multi-segment.
        resumed=bool(session.get("resumed") or summary.get("continued_from")),
    )
