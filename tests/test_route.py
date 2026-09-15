"""The route builder (src/app/route.py): trace samples + polls → one ordered
tile sequence with its walk-graph transitions. Plan artifacts/route-fidelity/plan.md."""

from __future__ import annotations

import json

import pytest

from src.app import route
from src.referee.trace import MAX_TILES_PER_INPUT
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph


@pytest.fixture(scope="module")
def firered() -> WalkGraph:
    return WalkGraph.load(DEFAULT_GRAPH_PATH)


def sample(i, inp, g, m, x, y, battle=False):
    return {"i": i, "input": inp, "map_group": g, "map_num": m, "x": x, "y": y, "in_battle": battle, "battles_total": 0}


def trace_event(turn, samples):
    return {"type": "turn_input_trace", "turn": turn, "samples": samples}


def poll_event(turn, g, m, x, y):
    return {"type": "referee_position", "turn": turn, "map_group": g, "map_num": m, "x": x, "y": y}


def test_no_trace_means_no_route(firered):
    assert route.build_route([poll_event(1, 3, 0, 6, 8), poll_event(2, 3, 0, 6, 12)], firered) is None


def test_route_orders_samples_then_poll_and_collapses_repeats(firered):
    """Turn 1 walks two tiles down from the player's door step, presses A on the
    spot; turn 2 the poll agrees with the last sample (collapsed)."""
    events = [
        poll_event(1, 3, 0, 6, 10),
        trace_event(1, [sample(0, "D", 3, 0, 6, 9), sample(1, "D", 3, 0, 6, 10), sample(2, "A", 3, 0, 6, 10)]),
        trace_event(2, [sample(0, "D", 3, 0, 6, 11)]),
        poll_event(2, 3, 0, 6, 11),
    ]
    r = route.build_route(events, firered)
    assert r["visits"] == [[1, 0, 3, 0, 6, 9, 0], [1, 1, 3, 0, 6, 10, 0], [2, 0, 3, 0, 6, 11, 0]]
    assert r["transitions"] == {"step": 2} and r["tiles_moved"] == 2 and r["fills"] == {}
    assert r["turns"] == {"total": 2, "traced": 2, "blind": 0} and r["coverage"] == 1.0
    assert r["maps"] == {"3:0": {"name": "PalletTown", "width": 24, "height": 20, "world": [0, 0]}}
    assert r["tile_px"] == 16 and r["version"] == route.ROUTE_VERSION


def test_a_warp_the_sample_missed_is_closed_by_the_poll(firered):
    """Live 2026-09-14: the last sample of a turn read the door tile (6,7); the
    poll a second later stood inside the house. One warp transition, no break."""
    events = [
        trace_event(3, [sample(0, "U", 3, 0, 6, 8), sample(1, "U", 3, 0, 6, 7)]),
        poll_event(3, 4, 0, 4, 8),
    ]
    r = route.build_route(events, firered)
    assert [v[1:6] for v in r["visits"]] == [[0, 3, 0, 6, 8], [1, 3, 0, 6, 7], [route.POLL, 4, 0, 4, 8]]
    assert r["transitions"] == {"step": 1, "warp": 1}
    assert r["maps"]["4:0"]["world"] is None  # indoor: no frame


def test_a_scripted_walk_between_samples_is_a_jump_filled_with_the_shortest_path(firered):
    """Oak's escort (turn 24 of gemini-3.8-flash low): (12,1) → (11,5) under one
    B press. The drawn route follows the ground: fill = the tiles in between."""
    events = [trace_event(24, [sample(0, "B", 3, 0, 12, 1), sample(1, "B", 3, 0, 11, 5)])]
    r = route.build_route(events, firered)
    assert r["transitions"] == {"jump": 1} and r["tiles_moved"] == 5
    fill = r["fills"]["0"]
    assert len(fill) == 4 and all(len(t) == 4 for t in fill)
    # contiguous: every consecutive pair on the filled path is a graph edge
    chain = [(3, 0, 12, 1)] + [tuple(t) for t in fill] + [(3, 0, 11, 5)]
    for a, b in zip(chain, chain[1:]):
        assert firered.node_id(*b) in firered.adj[firered.node_id(*a)]


def test_a_blackout_is_a_teleport_drawn_as_two_ends_not_a_line(firered):
    """Live 2026-09-15: Viridian Forest -> the player's house, 224 tiles under one
    B press. A jump that long is the game relocating the player; filling it would
    draw a line across half the world the run never walked."""
    events = [trace_event(99, [sample(0, "B", 1, 0, 43, 5), sample(1, "B", 4, 0, 8, 5)])]
    r = route.build_route(events, firered)
    assert r["transitions"] == {"teleport": 1} and r["tiles_moved"] == 1 and r["fills"] == {}


def test_a_long_span_across_a_blind_turn_stays_a_jump(firered):
    """The per-input cap must not fire on poll -> poll, which spans a whole turn
    of walking: a blind turn can legitimately cover far more than one input."""
    far = [poll_event(1, 3, 0, 6, 8), {"type": "turn_input_trace", "turn": 2, "samples": []},
           poll_event(2, 3, 19, 10, 20)]
    r = route.build_route(far, firered)
    assert list(r["transitions"]) == ["jump"] and r["tiles_moved"] > route.MAX_TILES_PER_INPUT
    assert r["fills"]["0"]  # the walked ground is still drawn


def test_a_seam_is_one_tile_across_an_outdoor_connection(firered):
    events = [trace_event(50, [sample(0, "U", 3, 0, 12, 0), sample(1, "U", 3, 19, 12, 39)])]  # Pallet Town's north exit
    r = route.build_route(events, firered)
    assert r["transitions"] == {"seam": 1}
    assert r["maps"]["3:19"]["world"] == [0, -40]


