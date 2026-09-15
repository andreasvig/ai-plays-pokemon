"""The route builder (src/app/route.py): trace samples + polls → one ordered
tile sequence with its walk-graph transitions. Plan artifacts/route-fidelity/plan.md."""

from __future__ import annotations

import json

import pytest

from src.app import route
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
