"""src/referee/walkgraph.py — the tile graph the progress tracker measures on.

Two layers: a hand-built toy graph pins the semantics (directed edges, cached
distance fields, map entry tiles, locus resolution), and the committed
``data/firered-walkgraph.json`` is checked for the story it must tell (bed →
Brock reachable, the Forest on the Viridian → Pewter path, no cut-tree bypass).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph

REPO = Path(__file__).resolve().parents[1]


@dataclass
class _Gate:
    type: str
    signature: dict
    locus: dict | None = None


def toy() -> WalkGraph:
    """Map (3,0): a corridor x=0..5 on y=0. Map (4,3): a room x=0..2 on y=0.

    corridor 3 → room 0 is a warp (one directed edge each way as two warps
    would give); corridor 1 → 2 is a one-way ledge (no edge back).
    """
    nodes = [(3, 0, x, 0) for x in range(6)] + [(4, 3, x, 0) for x in range(3)]
    idx = {c: i for i, c in enumerate(nodes)}
    adj: list[set[int]] = [set() for _ in nodes]

    def link(a, b, both=True):
        adj[idx[a]].add(idx[b])
        if both:
            adj[idx[b]].add(idx[a])

    link((3, 0, 0, 0), (3, 0, 1, 0))
    link((3, 0, 1, 0), (3, 0, 2, 0), both=False)  # ledge, east only
    link((3, 0, 2, 0), (3, 0, 3, 0))
    link((3, 0, 3, 0), (3, 0, 4, 0))
    link((3, 0, 4, 0), (3, 0, 5, 0))
    link((3, 0, 3, 0), (4, 3, 0, 0))  # door
    link((4, 3, 0, 0), (4, 3, 1, 0))
    link((4, 3, 1, 0), (4, 3, 2, 0))
    return WalkGraph(nodes, [sorted(a) for a in adj], {"3:0": {"name": "Corridor", "width": 6, "height": 1},
                                                        "4:3": {"name": "Room", "width": 3, "height": 1}})


def test_lookups_and_round_trip():
    g = toy()
    assert g.node_id(3, 0, 2, 0) == 2
    assert g.node_id(3, 0, 9, 0) is None and g.node_id(9, 9, 0, 0) is None
    assert g.has_map(4, 3) and not g.has_map(1, 0)
    assert g.map_name(4, 3) == "Room"
    again = WalkGraph.from_dict(g.to_dict())
    assert again.nodes == g.nodes and again.adj == g.adj and again.maps == g.maps


def test_distance_field_respects_direction_and_is_cached():
    g = toy()
    room_end = {g.node_id(4, 3, 2, 0)}
    field = g.distance_field(room_end)
    # corridor 0 → 1 → 2 (ledge) → 3 → door → room 0 → 1 → 2 = 6 steps
    assert field[g.node_id(3, 0, 0, 0)] == 6
    assert field[g.node_id(3, 0, 5, 0)] == 5
    assert g.distance_to(room_end, g.node_id(4, 3, 2, 0)) == 0
    # the ledge is one-way: from x=2 you cannot get back to x=0
    assert g.distance_to({g.node_id(3, 0, 0, 0)}, g.node_id(3, 0, 2, 0)) is None
    assert g.distance_to({g.node_id(3, 0, 0, 0)}, g.node_id(3, 0, 1, 0)) == 1
    assert g.distance_field(room_end) is field, "same target set → cached list"
    assert g.steps_between(g.node_id(3, 0, 0, 0), g.node_id(4, 3, 2, 0)) == 6
    assert g.steps_between(g.node_id(3, 0, 2, 0), g.node_id(3, 0, 0, 0)) is None
    assert g.steps_between(3, 3) == 0


def test_map_entry_nodes_and_neighbours():
    g = toy()
    assert g.map_entry_nodes(4, 3) == {g.node_id(4, 3, 0, 0)}
    assert g.map_entry_nodes(3, 0) == {g.node_id(3, 0, 3, 0)}
    # an NPC standing at (4,3,1,-1) (off-grid, impassable) is approached from (1,0) only
    assert g.passable_neighbours(4, 3, 1, -1) == {g.node_id(4, 3, 1, 0)}
    assert g.passable_neighbours(4, 3, 1, 0) == {g.node_id(4, 3, 0, 0), g.node_id(4, 3, 2, 0)}


def test_resolve_locus_map_tiles_near_and_none():
    g = toy()
    assert g.resolve_locus(_Gate("map", {"map_group": 4, "map_num": 3})) == {g.node_id(4, 3, 0, 0)}
    # an explicit locus beats the map's entry tiles (Oak's trigger, 2026-09-09)
    assert g.resolve_locus(_Gate("map", {"map_group": 4, "map_num": 3}, {"map_group": 3, "map_num": 0, "tiles": [[5, 0]]})) == {5}
    assert g.resolve_locus(_Gate("flag", {"flag_id": 1}, {"map_group": 3, "map_num": 0, "tiles": [[5, 0]]})) == {5}
    assert g.resolve_locus(_Gate("flag", {"flag_id": 1}, {"map_group": 4, "map_num": 3, "near": [[1, -1]]})) == {g.node_id(4, 3, 1, 0)}
    both = g.resolve_locus(_Gate("var", {}, {"map_group": 4, "map_num": 3, "tiles": [[2, 0]], "near": [[0, -1]]}))
    assert both == {g.node_id(4, 3, 2, 0), g.node_id(4, 3, 0, 0)}
    assert g.resolve_locus(_Gate("flag", {"flag_id": 1})) == set()
    assert g.resolve_locus(_Gate("flag", {"flag_id": 1}, {"map_group": 9, "map_num": 9, "tiles": [[0, 0]]})) == set()
    assert g.resolve_locus(_Gate("flag", {"flag_id": 1}, {"tiles": [[0, 0]]})) == set(), "no map → no target"


# ─────────────────────────── the committed FireRed graph ───────────────────────────

@pytest.fixture(scope="module")
def firered() -> WalkGraph:
    path = REPO / DEFAULT_GRAPH_PATH
    if not path.exists():
        pytest.skip("data/firered-walkgraph.json not built")
    return WalkGraph.load(path)


def _gn(g: WalkGraph, name: str) -> tuple[int, int]:
    for key, m in g.maps.items():
        if m["name"] == name:
            a, b = key.split(":")
            return int(a), int(b)
    raise AssertionError(f"{name} not in graph")


def test_firered_graph_places_outdoor_maps_in_one_world_frame(firered):
    """Graph version 2 (2026-09-15): Pallet Town's top-left is (0, 0); Route 1
    sits directly above it (40 tall), Viridian above that; indoor maps have no
    frame. Pixel = world × meta["tile_px"]."""
    assert firered.meta["version"] == 2 and firered.meta["tile_px"] == 16
    assert firered.world_xy(3, 0, 6, 8) == (6, 8)          # Pallet Town, the player's door step
    assert firered.world_xy(3, 19, 10, 39) == (10, -1)      # Route 1's bottom row touches Pallet Town's top
    assert firered.world_xy(3, 1, 0, 39) == (-12, -41)      # Viridian, offset 12 west of Route 1
    assert firered.world_xy(4, 0, 4, 8) is None             # the player's house: indoor, no frame


def test_firered_graph_covers_the_first_badge_route(firered):
    for name in ("PalletTown", "PalletTown_PlayersHouse_2F", "PalletTown_ProfessorOaksLab", "Route1", "ViridianCity",
                 "ViridianCity_Mart", "ViridianCity_PokemonCenter_1F", "Route2", "ViridianForest", "PewterCity", "PewterCity_Gym"):
        _gn(firered, name)
    # group/num agree with the referee's gate signatures (checkpoints-firered-firstbadge.yaml)
    assert _gn(firered, "PalletTown") == (3, 0) and _gn(firered, "Route1") == (3, 19)
    assert _gn(firered, "PalletTown_ProfessorOaksLab") == (4, 3) and _gn(firered, "ViridianForest") == (1, 0)
    assert _gn(firered, "PewterCity") == (3, 2) and _gn(firered, "PewterCity_Gym") == (6, 2)


def test_firered_bed_to_brock_is_walkable_in_ladder_order(firered):
    g = firered
    bed = _gn(g, "PalletTown_PlayersHouse_2F")
    start = next(n for n in (g.node_id(*bed, 5, 6), g.node_id(*bed, 4, 6), g.node_id(*bed, 6, 6)) if n is not None)
    dist = {name: g.distance_to(g.map_entry_nodes(*_gn(g, name)), start)
            for name in ("PalletTown", "PalletTown_ProfessorOaksLab", "Route1", "ViridianCity", "Route2", "ViridianForest", "PewterCity")}
    assert all(d is not None for d in dist.values()), dist
    outdoor = [dist[n] for n in ("PalletTown", "Route1", "ViridianCity", "Route2", "ViridianForest", "PewterCity")]
    assert outdoor == sorted(outdoor), f"outdoor gates must get farther in ladder order: {dist}"
    brock = g.passable_neighbours(*_gn(g, "PewterCity_Gym"), 6, 5)
    assert brock and g.distance_to(brock, start) is not None
    # from Brock's tile you can NOT walk back up the Route 1 ledges the short way,
    # but you can walk back at all (the graph is connected in both directions)
    assert g.distance_to({start}, next(iter(brock))) is not None


def test_firered_viridian_to_pewter_goes_through_the_forest(firered):
    """Cut trees are object events, not tiles; without treating them as barriers
    the shortest path took Route 2's east side and skipped the maze (2026-09-09)."""
    g = firered
    forest = _gn(g, "ViridianForest")
    field = g.distance_field(g.map_entry_nodes(*_gn(g, "PewterCity")))
    n = min(g.map_entry_nodes(*_gn(g, "ViridianCity")), key=lambda i: field[i])
    forest_tiles = 0
    while field[n] > 0:
        n = min(g.adj[n], key=lambda u: field[u] if field[u] >= 0 else 10**9)
        forest_tiles += g.coord(n)[:2] == forest
    assert forest_tiles > 50, f"path uses only {forest_tiles} Forest tiles"
    # the maze itself: south warps → north warps is a long walk, not a straight line
    south = {g.node_id(*forest, x, 62) for x in (28, 29, 30)} - {None}
    north = {g.node_id(*forest, x, 9) for x in (4, 5, 6)} - {None}
    assert min(g.distance_to(north, s) for s in south) > 100


