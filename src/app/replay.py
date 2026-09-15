"""Rebuild the referee's DERIVED numbers from ``events.jsonl`` alone.

Why this exists (2026-09-15, ``artifacts/route-fidelity/plan.md`` R8). A run
folder used to be the source of truth for its own score: ``run_summary.json``
stored the tracker's finished legs (steps walked, efficiency, distances) and
the battle summary, and ``referee_state.json`` stored ``traced_steps`` — itself
already derived, by :func:`src.referee.trace.derive`, under whatever rules the
daemon happened to be running. So every rule change needed a BACK-FILL script
that rewrote stored runs, and a run played on yesterday's daemon carried
yesterday's rules into the leaderboard.

``events.jsonl`` is the raw record and never needs rewriting:

===========================  ===========================================
``turn_input_trace``         every button's tile + in-battle bit + counter
``referee_position``         the polled tile per turn
``referee_battle_state``     the game's own battle counters and flags
``referee_checkpoint``       the gate latch
===========================  ===========================================

Everything published is a pure function of those four. This module replays them
through the SAME :class:`~src.referee.progress.ProgressTracker` and
:class:`~src.referee.battles.BattleTracker` the live referee uses, so a rule
change is a projection bump plus ``pokemon publish --site-only --refresh-rows``
— never a script that edits run folders.

Two properties worth stating out loud:

- **The ladder is the scoring rule.** The run's own ``config.json`` names its
  checkpoint file, and editing that file re-scores every run that used it at
  the next refresh. That is the intent (the 2026-09-14 ``score_to: reached``
  change wanted exactly this), not an accident.
- **Last occurrence of a turn wins.** A continued run's ``events.jsonl`` is the
  source segment's file with the new segment appended, so a turn the source
  played PAST its savepoint appears twice; the later line is the one the run
  actually lived (2 of 16 stored continued runs have such a turn).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.referee import trace
from src.referee.battles import BattleTracker
from src.referee.checkpoints import load_ladder
from src.referee.progress import ProgressTracker
from src.referee.walkgraph import WalkGraph

DEFAULT_LADDER = Path("configs") / "checkpoints-firered-firstbadge.yaml"

_RAW_TYPES = ("turn_input_trace", "referee_position", "referee_battle_state", "referee_checkpoint")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ladder_path(run_dir: Path) -> Path:
    """The checkpoint file this run was scored against, from its own config."""
    try:
        cfg = json.loads((Path(run_dir) / "config.json").read_text())
        p = ((cfg.get("referee") or {}).get("checkpoints"))
        if isinstance(p, str) and p:
            path = Path(p)
            return path if path.is_absolute() else _repo_root() / path
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass
    return _repo_root() / DEFAULT_LADDER


def read_raw(run_dir: Path) -> dict[str, Any]:
    """The four raw event streams, keyed by turn, last occurrence winning."""
    polls: dict[int, tuple[int, int, int, int, int]] = {}
    samples: dict[int, list[dict]] = {}
    battles: dict[int, dict] = {}
    stamps: dict[str, int] = {}
    path = Path(run_dir) / "events.jsonl"
    if not path.is_file():
        return {"polls": polls, "samples": samples, "battles": battles, "stamps": stamps}
    with path.open() as fh:
        for line in fh:
            if not any(f'"{t}"' in line for t in _RAW_TYPES):
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind, turn = e.get("type"), e.get("turn")
            if kind not in _RAW_TYPES or not isinstance(turn, int):
                continue
            if kind == "referee_position":
                if e.get("x") is None:
                    continue
                polls[turn] = (turn, e["map_group"], e["map_num"], e["x"], e["y"])
            elif kind == "turn_input_trace":
                s = e.get("samples")
                samples[turn] = s if isinstance(s, list) else []
            elif kind == "referee_battle_state":
                battles[turn] = e
            else:  # referee_checkpoint — the FIRST latch of a gate is its turn
                cid = e.get("checkpoint_id")
                if isinstance(cid, str) and (cid not in stamps or turn < stamps[cid]):
                    stamps[cid] = turn
    return {"polls": polls, "samples": samples, "battles": battles, "stamps": stamps}


def replay_referee(run_dir: Path, graph: Optional[WalkGraph] = None,
                   ladder: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """``{"progress", "battles", "inputs"}`` rebuilt from the raw events, or None.

    None when the run recorded no per-input trace (every run before
    2026-09-14): there is nothing to re-derive that the stored summary does not
    already hold, and a poll-only replay would be a worse measurement, not a
    better one. ``graph`` defaults to the committed walk graph.
    """
    run_dir = Path(run_dir)
    raw = read_raw(run_dir)
    if not raw["samples"] or not raw["polls"]:
        return None
    if graph is None:
        from src.app.route import default_graph
        graph = default_graph()
    if graph is None:
        return None
    nodes = load_ladder(ladder or ladder_path(run_dir)).nodes

    def distance(a: tuple, b: tuple) -> Optional[int]:
        na, nb = graph.node_id(*a), graph.node_id(*b)
        return graph.steps_between(na, nb) if na is not None and nb is not None else None

    def passable(tile: tuple, direction: str) -> Optional[bool]:
        return graph.passable_in(*tile, direction)

    progress = ProgressTracker(graph, nodes)
    battles = BattleTracker()
    defeated: set[int] = set()
    # Run-level input census. Every bucket is re-derived here, so a rule change
    # is a PROJECTION_VERSION bump and never a back-fill (plan R8).
    inputs: dict[str, int] = {k: 0 for k in
                              ("inputs", "overworld_steps", "inputs_lost", *trace.INPUT_BUCKETS)}
    worst: list[tuple[int, int, int]] = []  # (walls, inputs, turn)
    for turn in sorted(set(raw["polls"]) | set(raw["samples"])):
        # The trace is folded in BEFORE the poll, exactly as Referee.poll does,
        # so the turn's exact step count replaces the between-poll bound.
        if turn in raw["samples"]:
            before = [t for t in raw["polls"] if t < turn]
            start_tile = tuple(raw["polls"][max(before)][1:]) if before else None
            prev = battles.records[-1] if battles.records else None
            start_in_battle = prev[1] if prev else (False if turn <= 1 else None)
            d = trace.derive(raw["samples"][turn], start_tile, start_in_battle,
                             distance=distance, passable=passable)
            if d["overworld_steps"] is not None:
                progress.record_traced_steps(turn, d["overworld_steps"], end_tile=d["end_tile"],
                                             walls=d["walls_hit"])
            for k in inputs:
                v = d.get(k)
                if isinstance(v, int):
                    inputs[k] += v
            if d["walls_hit"]:
                worst.append((d["walls_hit"], d["inputs"], turn))
        if turn in raw["polls"]:
            progress.record(*raw["polls"][turn])
        b = raw["battles"].get(turn)
        if b is not None:
            # The event carries the DELTA (trainers_new); the record holds the
            # cumulative set, so accumulate it the way the live tracker does.
            defeated |= {int(i) for i in (b.get("trainers_new") or [])}
            battles.record(turn, bool(b.get("in_battle")), int(b.get("battles_total") or 0),
                           int(b.get("wild_battles") or 0), int(b.get("trainer_battles") or 0),
                           sorted(defeated), b.get("opponent"))
    progress.observe_stamps(raw["stamps"])
    inputs["traced_turns"] = len(raw["samples"])
    # Presses that could have moved the player: everything outside a battle.
    # The rate is what the board column shows, so its denominator must exclude
    # battle inputs — menu presses are not movement and cannot hit a wall.
    overworld = inputs["inputs"] - inputs["battle_inputs"]
    inputs["overworld_inputs"] = overworld
    inputs["wall_rate"] = round(inputs["walls_hit"] / overworld, 6) if overworld > 0 else None
    worst.sort(reverse=True)
    inputs["worst_turns"] = [{"turn": t, "walls": w, "inputs": n} for w, n, t in worst[:10]]
    return {"progress": progress.summary(), "battles": battles.summary(), "inputs": inputs}


def referee_view(run_dir: Path, stored: Any, graph: Optional[WalkGraph] = None) -> Any:
    """The referee dict the projection should score: the stored one with its
    DERIVED halves replaced by a replay of the raw events, when one is possible.

    Gates, checkpoints and the termination reason stay as recorded — they are
    decisions the referee made during the run, not derivations.

    A run with raw events but no stored block gets the replay on its own: the
    derivations are as good either way, and returning nothing would leave the
    report with no movement at all.
    """
    fresh = replay_referee(run_dir, graph)
    if fresh is None:
        return stored
    return {**stored, **fresh} if isinstance(stored, dict) else fresh


__all__ = ["replay_referee", "referee_view", "read_raw", "ladder_path", "DEFAULT_LADDER"]
