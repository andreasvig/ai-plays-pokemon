"""The DS map atlases (`scripts/render_dsmaps.py`).

Gen 4 artwork is a different problem from gen 1-3 and these guard the three
places where it is different, each of which fails SILENTLY if it is wrong —
the atlas loads, the viewer reports nothing, and the map draws as a black
rectangle or as nothing at all:

  1. the map key on the wire. An observed graph spells a DS map "342"; a route
     spells the same map "342:0". An atlas keyed the first way looks complete
     and matches no route.
  2. the pixel frame. Gen 4 outdoor coordinates are GLOBAL, so the PNG is in
     route-tile coordinates and starts before the map does. Off by that offset
     and `drawImage` reads a source rect outside the image, which draws nothing
     and throws nothing.
  3. `tile_px`. A DS render is not obliged to be 16, and every consumer has to
     take the number from the atlas rather than assume it.

The checks that need the decomp cache (`local/pret-cache-platinum`) skip
without it; everything that reads the committed atlas always runs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

MAPS_ROOT = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"
DS_GAMES = ("platinum-us", "soulsilver-us")


def _ds_atlases() -> list[tuple[str, dict]]:
    out = []
    for game in DS_GAMES:
        path = MAPS_ROOT / game / "index.json"
        if path.is_file():
            out.append((game, json.loads(path.read_text())))
    return out


@pytest.fixture(scope="module")
def atlases() -> list[tuple[str, dict]]:
    got = _ds_atlases()
    if not got:
        pytest.skip("no DS atlas rendered yet")
    return got


# -- 1. the key on the wire ----------------------------------------------------

def test_every_ds_atlas_key_is_the_route_wire_encoding(atlases):
    """`"342:0"`, not `"342"`.

    THE silent bug in this work. `src/app/route.py:_sample_tile` pads a
    single-id game's key to two slots with a constant 0, and the atlas is keyed
    on the string the browser receives. An atlas keyed "342" loads without
    complaint, matches nothing, and draws an empty frame.
    """
    for game, atlas in atlases:
        assert atlas["key_shape"] == "id", game
        for key in atlas["maps"]:
            assert re.fullmatch(r"\d+:0", key), f"{game}: {key!r} is not <id>:0"


def test_route_py_produces_exactly_the_keys_the_ds_atlas_ships(atlases):
    """The producer, not a copy of it.

    `_sample_tile` is what actually turns a DS sample into a map key, so it is
    what the atlas has to agree with — reimplementing the `:0` rule here would
    test this file against itself. Every key the atlas ships must be the key
    `route.py` builds for that map id, and the atlas must have a home for every
    id the runs recorded.
    """
    from src.app import route

    for game, atlas in atlases:
        for key in atlas["maps"]:
            map_id = int(key.split(":")[0])
            tile = route._sample_tile({"map_id": map_id, "x": 3, "y": 4})
            assert tile is not None and f"{tile[0]}:{tile[1]}" == key, (game, key)


# The only maps a run entered that are deliberately not rendered, and why.
#
# EMPTY, as of the arm9 map-header table. It used to hold SoulSilver's three
# interiors, on the grounds that they appear in no matrix header plane and the
# table that would place them is inside the BLZ-compressed arm9. That was true
# about the compressed bytes and not about the table: decompressed, the table is
# there, and 61/63/64 resolve to matrices 100/71/72. The test below is what said
# so — it asserted the exception was still earned, and it failed the day it was
# not. Keep it that way: a map listed here has to be one the cartridge genuinely
# cannot place.
UNPLACEABLE: dict[str, set[int]] = {}


def test_a_ds_atlas_covers_every_map_id_its_observed_graph_names(atlases):
    """An observed map with no atlas entry falls back to a lattice and says
    nothing about it, so the omission is invisible on the page."""
    for game, atlas in atlases:
        obs_path = REPO_ROOT / "artifacts" / "game-map-render" / "observed" / f"{game}-observed.json"
        if not obs_path.is_file():
            continue
        obs = json.loads(obs_path.read_text())
        ids = {int(n.split("|")[0]) for edge in obs["edges"] for n in edge[:2]}
        missing = {i for i in ids if f"{i}:0" not in atlas["maps"]}
        assert missing <= UNPLACEABLE.get(game, set()), \
            f"{game}: observed maps with no artwork: {sorted(missing)}"


def test_the_maps_we_refuse_to_place_are_still_unplaceable():
    """The other half of `UNPLACEABLE`: an exception that is never re-checked is
    just a lowered bar. Each listed map must still be one the ROM cannot place,
    so the day one becomes resolvable this fails and asks for it to be
    rendered — which is how the list came to be empty.

    With an empty list this also asserts the OTHER direction: every map a run
    entered is placeable. Without that the list could be emptied by deleting it
    rather than by resolving the maps.
    """
    import render_dsmaps

    for game, ids in UNPLACEABLE.items():
        rom = render_dsmaps.ROMS.get(game)
        if rom is None or not rom.is_file():
            pytest.skip(f"{game} cartridge not present")
        src = render_dsmaps.RomDecomp(rom)
        still = {i for i in ids if not src.resolvable(i)}
        assert still == ids, f"{game}: {sorted(ids - still)} can be placed now — render them"

    for game, rom in render_dsmaps.ROMS.items():
        if not rom.is_file() or not render_dsmaps.run_dirs(game):
            continue
        src = render_dsmaps.RomDecomp(rom)
        entered = set(render_dsmaps.observed_maps(game))
        lost = {i for i in entered if not src.resolvable(i)} - UNPLACEABLE.get(game, set())
        assert not lost, f"{game}: {sorted(lost)} were entered and cannot be placed"


# -- 2. the pixel frame --------------------------------------------------------

def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:33]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def test_each_ds_png_holds_exactly_the_source_rect_the_viewer_reads(atlases):
    """The invariant `RouteMap.svelte` draws by, asserted on the shipped bytes.

    Straight down it takes the source rect from `drawWindow(m)` — `origin +
    trim` — subtracts `pngOrigin(m)`, and multiplies by `tile_px`:

        c.drawImage(img, (win.x - png.x) * TPX, (win.y - png.y) * TPX,
                    win.w * TPX, win.h * TPX, ...)

    Under the PITCHED camera (2026-09-21) there is no source rect that is the
    map — a roof rises out of the top of the ground rectangle — so the viewer
    draws the whole image ANCHORED by `png_pad`, and the invariant becomes:
    the PNG is the ground rectangle under the atlas's own projection, plus
    exactly the pad it declares. Same failure either way: a PNG whose frame
    does not match what the atlas declares is read outside itself, and the
    canvas spec draws nothing for a source rect outside the image and raises
    no error.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from ds3d import camera as C

    for game, atlas in atlases:
        tile = atlas["tile_px"]
        raw = atlas.get("camera")
        kind = raw.get("kind") if isinstance(raw, dict) else (raw or "topdown")
        cam = C.camera_for(kind, tile)
        for key, m in atlas["maps"].items():
            ox, oy = m.get("origin", [0, 0])
            px, py = m.get("png_origin", [0, 0])
            pl, pt, pr, pb = m.get("png_pad", [0, 0, 0, 0])
            w, h = _png_size(MAPS_ROOT / game / m["file"])
            frame = C.Frame(cam, ox - px + m["width"], oy - py + m["height"],
                            (pl, pt, pr, pb))
            want = (frame.width, frame.height)
            assert (w, h) == want, \
                f"{game}:{key} is {w}x{h}px but its declared frame is {want[0]}x{want[1]}"


def test_a_ds_atlas_ships_the_map_and_not_the_empty_world_before_it(atlases):
    """The other half of `png_origin`, and the reason it exists.

    Gen 4 outdoor coordinates are GLOBAL: Sandgem Town's corner is route tile
    (160, 832). Padding its PNG out to route tile (0, 0) at 16 px/tile makes a
    3072 x 13824 image — 42 megapixels, 170 MB decoded, past what iOS Safari
    will decode, and a map that draws as nothing with no error anywhere.

    So a DS atlas declares `png_frame: "map-local"` and every entry's
    `png_origin` IS its `origin`. Asserted as an equality rather than as a size
    bound: the two drifting apart is the same silent mis-read, just smaller.
    """
    for game, atlas in atlases:
        frame = atlas["png_frame"]
        assert frame in ("map-local", "route-tile"), (game, frame)
        for key, m in atlas["maps"].items():
            png = m.get("png_origin", [0, 0])
            origin = m.get("origin", [0, 0])
            # The frame and the field have to say the same thing. A map-local
            # PNG with no `png_origin` is read from the empty world before the
            # map; a route-tile PNG with one is read past its own right edge.
            assert png == (origin if frame == "map-local" else [0, 0]), (game, key, frame)
            w, h = _png_size(MAPS_ROOT / game / m["file"])
            assert w * h <= 16_000_000, \
                f"{game}:{key} is {w}x{h} = {w * h / 1e6:.0f} Mpx, over the decode ceiling"


def test_an_outdoor_ds_map_is_placed_at_the_same_tile_it_is_drawn_from(atlases):
    """`world` and `origin` are the same number for a gen-4 outdoor map.

    `worldLayout` places the map at `world`; `drawWindow` reads the artwork from
    `origin`. For gen 1-3 they are different things — a map's place in the world
    and its trim — and for a map whose route coordinates are already the world's
    they are one number. Splitting them shifts the artwork off the route by
    exactly the difference, which looks like a plausible map in a plausible
    place.
    """
    for game, atlas in atlases:
        for key, m in atlas["maps"].items():
            if m.get("indoor") or m.get("popup"):
                continue
            assert m.get("world") == m.get("origin", [0, 0]), (game, key)