def test_a_blind_turn_and_an_off_graph_tile_are_counted_not_hidden(firered):
    events = [
        trace_event(1, [sample(0, "A", 3, 0, 6, 8)]),
        trace_event(2, [{"i": 0, "input": "U", "x": None, "y": None, "map_group": None, "map_num": None,
                         "in_battle": None, "battles_total": None}]),
        poll_event(2, 3, 0, 6, 8),
        trace_event(3, [sample(0, "U", 9, 9, 1, 1)]),  # not a map in the graph
    ]
    r = route.build_route(events, firered)
    assert r["turns"] == {"total": 3, "traced": 2, "blind": 1}
    assert r["transitions"] == {"break": 1}
    assert r["maps"]["9:9"] == {"name": None, "width": None, "height": None, "world": None}


def test_in_battle_flag_rides_along_and_polls_inherit_it(firered):
    events = [
        trace_event(5, [sample(0, "R", 3, 19, 5, 30), sample(1, "R", 3, 19, 6, 30, battle=True)]),
        poll_event(5, 3, 19, 7, 30),
    ]
    r = route.build_route(events, firered)
    assert [v[6] for v in r["visits"]] == [0, 1, 1]


def test_load_route_reads_events_jsonl(tmp_path, firered):
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "turn_start", "turn": 1},
        trace_event(1, [sample(0, "D", 3, 0, 6, 9)]),
        poll_event(1, 3, 0, 6, 9),
    ]) + "\n")
    r = route.load_route(tmp_path, firered)
    assert r["visits"] == [[1, 0, 3, 0, 6, 9, 0]]
    assert route.load_route(tmp_path / "missing", firered) is None


# -- battles on the map (M15) --------------------------------------------------

def battle_event(turn, *, in_battle, total, wild=0, trainer=0, opponent=None, new=()):
    return {"type": "referee_battle_state", "turn": turn, "in_battle": in_battle,
            "battles_total": total, "wild_battles": wild, "trainer_battles": trainer,
            "opponent": opponent, "trainers_new": list(new)}


def test_a_wild_battle_is_placed_on_the_tile_it_opened_on(firered):
    # Route 1 grass: the run walks two tiles, the second one starts a fight.
    events = [
        trace_event(1, [sample(0, "U", 3, 19, 8, 32), sample(1, "U", 3, 19, 8, 31)]),
        battle_event(1, in_battle=False, total=0),
        trace_event(2, [sample(0, "U", 3, 19, 8, 30), sample(1, "A", 3, 19, 8, 30, battle=True)]),
        battle_event(2, in_battle=True, total=1, wild=1),
        trace_event(3, [sample(0, "A", 3, 19, 8, 30)]),
        battle_event(3, in_battle=False, total=1, wild=1),
    ]
    r = route.build_route(events, firered)
    assert len(r["battles"]) == 1
    b = r["battles"][0]
    assert b["kind"] == "wild"
    assert b["opened_turn"] == 2 and b["closed_turn"] == 3
    # the last tile stood on with no battle running — where the grass was walked into
    assert b["tile"] == [3, 19, 8, 30]
    assert b["trainer_id"] is None and b["trainer"] is None


def test_a_trainer_battle_carries_its_name_and_whether_it_was_won(firered):
    events = [
        trace_event(1, [sample(0, "U", 3, 20, 14, 50)]),
        battle_event(1, in_battle=False, total=0),
        trace_event(2, [sample(0, "U", 3, 20, 14, 49, battle=True)]),
        battle_event(2, in_battle=True, total=1, trainer=1, opponent=104),
        trace_event(3, [sample(0, "A", 3, 20, 14, 49)]),
        battle_event(3, in_battle=False, total=1, trainer=1, opponent=104, new=[104]),
    ]
    r = route.build_route(events, firered)
    b = r["battles"][0]
    assert b["kind"] == "trainer"
    assert b["trainer_id"] == 104 and b["trainer"] == "Bug Catcher Sammy"
    assert b["won"] is True
    assert b["tile"] == [3, 20, 14, 50]


def test_a_run_with_no_battle_events_lists_no_battles(firered):
    r = route.build_route([trace_event(1, [sample(0, "D", 3, 0, 6, 9)])], firered)
    assert r["battles"] == []


def test_the_segments_drawn_are_the_segments_the_board_counts(firered):
    # Same events through BattleTracker directly: one list is a projection of
    # the other, so a fight on the map is a fight in the count.
    from src.referee.battles import BattleTracker
    events = [
        trace_event(1, [sample(0, "U", 3, 19, 8, 32)]),
        battle_event(1, in_battle=False, total=0),
        trace_event(2, [sample(0, "U", 3, 19, 8, 31, battle=True)]),
        battle_event(2, in_battle=True, total=1, wild=1),
        battle_event(3, in_battle=False, total=1, wild=1),
        trace_event(4, [sample(0, "U", 3, 19, 8, 30)]),
        battle_event(4, in_battle=False, total=1, wild=1),
        trace_event(5, [sample(0, "U", 3, 19, 8, 29, battle=True)]),
        battle_event(5, in_battle=True, total=2, wild=2),
        battle_event(6, in_battle=False, total=2, wild=2),
    ]
    t = BattleTracker()
    for e in events:
        if e["type"] == "referee_battle_state":
            t.record(e["turn"], e["in_battle"], e["battles_total"], e["wild_battles"],
                     e["trainer_battles"], [], e["opponent"])
    r = route.build_route(events, firered)
    assert [(s["kind"], s["opened_turn"], s["turns"]) for s in t.segments()] == \
           [(b["kind"], b["opened_turn"], b["turns"]) for b in r["battles"]]
