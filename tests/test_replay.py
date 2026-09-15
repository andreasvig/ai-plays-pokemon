"""src/app/replay.py — the published numbers are rebuilt from events.jsonl, so
a rule change needs a projection bump, never a back-fill that edits run folders.
Plan: artifacts/route-fidelity/plan.md R8.
"""

from __future__ import annotations

import json

import pytest

from src.app import replay
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph


@pytest.fixture(scope="module")
def firered() -> WalkGraph:
    return WalkGraph.load(DEFAULT_GRAPH_PATH)


def sample(i, inp, g, m, x, y, battle=False, total=0):
    return {"i": i, "input": inp, "map_group": g, "map_num": m, "x": x, "y": y,
            "in_battle": battle, "battles_total": total}


def trace_ev(turn, samples):
    return {"type": "turn_input_trace", "turn": turn, "samples": samples}


def poll_ev(turn, g, m, x, y):
    return {"type": "referee_position", "turn": turn, "map_group": g, "map_num": m, "x": x, "y": y}


def battle_ev(turn, in_battle=False, total=0, wild=0, trainer=0, new=(), opponent=None):
    return {"type": "referee_battle_state", "turn": turn, "in_battle": in_battle, "battles_total": total,
            "wild_battles": wild, "trainer_battles": trainer, "trainers_new": list(new), "opponent": opponent}


def write(run, events, *, ladder="configs/checkpoints-firered-firstbadge.yaml"):
    run.mkdir(parents=True, exist_ok=True)
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    if ladder is not None:
        (run / "config.json").write_text(json.dumps({"referee": {"checkpoints": ladder}}))
    return run


# The player's bedroom → down the stairs; enough to open and close a leg.
BEDROOM = (4, 1, 5, 6)


def walk_events():
    """Two turns walking in the bedroom, then the stairs gate is stamped."""
    return [
        poll_ev(0, 4, 1, 5, 6),
        trace_ev(1, [sample(0, "L", 4, 1, 4, 6), sample(1, "U", 4, 1, 4, 5)]),
        poll_ev(1, 4, 1, 4, 5),
        trace_ev(2, [sample(0, "U", 4, 1, 4, 4), sample(1, "U", 4, 1, 4, 3)]),
        poll_ev(2, 4, 1, 4, 3),
        battle_ev(0), battle_ev(1), battle_ev(2),
        {"type": "referee_checkpoint", "turn": 2, "checkpoint_id": "left_bedroom",
         "name": "Left the bedroom", "checkpoint_type": "map", "auto": False},
    ]


def test_replay_rebuilds_legs_and_battles_from_events_alone(tmp_path, firered):
    run = write(tmp_path / "run", walk_events())
    out = replay.replay_referee(run, firered)
    leg = out["progress"]["legs"][0]
    assert leg["node_id"] == "left_bedroom" and leg["status"] == "closed"
    assert leg["steps_walked"] == 4 and leg["steps_source"] == "trace" and leg["traced_turns"] == 2
    assert out["battles"]["available"] is True and out["battles"]["polls"] == 3


def test_a_run_with_no_per_input_trace_is_left_to_its_stored_numbers(tmp_path, firered):
    """Every run before 2026-09-14. A poll-only replay would be a WORSE
    measurement (the between-poll bound), not a better one."""
    run = write(tmp_path / "run", [poll_ev(0, 4, 1, 5, 6), poll_ev(1, 4, 1, 4, 3)])
    assert replay.replay_referee(run, firered) is None
    stored = {"gates": [{"id": "left_bedroom"}], "progress": {"legs": ["kept"]}, "battles": {"available": False}}
    assert replay.referee_view(run, stored) is stored


def test_referee_view_replaces_only_the_derived_halves(tmp_path, firered):
    run = write(tmp_path / "run", walk_events())
    stored = {"gates": [{"id": "left_bedroom", "status": "done"}], "furthest": "left_bedroom",
              "termination_reason": "leg_cap:x", "progress": {"legs": ["stale"]}, "battles": {"available": False}}
    view = replay.referee_view(run, stored, firered)
    assert view["gates"] == stored["gates"] and view["furthest"] == "left_bedroom"
    assert view["termination_reason"] == "leg_cap:x"          # a decision, kept
    assert view["progress"]["legs"] != ["stale"] and view["battles"]["available"] is True  # derivations, replaced


def test_the_last_line_for_a_turn_wins_so_a_continued_run_is_not_double_counted(tmp_path, firered):
    """A continued run's events.jsonl is the SOURCE segment's file with the new
    segment appended, so a turn the source played past its savepoint appears
    twice (2 of 16 stored continued runs). The later line is the one the run
    lived — the live referee restores state capped at the savepoint and records
    those turns again."""
    stale = [poll_ev(0, 4, 1, 5, 6),
             trace_ev(1, [sample(0, "R", 4, 1, 6, 6), sample(1, "R", 4, 1, 7, 6)]),  # the abandoned attempt
             poll_ev(1, 4, 1, 7, 6)]
    run = write(tmp_path / "run", stale + walk_events()[1:])
    out = replay.replay_referee(run, firered)
    leg = out["progress"]["legs"][0]
    assert leg["steps_walked"] == 4  # the replayed turn 1, not the abandoned one, and not both
    raw = replay.read_raw(run)
    assert raw["polls"][1] == (1, 4, 1, 4, 5) and len(raw["samples"][1]) == 2