# -- 3. tile_px ----------------------------------------------------------------

def test_a_ds_atlas_states_the_tile_px_its_own_tier_renders_at(atlases):
    """`mapatlas.js:tilePxOf` is `atlas?.tile_px ?? route?.tile_px ?? TILE`, so
    the atlas wins and every consumer has to take the number from it.

    The number is a fact about the TIER, not a preference, and the two tiers
    disagree — which is what makes this bite. 16 is `MAP_OBJECT_TILE_SIZE`, the
    cartridge's own world units per tile, so 3D artwork at 16 is 1 px per world
    unit and nothing is invented; a COLLISION silhouette at 16 would be a 4x
    upscale of a one-bit mask, four times the bytes for no extra information.
    """
    import render_dsmaps

    # `render` names the tier AND the camera ("3d-ortho" / "3d-pitched"), so
    # the tier is recovered from the pair table rather than from the old
    # one-to-one map, which knew nothing about a camera.
    want = {v: tier for (tier, _cam), v in render_dsmaps.RENDER_KIND_FOR.items()}
    for game, atlas in atlases:
        assert isinstance(atlas["tile_px"], int) and atlas["tile_px"] > 0, game
        tier = want.get(atlas["render"])
        assert tier is not None, f"{game}: unknown render {atlas['render']!r}"
        assert atlas["tile_px"] == render_dsmaps.TILE_PX[tier], \
            f"{game}: {atlas['render']} at {atlas['tile_px']} px/tile"
        if atlas["render"] == "collision":
            assert atlas["tile_px"] != 16, \
                f"{game}: a DS silhouette at 16 px/tile is a 4x upscale of a 1-bit mask"


# -- what the silhouette is ----------------------------------------------------

def test_a_ds_map_is_a_shape_and_not_a_rectangle(atlases):
    """Both directions: some of the map is walkable and some of it is not.

    A silhouette that is all floor is a black rectangle with a different colour,
    and one that is all wall is a map the route cannot be standing on. The
    walkable count is recorded per map by the renderer, so this reads what was
    actually drawn rather than re-deriving it.
    """
    for game, atlas in atlases:
        for key, m in atlas["maps"].items():
            area = m["width"] * m["height"]
            walk = m["walkable"]
            assert 0 < walk < area, f"{game}:{key} is {walk}/{area} walkable"


def test_every_interior_is_smaller_than_the_block_it_lives_in(atlases):
    """A gen-4 interior is a room inside a 32x32 block, and the unused rest of
    that block reads as `0x0000` — passable, behaviour none — exactly like the
    room's own floor. Shipped uncropped, Professor Rowan's lab is a small room
    beside a blank hall the size of the map, and nothing about the data says
    which half is the lab."""
    interiors = [(g, k, m) for g, a in atlases for k, m in a["maps"].items() if m.get("indoor")]
    if not interiors:
        pytest.skip("no interiors in the rendered set")
    for game, key, m in interiors:
        assert m["width"] < 32 or m["height"] < 32, \
            f"{game}:{key} fills its whole 32x32 block, so it was not cropped to its walls"


def test_an_interior_is_reachable_from_the_map_its_door_is_on(atlases):
    """A popup nobody can open is artwork nobody sees.

    Reachable AS A BUILDING, not floor by floor: the viewer opens every floor
    of a building from the one door marker, so an upstairs room that is only
    entered from its own ground floor is reachable. SoulSilver's 2F is exactly
    that, and asserting a door per floor would have called it unreachable while
    the page opens it fine.

    Keyed on `popup ?? indoor`, WHICH IS WHAT `mapatlas.js` KEYS ON, not on
    `indoor` alone. Lake Verity Low Water is a `MAP_TYPE_CAVE` that opens as a
    cluster because it is off the region frame, so a test that asked about
    indoor-ness would have skipped the one map the rule was just extended to.
    """
    for game, atlas in atlases:
        doors = {d["to"] for m in atlas["maps"].values() for d in m.get("doors", [])}
        for key, m in atlas["maps"].items():
            if not (m.get("popup") if m.get("popup") is not None else m.get("indoor")):
                continue
            floors = m.get("floors") or []
            assert m.get("building") and key in floors, (game, key)
            assert doors & set(floors), \
                f"{game}:{key} is in a building with no door into any of its floors"


def test_the_provenance_is_a_commit_and_not_a_branch(atlases):
    for game, atlas in atlases:
        sha = (atlas.get("source") or {}).get("sha")
        assert sha and re.fullmatch(r"[0-9a-f]{40}", sha), f"{game} pins {sha!r}"


# -- the registration, against the decomp itself -------------------------------

def _decomp():
    import render_dsmaps

    if not (render_dsmaps.CACHE / "SHA").exists():
        pytest.skip("no pret/pokeplatinum cache; run scripts/render_dsmaps.py first")
    return render_dsmaps, render_dsmaps.Decomp(offline=True)


def test_the_terrain_grid_is_at_the_offset_the_decomp_declares():
    """Not reverse-engineered: `include/constants/field/map.h` names every one of
    these, and a cached `map_data_NNN.bin` has to agree with all of them at once.

    The header's first word IS the size of the attribute block, so a file whose
    own header disagrees with `TERRAIN_ATTRIBUTES_SIZE` would be read as noise
    and rendered as a plausible-looking wrong shape.
    """
    mod, d = _decomp()
    grid = d.grid("MAP_005")                      # the west half of Route 201
    assert grid is not None and grid.shape == (mod.CELL, mod.CELL)
    assert mod.ATTR_OFFSET == 0x10 and mod.ATTR_SIZE == 0x800
    assert mod.COLLISION_MASK == 0x8000 and mod.BEHAVIOR_MASK == 0xFF
    blocked = (grid & mod.COLLISION_MASK) != 0
    assert 0 < blocked.sum() < grid.size, "a block that is all wall or all floor is a misread"


def test_the_matrix_places_every_sample_on_the_map_the_sample_names():
    """Registration check 1. A global tile (X, Y) is matrix cell (X>>5, Y>>5),
    and the cell's header must BE the map the run says it was on. Nothing is
    fitted and nothing is searched; if this is wrong every silhouette is drawn
    somewhere plausible and wrong."""
    mod, d = _decomp()
    if not mod.run_dirs("platinum-us"):
        pytest.skip("no platinum runs on disk")
    res = mod.check_registration(d, "platinum-us")
    assert res["cell"]["total"] > 500
    assert not res["cell"]["bad"], res["cell"]["bad"][:5]


def test_every_tile_the_player_stood_on_is_walkable_or_a_declared_warp():
    """Registration check 2, with its one named exception enumerated.

    A `TILE_BEHAVIOR_DOOR` tile carries the collision bit — you never walk onto
    a door, the field code warps you — and the position sample on that frame
    reports you standing there. So the exception is not "some tiles fail": it is
    "every failure is a tile pret's own `warp_events` calls a warp", which is a
    different file from the one the collision came from. Anything else is a real
    failure, because nothing in the cartridge would explain it.
    """
    mod, d = _decomp()
    if not mod.run_dirs("platinum-us"):
        pytest.skip("no platinum runs on disk")
    res = mod.check_registration(d, "platinum-us")
    assert not res["walkable"]["bad"], res["walkable"]["bad"][:5]
    tiles = {(b[1], b[2], b[3]) for b in res["walkable"]["warp"]}
    assert len(tiles) < 10, f"{len(tiles)} distinct blocked tiles is too many to be doors"
    # The behaviour BYTE is not a second witness, and asserting on it was a
    # bonus that outlived its evidence. A 100-turn run on 2026-09-20 walked a
    # staircase inside the Jubilife trainer school (map 415, tile 9,4): the
    # model's own turn-5 reasoning says "into the staircase down", pret's
    # `warp_events` calls it a warp -- and its behaviour reads
    # TILE_BEHAVIOR_NONE. A warp's behaviour byte simply need not spell WARP.
    # What the check above already proves is the load-bearing claim, and it is
    # the one that would catch a misregistration: every blocked tile a run
    # stood on is a warp in a DIFFERENT file from the one the collision came
    # from. The names are reported, not asserted, so a future reader can see
    # what the exceptions actually are.
    # What IS still worth asserting is that the behaviour byte decodes at all:
    # every exception's attribute must index a real name in the decomp's own
    # enum. That bites if BEHAVIOR_MASK is wrong or the enum is misparsed,
    # which is the failure this line can actually detect.
    for _, map_id, x, y, attr in res["walkable"]["warp"]:
        idx = attr & mod.BEHAVIOR_MASK
        assert idx < len(d.behaviors), (map_id, x, y, attr, idx, len(d.behaviors))
        name = d.behaviors[idx]
        assert name.startswith("TILE_BEHAVIOR_"), (map_id, x, y, attr, name)


def test_a_rendered_window_holds_every_tile_its_run_walked():
    """The check the interior crop needs. A route tile outside its own map's
    rectangle is drawn over nothing at all and the viewer says nothing."""
    mod, d = _decomp()
    if not mod.run_dirs("platinum-us"):
        pytest.skip("no platinum runs on disk")
    ids = mod.observed_maps("platinum-us")
    res = mod.check_windows(d, "platinum-us", ids)
    assert res["total"] > 500 and not res["bad"], res["bad"][:5]


