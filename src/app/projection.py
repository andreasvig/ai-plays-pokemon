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
from typing import Any

from src.app import battle_stats, replay, route
from src.app.models import RunKind, RunStatus, RunSummary
from src.config import _load_models_registry, renamed_models
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
PROJECTION_VERSION = 15  # 15 (2026-09-18): list_price_* — what a run played under a cloaked (free) listing would cost at the model's price once the lab announced it, plus the `former:` rename so the row carries the name the model has today; 14 (2026-09-16): endpoint_price_usd_per_m — the serving endpoint's LIST price, so the board can tell a free model from a cheap one; 13 (2026-09-15): wall presses charged against movement_efficiency + the per-run input breakdown (artifacts/wasted-inputs/plan.md); 12 (2026-09-15): legs + battles replayed from events.jsonl (src/app/replay.py), so a rule change needs no back-fill; 11: route_points / route_coverage; 10 (2026-09-14): starter leg re-scored to the ball taken (score_to reached) in every run_summary; 9: Oak's Parcel leg re-scored (scripts/backfill_parcel_leg.py); 8: gate_times_s / gate_costs_usd / movement_legs

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


def _endpoint_price(run_dir: Path) -> dict[str, float] | None:
    """The Player endpoint's LIST price, USD per million prompt/completion tokens.

    Raw, not a verdict. The board derives "this model is free" from a pair of
    zeroes and shows N/A instead of $0.00 for it — a free model is not the
    cheapest model, it is an unpriced one, and letting it place on a cost axis
    would hand it the whole price frontier at x=0 (Andreas 2026-09-16).

    The source is conversation/endpoint-pricing.json, which the append agent
    snapshots once per run from OpenRouter. So this is what the PLAYER's endpoint
    charged — deliberately not the run's total, which also carries OCR (a paid
    model: gpt-oss-120b), and would therefore be non-zero on a free run and give
    the opposite answer.

    None when the file is missing (every pre-append run) or the serving endpoint
    cannot be identified. None must read as UNKNOWN downstream, never as free:
    the failure that matters here is a paid model quietly shown as unpriced, so
    the ambiguous cases all fall on the priced side.
    """
    pricing = _load_json(run_dir / "conversation" / "endpoint-pricing.json")
    endpoints = (pricing or {}).get("endpoints") or []
    if not endpoints:
        return None
    # A profiled run pins one tag, and the resolved profile is saved with the run
    # config — NOT in run_summary.json, which carries no endpoint at all. With
    # exactly one served endpoint there is nothing to disambiguate. Anything else
    # (an unprofiled run, several tags, a renamed tag) is a run whose endpoint
    # cannot be named from here, and it stays unknown rather than guessing the
    # cheapest or the first.
    pinned = (_load_json(run_dir / "config.json") or {}).get("_provider_profile", {}).get("endpoint")
    if pinned:
        # A pinned tag is an exact claim. If it is not among the served endpoints
        # the run's routing and this file disagree, and the lone-endpoint shortcut
        # below must NOT paper over it — that is how a $0 endpoint would get
        # attributed to a run that was pinned somewhere else entirely.
        match = [e for e in endpoints if e.get("tag") == pinned]
    else:
        # Nothing pinned (an unprofiled run). One served endpoint is not a guess;
        # several are.
        match = endpoints
    if len(match) != 1:
        return None
    return _per_million(match[0].get("pricing"))


def _per_million(prices: Any) -> dict[str, float] | None:
    """One endpoint's ``pricing`` block in USD per MILLION tokens.

    ``prompt`` and ``completion`` are required — an endpoint missing either is
    not priced. The two cache rates are optional and absent means something
    different for each, which is measured rather than assumed (2026-09-18, over
    all 27 published runs):

    * no ``input_cache_read`` → cache reads cost NOTHING. Checked against the
      billed total on glm, deepseek, kimi, grok and muse, which list no read
      price and whose bills come out exact only when cached tokens are free.
      Pricing them at the prompt rate instead overstates every one of them.
    * no ``input_cache_write`` → a cache write is an ordinary prompt token, at
      the prompt rate. OpenAI, Anthropic and Google all list one and all charge
      a premium; the bills only reconcile when it is applied.
    """
    if not isinstance(prices, dict):
        return None
    try:
        # OpenRouter quotes USD per TOKEN as a string; per million is the readable unit.
        out = {"prompt": float(prices["prompt"]) * 1_000_000,
               "completion": float(prices["completion"]) * 1_000_000}
    except (KeyError, TypeError, ValueError):
        return None
    for key in ("input_cache_read", "input_cache_write"):
        raw = prices.get(key)
        if raw is None:
            continue
        try:
            out[key] = float(raw) * 1_000_000
        except (TypeError, ValueError):
            continue
    return out


