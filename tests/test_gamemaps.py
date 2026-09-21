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


def test_every_road_out_of_a_map_is_recorded_as_an_open_edge(atlas):
    """`open` is where the game draws the NEIGHBOUR, not the border block.

    Tiling the block across one put a wall of trees over Oldale Town's two
    exits — Andreas, 2026-09-20: "the woods you create cover possible roads, so
    it looks like there is no road there ... where you can both go up and to
    the right." Every outdoor map with a connection must carry the span, and a
    span has to lie on the edge it names, or the viewer punches the hole in the
    wrong place and the wall goes back up somewhere else.
    """
    for path in _atlases():
        d = json.loads(path.read_text())
        for key, m in d["maps"].items():
            for o in m.get("open", []):
                assert o["side"] in ("up", "down", "left", "right"), (key, o)
                along = m["width"] if o["side"] in ("up", "down") else m["height"]
                assert 0 <= o["from"] < o["to"] <= along, (key, o)
        outdoor = [m for m in d["maps"].values() if not m.get("indoor") and not m.get("popup")]
        assert any(m.get("open") for m in outdoor), f"{path.parent.name}: no map leads anywhere"


def test_the_spans_match_the_maps_they_connect_to(atlas):
    """Two maps that connect must agree about the seam.

    The offset is stored on ONE side, so the arithmetic that turns it into a
    span runs independently for each; if it were wrong, one map would open a
    24-tile doorway and the other a 48-tile one onto the same road. Only the
    pairs where both maps are rendered can be checked, which is the point —
    they are a free control on the ones where only one is.
    """
    by_name = {m["name"]: (k, m) for k, m in atlas["maps"].items()}
    # Route 2 runs north from Viridian City to Pewter City, 24 wide against
    # their 48, so each city opens exactly Route 2's width and Route 2 opens
    # its whole edge.
    r2 = by_name["Route2"][1]
    assert {o["side"]: (o["from"], o["to"]) for o in r2["open"]} == {"up": (0, 24), "down": (0, 24)}
    for city in ("ViridianCity", "PewterCity"):
        spans = {o["side"]: (o["from"], o["to"]) for o in by_name[city][1]["open"]}
        side = "up" if city == "ViridianCity" else "down"
        assert spans[side] == (12, 36), f"{city} must open exactly Route 2's 24 tiles"


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
    "Route22_NorthEntrance",                 # Route 22 <-> Route 23, which we do not render
    "Route2_EastBuilding",                   # Route 2 on both sides, a cliff between them
}
# The two forest gates were corridors by the same rule until 2026-09-16, when
# Andreas asked for the forest and its gates to open together as one place:
# "i would actually like viridian forest to be a separate room, such that when
# you click the gate openings you see the gate houses and the forest in a popup
# stacked on top of each other." They are still transitions — a COMPLEX is the
# statement that a transition plus what it transitions to is worth opening.
COMPLEX_MEMBERS = {
    "Route2_ViridianForest_NorthEntrance",
    "ViridianForest",
    "Route2_ViridianForest_SouthEntrance",
}
BUILDINGS = {
    "PalletTown_PlayersHouse", "PalletTown_RivalsHouse", "PalletTown_ProfessorOaksLab",
    "ViridianCity_House", "ViridianCity_Gym", "ViridianCity_School", "ViridianCity_Mart",
    "ViridianCity_PokemonCenter", "Route2_House", "PewterCity_Museum", "PewterCity_Gym",
    "PewterCity_Mart", "PewterCity_House1", "PewterCity_House2", "PewterCity_PokemonCenter",
}
COMPLEXES = {"ViridianForest"}


def marked(atlas) -> set[str]:
    return {d["building"] for m in atlas["maps"].values() for d in m.get("doors", [])}


def doors_of(atlas, building) -> list[dict]:
    return [d for m in atlas["maps"].values() for d in m.get("doors", []) if d["building"] == building]