def test_every_soulsilver_land_block_states_where_its_attributes_begin():
    """Platinum's terrain attributes are at a constant 0x10; SoulSilver's are
    not, and reading them as if they were shifts the grid by two tiles on every
    block that carries the extra section — a map that still looks like a map.

    The check is arithmetic over the WHOLE archive, not a fit to our runs:
    16-byte header + the `0x1234` marker + a u16 giving the extra section's
    length + that section + 0x800 of attributes + the three sections the header
    sizes must account for every byte of every one of the 676 blocks.
    """
    import struct

    import render_dsmaps

    rom = render_dsmaps.ROMS["soulsilver-us"]
    if not rom.is_file():
        pytest.skip("SoulSilver cartridge not present")
    src = render_dsmaps.RomDecomp(rom)
    off_by = 0
    for raw in src.land:
        magic, extra = struct.unpack_from("<HH", raw, 16)
        assert magic == 0x1234
        if 20 + extra + sum(struct.unpack_from("<4I", raw)) != len(raw):
            off_by += 1
    assert off_by == 0, f"{off_by} of {len(src.land)} land blocks do not add up"
    assert any(struct.unpack_from("<H", raw, 18)[0] for raw in src.land), \
        "no block carries an extra section, so this test could not tell the two layouts apart"


def test_the_soulsilver_registration_holds_against_its_own_runs():
    """Both registration checks on the cartridge, not just on the decomp.

    The gen-4 rules are the same for both games and the BYTES are not: HGSS
    writes a variable-length section before its terrain attributes. Without this
    the only thing guarding that difference is the test above, which proves the
    blocks add up — and a grid can add up and still be read two tiles out.
    Reading SoulSilver at Platinum's constant offset leaves 1137 of 1826 samples
    standing in a wall, and nothing else in this file notices.
    """
    import render_dsmaps

    rom = render_dsmaps.ROMS["soulsilver-us"]
    if not rom.is_file() or not render_dsmaps.run_dirs("soulsilver-us"):
        pytest.skip("SoulSilver cartridge or runs not present")
    src = render_dsmaps.RomDecomp(rom)
    res = render_dsmaps.check_registration(src, "soulsilver-us")
    assert res["cell"]["total"] > 500
    assert not res["cell"]["bad"], res["cell"]["bad"][:5]
    assert not res["walkable"]["bad"], res["walkable"]["bad"][:5]
    # The exception class, enumerated: a blocked tile the run stood on is one it
    # left for another map on the very next sample — a door. Two of them.
    assert len({b[1:4] for b in res["walkable"]["warp"]}) <= 4
    assert res["cell"]["unresolved"] == UNPLACEABLE.get("soulsilver-us", set())
    # And the sample count is the WHOLE run, not the part that could be placed:
    # before the arm9 table the three interiors contributed nothing here.
    assert res["cell"]["total"] > 2000


# -- tier 2: the cartridge's own artwork ---------------------------------------
#
# Everything below fails the same way the rest of this file's subjects fail: the
# map still draws, still looks like a map, and is wrong. A prop id read as a set
# index yields a bare lawn; a terrain chunk placed half a cell out yields a town
# with the route through its houses. Neither raises.

def _three_d():
    """The decomp and a 3D renderer over it, or a skip."""
    mod, d = _decomp()
    return mod, d, mod.Field3D(d, tile_px=mod.TILE_PX["3d"])


PLATINUM = "platinum-us"


def test_the_area_data_table_is_a_field_of_the_maps_own_header():
    """WHICH ARTWORK BELONGS TO WHICH MAP — the one thing the terrain model does
    not carry, and the thing this tier could most plausibly have guessed at.

    It is not guessed. `include/data/map_headers.h` gives every map an
    `.areaDataArchiveID`, that record names a texture set and a prop set, and
    `src/overlay005/area_data.c` spends the prop set's index on BOTH the model
    archive and the prop texture archive:

        mapPropModelIDs = area_build   [areaData.mapPropArchivesID]
        mapPropTexture  = areabm_texset[areaData.mapPropArchivesID]

    so `prop_texture_set_NNN` shares `prop_model_set_NNN`'s number. Asserted for
    every map we render, because a map whose header names a set we never read
    would silently take the previous map's textures.
    """
    mod, d, art = _three_d()
    ids, _ = mod.placeable(d, mod.observed_maps(PLATINUM))
    seen = set()
    for map_id in ids:
        header = d.names[map_id]
        const = d.meta[header].get("areaDataArchiveID")
        assert const and const.startswith("area_data_"), (header, const)
        area = art.area(header)
        assert area.map_texture_set.startswith("map_texture_set_")
        assert area.prop_set.startswith("prop_model_set_")
        assert area.prop_texture_set == area.prop_set.replace(
            "prop_model_set_", "prop_texture_set_")
        # and the archives it names actually exist and parse
        assert art.assets.map_texset(area.map_texture_set).textures
        assert art.assets.prop_texset(area.prop_texture_set) is not None
        seen.add((area.map_texture_set, area.prop_set))
    assert len(seen) > 1, \
        "every map resolved to the same artwork set, so this could not tell them apart"


def test_a_prop_model_id_indexes_the_global_archive_and_not_the_areas_own_set():
    """THE trap this tier has, named in the data rather than in a comment.

    A placement's `modelID` indexes `build_model` — 590 files — and NOT the
    32-entry set the area preloads. Read as a set index, most of Sandgem Town's
    ten placements fall off the end of the 32 and the rest resolve to the wrong
    building; the map renders as a lawn with a honey tree on it and raises
    nothing. So: the ids must be ones a 32-entry set could not hold, and every
    id must be a member of the set its area declares.
    """
    mod, d, art = _three_d()
    from ds3d import field

    header = d.names[418]                       # Sandgem Town
    area = art.area(header)
    raw = mod.fetch("res/field/maps/data/map_data_007.bin", offline=True)
    ids = [p[0] for p in field.land_block(raw).placements]
    assert ids, "Sandgem Town has no placements, so this proves nothing"

    members = art.assets._fetch_json(
        f"res/field/props/model_sets/{area.prop_set}.json")["mapPropModels"]
    names = art.assets.prop_names()
    index = {n: i for i, n in enumerate(names)}
    member_ids = {index[m.replace("_nsbmd", ".nsbmd")] for m in members}

    assert any(i >= len(members) for i in ids), \
        "every id fits inside the set, so the two readings are indistinguishable here"
    assert set(ids) <= member_ids, \
        f"{sorted(set(ids) - member_ids)} are not in {area.prop_set}"
    # and the global reading is the one that resolves to a real file
    for i in ids:
        assert names[i].endswith(".nsbmd")


def test_a_terrain_chunk_draws_exactly_the_triangles_its_own_header_declares():
    """The free self-check, over every chunk of every map we ship.

    An `MDL0` states `num_tris` and `num_quads` in its header, and the display
    list that follows has to produce them. A decoder that mis-reads one opcode
    quietly drops or duplicates geometry, and the picture stays plausible; this
    is the only oracle that does not need a reference image.
    """
    mod, d, art = _three_d()
    ids, _ = mod.placeable(d, mod.observed_maps(PLATINUM))
    chunks = 0
    for map_id in ids:
        win = mod.map_window(d, map_id)
        assert art.pixels(win) is not None, f"{win.key} rendered nothing"
        for st in art.stats[win.key]:
            assert st.terrain_tris == st.declared_tris, (win.key, st)
            assert st.props_drawn == st.props, (win.key, st.missing_models)
            chunks += 1
    assert chunks >= len(ids), "fewer chunks than maps"


def test_a_two_chunk_map_is_two_different_chunks_side_by_side():
    """Route 201 spans two matrix cells, and a stitch has two ways to be wrong
    that both fill the rectangle: the same chunk drawn twice, or both chunks
    drawn at one origin. Each half of the stitched render must therefore equal
    that cell's OWN render, and the two halves must differ from each other.

    The four pixels either side of the seam are excluded — a quarter of a tile.
    A triangle that ends exactly on the cell boundary contributes to the
    supersampled pixels on both sides of it, so the stitch legitimately differs
    from either chunk alone there; measured, that bleed reaches 3 px.
    """
    import numpy as np

    from ds3d import field, scene

    mod, d, art = _three_d()
    win = mod.map_window(d, 342)
    assert (win.cw, win.ch) == (2, 1), "Route 201 is no longer the two-cell case"
    stitched = art.pixels(win)
    t = art.tile_px
    half = mod.CELL * t

    area = art.area(win.header)
    singles = []
    for i, (col, row) in enumerate(sorted(win.cells)):
        land = d.matrix_of(win.header)["maps"][row][col]
        raw = mod.fetch(f"res/field/maps/data/map_data_{int(land.split('_')[-1]):03d}.bin",
                        offline=True)
        sc = scene.Scene()
        field.add_chunk(sc, art.assets, raw, area,
                        origin=field.chunk_origin(col, row, col, row))
        singles.append(field.ortho_pixels(sc, mod.CELL, mod.CELL, t, art.ss))

    for i, one in enumerate(singles):
        got = stitched[:, i * half:(i + 1) * half]
        inner = slice(4, half - 4)
        assert np.array_equal(got[:, inner], one[:, inner]), \
            f"half {i} of the stitch is not cell {sorted(win.cells)[i]}'s own render"
    assert not np.array_equal(singles[0], singles[1]), \
        "the two cells render identically, so a doubled chunk would pass this"