def _list_price(run_dir: Path) -> dict[str, float] | None:
    """Today's list price for a model the run played under a CLOAKED listing.

    ``conversation/endpoint-pricing-revealed.json`` exists only where the two
    differ: a stealth listing serves free, and the same weights get a name and
    a price when the lab announces them. The run-time snapshot stays the record
    of what was charged; this is the price the model carries now, written by
    ``scripts/snapshot_revealed_pricing.py``.

    One endpoint or none — a revealed model that serves from several is a run
    whose rate cannot be named, and an unnameable rate must stay None rather
    than pick the cheapest.
    """
    revealed = _load_json(run_dir / "conversation" / "endpoint-pricing-revealed.json")
    endpoints = (revealed or {}).get("endpoints") or []
    if len(endpoints) != 1:
        return None
    return _per_million(endpoints[0].get("pricing"))


def token_usage(run_dir: Path) -> dict[str, int] | None:
    """Every token the Player was billed for, from the ``llm_request_usage`` events.

    The events are the one accumulator that is internally consistent: prompt,
    cached, written and completion all come off the same provider usage block,
    call by call. ``run_summary.json``'s ``cost.total_*_tokens`` is a separate
    count and the two DISAGREE on at least one run (2026-09-11 gpt-6-astra-low:
    3,416,654 against the events' 5,290,213), which is why this does not read it.

    None when the run has no usage events at all.
    """
    keys = {"prompt": "request_tokens", "cached": "cached_tokens",
            "written": "cache_write_tokens", "completion": "response_tokens"}
    # A run logs the same call under BOTH names — `llm_request_usage` and the
    # legacy `turn_usage` — so these are summed apart and the newer one wins,
    # exactly as ``_gate_clock`` does with the billed cost. Adding them together
    # doubles every token the run spent.
    totals = {"llm_request_usage": dict.fromkeys(keys, 0), "turn_usage": dict.fromkeys(keys, 0)}
    seen = set()
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return None
    try:
        with path.open() as fh:
            for line in fh:
                if '"llm_request_usage"' not in line and '"turn_usage"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind not in totals:
                    continue
                seen.add(kind)
                for name, field in keys.items():
                    value = event.get(field)
                    if isinstance(value, (int, float)):
                        totals[kind][name] += int(value)
    except OSError:
        return None
    for kind in ("llm_request_usage", "turn_usage"):
        if kind in seen:
            return totals[kind]
    return None


def token_cost(usage: dict[str, int] | None, price_per_m: dict[str, float] | None) -> float | None:
    """What those tokens cost at that list price, in USD. None if either is absent.

    THE formula, in one place, because the only thing that makes the number
    trustworthy is that it reproduces bills nobody derived: run it over the
    published corpus against each run's billed ``llm_usd`` and 22 of 27 land
    EXACTLY, to the cent (tests/test_projection_list_cost.py). The five that do
    not are four continued runs, whose bill and whose events cover different
    spans, and the one run whose own token counters disagree with its events.

    Cached and written tokens are SUBSETS of the prompt count, each billed at
    its own rate instead of the prompt rate — not extras to be added on top.
    """
    if not usage or not price_per_m:
        return None
    prompt = price_per_m["prompt"]
    fresh = usage["prompt"] - usage["cached"] - usage["written"]
    return (fresh * prompt
            + usage["cached"] * price_per_m.get("input_cache_read", 0.0)
            + usage["written"] * price_per_m.get("input_cache_write", prompt)
            + usage["completion"] * price_per_m["completion"]) / 1_000_000


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


