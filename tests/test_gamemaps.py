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
# The atlas is per game (schema 2, 2026-09-20): `public/maps/<game>/index.json`.
# It was one flat namespace holding one game, which is why FireRed's `3-0.png`
# and Emerald's would have been the same file.
MAPS_ROOT = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"
ATLAS = MAPS_ROOT / "firered-us" / "index.json"
GRAPH = REPO_ROOT / "data" / "firered-walkgraph.json"


def _atlases() -> list[Path]:
    return sorted(MAPS_ROOT.glob("*/index.json"))


@pytest.fixture(scope="module")
def atlas() -> dict:
    return json.loads(ATLAS.read_text())


@pytest.fixture(scope="module")
def graph() -> dict:
    return json.loads(GRAPH.read_text())


def test_the_atlas_covers_every_map_the_graph_knows(atlas, graph):
    """Exact equality, still — but against THIS game's graph.

    It used to compare the one global atlas against the one committed walk
    graph, which breaks the moment a second game exists. Weakening it to a
    subset check would have been the easy migration and the wrong one: an atlas
    missing a map the graph knows is a map that renders as a hole.
    """
    assert set(atlas["maps"]) == set(graph["maps"])


def test_every_atlas_names_its_own_game_and_that_game_owns_its_directory(atlas):
    """The namespacing invariant. Two games' `3-0.png` are different pictures,
    and nothing but the directory keeps them apart."""
    for path in _atlases():
        d = json.loads(path.read_text())
        assert d.get("schema") == 2, f"{path} predates the per-game atlas"
        assert d["game"] == path.parent.name, f"{path} claims to be {d['game']}"


def test_no_map_image_is_shared_between_two_games(atlas):
    """A file in two atlases would be one entry in the browser's image cache,
    which is exactly the bug the per-game key was added to stop."""
    seen: dict[str, str] = {}
    for path in _atlases():
        game = path.parent.name
        for key, m in json.loads(path.read_text())["maps"].items():
            f = m.get("file")
            if not f:
                continue
            full = f"{game}/{f}"
            assert full not in seen, f"{full} is claimed by {seen[full]} too"
            seen[full] = f"{game}:{key}"


def test_every_map_declares_whether_it_is_indoors(atlas):
    """`indoor` is a normalised boolean because `MAP_TYPE_INDOOR` is a pret
    gen-3 string no other source emits — branching on it classified every map of
    every other game as outdoor."""
    for path in _atlases():
        for key, m in json.loads(path.read_text())["maps"].items():
            assert isinstance(m.get("indoor"), bool), f"{path.parent.name}:{key} has no indoor flag"


def test_every_rendered_map_ships_the_border_block_it_is_drawn_inside(atlas):
    """The block the game repeats outside a map's own bounds.

    Without it a town ends at a hard edge with black beyond, which is what the
    map looked like until 2026-09-20. It is emitted once per map and tiled by
    the viewer, so it is tiny — the whole FireRed set is about 9 KB — but a map
    that is MISSING one draws that black edge again and nothing else fails.
    """
    for path in _atlases():
        d = json.loads(path.read_text())
        for key, m in d["maps"].items():
            if not m.get("file"):
                continue                      # no artwork yet: the lattice draws it
            b = m.get("border")
            assert b, f"{path.parent.name}:{key} has artwork but no border block"
            assert (path.parent / b["file"]).is_file(), f"{b['file']} is declared and absent"
            assert b["w"] > 0 and b["h"] > 0


def test_a_border_block_is_the_size_it_declares(atlas):
    """Read from the PNG header, not from the entry that claims it — the viewer
    tiles this by pattern, so a wrong size shears the whole bleed."""
    for path in _atlases():
        d = json.loads(path.read_text())
        tile = d["tile_px"]
        for key, m in d["maps"].items():
            b = m.get("border")
            if not b:
                continue
            raw = (path.parent / b["file"]).read_bytes()
            w = int.from_bytes(raw[16:20], "big")
            h = int.from_bytes(raw[20:24], "big")
            assert (w, h) == (b["w"] * tile, b["h"] * tile), \
                f"{b['file']} is {w}x{h} but declares {b['w']}x{b['h']} tiles"


def test_the_atlas_and_the_graph_were_built_from_the_same_pret_tree(atlas, graph):
    # A map image one tile out from the geometry drawn on it is invisible until
    # a route lands in a wall, so the two must name the same source.
    assert atlas["source"]["sha"] in str(graph.get("source"))
    assert atlas["walkgraph"]["version"] == graph["version"]
    assert atlas["tile_px"] == graph["tile_px"]


def test_every_atlas_pins_a_real_commit_not_a_floating_branch():
    """`pinned_sha()` falls back to "master" when the cache marker is missing,
    which makes the recorded provenance unverifiable. A 40-hex sha or nothing."""
    import re
    for path in _atlases():
        sha = (json.loads(path.read_text()).get("source") or {}).get("sha")
        assert sha and re.fullmatch(r"[0-9a-f]{40}", sha), f"{path} pins {sha!r}"


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
            assert atlas["maps"][d["to"]]["indoor"] is True, d


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