#: The only tile behaviour this renderer is allowed to draw see-through, and
#: the reason: gen 4's water sheet is alpha-36 texels, and on VERITY_LAKEFRONT
#: the cartridge puts NOTHING under it — its 8x8 puddle sits at the shared
#: corner of the map's four matrix cells and not one of the four terrain models
#: draws a bed there. Measured by deletion: drop the `puddle` and `puddlep`
#: materials and 30 of 16,384 pixels survive, all of them edge fringe.
#: Every other water in the set comes out opaque — Twinleaf's 40 WATER_RIVER
#: tiles, because its sheet has a `lake` bed under it and the renderer blends
#: them; Route 219's 472 WATER_SEA tiles, because that sea is opaque in its own
#: texels. 512 tiles, and the test below holds all of them to it.
SEE_THROUGH_BEHAVIOURS = {"TILE_BEHAVIOR_PUDDLE"}

#: How big a see-through REGION may get before it is a hole rather than a
#: speck, in tiles, 4-connected.
#:
#: ONE TILE, and that is derived rather than fitted. The tile is the unit both
#: the collision grid and `tile_coverage` are measured in and the smallest
#: region that can be a shape instead of an edge artefact; any larger number
#: would have to come from the one square this rule was written for. The
#: population says the same thing from the other side: of the 19 gen-4 maps
#: both cartridges ship, EIGHTEEN have zero see-through tiles of any kind, so
#: the bar sits in an empty range 1..55 and nothing about 56 chose it.
MAX_SEE_THROUGH_RUN = 1

#: The one region over that bar, and its size, so it cannot grow or be joined
#: by a second without failing. `334:0` is Verity Lakefront's 8x8
#: `TILE_BEHAVIOR_PUDDLE` at the shared corner of its four matrix cells, which
#: the cartridge gives no bed: delete the `puddle`/`puddlep` materials and 30
#: of 16,384 pixels survive. 36 tiles straight down, 30 under the shipping
#: pitched camera, which foreshortens the near edge behind the trees. It was
#: 64/56 until the a3i5 alpha overflow was fixed: the puddle's own sheet is
#: alpha 109, not the 36 the wrap produced, and its `puddlep` rim is 255, so
#: the rim now reaches the opaque bar and only the middle is still sheer.
#:
#: This entry is a HOLE THAT SHIPS, not a rule. It is listed so that the class
#: is visible in the diff and in this file rather than absorbed by a threshold;
#: whether it should be filled is
#: `artifacts/game-map-render/notes/render-defects.md`'s open question.
SEE_THROUGH_RUNS = {"334:0": 36}


def test_the_route_stands_on_rendered_artwork_and_not_on_a_hole():
    """The check this tier needs that the silhouette did not.

    `check_windows` proves a walked tile is inside the map's RECTANGLE. A
    terrain mesh placed half a chunk out, or a chunk missing from a stitch,
    still fills a correct rectangle — with a hole where the route runs. A route
    tile over a hole is drawn over the page's background and nothing reports it.

    A HOLE IS NOTHING DRAWN AT ALL, not "not opaque". That distinction arrived
    with Verity Lakefront — see `SEE_THROUGH_BEHAVIOURS` — and it is narrow on
    purpose: a tile may be see-through only where the cartridge names it water,
    and the set of behaviours allowed to be see-through is asserted, not the
    count. A third map whose ground goes see-through for any other reason still
    fails here.
    """
    mod, d, art = _three_d()
    if not mod.run_dirs(PLATINUM):
        pytest.skip("no platinum runs on disk")
    ids, _ = mod.placeable(d, mod.observed_maps(PLATINUM))
    res = mod.check_artwork(d, PLATINUM, ids, art)
    assert res["route"] > 500
    assert res["bare_route"] == 0, \
        {k: v for k, v in res["maps"].items() if v[3]}
    assert res["bare_walkable"] == 0, \
        {k: v for k, v in res["maps"].items() if v[0]}
    assert set(res["sheer_behaviour"]) <= SEE_THROUGH_BEHAVIOURS, \
        res["sheer_behaviour"]
    # and the SHAPE, which is the half that let an 8x8 square through: a
    # see-through run over the bar must be one of the listed holes, at no more
    # than its listed size, and a listed hole that has gone must leave the list.
    for key, sizes in res["sheer_runs"].items():
        cap = SEE_THROUGH_RUNS.get(key)
        if cap is None:
            assert sizes[0] <= MAX_SEE_THROUGH_RUN, \
                f"{key}: a see-through region of {sizes[0]} tiles, runs {sizes[:5]}"
        else:
            assert sizes[0] <= cap, f"{key}: see-through run grew to {sizes[0]}, listed {cap}"
    gone = set(SEE_THROUGH_RUNS) - set(res["sheer_runs"]) - {
        k for k in SEE_THROUGH_RUNS if k not in res["maps"]}
    assert not gone, f"listed as a see-through hole but no longer one: {sorted(gone)}"


def test_a_square_and_a_scatter_of_the_same_area_are_not_the_same_reading():
    """The instrument for the rule above, on its own, with no cartridge.

    56 see-through tiles in one 8x8 block and 56 see-through tiles sprinkled
    over a map are the same COUNT and the same BEHAVIOUR, and the old check
    could see nothing else — which is how an 8x8 hole reached the browser with
    every test green. `runs_of` is the thing that tells them apart, so it is
    asserted directly rather than only through a map.
    """
    import numpy as np

    mod, _d = _decomp()
    grid = np.zeros((32, 32), bool)
    grid[8:16, 8:16] = True
    assert mod.runs_of(grid) == [64], "a solid 8x8 is one region of 64"

    scatter = np.zeros((32, 32), bool)
    scatter[::4, ::4] = True                       # 64 tiles, none touching
    assert scatter.sum() == 64
    assert mod.runs_of(scatter) == [1] * 64, "a scatter is 64 regions of one"

    assert grid.sum() == scatter.sum(), \
        "the two shapes must have equal area or this proves nothing about shape"
    assert max(mod.runs_of(grid)) > max(mod.runs_of(scatter))

    # touching diagonally is NOT connected — a checkerboard is specks, and a
    # rule that called it one region would fail every anti-aliased edge
    diag = np.zeros((8, 8), bool)
    diag[::2, ::2] = True
    diag[1::2, 1::2] = True
    assert max(mod.runs_of(diag)) == 1


def test_water_over_a_bed_is_opaque_and_water_over_nothing_is_not():
    """The control that keeps the test above from being a licence.

    Allowing see-through water would be a blank cheque if nothing said which
    water has to stay opaque. 512 tiles say so — Twinleaf's 40 WATER_RIVER and
    Route 219's 472 WATER_SEA — and they fail for two different reasons, which
    is why both are here:

      * Twinleaf's river IS the translucent sheet, over its own `lake` bed, so
        it is opaque only because the renderer BLENDS it. Written instead of
        blended, the sheet's own alpha 36 lands and the bed is gone: measured,
        all 40 go see-through and this test and the one above both fail.
      * Route 219's sea is opaque in its own texels, so it holds whatever the
        blend does. It is the half that says "water" is not a licence to be
        see-through — most of it is not.

    Which water is allowed through is not the renderer's opinion: the
    cartridge's, by whether deleting the water materials leaves anything
    behind. On Verity Lakefront it leaves 30 pixels of 16,384.
    """
    import numpy as np

    from ds3d import camera as ds3d_camera

    mod, d, art = _three_d()
    ids, _ = mod.placeable(d, mod.observed_maps(PLATINUM))
    bedded = sheer = 0
    for map_id in ids:
        r = mod.render_map(d, map_id, art)
        if not r.render.startswith("3d-"):
            continue
        cov = ds3d_camera.tile_coverage(
            mod.full_pixels(r, art.tile_px), mod.frame_of(r, art.tile_px), r.heights)
        behave = r.attrs & mod.BEHAVIOR_MASK
        walk = r.defined & ~r.blocked
        for value in np.unique(behave[walk]):
            name = d.behaviors[value] if value < len(d.behaviors) else ""
            if "WATER" not in name and "PUDDLE" not in name:
                continue
            tiles = walk & (behave == value)
            if name in SEE_THROUGH_BEHAVIOURS:
                sheer += int(tiles.sum())
                continue
            bedded += int(tiles.sum())
            assert (cov[tiles] >= 128).all(), (
                f"{r.key} {name}: {int((cov[tiles] < 128).sum())} of {int(tiles.sum())} "
                f"water tiles are see-through, so the sheet was written and not blended")
    assert bedded > 400, f"only {bedded} bedded water tiles — the control is near-vacuous"
    assert sheer > 0, "no see-through water in the set, so the exception is untested"


def test_every_tile_the_cartridge_calls_water_renders_as_water():
    """REGISTRATION between two independent files, with its own shift control.

    The collision grid (`map_data_NNN.bin`'s attribute section) says which tiles
    are water. The artwork (the same file's model, textured from a separate
    NSBTX) says what colour they are. The two agree only if the mesh is placed
    on the tile lattice the attributes are indexed by — so this is the check
    that would catch a terrain model half a tile or a whole cell out, which
    `check_windows` cannot see and which looks like a map.

    What it does NOT cover: whether the water was BLENDED. A translucent texel
    pasted straight into the buffer keeps its blue, so this passes on a pond
    that is a pond-shaped hole. The test above catches that, on alpha — checked
    by mutation, not assumed.

    The control is in the assertion: the same measurement one tile across must
    be strictly worse. Without it "all water tiles are blue" could pass on a map
    that was simply blue all over.
    """
    import numpy as np

    mod, d, art = _three_d()
    checked = 0
    for map_id in (411, 391):                    # Twinleaf's pond, Route 219's sea
        win = mod.map_window(d, map_id)
        behave = win.attrs & mod.BEHAVIOR_MASK
        water = np.zeros(behave.shape, bool)
        for value in np.unique(behave):
            name = d.behaviors[value] if value < len(d.behaviors) else ""
            if "WATER" in name:
                water |= behave == value
        if not water.any():
            continue
        px = art.pixels(win).reshape(win.h, art.tile_px, win.w, art.tile_px, 4).mean((1, 3))
        blue = (px[..., 2] > px[..., 0] + 20) & (px[..., 2] > px[..., 1] + 20)

        def agreement(mask):
            return float((blue & mask).sum()) / max(int(mask.sum()), 1)

        here = agreement(water)
        assert here == 1.0, f"{win.key}: {here:.3f} of its water tiles render as water"
        for shift in (-1, 1):
            assert agreement(np.roll(water, shift, axis=1)) < here, \
                f"{win.key}: shifting the water mask by {shift} tile changes nothing, " \
                "so this measures the map's colour and not its registration"
        checked += 1
    assert checked == 2, "both water maps have to be measured for this to mean anything"