def _gate_clock(run_dir: Path, gate_turns: dict[str, int] | None, list_price: dict[str, float] | None = None):
    """Wall time and money at each cleared gate, from ``events.jsonl``.

    A gate stamped at turn T is reached when turn T's inputs have settled, which
    is the moment turn T+1 starts — so time-at-gate is the elapsed wall clock at
    ``turn_start`` of T+1 (the run's end when T was the last turn). Elapsed time
    counts only the stretches between a segment's ``run_start`` and its
    ``run_end``: a continued run's events file carries the source's segment and
    then its own, two days apart, and the days between were not played. Cost is
    the sum of ``cost_usd`` over every ``llm_request_usage`` up to and including
    turn T (``turn_usage`` is the same event under its older name — read only
    when the newer one is absent, never both). None without an events file or
    without gate stamps.

    With a ``list_price`` a THIRD dict comes back: the same running total priced
    at that rate instead of at what was charged. It exists for the one kind of
    run whose charged column is twelve zeroes that mean nothing — a model played
    under a cloaked free listing — so the ladder can show the same basis as the
    header above it. Without a price it is None and the ladder shows the bill.
    """
    if not gate_turns:
        return None, None, None
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return None, None, None
    start_at: dict[int, float] = {}          # turn → elapsed seconds at its first turn_start
    cost_new: dict[int, float] = {}          # turn → Σ cost_usd (llm_request_usage)
    cost_old: dict[int, float] = {}          # turn → Σ cost_usd (turn_usage, legacy name)
    # Per event NAME, like the pair above: a run carries the same call under both
    # `llm_request_usage` and the legacy `turn_usage`, so summing across the two
    # doubles it. That is not hypothetical — it is what this did on its first
    # run, and the ladder came out at $11.17 against a $5.58 header.
    list_new: dict[int, float] = {}
    list_old: dict[int, float] = {}
    elapsed = 0.0
    seg_start: float | None = None
    last_ts: float | None = None
    markers = ('"turn_start"', '"run_start"', '"run_end"', '"llm_request_usage"', '"turn_usage"')
    try:
        with path.open() as fh:
            for line in fh:
                if not any(m in line for m in markers):
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = e.get("type")
                ts = e.get("timestamp")
                turn = e.get("turn")
                if kind in ("llm_request_usage", "turn_usage"):
                    c = e.get("cost_usd")
                    if isinstance(c, (int, float)) and isinstance(turn, int):
                        bucket = cost_new if kind == "llm_request_usage" else cost_old
                        bucket[turn] = bucket.get(turn, 0.0) + float(c)
                    if list_price and isinstance(turn, int):
                        # Same call, priced at the list rate. `token_cost` is the
                        # one formula, so the ladder and the row cannot drift.
                        one = token_cost({"prompt": e.get("request_tokens") or 0,
                                          "cached": e.get("cached_tokens") or 0,
                                          "written": e.get("cache_write_tokens") or 0,
                                          "completion": e.get("response_tokens") or 0}, list_price)
                        if one is not None:
                            side = list_new if kind == "llm_request_usage" else list_old
                            side[turn] = side.get(turn, 0.0) + one
                    continue
                if not isinstance(ts, (int, float)):
                    continue
                if kind == "run_start":
                    if seg_start is not None and last_ts is not None:   # a segment that never logged run_end
                        elapsed += max(0.0, last_ts - seg_start)
                    seg_start = float(ts)
                elif kind == "run_end":
                    if seg_start is not None:
                        elapsed += max(0.0, ts - seg_start)
                    seg_start = None
                elif kind == "turn_start" and isinstance(turn, int) and turn not in start_at:
                    start_at[turn] = elapsed + (max(0.0, ts - seg_start) if seg_start is not None else 0.0)
                last_ts = float(ts)
    except OSError:
        return None, None, None
    if seg_start is not None and last_ts is not None:
        elapsed += max(0.0, last_ts - seg_start)
    if not start_at:
        return None, None, None
    costs = cost_new or cost_old
    cost_list = list_new or list_old
    times: dict[str, float] = {}
    money: dict[str, float] = {}
    listed: dict[str, float] = {}
    last_turn = max(start_at)
    for gate, t in gate_turns.items():
        if not isinstance(t, int):
            continue
        if t + 1 in start_at:
            times[gate] = round(start_at[t + 1] - start_at[min(start_at)], 3)
        elif t >= last_turn:
            times[gate] = round(elapsed - start_at[min(start_at)], 3)
        if costs:
            money[gate] = round(sum(v for k, v in costs.items() if k <= t), 6)
        if cost_list:
            listed[gate] = round(sum(v for k, v in cost_list.items() if k <= t), 6)
    return (times or None), (money or None), (listed or None)


def _input_breakdown(referee: dict | None) -> dict | None:
    """The run's per-input census for the report, or None for a run with no
    per-input trace (everything before 2026-09-14 — 23 of the 25 rows published
    on 2026-09-15). None, never a zeroed dict: a run that never measured the
    buckets must not render as a run that measured them and found nothing.
    """
    inputs = (referee or {}).get("inputs") if isinstance(referee, dict) else None
    if not isinstance(inputs, dict) or not inputs.get("inputs"):
        return None
    from src.referee import trace as _trace
    keys = ("inputs", "overworld_inputs", "overworld_steps", "inputs_lost",
            *_trace.INPUT_BUCKETS, "traced_turns", "wall_rate", "worst_turns")
    return {k: inputs[k] for k in keys if k in inputs}


