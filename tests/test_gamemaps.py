"""The committed map atlas (src/dashboard/web/public/maps/index.json).

The PNGs and this index are generated offline by scripts/render_gamemaps.py and
committed, so what the site draws is fixed until someone regenerates it. These
tests guard the two things a regeneration could silently break: the atlas
agreeing with the walk graph it is drawn under, and which rooms count as
buildings (artifacts/game-map-render/plan.md M3, M10-M12).

The render itself is checked against real screenshots by
scripts/verify_gamemap_render.py, which needs a run folder and so cannot live
here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ATLAS = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps" / "index.json"
GRAPH = REPO_ROOT / "data" / "firered-walkgraph.json"


@pytest.fixture(scope="module")
def atlas() -> dict:
    return json.loads(ATLAS.read_text())


@pytest.fixture(scope="module")
def graph() -> dict:
    return json.loads(GRAPH.read_text())


def test_the_atlas_covers_every_map_the_graph_knows(atlas, graph):
    assert set(atlas["maps"]) == set(graph["maps"])


def test_the_atlas_and_the_graph_were_built_from_the_same_pret_tree(atlas, graph):
    # A map image one tile out from the geometry drawn on it is invisible until
    # a route lands in a wall, so the two must name the same source.
    assert atlas["pret_sha"] in str(graph.get("source"))
    assert atlas["graph_version"] == graph["version"]
    assert atlas["tile_px"] == graph["tile_px"]


def test_every_map_image_exists_and_matches_its_declared_size(atlas):
    png = ATLAS.parent
    for key, m in atlas["maps"].items():
        f = png / m["file"]
        assert f.is_file(), f"{key}: {m['file']} missing"
        head = f.read_bytes()[:33]
        assert head[:8] == b"\x89PNG\r\n\x1a\n"
        w = int.from_bytes(head[16:20], "big")
        h = int.from_bytes(head[20:24], "big")
        assert (w, h) == (m["width"] * 16, m["height"] * 16), key


def test_every_map_the_graph_places_in_the_world_is_placed_here_too(atlas, graph):
    for key, m in graph["maps"].items():
        assert atlas["maps"][key].get("world") == m.get("world"), key


# -- which rooms are buildings ------------------------------------------------
# Andreas, 2026-09-15: "rooms which aren't transition rooms like the ones to
# Viridian Forest should have a house marker on the map". The four gate houses
# below are the transitions; everything else is a place worth opening.

CORRIDORS = {
    "Route2_ViridianForest_NorthEntrance",   # Route 2 <-> the forest
    "Route2_ViridianForest_SouthEntrance",   # the same, at the other end
    "Route22_NorthEntrance",                 # Route 22 <-> Route 23, which we do not render
    "Route2_EastBuilding",                   # Route 2 on both sides, a cliff between them
}
BUILDINGS = {
    "PalletTown_PlayersHouse", "PalletTown_RivalsHouse", "PalletTown_ProfessorOaksLab",
    "ViridianCity_House", "ViridianCity_Gym", "ViridianCity_School", "ViridianCity_Mart",
    "ViridianCity_PokemonCenter", "Route2_House", "PewterCity_Museum", "PewterCity_Gym",
    "PewterCity_Mart", "PewterCity_House1", "PewterCity_House2", "PewterCity_PokemonCenter",
}


def marked(atlas) -> set[str]:
    return {d["building"] for m in atlas["maps"].values() for d in m.get("doors", [])}


def test_every_building_has_exactly_one_door_marker(atlas):
    assert marked(atlas) == BUILDINGS


def test_no_transition_room_is_marked(atlas):
    names = {k: m["name"] for k, m in atlas["maps"].items()}
    entered = {names[d["to"]] for m in atlas["maps"].values() for d in m.get("doors", [])}
    assert entered & CORRIDORS == set()


def test_a_door_points_at_an_indoor_map_on_its_own_outdoor_map(atlas):
    for key, m in atlas["maps"].items():
        for d in m.get("doors", []):
            assert m.get("type") != "MAP_TYPE_INDOOR", key
            assert 0 <= d["x"] < m["width"] and 0 <= d["y"] < m["height"], (key, d)
            assert atlas["maps"][d["to"]]["type"] == "MAP_TYPE_INDOOR", d


def test_a_multi_floor_building_lists_all_its_floors_in_order(atlas):
    # Andreas: "for multi level houses such as pokecenter or reds house I would
    # expect all floors to be present in the popup" (M11).
    by_building: dict[str, list[str]] = {}
    for key, m in atlas["maps"].items():
        if m.get("building"):
            by_building.setdefault(m["building"], []).append(key)
    multi = {b: ks for b, ks in by_building.items() if len(ks) > 1}
    assert "PalletTown_PlayersHouse" in multi and "PewterCity_PokemonCenter" in multi
    for b, keys in multi.items():
        for k in keys:
            assert atlas["maps"][k]["floors"] == sorted(keys, key=lambda x: atlas["maps"][x]["name"]), b


# -- trimmed edges (2026-09-15) ------------------------------------------------
# Andreas: "I still see this bar". Every FireRed interior ends in a flat strip
# nothing can stand on, and drawn honestly it reads as an empty progress bar
# under the floor. The renderer marks those edges; the viewer draws a shorter
# image. The invariant that makes it safe is below.

def test_no_trimmed_edge_holds_a_walkable_tile(atlas, graph):
    """Cutting a row the run could stand on would erase part of the route."""
    walkable: dict[str, set[tuple[int, int]]] = {}
    for g, n, x, y in graph["nodes"]:
        walkable.setdefault(f"{g}:{n}", set()).add((x, y))
    for key, m in atlas["maps"].items():
        trim = m.get("trim") or {}
        tiles = walkable.get(key, set())
        for x, y in tiles:
            assert x >= trim.get("left", 0), (key, "left", x, y)
            assert y >= trim.get("top", 0), (key, "top", x, y)
            assert x < m["width"] - trim.get("right", 0), (key, "right", x, y)
            assert y < m["height"] - trim.get("bottom", 0), (key, "bottom", x, y)


def test_only_interiors_are_trimmed(atlas):
    # An outdoor map's edges have to line up with its neighbours in the world
    # frame, so none of them may be cut, however blank they look.
    for key, m in atlas["maps"].items():
        if m.get("trim"):
            assert m["type"] == "MAP_TYPE_INDOOR", key


def test_a_trim_never_swallows_the_map(atlas):
    for key, m in atlas["maps"].items():
        t = m.get("trim") or {}
        assert m["width"] - t.get("left", 0) - t.get("right", 0) >= 1, key
        assert m["height"] - t.get("top", 0) - t.get("bottom", 0) >= 1, key