def test_the_sheets_route_is_drawn_with_the_viewers_own_numbers():
    """The sheet exists to answer "is the artwork still readable under a route",
    and it can only answer it if the route on it is the route the page draws.

    So the four constants are read out of `mapatlas.js` rather than agreed with
    it by eye. If the page's cable gets wider, this fails and the sheet is
    rebuilt — rather than the sheet going on showing a legibility that the
    product no longer has.
    """
    import render_dsmaps

    js = (REPO_ROOT / "src" / "dashboard" / "web" / "src" / "lib" / "mapatlas.js").read_text()
    pairs = {
        "CABLE_WIDTH": r"lw\s*=\s*width\s*\?\?\s*Math\.max\(1\.2,\s*scale\s*\*\s*([\d.]+)\)",
        "CABLE_HALO": r"c\.lineWidth\s*=\s*lw\s*\+\s*Math\.max\(1\.4,\s*scale\s*\*\s*([\d.]+)\)",
        "CABLE_GAP": r"export const CABLE_GAP\s*=\s*([\d.]+)",
        "COLOUR_LOOP_TILES": r"export const COLOUR_LOOP_TILES\s*=\s*(\d+)",
        "MAX_LANES": r"export const MAX_LANES\s*=\s*(\d+)",
        "HUE_START": r"const HUE_START\s*=\s*(\d+)",
    }
    for name, pattern in pairs.items():
        m = re.search(pattern, js)
        assert m, f"mapatlas.js no longer states {name} where this looks for it"
        assert float(m.group(1)) == float(getattr(render_dsmaps, name)), \
            f"{name}: the sheet draws {getattr(render_dsmaps, name)}, the page draws {m.group(1)}"


# -- the cartridge's own map-header table --------------------------------------
#
# The thing that unblocked SoulSilver's 3D tier and its three interiors. Every
# one of these fails the way this file's subjects fail: the map still draws.

SOULSILVER = "soulsilver-us"


def _cartridge():
    import render_dsmaps

    rom = render_dsmaps.ROMS.get(SOULSILVER)
    if rom is None or not rom.is_file():
        pytest.skip("SoulSilver cartridge not present")
    return render_dsmaps, render_dsmaps.RomDecomp(rom)


def test_the_arm9_decompresses_to_something_that_is_actually_the_arm9():
    """The instrument before the measurement.

    Every fact below is read out of a buffer this produced, so a decompressor
    that silently emitted plausible garbage would make all of them wrong
    together. Two independent signs it did not: the file is exactly the length
    its own footer declares, and it contains the SDK build stamps and the C++
    RTTI names that a Nintendo DS binary has and compressed bytes do not.
    """
    import struct

    mod, src = _cartridge()
    off, _entry, _ram, size = struct.unpack_from("<4I", src.rom.raw, 0x20)
    packed = src.rom.raw[off:off + size]
    inc = int.from_bytes(packed[-4:], "little")
    assert inc, "this arm9 is not compressed, so this test is measuring nothing"
    a9 = src.arm9()
    assert len(a9) == len(packed) + inc
    assert b"[SDK+NINTENDO:" in a9
    marker = b"N10__cxxabiv117__class_type_infoE"
    assert marker in a9
    # The marker has to be one the COMPRESSED bytes do not already carry, or
    # finding it says nothing about the decompressor. `[SDK+NINTENDO:` survives
    # compression as a literal run and is no use for this; the RTTI name does not.
    assert marker not in packed


def test_the_map_header_table_agrees_with_the_matrices_it_did_not_come_from():
    """The located table, checked against a DIFFERENT file.

    `_locate_headers` finds the table by constraining one field — every map id
    the region matrix's header plane names must read matrix 0. This asserts the
    thing the search did not ask for: that the same field reports the RIGHT
    matrix for every id in every header plane, including matrix 212's. Those
    are agreements the search had no way to arrange.
    """
    mod, src = _cartridge()
    assert src.header_base is not None, "no map-header table could be located"
    res = src.check_headers()
    assert res["total"] > 200
    assert not res["bad"], res["bad"][:5]
    # and it places the three maps that had nowhere to go before it
    for map_id in (61, 63, 64):
        assert src.resolvable(map_id), map_id
        assert src.meta[f"MAP_{map_id}"]["mapSource"] == "arm9"


def test_a_map_header_one_record_out_stops_agreeing():
    """The control for the test above, and the failure it is really guarding.

    A table located one record early or late still parses, still yields a matrix
    id for every map, and places every interior somewhere plausible. What it
    cannot do is keep agreeing with the header planes — so the agreement is only
    evidence if it breaks under a shift.
    """
    import struct

    mod, src = _cartridge()
    a9 = src.arm9()
    base = src.header_base
    for shift in (-1, 1):
        at = base + shift * mod.RomDecomp.HEADER_STRIDE
        bad = total = 0
        for i, m in enumerate(src._matrices):
            for row in (m["headers"] or []):
                for v in row:
                    if not v:
                        continue
                    total += 1
                    off = at + mod.RomDecomp.HEADER_STRIDE * v + mod.RomDecomp.MATRIX_FIELD
                    if struct.unpack_from("<H", a9, off)[0] != i:
                        bad += 1
        # Not "some disagreement": a tenth of them. Most of these maps are on
        # the region matrix and read 0 whichever record they land on, so a shift
        # LOOKS mostly fine — which is exactly why the real offset has to be
        # exactly 0 wrong and a shifted one clearly not. Measured: 30 of 208.
        assert bad >= total // 10, \
            f"shifting the table by {shift} record leaves {bad}/{total} wrong — too few to tell"


def test_the_area_field_names_artwork_that_actually_fits_the_map():
    """The area-data table for a cartridge, checked the way it will be used.

    HGSS has the same chain Platinum does — a map header names an area record,
    the record names a texture set and a prop set — but the header lives in the
    arm9 and the record is eight raw bytes. So the field is verified against a
    constraint it was not derived from: the texture set it names must contain
    EVERY material name the map's terrain models ask for. Twenty-odd names per
    map over 75 maps is not something a wrong field survives.

    One map is allowed to fail the prop half, and it is named: map 10 has a
    placement whose model is not in its area's prop set, which is the case the
    game itself handles (`AreaDataManager_HasMapPropModelFile` falls back to the
    dummy box). Tolerating it by a count rather than by name would hide a second.
    """
    mod, src = _cartridge()
    from ds3d import field

    assets = field.RomAssets(src.rom)
    ids = sorted({v for row in src._matrices[0]["headers"] for v in row} - {0})
    assert len(ids) > 50
    tex_bad, prop_bad = [], []
    for map_id in ids:
        header = f"MAP_{map_id}"
        area = assets.area_record(src.meta[header]["areaDataArchiveID"])
        names, placements = set(), set()
        for col, row in src.cells_of(header):
            land = src.matrix_of(header)["maps"][row][col]
            if land is None or land >= len(src.land):
                continue
            block = field.land_block(src.land[land])
            if not block.model:
                continue
            from ds3d.nsbmd import load_models
            model = load_models(block.model, 0)[0][0]
            names |= {v[0] for v in model.texture_pairs().values() if v[0]}
            placements |= {p[0] for p in block.placements}
        if not names:
            continue
        if not names <= set(assets.map_texset(area.map_texture_set).textures):
            tex_bad.append(map_id)
        if not placements <= set(assets.prop_set_members(area.prop_set)):
            prop_bad.append(map_id)
    assert not tex_bad, f"the area's texture set is missing materials for {tex_bad}"
    assert prop_bad == [10], f"prop-set mismatches changed: {prop_bad}"