# --- passable_in: the rule behind walls_hit (artifacts/wasted-inputs/plan.md) --

def test_passable_in_calls_the_route1_ledge_a_wall_from_below(firered):
    """The tile gemini-3.8-flash(high) pressed U into 46 times on 2026-09-15."""
    g = firered
    route1 = _gn(g, "Route1")
    assert g.passable_in(*route1, 14, 17, "U") is False
    assert g.passable_in(*route1, 14, 17, "D") is True
    assert g.passable_in(*route1, 14, 17, "L") is True


def test_passable_in_accepts_a_ledge_hop_which_moves_two_tiles(firered):
    """A ledge edge targets the tile TWO away, so an adjacent-only test called
    it a wall — 4 of 1467 observed moves in the 2026-09-15 control."""
    g = firered
    route1 = _gn(g, "Route1")
    assert g.passable_in(*route1, 13, 30, "D") is True
    assert g.node_id(*route1, 13, 31) is None  # the tile between is not walkable


def test_passable_in_refuses_to_judge_a_tile_a_warp_leaves(firered):
    """A door's target is on another map, so the same-map neighbour set is
    empty that way and a legitimate door approach read as a wall (9 presses in
    the 2026-09-15 control). None means "cannot say", and nothing is charged."""
    g = firered
    assert g.passable_in(*_gn(g, "PalletTown"), 16, 13, "U") is None
    assert g.passable_in(999, 999, 0, 0, "U") is None       # off-graph
    assert g.passable_in(*_gn(g, "Route1"), 14, 17, "A") is None  # not a direction
