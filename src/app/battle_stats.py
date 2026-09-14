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
import re
from pathlib import Path
from typing import Any, Optional

from src.referee.battles import BattleTracker, MANDATORY_TRAINERS, TRAINER_NAMES  # noqa: F401  (re-exported for the projection)

# "BUG CATCHER RICK would like to battle!" → 102, keyed by the GIVEN name (the
# last word): an OCR line that only caught "BUG CATCHER" names the kind, not the
# trainer. The rival is left out (his id depends on the starter; the flags
# always name him).
_OCR_TRAINERS = {name.upper().split()[-1]: tid for tid, name in TRAINER_NAMES.items() if not name.startswith("Rival")}
_TRAINER_RE = re.compile(r"([A-Z][A-Z .'-]{2,40}?)\s+would like to battle", re.I)
_WILD_RE = re.compile(r"wild\s+[A-Z][A-Za-z'.\- ]{1,20}\s+appeared", re.I)


def ocr_hints(run_dir: Path, upto: Optional[int] = None) -> dict[int, dict[str, Any]]:
    """What the run's own OCR text says began during each turn: ``{turn: {"kind":
    "trainer"|"wild", "trainer_id": id|None}}``. The ``ocr_flush`` of turn N is
    the text captured while turn N-1's inputs ran, so a battle intro read at
    flush N began during turn N-1. A trainer intro outranks a wild one in the
    same window (both can occur; the trainer fight is the one that lasts)."""
    path = run_dir / "events.jsonl"
    out: dict[int, dict[str, Any]] = {}
    if not path.is_file():
        return out
    try:
        with path.open() as fh:
            for line in fh:
                if '"ocr_flush"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "ocr_flush":
                    continue
                try:
                    began = int(e.get("turn")) - 1
                except (TypeError, ValueError):
                    continue
                if began < 1 or (upto and began > upto):
                    continue
                text = str(e.get("cleaned") or "")
                m = _TRAINER_RE.search(text)
                if m:
                    words = re.sub(r"[^A-Z ]", " ", m.group(1).upper()).split()
                    tid = next((_OCR_TRAINERS[w] for w in reversed(words) if w in _OCR_TRAINERS), None)
                    out[began] = {"kind": "trainer", "trainer_id": tid}
                elif _WILD_RE.search(text) and began not in out:
                    out[began] = {"kind": "wild", "trainer_id": None}
    except OSError:
        return out
    return out

LIVE_MIN_COVERAGE = 0.9  # polls ÷ turns below this → the live series is too gappy; fall back


def _load_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- battles -------------------------------------------------------------------