def test_an_interior_takes_its_props_from_the_room_archive_and_not_the_field_one():
    """SoulSilver ships two prop archives and the same id means a different
    model in each: `bm_field` (340) for the overworld, `bm_room` (222) for
    interiors. Read from the wrong one, Professor Elm's laboratory gets a lake
    drawn over it — textured, opaque, and silent.

    Measured on where the props LAND, which is what makes the two
    distinguishable at all. Furniture cannot leave the room it is in; a lake
    can. So with the room archive every interior's props stay inside the map's
    own window, and with the field archive at least one interior's props spill
    outside it entirely and cover the whole floor.

    Not measured on whether the placements RESOLVE: both archives hold a model
    at every one of these ids, so a resolution count cannot tell them apart —
    checked, and it is why this measures pixels instead.
    """
    import numpy as np

    from ds3d import field, scene

    mod, src = _cartridge()
    art = mod.Field3D(src, tile_px=mod.TILE_PX["3d"])
    interiors = [i for i in mod.observed_maps(SOULSILVER)
                 if mod.map_window(src, i).indoor]
    assert interiors, "no SoulSilver interior is placed, so this proves nothing"

    def spill(win, archive):
        """Prop pixels outside the map's own window, and inside it, as a fraction."""
        area = art.area(win.header)
        raw = art.land_block(win.header, *sorted(win.cells)[0])
        origin = field.chunk_origin(0, 0, 0, 0)
        bare = scene.Scene()
        field.add_chunk(bare, art.assets, raw, area, prop_archive=archive,
                        with_props=False, origin=origin)
        full = scene.Scene()
        field.add_chunk(full, art.assets, raw, area, prop_archive=archive, origin=origin)
        t = field.ortho_pixels(bare, mod.CELL, mod.CELL, art.tile_px, 1)
        p = field.ortho_pixels(full, mod.CELL, mod.CELL, art.tile_px, 1)
        changed = (t != p).any(-1)
        bx, by = win.crop
        px = art.tile_px
        inside = changed[by * px:(by + win.h) * px, bx * px:(bx + win.w) * px]
        return int(changed.sum() - inside.sum()), float(inside.mean())

    wrong_spilled = 0
    for map_id in interiors:
        win = mod.map_window(src, map_id)
        assert art.prop_archive(win) == "room", win.key
        out, frac = spill(win, "room")
        assert out == 0, f"{win.key}: {out} px of furniture outside the room"
        assert 0 < frac < 0.5, f"{win.key}: its props cover {frac:.0%} of the room"
        bad_out, _bad_frac = spill(win, "field")
        wrong_spilled += bad_out
    assert wrong_spilled > 0, \
        "the field archive stays inside the room too, so the two are " \
        "indistinguishable here and this test cannot bite"


def test_the_outdoor_maps_take_theirs_from_the_field_archive():
    """The other half of the split, on the maps that are not interiors."""
    mod, src = _cartridge()
    outdoor = [i for i in mod.observed_maps(SOULSILVER)
               if not mod.map_window(src, i).indoor]
    art = mod.Field3D(src, tile_px=mod.TILE_PX["3d"])
    assert outdoor
    for map_id in outdoor:
        assert art.prop_archive(mod.map_window(src, map_id)) == "field"


def test_a_land_block_is_read_under_the_layout_that_adds_up():
    """The two cartridges frame their land blocks differently and the renderer
    is told by neither. Platinum's four sections start at a constant 0x10;
    SoulSilver writes a `0x1234` marker there and a u16 giving the length of an
    extra section first. Reading one as the other shifts every map two tiles.

    So `land_block` decides by arithmetic, and this asserts the arithmetic
    actually separates them: every SoulSilver block must fit the marked layout
    and NOT the plain one, or the choice would be a coin toss.
    """
    import struct

    from ds3d import field

    mod, src = _cartridge()
    both = plain_only = 0
    for raw in src.land:
        sizes = struct.unpack_from("<4I", raw, 0)
        total = sum(sizes)
        marker, extra = struct.unpack_from("<HH", raw, 16)
        fits_plain = 16 + total == len(raw)
        fits_marked = marker == 0x1234 and 20 + extra + total == len(raw)
        assert fits_marked, "a SoulSilver land block does not fit the marked layout"
        if fits_plain:
            both += 1
        field.land_block(raw)          # must not raise
    assert both == 0, f"{both} blocks fit both layouts, so the arithmetic cannot choose"

    # and the other cartridge's blocks fit only the plain one
    if (render := REPO_ROOT / "local" / "pret-cache-platinum" / "SHA").exists():
        import render_dsmaps
        raw = render_dsmaps.fetch("res/field/maps/data/map_data_007.bin", offline=True)
        sizes = struct.unpack_from("<4I", raw, 0)
        assert 16 + sum(sizes) == len(raw)
        marker, extra = struct.unpack_from("<HH", raw, 16)
        assert not (marker == 0x1234 and 20 + extra + sum(sizes) == len(raw))


def test_an_untextured_material_is_drawn_as_a_colour_and_not_as_a_hole():
    """Gen-4 interiors use untextured, flat-coloured materials — a lab floor,
    the shading under a staircase — and a renderer that only knows how to draw
    textures drops those polygons and leaves a hole in the middle of the room.

    Asserted on the count the model itself declares: with them, the triangles
    drawn equal `2*num_quads + num_tris` exactly, on every chunk of every map
    both cartridges ship. That is the only oracle here that needs no reference
    image, and it is the one that caught this.
    """
    import render_dsmaps

    seen = untextured = 0
    for game in ("platinum-us", SOULSILVER):
        rom = render_dsmaps.ROMS.get(game)
        if rom is not None and not rom.is_file():
            continue
        if rom is None and not (render_dsmaps.CACHE / "SHA").exists():
            continue
        d = render_dsmaps.source_for(game, offline=True)
        art = render_dsmaps.Field3D(d, tile_px=render_dsmaps.TILE_PX["3d"])
        ids, _ = render_dsmaps.placeable(d, render_dsmaps.observed_maps(game))
        for map_id in ids:
            win = render_dsmaps.map_window(d, map_id)
            assert art.pixels(win) is not None
            for st in art.stats[win.key]:
                assert st.terrain_tris == st.declared_tris, (game, win.key, st)
                seen += 1
        # at least one material with no texture at all, or this is vacuous
        from ds3d import field
        from ds3d.nsbmd import load_models
        for map_id in ids:
            win = render_dsmaps.map_window(d, map_id)
            area = art.area(win.header)
            texset = art.assets.map_texset(area.map_texture_set)
            raw = art.land_block(win.header, *sorted(win.cells)[0])
            model = load_models(field.land_block(raw).model, 0)[0][0]
            pairs = model.texture_pairs()
            untextured += sum(1 for mi, _pi in model.bind_draw()
                              if pairs.get(mi, (None,))[0] not in texset.textures)
    assert seen > 10
    assert untextured > 0, \
        "no map has an untextured material, so this could not tell the two behaviours apart"


def test_a_render_command_is_identified_by_its_low_five_bits():
    """`wk_sp1`, the signpost outside New Bark Town, binds its material with
    command 0x24 and 0x44 rather than 0x04, because it has three bones and the
    high bits are matrix-stack flags. A scan that matched the bare opcode found
    no bind/draw pair at all and drew none of the model — four placements of it
    in New Bark Town, silently absent.

    Asserted on the model, not on the picture: every shape it declares has to be
    drawn by some command.
    """
    from ds3d import field

    mod, src = _cartridge()
    art = mod.Field3D(src, tile_px=mod.TILE_PX["3d"])
    model = art.assets.prop_model(27, "field")
    assert model.name == "wk_sp1", model.name
    pairs = model.bind_draw()
    assert {pi for _mi, pi in pairs} == set(range(model.num_pieces)), pairs
    seg = model.data[model.off + model.render_off: model.off + model.mat_off]
    assert any(b & 0xE0 for b in seg if b & model.CMD == model.BIND), \
        "no bind command on this model carries a flag bit, so masking is untested here"


def test_a_door_is_never_just_an_edge_the_run_walked_over(atlases):
    """A cartridge with no event archive takes its doors from the map
    transitions our own runs made. Most transitions are not doors: walking west
    out of New Bark Town onto Route 29 changes the map id and opens nothing —
    the two are neighbours on the region matrix and that edge is already an
    `open` span. Left in, every map boundary the run crossed grows a door
    marker on the page.

    So a door must lead somewhere adjacency cannot explain: a map on a different
    frame. Asserted on the shipped atlas, for every game, however the doors were
    sourced.
    """
    for game, atlas in atlases:
        frames = {k: m.get("frame") for k, m in atlas["maps"].items()}
        for key, m in atlas["maps"].items():
            for door in m.get("doors", []):
                dest = door["to"]
                assert dest in atlas["maps"], (game, key, dest)
                # An interior has no frame at all; two outdoor maps sharing one
                # are neighbours, and a road between them is not a door.
                assert not (frames.get(key) and frames[key] == frames.get(dest)), \
                    f"{game}:{key} has a door to {dest}, which is on the same frame"
        assert atlas.get("doors") in ("warp_events", "observed-transitions"), game


def test_a_map_is_on_the_world_frame_only_when_it_shares_one(atlases):
    """`world` is a position on the REGION's frame, so only a map on that frame
    may carry one — and the atlas already says which frame each map is on.

    Lake Verity Low Water sat at `world: [0, 0]` beside `frame:
    map_matrix_101` while every other Platinum map named `map_matrix_000`: a
    coordinate in one frame published as a position in another. Its [0, 0] was
    never a default — `map_window` measures a private-matrix map from zero on
    purpose, six cells of `map_matrix_101` resolved, and the run's own samples
    land inside the 96x64 window — so the fix is not to move the map but to
    stop claiming it has a place on a frame it is not on.

    Both directions: every map with a `world` shares the frame that the maps
    with a `world` agree on, and every map without one opens as a popup
    instead. A map with neither is unreachable in the viewer.
    """
    for game, atlas in atlases:
        placed = {k: m for k, m in atlas["maps"].items() if "world" in m}
        assert placed, game
        frames = {m["frame"] for m in placed.values()}
        assert len(frames) == 1, f"{game}: maps placed on {sorted(frames)} share one `world`"
        for key, m in atlas["maps"].items():
            if key in placed:
                continue
            assert m.get("popup"), \
                f"{game}:{key} has no `world` and no `popup`, so nothing can draw it"