def test_every_building_and_complex_has_a_door_marker_and_no_one_else_does(atlas):
    assert marked(atlas) == BUILDINGS | COMPLEXES


def test_a_building_gets_ONE_marker_and_a_complex_one_per_way_in(atlas):
    for b in BUILDINGS:
        assert len(doors_of(atlas, b)) == 1, b
    # Route 2 meets the forest at two gates a long way apart; a marker on only
    # one leaves the other opening looking like scenery. One per WAY IN, not per
    # warp — a gate is two tiles wide and carries a warp on each.
    forest = doors_of(atlas, "ViridianForest")
    assert len(forest) == 2, forest
    assert len({d["to"] for d in forest}) == 2, "two gates, not two tiles of one gate"


def test_no_transition_room_is_marked_unless_it_belongs_to_a_complex(atlas):
    names = {k: m["name"] for k, m in atlas["maps"].items()}
    entered = {names[d["to"]] for m in atlas["maps"].values() for d in m.get("doors", [])}
    assert entered & CORRIDORS == set()
    assert entered & COMPLEX_MEMBERS, "the forest gates are the way into the complex"


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
        if b in COMPLEXES:
            continue                      # geographic order, asserted below
        for k in keys:
            assert atlas["maps"][k]["floors"] == sorted(keys, key=lambda x: atlas["maps"][x]["name"]), b


def test_a_complex_stacks_geographically_and_stays_off_the_world_frame(atlas):
    names = {k: m["name"] for k, m in atlas["maps"].items()}
    forest = [k for k, m in atlas["maps"].items() if m.get("building") == "ViridianForest"]
    assert {names[k] for k in forest} == COMPLEX_MEMBERS
    for k in forest:
        m = atlas["maps"][k]
        # north at the top, south at the bottom — NOT sorted, which would put
        # the forest beside a gate rather than between the two
        assert [names[f] for f in m["floors"]] == [
            "Route2_ViridianForest_NorthEntrance",
            "ViridianForest",
            "Route2_ViridianForest_SouthEntrance",
        ], k
        assert m["complex"] is True, k
        # the flag the world frame reads. The forest is a ROUTE, so the old
        # `type == MAP_TYPE_INDOOR` spelling would have left it on the world —
        # and so would `indoor`, which is False on it for the same reason.
        assert m["popup"] is True, k
    assert atlas["maps"]["1:0"]["indoor"] is False, "the forest is still a route"


def test_every_map_drawn_on_the_world_frame_says_so(atlas):
    # The converse, so `popup` cannot quietly spread: only a building floor or a
    # complex member carries it.
    for key, m in atlas["maps"].items():
        assert bool(m.get("popup")) == bool(m.get("building")), key


# -- the way back out (2026-09-16) --------------------------------------------
# The map walks INTO a building now rather than opening one over itself, so the
# way back has to be a thing on the map. Andreas: "the whole map should change
# to that sub-map with an arrow to go back, or the ability to just press on the
# door to get back out."


def test_every_cluster_has_a_way_out_and_it_leaves_the_cluster(atlas):
    maps = atlas["maps"]
    names = {k: m["name"] for k, m in maps.items()}
    by_building: dict[str, list[str]] = {}
    for key, m in maps.items():
        if m.get("building"):
            by_building.setdefault(m["building"], []).append(key)

    for building, keys in by_building.items():
        exits = [(k, e) for k in keys for e in maps[k].get("exits", [])]
        assert exits, f"{building} has no way out"
        for k, e in exits:
            # An exit into your own cluster is a STAIR, not a way out. Without
            # this the forest hands you two doors back to its own gate houses
            # and the 2F of every Centre offers its staircase as an exit.
            assert maps[e["to"]].get("building") != building, (building, names[k], names[e["to"]])
            assert 0 <= e["x"] < maps[k]["width"] and 0 <= e["y"] < maps[k]["height"], (k, e)


