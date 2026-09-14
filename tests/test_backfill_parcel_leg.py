"""scripts/backfill_parcel_leg.py — re-score a leg from stored positions with the
corrected Oak's Parcel locus (2026-09-14). Uses the committed walk graph."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph

spec = importlib.util.spec_from_file_location("bpl", Path("scripts/backfill_parcel_leg.py"))
bpl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bpl)


def test_the_corrected_locus_is_reachable_from_viridian_and_the_leg_rescores():
    graph = WalkGraph.load(DEFAULT_GRAPH_PATH)
    targets = bpl.leg_targets(graph, Path("configs/checkpoints-firered-firstbadge.yaml"), "parcel_delivered")
    assert {graph.nodes[i] for i in targets} == {(5, 3, 2, 5)}          # in front of the counter, not behind it
    door = graph.node_id(5, 3, 4, 7)
    assert graph.distance_to(targets, door) == 4
    # An OPEN leg: opened at T100 on the Viridian entry tile, walked into the Mart, ended two tiles from the clerk.
    entry = graph.nodes[sorted(graph.map_entry_nodes(3, 1))[0]]
    d_entry = graph.distance_to(targets, graph.node_id(*entry))
    # T99 (4 tiles from the clerk) predates the leg and must not seed it.
    positions = [[99, 5, 3, 4, 7], [100, *entry], [101, 3, 1, 36, 19], [102, 5, 3, 4, 7], [103, 5, 3, 3, 7], [104, 5, 3, 9, 7]]
    leg = {"node_id": "parcel_delivered", "status": "open", "scored": True, "opened_turn": 100, "closed_turn": None,
           "d_open": None, "d_min": None, "distance_now": None, "fraction": None, "steps_walked": 40, "efficiency": None}
    out = bpl.rescore_leg(leg, positions, graph, targets, last_turn=104)
    assert out["d_open"] == d_entry and out["d_min"] == 3 and out["distance_now"] == graph.distance_to(targets, graph.node_id(5, 3, 9, 7))
    assert abs(out["fraction"] - min(0.95, 1 - 3 / d_entry)) < 1e-9 and out["efficiency"] is None
    assert out["steps_walked"] == 40                                     # steps are left as recorded
    assert out["d_open"] != 4   # the T99 position is ignored: the opening distance is T100's
    # A CLOSED leg gets efficiency = d_open / max(steps, d_open) and an uncapped fraction.
    # The stamp turn's position (T105, on the clerk's tile) belongs to the leg it closes, as in the tracker.
    closed = bpl.rescore_leg({**leg, "status": "closed", "closed_turn": 105, "steps_walked": 60}, positions + [[105, 5, 3, 2, 5]], graph, targets)
    assert closed["fraction"] == 1.0 and abs(closed["efficiency"] - d_entry / 60) < 1e-9
    # Summary-level: the current leg and the progress score follow.
    summary = {"session": {"total_turns": 104},
               "referee": {"progress": {"progress": 7.0, "gates_reached": 7, "current_leg": dict(leg), "legs": [{"node_id": "viridian_reached", "d_open": 5}, dict(leg)]}}}
    assert bpl.rescore_summary(summary, positions, graph, targets, "parcel_delivered") is True
    prog = summary["referee"]["progress"]
    assert prog["legs"][1]["d_open"] == d_entry and prog["current_leg"]["d_min"] == 3
    assert abs(prog["progress"] - (7 + prog["legs"][1]["fraction"])) < 1e-9
    assert prog["legs"][0] == {"node_id": "viridian_reached", "d_open": 5}   # other legs untouched
    # Idempotent: a leg that already has a distance is not rescored.
    assert bpl.rescore_summary(summary, positions, graph, targets, "parcel_delivered") is False