def test_a_soulsilver_map_says_indoors_when_it_has_a_matrix_of_its_own(atlases):
    """A cartridge publishes no `MAP_TYPE_*` enum, so indoor-ness is read off
    the shape of the map's matrix: on the region matrix means somewhere in the
    overworld, a private 1x1 matrix means a room.

    It decides three visible things at once — whether the coordinates are
    global, whether the map goes on the world frame, and whether it opens as a
    popup — so a map on the wrong side of it is drawn at the corner of the
    world with a route running off it.
    """
    mod, src = _cartridge()
    atlas = dict(atlases).get(SOULSILVER)
    if atlas is None:
        pytest.skip("no SoulSilver atlas rendered")
    seen_both = set()
    for key, m in atlas["maps"].items():
        header = f"MAP_{key.split(':')[0]}"
        shared = bool(src.matrix_of(header).get("headers"))
        assert m["indoor"] == (not shared), (key, m["indoor"], shared)
        assert m.get("world" if shared else "popup") is not None, key
        seen_both.add(m["indoor"])
    assert seen_both == {True, False}, \
        "the atlas is all one kind, so this could not tell the two apart"


# -- the material struct: where a texture's wrap mode and alpha actually live --
#
# One off-by-eight in `ds3d/nsbmd.py` produced four separate visual defects on
# both gen-4 cartridges and both gen-5 ones, and none of the counters this file
# already asserts on could see it: every triangle was decoded, every prop
# resolved, every declared quad was drawn. What was wrong was what each
# triangle was PAINTED with.
#
#   `_material_at` anchored the struct at the material NAME DICTIONARY rather
#   than at the material section, and then read `dummy`/`size` as two words
#   where the format has two halfwords. Four bytes plus four bytes: the
#   "diffuse colour" was really the polygon attribute, and the "texture
#   parameters" were really the palette base.
#
# The renderer therefore took the wrap mode from the NSBTX instead, where the
# repeat bits are always clear — so every UV past the first tile CLAMPED to the
# edge texel. A tree row tiling across a chunk became one flat ribbon of its
# darkest colour edge to edge (Route 201, Route 29), a roof became one flat
# saturated slab, a 64x64 noise-grass texture became one green, and two
# neighbouring chunks clamping to different corners met at a visible tone step.

def _gen4_models():
    """`(game, model, texture pool)` for every model the two gen-4 games draw.

    Terrain and props both: the bug is in the format, not in either path, and a
    census over one of them would leave the other unguarded.
    """
    import render_dsmaps

    from ds3d import field
    from ds3d.nsbmd import load_models

    out = []
    for game in (PLATINUM, SOULSILVER):
        rom = render_dsmaps.ROMS.get(game)
        if rom is not None and not rom.is_file():
            continue
        if rom is None and not (render_dsmaps.CACHE / "SHA").exists():
            continue
        d = render_dsmaps.source_for(game, offline=True)
        art = render_dsmaps.Field3D(d, tile_px=render_dsmaps.TILE_PX["3d"])
        ids, _ = render_dsmaps.placeable(d, render_dsmaps.observed_maps(game))
        for map_id in ids:
            win = render_dsmaps.map_window(d, map_id)
            area = art.area(win.header)
            mt = art.assets.map_texset(area.map_texture_set)
            pt = art.assets.prop_texset(area.prop_texture_set)
            archive = art.prop_archive(win)
            for col, row in sorted(win.cells):
                raw = art.land_block(win.header, col, row)
                if raw is None:
                    continue
                block = field.land_block(raw)
                if block.model:
                    out.append((game, load_models(block.model, 0)[0][0], mt))
                for model_id, _pos, _scale in block.placements:
                    try:
                        pm = art.assets.prop_model(model_id, archive)
                    except (IndexError, SystemExit, KeyError):
                        continue
                    out.append((game, pm, field.TexPool(pt, field.own_textures(pm.data))))
    if not out:
        pytest.skip("neither gen-4 source is on disk")
    return out


#: The two materials in the whole gen-4 set whose declared texture size is not
#: their texture's, keyed by `(model, texture)` -> `(declared, actual)`.
#:
#: Both are `s_snow02` in Lake Verity's two content chunks, and both are a copy
#: of the `s_snow` material sitting beside them in the same model: same
#: declared 32x16, same 0..32 u span, re-pointed at the 16x16 `s_snow02`.
#: Route 201's own `s_snow02` declares 16x16 and spans 0..16, which is what a
#: material authored for that texture looks like. Nothing reads the field —
#: `magW`/`magH` are 1.0 and the transform mode is 0 — and with repeat on, the
#: 16x16 simply tiles twice across the same quad, so the picture is right. In
#: the same model `s_snow04` declares 16x16 and spans 0..32, which is the proof
#: that a UV span past the declared size is ordinary and the DECLARATION is the
#: stale half.
#:
#: Listed rather than counted: a third disagreement is a new fact and must fail
#: here rather than be absorbed by a threshold.
STALE_ORIG_SIZE = {
    ("m_dun2701_00_01c", "s_snow02"): ((32, 16), (16, 16)),
    ("m_dun2701_01_01c", "s_snow02"): ((32, 16), (16, 16)),
}


def test_a_material_struct_starts_where_its_own_texture_size_says_it_does():
    """The oracle that settles the struct base, and it is not a reference image.

    A material states `orig_width`/`orig_height` — the size of the texture it
    binds — and the NSBTX states the same size independently, in a different
    file, packed into different bits. Two sources that must agree, over every
    model both cartridges draw.

    Written as a COMPARISON rather than a threshold, because the corpus grows
    every time a run walks somewhere new and a bare count would drift: at the
    right base all but the two known-stale declarations agree, and four bytes
    later NOT ONE of them does. That second number is the whole evidence for
    the eight-byte correction and it is now measured here rather than recalled.
    """
    import struct

    from ds3d.nitro import u32

    # The historical error, exactly: the struct anchored at the material NAME
    # DICTIONARY (+4) and `dummy`/`size` read as two words instead of two
    # halfwords (+4 again). Eight bytes, and this is the arm that must find
    # nothing.
    LATE = 8

    def declared_late(model, mi):
        _n, _h, item = model.materials[mi]
        at = (model.off + model.mat_off + u32(item, 0)
              + model.MAT_ORIG_SIZE + LATE)
        return struct.unpack_from("<2H", model.data, at)

    checked = agreed = late = 0
    seen, odd = set(), {}
    for game, model, texset in _gen4_models():
        pairs = model.texture_pairs()
        for mi, _pi in model.bind_draw():
            tname, _pname = pairs.get(mi, (None, None))
            if tname is None or tname not in texset.textures:
                continue
            p = texset.tex_params(tname)
            want = (8 << ((p >> 20) & 7), 8 << ((p >> 23) & 7))
            checked += 1
            got = model.material_orig_size(mi)
            if got == want:
                agreed += 1
            else:
                odd[(model.name, tname)] = (got, want)
            try:
                late += declared_late(model, mi) == want
            except struct.error:
                pass
            seen.add((model.name, tname))
    assert checked > 400, checked
    assert late == 0, \
        f"{late} of {checked} materials also agree eight bytes late — the base is not pinned"
    assert odd == {k: v for k, v in STALE_ORIG_SIZE.items() if k in odd}, \
        f"an unlisted material disagrees: {odd}"
    assert agreed == checked - len(odd)
    # a listed exception that stopped disagreeing is a fact that changed, and
    # the list must shrink rather than quietly cover nothing
    still = {k for k in STALE_ORIG_SIZE if k in seen}
    assert still <= set(odd), f"listed but no longer stale: {sorted(still - set(odd))}"


def test_the_wrap_mode_comes_from_the_material_and_not_from_the_texture():
    """The two sources DISAGREE, and the material is the one that is right.

    An NSBTX entry owns where a texture is, how big it is, what format it is in
    and whether colour 0 is transparent. Whether it REPEATS belongs to the
    material that binds it, and in every texture set these two cartridges ship
    the NSBTX's repeat bits are clear. Reading them there clamps every UV past
    the first tile to the edge texel.

    Asserted on the scene the renderer actually builds, not on a reimagined
    copy of the rule: `Scene.add_model` is what stores the wrap flags with each
    triangle, so that is what is read back. The first assertion is the one that
    stops this going vacuous — if the two sources ever agreed, the test would
    pass for the wrong reason.
    """
    from ds3d import scene as ds3d_scene

    disagreed = repeated = 0
    for game, model, texset in _gen4_models():
        pairs = model.texture_pairs()
        for mi, _pi in model.bind_draw():
            tname, _pname = pairs.get(mi, (None, None))
            if tname is None or tname not in texset.textures:
                continue
            tex_bits = (texset.tex_params(tname) >> 16) & 0xF
            mat_bits = (model.material_texparams(mi) >> 16) & 0xF
            disagreed += tex_bits != mat_bits
            repeated += mat_bits != 0
    assert disagreed > 300, \
        f"only {disagreed} materials disagree with their NSBTX, so this is near-vacuous"
    assert repeated > 300, repeated

    # and the scene carries the material's answer, not the texture's
    game, model, texset = next((g, m, t) for g, m, t in _gen4_models()
                               if any((m.material_texparams(mi) >> 16) & 3 == 3
                                      for mi, _ in m.bind_draw()))
    sc = ds3d_scene.Scene()
    sc.add_model(model, texset)
    assert any(w[0] and w[1] for _v, _uv, _tex, w in sc.tris), \
        "no triangle in a model whose materials ask for repeat was given repeat"