def test_the_forest_leaves_through_its_gates_not_by_itself(atlas):
    maps = atlas["maps"]
    assert maps["1:0"].get("exits", []) == [], "the forest's only neighbours are its own gates"
    for gate in ("15:0", "15:3"):
        outs = maps[gate]["exits"]
        assert len(outs) == 1, gate
        assert maps[outs[0]["to"]]["name"] == "Route2", gate


def test_a_three_tile_doorway_is_ONE_way_out(atlas):
    # Every FireRed doorway is three tiles wide and carries a warp on each.
    # Compared pairwise against only the doors already kept, the third tile
    # starts a second door of its own — which is what it did.
    gym = atlas["maps"]["6:2"]
    assert gym["name"] == "PewterCity_Gym"
    assert len(gym["exits"]) == 1, gym["exits"]
    # and a building with two REAL doors still gets two
    museum = next(m for m in atlas["maps"].values() if m["name"] == "PewterCity_Museum_1F")
    assert len(museum["exits"]) == 2, museum["exits"]


def test_only_a_map_drawn_in_a_cluster_carries_exits(atlas):
    for key, m in atlas["maps"].items():
        if m.get("exits"):
            assert m.get("popup"), key


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


# -- every floor of a visited building (2026-09-20) ----------------------------
# Andreas, looking at the rendered Emerald world: "the pokecenter (and other 2
# story/multiroom) rooms needs to show the multi rooms in their sub world". He
# clicked Oldale Town's Pokemon Center and the popup was one panel labelled 1F.
#
# The grouping was never the bug — `building_of` already puts `_1F` and `_2F` in
# one building. The 2F was not in the atlas at all, because an OBSERVED manifest
# is the maps a run entered and no run has walked upstairs in a Centre. FireRed
# never had the problem: its manifest is the decomp walk graph, so both Centre
# 2Fs have always been rendered.