def test_the_ladder_comes_from_the_run_s_own_config(tmp_path, firered):
    """A casual run scored against another ladder must not be re-scored against
    the default one — the back-fill script did exactly that on 2026-09-15 and
    moved a leg's D from 1 to 4."""
    run = write(tmp_path / "run", walk_events(), ladder="configs/checkpoints-firered-v1.yaml")
    assert replay.ladder_path(run).name == "checkpoints-firered-v1.yaml"
    bare = write(tmp_path / "bare", walk_events(), ladder=None)
    assert replay.ladder_path(bare).name == replay.DEFAULT_LADDER.name


def test_a_blackout_is_corrected_at_read_time_without_touching_the_run(tmp_path, firered):
    """The whole point: a run played by a daemon holding older code is scored by
    TODAY's rules. Viridian Forest -> the player's house on one B press is a
    relocation, not 224 steps of walking."""
    events = [poll_ev(0, 1, 0, 43, 5), battle_ev(0),
              trace_ev(1, [sample(0, "B", 1, 0, 43, 5), sample(1, "B", 4, 0, 8, 5)]),
              poll_ev(1, 4, 0, 8, 5), battle_ev(1)]
    run = write(tmp_path / "run", events)
    out = replay.replay_referee(run, firered)
    assert out["progress"]["legs"][0]["steps_walked"] == 1


def test_missing_or_unreadable_events_never_raise(tmp_path, firered):
    assert replay.replay_referee(tmp_path / "nope", firered) is None
    run = tmp_path / "junk"; run.mkdir()
    (run / "events.jsonl").write_text('{"type": "referee_position"\n{"type": "turn_input_trace", "turn": 1}\n')
    assert replay.replay_referee(run, firered) is None


# --- the input census and the wall charge (artifacts/wasted-inputs/plan.md) ---

def test_replay_counts_wall_presses_and_charges_them_to_the_leg(tmp_path, firered):
    """Route 1 (14,17) has no edge north — the ledge. Facing it and pressing U
    again is a wall; the FIRST U only turns the player and is free."""
    events = [
        poll_ev(0, 3, 19, 14, 18),
        trace_ev(1, [sample(0, "U", 3, 19, 14, 17),          # a real step
                     sample(1, "U", 3, 19, 14, 17),          # facing north: wall
                     sample(2, "U", 3, 19, 14, 17),          # wall
                     sample(3, "A", 3, 19, 14, 17)]),        # idle A/B
        poll_ev(1, 3, 19, 14, 17),
        battle_ev(0), battle_ev(1),
    ]
    out = replay.replay_referee(write(tmp_path / "r", events), firered)
    inputs = out["inputs"]
    assert inputs["inputs"] == 4 and inputs["overworld_steps"] == 1
    assert inputs["walls_hit"] == 2 and inputs["turns_to_face"] == 0
    assert inputs["idle_ab"] == 1 and inputs["blocked_by_actor"] == 0
    assert inputs["overworld_inputs"] == 4 and inputs["wall_rate"] == 0.5
    assert inputs["worst_turns"][0] == {"turn": 1, "walls": 2, "inputs": 4}
    leg = out["progress"]["legs"][0]
    # steps_walked stays the tiles actually covered; the charge rides beside it.
    assert leg["walls_hit"] == 2 and leg["charged_steps"] == leg["steps_walked"] + 2


def test_a_run_with_no_trace_gets_no_census_at_all(tmp_path, firered):
    """Not a zeroed census: 23 of the 25 rows published on 2026-09-15 predate
    the per-input trace and must render as unmeasured, not as clean runs."""
    events = [poll_ev(0, 4, 1, 5, 6), poll_ev(1, 4, 1, 4, 5), battle_ev(0), battle_ev(1)]
    assert replay.replay_referee(write(tmp_path / "r", events), firered) is None


def test_the_wall_charge_lowers_efficiency_and_leaves_steps_walked_alone(tmp_path, firered):
    """The whole point of W4: a bump used to be free."""
    from src.app import battle_stats
    # Walk up to the ledge tile (14,17), where U is a wall, then bump it.
    clean = [poll_ev(0, 3, 19, 14, 19),
             trace_ev(1, [sample(0, "U", 3, 19, 14, 18), sample(1, "U", 3, 19, 14, 17)]),
             poll_ev(1, 3, 19, 14, 17), battle_ev(0), battle_ev(1)]
    bumpy = clean[:1] + [trace_ev(1, clean[1]["samples"] + [sample(2, "U", 3, 19, 14, 17)] * 3)] + clean[2:]
    a = replay.replay_referee(write(tmp_path / "a", clean), firered)
    b = replay.replay_referee(write(tmp_path / "b", bumpy), firered)
    la, lb = a["progress"]["legs"][0], b["progress"]["legs"][0]
    assert la["steps_walked"] == lb["steps_walked"]      # same ground covered
    assert la["walls_hit"] == 0 and lb["walls_hit"] == 3
    assert lb["charged_steps"] > la["charged_steps"]
    # and it reaches the aggregate the row is built from, not just the leg dict
    ma = battle_stats.movement({"progress": a["progress"]}, None)
    mb = battle_stats.movement({"progress": b["progress"]}, None)
    assert mb["steps"] == ma["steps"]                    # walked: unchanged
    assert mb["walls"] == 3 and ma["walls"] == 0
    assert mb["charged"] == ma["charged"] + 3            # the denominator moved