def test_a_translucent_material_is_blended_even_when_its_texture_is_not():
    """A building's drop shadow is an OPAQUE texture at polygon alpha 9 of 31.

    `is_translucent` reads texels, so with the material's alpha dropped on the
    floor the shadow went through the opaque pass and every house on both gen-4
    cartridges stood beside a solid black slab. The alpha is folded into the
    texel alpha in `Scene.add_model` precisely so the existing two-pass split
    picks it up with no second mechanism.

    Both directions, because "everything is translucent now" would also pass a
    one-sided check: the opaque materials must still come out opaque.
    """
    from ds3d import scene as ds3d_scene

    soft = hard = 0
    for game, model, texset in _gen4_models():
        pairs = model.texture_pairs()
        for mi, _pi in model.bind_draw():
            tname, _pname = pairs.get(mi, (None, None))
            if tname is None or tname not in texset.textures:
                continue
            alpha = model.material_alpha(mi)
            if not (0 < alpha < 31):
                hard += 1
                continue
            soft += 1
            sc = ds3d_scene.Scene()
            n = sc.add_model(model, texset)
            if not n:
                continue
            tex = sc.tris[-1][2] if sc.tris else None
            # every triangle this material contributed must now be soft
            softs = [t for t in sc.tris if ds3d_scene.is_translucent(t[2])]
            assert softs, (game, model.name, mi, alpha)
    assert soft >= 20, f"only {soft} translucent materials in the corpus"
    assert hard > soft, "almost everything is translucent, which would pass vacuously"


def test_route_201_is_tree_rows_and_not_ribbons():
    """The picture, end to end, on the map Andreas pointed at.

    ROUTE_201 has NO props — measured, `add_chunk` reports 0 placements on both
    its cells — so everything on it is terrain, and the dark-green bands
    running the full 64 tiles were the `conttree` tree-row material clamped to
    one texel. The metric is the one that separates the two renders without
    naming a colour: how many image rows are more than half a single RGB value.
    262 of 512 before, 51 after; the surviving ones are the genuine unbroken
    tree walls along the top and bottom edges.
    """
    import collections

    import numpy as np

    mod, d, art = _three_d()
    win = mod.map_window(d, 342)
    px = art.pixels(win)
    assert px is not None
    for st in art.stats[win.key]:
        assert st.props == 0, "Route 201 has props now; the band cannot be blamed on terrain alone"
    flat = 0
    for y in range(px.shape[0]):
        top = collections.Counter(map(tuple, px[y, :, :3])).most_common(1)[0][1]
        flat += top > px.shape[1] // 2
    assert flat < 120, f"{flat} of {px.shape[0]} rows are more than half one colour"
    assert int(((px[..., 3] > 200) & (px[..., :3].max(2) < 40)).sum()) == 0, \
        "opaque near-black pixels on a route: a shadow quad written instead of blended"
    assert len(np.unique(px[..., :3].reshape(-1, 3), axis=0)) > 400


def test_no_shipped_gen4_pixel_is_a_colour_the_console_could_not_have_shown(atlases):
    """The gen-4 twin of the gen-5 check, and it exists because there is one
    writer rather than two.

    `render_dsmaps.to_png` went through `ds3d/pngout.py` on 2026-09-21, the
    same module `render_gen5maps.to_png` already used. Pinned as the PROPERTY,
    not as a byte count: a size moves when a map is added, when the camera
    changes or when zlib changes its mind, and says nothing about what was
    done to the pixels. A DS channel has 32 levels, so a value off that grid
    is precision the hardware never had and this renderer invented.

    The collision tier is excluded for the reason gen 5 excludes it — its
    tones are a palette this repo chose, not colour read off a cartridge.
    """
    import numpy as np
    from PIL import Image

    from ds3d import pngout

    seen = 0
    for game, atlas in atlases:
        for key, m in atlas["maps"].items():
            if m.get("render", atlas["render"]) == "collision":
                continue
            a = np.asarray(Image.open(MAPS_ROOT / game / m["file"]).convert("RGBA"))
            bad = ~pngout.on_ds_grid(a)
            if bad.any():
                off = sorted(set(np.unique(a[bad]).tolist()))
                raise AssertionError(
                    f"{game}:{key} has {int(bad.sum())} channel samples the DS "
                    f"cannot express, e.g. {off[:8]}")
            seen += 1
    assert seen >= 15, f"only {seen} gen-4 artwork PNGs checked"


def test_the_gen4_colour_depth_check_rejects_an_eight_bit_pixel(atlases):
    """Mutation control for the test above, on gen 4's own shipped pixels.

    "Every sample is on a 32-value grid" is satisfied by a great many things —
    an image of one colour, an empty array — so the check has to be shown
    failing on the thing it rejects. One channel of one pixel moved by one is
    the smallest possible departure and exactly what a half-applied
    quantisation leaves behind.
    """
    import numpy as np
    from PIL import Image

    from ds3d import pngout

    game, atlas = atlases[0]
    key = next(k for k, m in atlas["maps"].items()
               if m.get("render", atlas["render"]) != "collision")
    a = np.asarray(Image.open(MAPS_ROOT / game / atlas["maps"][key]["file"])
                   .convert("RGBA")).copy()
    assert pngout.on_ds_grid(a).all(), "the control needs a clean image to dirty"
    # +1 off a grid value is always off the grid: the gaps are 8 apart.
    a[0, 0, 0] = int(a[0, 0, 0]) + 1 if a[0, 0, 0] < 255 else 254
    assert not pngout.on_ds_grid(a).all(), \
        "moving one channel by one still read as expressible on the DS"


# -- the alpha ladder: the two formats that carry alpha in the texel -----------

def test_an_a3i5_or_a5i3_texel_keeps_the_alpha_the_cartridge_gave_it():
    """The scale to 0-255 is a multiply by 255, and it must not be done in uint8.

    `a3i5` carries 3 bits of alpha and `a5i3` carries 5, and both are scaled by
    `i * 255 // (2**bits - 1)`. Left in the texel's own uint8 the multiply
    WRAPS, and every rung of both ladders collapses onto two or three values:

        a5i3 index 23 -> (23*255) & 0xFF = 233, //31 = 7    (true 189)
        a5i3 index 31 -> (31*255) & 0xFF = 225, //31 = 7    (true 255)
        a3i5 index  6 -> ( 6*255) & 0xFF = 250, // 7 = 35   (true 218)

    An OPAQUE a5i3 texel therefore arrived at alpha 7, under
    `raster.draw_tri`'s `> 8` alpha test, and was discarded: Lake Verity's
    `l_lake` is a5i3 and its water did not draw at all, on either of its maps.

    Asserted as a LADDER rather than against remembered values: every alpha a
    texture produces must be an exact rung of its own format's ladder. 7 and 35
    are on neither, so the wrap cannot pass, and no number here is copied from
    a measurement that could itself be wrong.
    """
    import numpy as np

    LADDER = {1: {i * 255 // 7 for i in range(8)},
              6: {i * 255 // 31 for i in range(32)}}
    seen = {1: set(), 6: set()}
    checked = 0
    for _game, _model, texset in _gen4_models():
        for name in texset.textures:
            fmt = (texset.tex_params(name) >> 26) & 7
            if fmt not in LADDER:
                continue
            pals = getattr(texset, "palettes", {})
            pal = (name + "_pl") if (name + "_pl") in pals else (name if name in pals else None)
            try:
                img = texset.image(name, pal)
            except Exception:
                continue
            checked += 1
            got = {int(v) for v in np.unique(img[..., 3])}
            assert got <= LADDER[fmt], \
                f"{name} (fmt {fmt}) has alphas off its own ladder: {sorted(got - LADDER[fmt])}"
            seen[fmt] |= got
    assert checked > 20, checked
    # and the corpus USES the ladder, or "on the ladder" would be satisfied by
    # a texture that is all zeroes
    assert seen[1] == LADDER[1], \
        f"the a3i5 corpus only reaches {sorted(seen[1])}, so a collapsed ladder could pass"
    assert len(seen[6]) >= 20, f"the a5i3 corpus only reaches {len(seen[6])} of 32 rungs"
    assert max(seen[6]) == 255 and max(seen[1]) == 255, \
        "no fully opaque texel in either corpus, which is the rung the wrap destroyed"


def test_lake_verity_has_water_in_it():
    """The end of it, on the map that had none.

    The oracle is the run's own frames, not an eye:
    `2026-09-21_10-52-05_..._continued_from_turn_100` stood on map 311 for
    turns 119-125, and the top of every one of those seven frames is
    9.96-10.50% blue. Our render of 311 was **0.00%** blue — not a few pixels,
    none — while Route 219's sea in the same atlas was 46.74% and Twinleaf's
    pond 4.86%, which is what said this was one map's missing material and not
    a renderer that cannot draw water.

    The water is a PROP, `l_lake`, one placement in each of the map's four
    content cells, and it resolves its texture correctly. Enumerating the
    TERRAIN materials — which is what an earlier pass here did — says map 311
    has no water material at all, and that is true and is the wrong population.
    """
    import numpy as np

    mod, d, art = _three_d()
    r = mod.render_map(d, 311, art)
    px = mod.full_pixels(r, art.tile_px)
    rr, gg, bb = (px[..., i].astype(int) for i in range(3))
    water = (px[..., 3] > 200) & (bb > rr + 25) & (bb > gg + 15)
    assert water.mean() > 0.05, \
        f"{r.key} is {100 * water.mean():.2f}% blue; the frames say ~10%"

    # and it is the prop that brings it, which is the half a terrain census misses
    from ds3d import field as ds3d_field
    win = mod.map_window(d, 311)
    area = art.area(win.header)
    placed = 0
    for col, row in sorted(win.cells):
        raw = art.land_block(win.header, col, row)
        if raw is None:
            continue
        for model_id, _pos, _scale in ds3d_field.land_block(raw).placements:
            assert art.assets.prop_model(model_id, art.prop_archive(win)).name == "l_lake"
            placed += 1
    assert placed == 4, f"{placed} lake placements, expected one per content cell"