def _render_gamemaps():
    """scripts/ is not a package; load the renderer by path.

    Registered in ``sys.modules`` BEFORE it is executed: `@dataclass` resolves
    its own annotations through `sys.modules[cls.__module__]`, and an unlisted
    module makes that lookup return None mid-import.
    """
    import importlib.util
    import sys
    if "render_gamemaps" in sys.modules:
        return sys.modules["render_gamemaps"]
    spec = importlib.util.spec_from_file_location(
        "render_gamemaps", REPO_ROOT / "scripts" / "render_gamemaps.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["render_gamemaps"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sibling_floors_adds_the_unvisited_floor_of_a_visited_building():
    """The rule itself, on a manifest with one floor of a two-floor building.

    This is the assertion that bites: with `sibling_floors` returning nothing
    (its body replaced by `return []`, the whole of the change under test), the
    first assert fails with `{'2:2'} != {'2:2', '2:3'}` — the 2F is absent, which
    is precisely what the atlas looked like. The second and third asserts are the
    over-reach controls and pass either way: they are what fails if the rule
    widens past a building.
    """
    mod = _render_gamemaps()
    mod.set_complexes("emerald-us")
    keys = {
        "OldaleTown_PokemonCenter_1F": "2:2",
        "OldaleTown_PokemonCenter_2F": "2:3",
        "OldaleTown_Mart": "2:4",            # same town, not the same building
        "PetalburgCity_PokemonCenter_1F": "3:0",   # same NAME shape, other town
        "PetalburgCity_PokemonCenter_2F": "3:1",
        "OldaleTown": "0:11",                # outdoors: never a floor of anything
    }
    indoor = {n for n in keys if n != "OldaleTown"}
    sel = {"2:2": "OldaleTown_PokemonCenter_1F"}
    added = mod.sibling_floors(sel, keys, lambda n: n in indoor)

    assert set(sel) == {"2:2", "2:3"}, "the visited building's other floor must be rendered"
    assert added == ["OldaleTown_PokemonCenter_2F"]
    # ...and nothing else. A rule that grouped on the town, or that let an
    # outdoor map count as a floor, would have pulled in the Mart, the other
    # town's Centre, or Oldale itself.
    assert set(sel.values()) & {"OldaleTown_Mart", "OldaleTown",
                                "PetalburgCity_PokemonCenter_1F"} == set()


def test_sibling_floors_expands_nothing_from_an_outdoor_map():
    """The guard that keeps the name rule from reading a town as a building."""
    mod = _render_gamemaps()
    mod.set_complexes("emerald-us")
    keys = {"Route104": "0:6", "Route104_MrBrineysHouse": "1:0"}
    sel = {"0:6": "Route104"}
    assert mod.sibling_floors(sel, keys, lambda n: n != "Route104") == []
    assert sel == {"0:6": "Route104"}


def test_the_oldale_pokemon_center_ships_both_of_its_floors():
    """The case Andreas reported, in the committed artifact.

    Fails before the change: `emerald-us/index.json` held `2:2` and no `2:3`.
    """
    d = json.loads((MAPS_ROOT / "emerald-us" / "index.json").read_text())
    floors = {k: m for k, m in d["maps"].items()
              if m.get("building") == "OldaleTown_PokemonCenter"}
    assert {m["name"] for m in floors.values()} == {
        "OldaleTown_PokemonCenter_1F", "OldaleTown_PokemonCenter_2F"}
    for key, m in floors.items():
        assert m["floors"] == sorted(floors), key
        assert (MAPS_ROOT / "emerald-us" / m["file"]).is_file(), key


def test_no_building_floor_points_outside_its_own_atlas():
    """The invariant the widening must not break: a panel with no picture."""
    for path in _atlases():
        d = json.loads(path.read_text())
        for key, m in d["maps"].items():
            for f in m.get("floors", []):
                assert f in d["maps"], f"{path.parent.name}:{key} lists a floor {f} it has not rendered"
                assert d["maps"][f].get("building") == m["building"], (key, f)


# A published game's artwork gets copied to gh-pages on every publish, and it
# stays in that branch's history. Nothing measured it until 2026-09-21, which
# is a reasonable way to wake up one morning with a 200 MB repository.
#
# The guard is bytes per TILE rather than bytes per game, because a game that
# ships more maps should be allowed to be bigger — what must not change is how
# expensive one tile of artwork is. Measured across all seven on 2026-09-21,
# every one of them 16 px per tile:
#
#   crystal 22.2   black 27.0   firered 38.9   emerald 41.8
#   black2 41.4    soulsilver 59.7   platinum 71.5
#
# The 2D games sit at 22-42 and the DS 3D renders at 60-72, which is the cost
# of real shading instead of a 16-colour tile repeated across a map.
#
# 200 is a regression alarm, not a budget: it is about 3x the worst game today,
# so it has room for the angled field camera (which draws the same map as a
# pitched parallelogram inside a larger bounding box) and for supersampling,
# while still catching the failure this is really for — a render that starts
# emitting full-colour PNGs, or forgets to quantise, and silently 10x's.
#
# If this fires because a render legitimately got richer, raise it and write
# the new measurement here. If it fires at 10x, something is broken.
MAX_BYTES_PER_TILE = 200


def test_no_game_ships_wildly_more_artwork_per_tile_than_the_others():
    measured = {}
    for path in _atlases():
        d = json.loads(path.read_text())
        tiles = sum(m["width"] * m["height"] for m in d["maps"].values())
        assert tiles, f"{path.parent.name} declares no tiles at all"
        size = sum(f.stat().st_size for f in path.parent.iterdir() if f.is_file())
        measured[path.parent.name] = size / tiles
    worst = max(measured, key=measured.get)
    assert measured[worst] <= MAX_BYTES_PER_TILE, (
        f"{worst} ships {measured[worst]:.1f} bytes of artwork per tile, over "
        f"the {MAX_BYTES_PER_TILE} alarm. All: "
        + ", ".join(f"{g} {v:.1f}" for g, v in sorted(measured.items(), key=lambda kv: -kv[1])))