def synthesize_records(records: list, states: dict[int, bool], hints: Optional[dict[int, dict[str, Any]]] = None) -> list[tuple]:
    """Per-turn records from 10-turn savepoint records + per-turn start states.

    ``records`` — ``[turn, in_battle, total, wild, trainer, [ids], opponent]`` at
    savepoint turns (exact; a 6-field record has no opponent). ``states[t]`` — was turn ``t`` STARTED in a battle (screenshot
    classifier); the state after turn ``t`` is therefore ``states[t+1]``.

    Inside each window (a, b]: visible battle segments are the maximal runs of
    after-turn battle states; the window's counter increments are assigned one
    per visible segment at the segment's opening turn (trainer increments to
    the LONGEST segments first — a trainer battle cannot be fled), the rest at
    ``b`` as contained battles. More visible segments than new battles means the
    classifier missed frames inside one battle: neighbouring segments are merged. New trainer flags land on the turn the last trainer
    segment of the window closed, else on ``b``, so the tracker credits the
    right attempt.
    """
    recs = sorted((tuple(r) for r in records if isinstance(r, (list, tuple)) and len(r) in (6, 7)), key=lambda r: r[0])
    if not recs:
        return []
    out: list[tuple] = []
    prev: Optional[tuple] = None
    for rec in recs:
        b, in_b, total, wild, trainer, flags = int(rec[0]), bool(rec[1]), int(rec[2]), int(rec[3]), int(rec[4]), tuple(sorted(rec[5]))
        opponent = int(rec[6]) if len(rec) == 7 and rec[6] is not None else None
        if prev is None:
            a = 0
            p_total = p_wild = p_trainer = 0
            p_flags: tuple = ()
            p_in = False
            p_opp = None
        else:
            a, p_in, p_total, p_wild, p_trainer, p_flags, p_opp = prev[0], prev[1], prev[2], prev[3], prev[4], prev[5], prev[6]
        turns = list(range(a + 1, b + 1))
        # after-state per turn: classifier for interior turns, the exact bit at b
        after = {t: bool(states.get(t + 1, False)) for t in turns}
        if turns:
            after[b] = in_b
        # Visible segments [start, end]. A battle already running at the window
        # start (p_in) is the CARRIED segment: not a new battle, but a gap in the
        # classifier can split it, so it takes part in the merging below.
        segs: list[list] = []          # [start, end, carried]
        cur: Optional[list] = [a, a, True] if p_in else None
        if cur is not None:
            segs.append(cur)
        for t in turns:
            if after[t]:
                if cur is None:
                    cur = [t, t, False]
                    segs.append(cur)
                else:
                    cur[1] = t
            else:
                cur = None
        d_total, d_wild, d_trainer = total - p_total, wild - p_wild, trainer - p_trainer
        # The counters are exact: without a counter move a battle cannot end and
        # another begin, so more visible segments than new battles means the
        # classifier missed frames INSIDE one battle (a black transition, a white
        # tutorial box). Merge the pair with the smallest gap until the count
        # fits; the gap turns become battle turns.
        def new_count():
            return sum(1 for x in segs if not x[2])
        while new_count() > max(d_total, 0) and len(segs) > 1:
            gaps = [(segs[i + 1][0] - segs[i][1], i) for i in range(len(segs) - 1)]
            gap, i = min(gaps)
            for t in range(segs[i][1] + 1, segs[i + 1][0]):
                after[t] = True
            segs[i][1] = segs[i + 1][1]
            del segs[i + 1]
        # Still too many with nothing left to merge into (a lone segment while
        # the counters are flat): classifier noise, cleared — except at b, where
        # the exact bit stands.
        if new_count() > max(d_total, 0):
            for x in [x for x in segs if not x[2]][max(d_total, 0):]:
                for t in range(x[0], x[1] + 1):
                    if t != b:
                        after[t] = False
                segs.remove(x)
        kept = [x for x in segs if not x[2]]
        carried_seg = next((x for x in segs if x[2]), None)
        # kinds: a segment the OCR says opened with a trainer intro is a trainer
        # fight; then the longest segments, measured PAST the window end through
        # the classifier states (a fight that runs into the next window is long
        # even if only its first turn is in this one — a trainer battle cannot
        # be fled, a one-turn wild one can).
        hint_kind = lambda x: ((hints or {}).get(x[0]) or {}).get("kind")
        def virtual_len(x):
            end = x[1]
            if end >= b:
                t = b
                while states.get(t + 2, False):
                    t += 1
                end = t
            return end - x[0]
        kept.sort(key=lambda x: (0 if hint_kind(x) == "trainer" else 2 if hint_kind(x) == "wild" else 1, -virtual_len(x)))
        kinds = ["trainer"] * min(d_trainer, len(kept)) + ["wild"] * max(len(kept) - min(d_trainer, len(kept)), 0)
        opens: dict[int, list[str]] = {}
        for s, k in zip(kept, kinds):
            opens.setdefault(s[0], []).append(k)
        contained_trainer = max(d_trainer - kinds.count("trainer"), 0)
        contained_wild = max(d_total - len(kept) - contained_trainer, 0)
        # Contained battles land on the turn the OCR says they began when it says so.
        seen_starts = {x[0] for x in kept}
        hinted = [(t, h["kind"]) for t, h in sorted((hints or {}).items()) if a < t <= b and t not in seen_starts]
        contained_at: dict[int, list[str]] = {}
        for t, k in hinted:
            if k == "trainer" and contained_trainer > 0:
                contained_trainer -= 1; contained_at.setdefault(t, []).append("trainer")
            elif k == "wild" and contained_wild > 0:
                contained_wild -= 1; contained_at.setdefault(t, []).append("wild")
        new_flags = [f for f in flags if f not in p_flags]
        trainer_segs = [x for x, k in zip(kept, kinds) if k == "trainer"]
        if not trainer_segs and carried_seg is not None and carried_seg[1] < b:
            trainer_segs = [carried_seg]  # the flag belongs to the battle that ran into this window
        flag_turn = max(x[1] + 1 for x in trainer_segs) if trainer_segs else b
        flag_turn = min(flag_turn, b)
        # The savepoint's opponent id names the LAST trainer fight started in the
        # window: it applies from that fight's opening turn on, so the tracker
        # reads it at the counter step. Earlier turns keep the previous value —
        # unless a second trainer fight started in this window before it, whose
        # opponent nobody read: those turns carry None (unknown), never the stale
        # value from the last window (that named a Pewter fight "Sammy").
        opp_turn = max(x[0] for x in trainer_segs) if trainer_segs and d_trainer > 0 else b
        carried_opp = None if d_trainer >= 2 else p_opp
        # emit per-turn records
        cur_total, cur_wild, cur_trainer = p_total, p_wild, p_trainer
        cur_flags = list(p_flags)
        cur_opp = carried_opp
        for t in turns:
            for k in opens.get(t, []) + contained_at.get(t, []):
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
            if t >= opp_turn:
                cur_opp = opponent
            out.append((t, after[t], cur_total, cur_wild, cur_trainer, tuple(cur_flags), cur_opp))
        # exactness at b: the savepoint's own values win
        if turns:
            out[-1] = (b, in_b, total, wild, trainer, flags, opponent)
        prev = (b, in_b, total, wild, trainer, flags, opponent)
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
    kept = [r for r in records if isinstance(r, (list, tuple)) and len(r) in (6, 7) and int(r[0]) <= turns]
    later = [r for r in records if isinstance(r, (list, tuple)) and len(r) in (6, 7) and int(r[0]) > turns]
    if later and (not kept or int(kept[-1][0]) < turns):
        first = min(later, key=lambda r: int(r[0]))
        kept.append([turns, *first[1:]])
    return kept