def _battle_fields(battles: dict | None, fidelity: str | None, turns: int) -> dict:
    """RunSummary battle fields from a BattleTracker summary. Counts are exact
    for every fidelity; turn figures need per-turn states, so a
    "savepoint"-only summary leaves them None."""
    if not battles or not battles.get("available"):
        return {}
    per_turn = fidelity in ("live", "backfill")
    trainers = [{k: g.get(k) for k in ("group", "id", "name", "attempts", "turns", "won", "mandatory")}
                for g in battles.get("trainers") or []]
    if not per_turn:
        for g in trainers:
            g["turns"] = None
    started = battles.get("turns_started_in_battle")
    return {
        "wild_battles": _int((battles.get("wild") or {}).get("count")),
        "wild_battle_turns": _int((battles.get("wild") or {}).get("turns")) if per_turn else None,
        "trainer_battles": trainers,
        "trainer_battle_turns": _int((battles.get("trainer") or {}).get("turns")) if per_turn else None,
        "battle_turn_share": (started / turns) if per_turn and turns and isinstance(started, int) else None,
        "battle_fidelity": fidelity,
    }


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
    # The referee's DERIVED halves (legs, battles) are replayed from
    # events.jsonl rather than read from the stored summary, so today's rules
    # score every traced run — including one played by a daemon still holding
    # older code — and a rule change never needs a back-fill
    # (src/app/replay.py). A run with no per-input trace has nothing to replay
    # and keeps its stored numbers.
    referee = replay.referee_view(run_dir, summary.get("referee")) or None

    # --- nested → flat (always present in the nested writer) ---
    # The name the model has TODAY, not the one it had the night it played: a
    # cloaked listing is renamed out from under its own runs (see
    # config.renamed_models). Both halves move together or the board would show
    # `pareto` sitting on a `stealth/` id and pick the wrong vendor row.
    renamed_aliases, renamed_ids = renamed_models(_load_models_registry())
    model = session.get("llm_alias") or session.get("llm_model") or "unknown"
    model = renamed_aliases.get(model, model)
    model_resolved = session.get("llm_model")
    model_resolved = renamed_ids.get(model_resolved, model_resolved)
    turns = session.get("total_turns") or 0
    duration_s = session.get("duration_seconds") or 0.0
    started_at = session.get("started_at")
    total_cost_usd = cost.get("total_usd") or 0.0

    avg_cost_per_turn_usd = _safe_div(total_cost_usd, turns)
    endpoint_price_usd_per_m = _endpoint_price(run_dir)
    # What the run would have cost at the model's CURRENT list price — set only
    # where that differs from what it was billed, i.e. a run played free under a
    # cloaked listing. Derived at read time from the run's own token counts and
    # today's snapshot; neither of those is a figure anybody wrote down as a
    # dollar amount, and the billed total above is left exactly as it was.
    list_price_usd_per_m = _list_price(run_dir)
    list_price_cost_usd = token_cost(token_usage(run_dir), list_price_usd_per_m)
    list_price_per_turn_usd = _safe_div(list_price_cost_usd, turns) if list_price_cost_usd else None
    avg_s_per_turn = _safe_div(duration_s, turns)
    avg_inputs_per_turn, input_counts = _input_stats(run_dir)
    avg_output_tokens_per_turn, thinking_share = _output_token_stats(run_dir, turns, cost)
    battles, battle_fidelity = battle_stats.battle_summary(run_dir, referee, turns)
    move = battle_stats.movement(referee, battle_stats.load_steps_backfill(run_dir),
                                 (summary.get("session") or {}).get("total_turns"))
    run_route = route.load_route(run_dir)

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
    gate_times_s, gate_costs_usd, gate_list_costs_usd = _gate_clock(run_dir, gate_turns, list_price_usd_per_m)

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
        endpoint_price_usd_per_m=endpoint_price_usd_per_m,
        list_price_usd_per_m=list_price_usd_per_m,
        list_price_cost_usd=list_price_cost_usd,
        list_price_per_turn_usd=list_price_per_turn_usd,
        avg_s_per_turn=avg_s_per_turn,
        furthest_gate=furthest_gate,
        furthest_gate_turn=furthest_gate_turn,
        gates_reached=gates_reached,
        total_gates=total_gates,
        gate_turns=gate_turns,
        gate_times_s=gate_times_s,
        gate_costs_usd=gate_costs_usd,
        gate_list_costs_usd=gate_list_costs_usd,
        avg_inputs_per_turn=avg_inputs_per_turn,
        input_counts=input_counts,
        avg_output_tokens_per_turn=avg_output_tokens_per_turn,
        thinking_share=thinking_share,
        **_battle_fields(battles, battle_fidelity, turns),
        overworld_steps=move["steps"] if move else None,
        shortest_steps=move["shortest"] if move else None,
        movement_efficiency=move["efficiency"] if move else None,
        steps_fidelity=move["fidelity"] if move else None,
        movement_legs=move["legs"] if move else None,
        route_points=len(run_route["visits"]) if run_route else None,
        route_coverage=run_route["coverage"] if run_route else None,
        charged_steps=move["charged"] if move else None,
        walls_hit=move["walls"] if move else None,
        wall_rate=(referee.get("inputs") or {}).get("wall_rate") if isinstance(referee, dict) else None,
        input_breakdown=_input_breakdown(referee),
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
