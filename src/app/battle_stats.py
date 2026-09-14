"""Battle and movement figures for a run's board row.

Plan §3.3 / §4.4, ``artifacts/battle-and-movement-fidelity/plan.md``
(2026-09-14). Two sources, one shape:

- **live** — the referee polled the in-battle bit and the battle counters every
  turn (``referee["battles"]`` in run_summary.json, src/referee/battles.py).
- **backfill** — a run from before the live reads: exact counters and trainer
  flags every 10 turns from savepoints (``battle_backfill.json``) plus the
  per-turn battle/overworld state classified from screenshots
  (``state_backfill.json``). :func:`synthesize_records` turns the two into the
  per-turn record list the live tracker would have produced, so
  :class:`BattleTracker` computes the same summary either way. Counts are exact;
  WHICH turn a contained battle happened in is not, and a segment's turns are
  as good as the classifier (97.7 % on the labelled set).

Movement efficiency (decision 4A): Σ shortest path over the closed MAP legs ÷
Σ overworld steps on those legs. Steps per leg come from, in order of trust,
the per-input trace (``steps_source`` "trace" on the leg), a video backfill
(``steps_backfill.json``, per turn), or the tracker's between-poll bound.
Every figure carries its fidelity so a row never presents a bound as a
measurement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.referee.battles import BattleTracker, MANDATORY_TRAINERS  # noqa: F401  (re-exported for the projection)

LIVE_MIN_COVERAGE = 0.9  # polls ÷ turns below this → the live series is too gappy; fall back


def _load_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- battles -------------------------------------------------------------------

def synthesize_records(records: list, states: dict[int, bool]) -> list[tuple]:
    """Per-turn records from 10-turn savepoint records + per-turn start states.

    ``records`` — ``[turn, in_battle, total, wild, trainer, [ids]]`` at savepoint
    turns (exact). ``states[t]`` — was turn ``t`` STARTED in a battle (screenshot
    classifier); the state after turn ``t`` is therefore ``states[t+1]``.

    Inside each window (a, b]: visible battle segments are the maximal runs of
    after-turn battle states; the window's counter increments are assigned one
    per visible segment at the segment's opening turn (trainer increments to
    the LONGEST segments first — a trainer battle cannot be fled), the rest at
    ``b`` as contained battles. Segments beyond the count are classifier noise
    and are cleared. New trainer flags land on the turn the last trainer
    segment of the window closed, else on ``b``, so the tracker credits the
    right attempt.
    """
    recs = sorted((tuple(r) for r in records if isinstance(r, (list, tuple)) and len(r) == 6), key=lambda r: r[0])
    if not recs:
        return []
    out: list[tuple] = []
    prev: Optional[tuple] = None
    for rec in recs:
        b, in_b, total, wild, trainer, flags = int(rec[0]), bool(rec[1]), int(rec[2]), int(rec[3]), int(rec[4]), tuple(sorted(rec[5]))
        if prev is None:
            a = 0
            p_total = p_wild = p_trainer = 0
            p_flags: tuple = ()
            p_in = False
        else:
            a, p_in, p_total, p_wild, p_trainer, p_flags = prev[0], prev[1], prev[2], prev[3], prev[4], prev[5]
        turns = list(range(a + 1, b + 1))
        # after-state per turn: classifier for interior turns, the exact bit at b
        after = {t: bool(states.get(t + 1, False)) for t in turns}
        if turns:
            after[b] = in_b
        # visible segments [start, end]: a battle that was already running at
        # the window start (p_in) is not a new one — it extends no segment.
        segs: list[list[int]] = []
        was = p_in
        for t in turns:
            if after[t]:
                if not was:
                    segs.append([t, t])
                elif segs and segs[-1][1] == t - 1:
                    segs[-1][1] = t
            was = after[t]
        d_total, d_wild, d_trainer = total - p_total, wild - p_wild, trainer - p_trainer
        # too many visible segments → keep the longest d_total, clear the rest
        segs.sort(key=lambda s: -(s[1] - s[0]))
        kept, noise = segs[:max(d_total, 0)], segs[max(d_total, 0):]
        for s in noise:
            for t in range(s[0], s[1] + 1):
                if t != b:
                    after[t] = False
        kept.sort(key=lambda s: -(s[1] - s[0]))
        kinds = ["trainer"] * min(d_trainer, len(kept)) + ["wild"] * max(len(kept) - min(d_trainer, len(kept)), 0)
        opens: dict[int, list[str]] = {}
        for s, k in zip(kept, kinds):
            opens.setdefault(s[0], []).append(k)
        contained_trainer = max(d_trainer - kinds.count("trainer"), 0)
        contained_wild = max(d_total - len(kept) - contained_trainer, 0)
        new_flags = [f for f in flags if f not in p_flags]
        trainer_segs = [s for s, k in zip(kept, kinds) if k == "trainer"]
        flag_turn = max(s[1] + 1 for s in trainer_segs) if trainer_segs else b
        flag_turn = min(flag_turn, b)
        # emit per-turn records
        cur_total, cur_wild, cur_trainer = p_total, p_wild, p_trainer
        cur_flags = list(p_flags)
        for t in turns:
            for k in opens.get(t, []):
                cur_total += 1
                if k == "trainer":
                    cur_trainer += 1
                else:
                    cur_wild += 1
            if t == b:
                cur_total += contained_trainer + contained_wild
                cur_trainer += contained_trainer
                cur_wild += contained_wild
            if t == flag_turn:
                cur_flags = sorted(set(cur_flags) | set(new_flags))
            out.append((t, after[t], cur_total, cur_wild, cur_trainer, tuple(cur_flags)))
        # exactness at b: the savepoint's own values win
        if turns:
            out[-1] = (b, in_b, total, wild, trainer, flags)
        prev = (b, in_b, total, wild, trainer, flags)
    return out


def cap_records(records: list, turns: int) -> list:
    """Drop backfill records after the run's counted turns. An adjudicated run
    (gpt-6-astra low: the badge landed at turn 145, the referee missed the flag
    and the model played to 224) keeps ``session.total_turns`` at the adjudicated
    end, so savepoints past it describe play the run is not scored on. The first
    record past the end is kept, clipped to ``turns``, so a battle in the last
    few turns is still counted (its counters may include up to one window of
    excluded play)."""
    if not turns:
        return list(records)
    kept = [r for r in records if isinstance(r, (list, tuple)) and len(r) == 6 and int(r[0]) <= turns]
    later = [r for r in records if isinstance(r, (list, tuple)) and len(r) == 6 and int(r[0]) > turns]
    if later and (not kept or int(kept[-1][0]) < turns):
        first = min(later, key=lambda r: int(r[0]))
        kept.append([turns, *first[1:]])
    return kept


def battle_summary(run_dir: Path, referee: Optional[dict], turns: int) -> tuple[Optional[dict], Optional[str]]:
    """``(BattleTracker.summary(), fidelity)`` — fidelity "live", "backfill",
    "savepoint" (counters only, no per-turn states) or ``(None, None)``.
    Backfill records and states past ``turns`` are dropped (:func:`cap_records`)."""
    live = (referee or {}).get("battles") if isinstance(referee, dict) else None
    if isinstance(live, dict) and live.get("available") and turns and (live.get("polls") or 0) >= LIVE_MIN_COVERAGE * turns:
        return live, "live"
    bf = _load_json(run_dir / "battle_backfill.json")
    if not bf or not bf.get("records"):
        return None, None
    records = cap_records(bf["records"], turns)
    sf = _load_json(run_dir / "state_backfill.json")
    states: dict[int, bool] = {}
    for k, v in ((sf or {}).get("states") or {}).items():
        try:
            t = int(k)
        except (TypeError, ValueError):
            continue
        if not turns or t <= turns + 1:
            states[t] = bool(v)
    tracker = BattleTracker()
    if states:
        for r in synthesize_records(records, states):
            tracker.record(*r)
        return tracker.summary(), "backfill"
    tracker.load_state({"battle_records": records})
    return tracker.summary(), "savepoint"


# --- movement ------------------------------------------------------------------

def load_steps_backfill(run_dir: Path) -> Optional[dict[int, int]]:
    """Per-turn overworld steps from a video backfill, ``{turn: steps}``. Accepts
    ``{"turns": [{"turn", "overworld_steps"}]}`` or ``{"steps": {"T": n}}``."""
    data = _load_json(run_dir / "steps_backfill.json")
    if not data:
        return None
    out: dict[int, int] = {}
    for row in data.get("turns") or []:
        try:
            out[int(row["turn"])] = int(row["overworld_steps"])
        except (KeyError, TypeError, ValueError):
            continue
    for k, v in (data.get("steps") or {}).items():
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out or None


def movement(referee: Optional[dict], video_steps: Optional[dict[int, int]]) -> Optional[dict[str, Any]]:
    """Directness over the closed map legs (decision 4A), with fidelity.

    Returns ``{shortest, steps, efficiency, legs, fidelity}`` or None when no
    closed map leg has a shortest path. ``fidelity`` is "trace" / "video" /
    "bound" when every leg used that source, else "mixed".
    """
    if not isinstance(referee, dict):
        return None
    gates = referee.get("gates") or []
    kind = {g.get("id"): g.get("type") for g in gates if isinstance(g, dict)}
    legs = ((referee.get("progress") or {}).get("legs")) or []
    shortest = steps = 0
    used: list[dict[str, Any]] = []
    sources: set[str] = set()
    for leg in legs:
        if not isinstance(leg, dict) or leg.get("status") != "closed" or kind.get(leg.get("node_id")) != "map":
            continue
        d = leg.get("d_open")
        if not isinstance(d, int) or d <= 0:
            continue
        opened, closed = leg.get("opened_turn"), leg.get("closed_turn")
        src = "bound"
        s = leg.get("steps_walked")
        if video_steps and isinstance(opened, int) and isinstance(closed, int) and closed > opened \
                and all(t in video_steps for t in range(opened + 1, closed + 1)):
            s = sum(video_steps[t] for t in range(opened + 1, closed + 1))
            src = "video"
        elif leg.get("steps_source") == "trace":
            src = "trace"
        elif leg.get("steps_source") == "mixed":
            src = "mixed"
        if not isinstance(s, int):
            continue
        shortest += d
        steps += max(s, d)
        sources.add(src)
        used.append({"node_id": leg.get("node_id"), "d_open": d, "steps": s, "source": src})
    if not used or steps <= 0:
        return None
    fidelity = sources.pop() if len(sources) == 1 else "mixed"
    return {"shortest": shortest, "steps": steps, "efficiency": shortest / steps, "legs": used, "fidelity": fidelity}


__all__ = ["battle_summary", "synthesize_records", "movement", "load_steps_backfill", "MANDATORY_TRAINERS"]