def reconcile_with_gates(summary: dict, referee: Optional[dict]) -> dict:
    """The scorecard outranks a missing trainer flag: when ``brock_defeated`` is
    stamped (adjudicated or live) but no Brock flag ever appeared, the run's
    last unidentified trainer attempt IS the Brock fight (gpt-6-astra low,
    2026-09-11: badge OCR'd at turn 145, flags never set). Relabels that group
    in place; everything else untouched."""
    if not isinstance(referee, dict) or not summary.get("available"):
        return summary
    stamped = any(isinstance(g, dict) and g.get("id") == "brock_defeated" and g.get("turn") is not None
                  for g in referee.get("gates") or [])
    groups = summary.get("trainers") or []
    if not stamped or any(g.get("group") == "414" for g in groups):
        return summary
    unknown = [g for g in groups if g.get("group") == "unknown"]
    if not unknown:
        return summary
    g = unknown[-1]
    g.update({"group": "414", "id": 414, "name": "Leader Brock", "won": True, "mandatory": True, "reconciled": "brock_defeated gate"})
    return summary


def battle_summary(run_dir: Path, referee: Optional[dict], turns: int) -> tuple[Optional[dict], Optional[str]]:
    """``(BattleTracker.summary(), fidelity)`` — fidelity "live", "backfill",
    "savepoint" (counters only, no per-turn states) or ``(None, None)``.
    Backfill records and states past ``turns`` are dropped (:func:`cap_records`)."""
    live = (referee or {}).get("battles") if isinstance(referee, dict) else None
    if isinstance(live, dict) and live.get("available") and turns and (live.get("polls") or 0) >= LIVE_MIN_COVERAGE * turns:
        return reconcile_with_gates(live, referee), "live"
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
        hints = ocr_hints(run_dir, turns)
        tracker.identity_hints = {t: h["trainer_id"] for t, h in hints.items() if h.get("trainer_id") is not None}
        for r in synthesize_records(records, states, hints):
            tracker.record(*r)
        return reconcile_with_gates(tracker.summary(), referee), "backfill"
    tracker.load_state({"battle_records": records})
    return reconcile_with_gates(tracker.summary(), referee), "savepoint"


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


def movement(referee: Optional[dict], video_steps: Optional[dict[int, int]],
             last_turn: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Directness over the map legs (decision 4A), with fidelity.

    Closed legs credit their full shortest path. The leg the run ended on
    (status "open") counts too — Andreas 2026-09-14: a run that burned 300
    turns in Viridian Forest must not look direct because it never closed the
    leg — credited with the ground it actually gained, ``d_open −
    distance_now`` (never below 0), against every step it took there. Its
    steps come from the video when turns ``opened+1..last_turn`` are covered,
    else the referee's bound.

    Returns ``{shortest, steps, efficiency, legs, fidelity}`` or None when no
    map leg has a shortest path. ``fidelity`` is "trace" / "video" / "bound"
    when every leg used that source, else "mixed".
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
        if not isinstance(leg, dict) or leg.get("status") not in ("closed", "open") or kind.get(leg.get("node_id")) != "map":
            continue
        d = leg.get("d_open")
        if not isinstance(d, int) or d <= 0:
            continue
        is_open = leg.get("status") == "open"
        opened, closed = leg.get("opened_turn"), (leg.get("closed_turn") if not is_open else last_turn)
        if is_open:
            now = leg.get("distance_now")
            if not isinstance(now, int):
                continue
            d = max(0, d - now)   # ground gained on the leg the run ended on
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
        used.append({"node_id": leg.get("node_id"), "d_open": d, "steps": s, "source": src, "status": leg.get("status")})
    if not used or steps <= 0:
        return None
    fidelity = sources.pop() if len(sources) == 1 else "mixed"
    return {"shortest": shortest, "steps": steps, "efficiency": shortest / steps, "legs": used, "fidelity": fidelity}


__all__ = ["battle_summary", "synthesize_records", "movement", "load_steps_backfill", "MANDATORY_TRAINERS"]
