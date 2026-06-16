"""Project a run folder's nested ``run_summary.json`` → flat :class:`RunSummary`.

The nested writer (``agent/turn.py:_write_run_summary``) is the source of truth;
this module reads it (plus ``config.json`` for the ladder pointer) and produces
the flat denormalized index entry. It must tolerate LEGACY runs that predate the
control-center fields — missing ``referee``, missing top-level ``kind``/``status``,
missing ``llm_alias`` — and still produce a valid entry, inferring defensively.

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

# Benchmark legacy official runs (a benchmark_version but no benchmark id) map to.
# They were scored on the full 20-gate ladder, so they belong to pokebench-full.
_LEGACY_OFFICIAL_BENCHMARK = "pokebench-full"

# Default ladder used when a run doesn't record which one it ran against, or the
# recorded one no longer exists. Read at runtime — never hardcode the count.
_DEFAULT_LADDER = Path("configs/checkpoints-firered-v1.yaml")

# Status values the report treats as "cleared" for a gate (mirror report.py).
_CLEARED_STATUSES = ("done", "auto")

# Cache: ladder path (as a string) -> node count, so a scan over many runs
# sharing one ladder doesn't re-parse the YAML per run.
_ladder_node_count_cache: dict[str, int] = {}


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
    if has_gates:
        gates = referee["gates"]
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
        termination_reason=termination_reason,
        continued_from=summary.get("continued_from"),
    )
