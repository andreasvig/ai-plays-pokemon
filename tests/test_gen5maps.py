"""The Gen 5 map atlases (`scripts/render_gen5maps.py`).

Black and Black 2 have no decomp, so unlike every other game in the set nothing
hands this renderer the map matrix, the map headers or the collision grids —
they were recovered from the cartridge, and these tests are what stands between
"recovered" and "guessed". They guard the four ways this can be wrong while
looking right, all of which are silent in the browser:

  1. THE KEY ON THE WIRE. An observed graph spells a Gen 5 map "389"; a route
     spells it "389:0". An atlas keyed the first way loads, matches no route,
     and reports nothing.
  2. REGISTRATION. A map placed in the wrong matrix cell renders a real picture
     in the wrong place, which is worse than no picture at all. Checked twice:
     against our runs' own samples, and against the cartridge's own per-zone
     spawn coordinates, which are 40 and 53 independent facts the renderer did
     not derive itself.
  3. THE WINDOW. A route tile outside its map's rectangle is drawn over nothing.
  4. THE ATLAS AGAINST THE PIXELS. A renderer that dies before writing leaves
     the PREVIOUS PNGs beside a new index.json, so the atlas can describe a map
     that is not the one on disk. Every entry's declared size is checked against
     the actual image.
  5. THE ARTWORK UNDER THE ROUTE. The textured tier can fail in a way the
     silhouette cannot: a chunk left out of a multi-cell stitch still fills a
     correct rectangle, with a hole where the route runs. Nothing raises.

Anything needing the cartridge skips without it; everything reading the
committed atlas always runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

MAPS_ROOT = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"
OBSERVED = REPO_ROOT / "artifacts" / "game-map-render" / "observed"
GEN5_GAMES = ("black-us", "black2-us")


def _atlases() -> list[tuple[str, dict]]:
    out = []
    for game in GEN5_GAMES:
        path = MAPS_ROOT / game / "index.json"
        if path.is_file():
            out.append((game, json.loads(path.read_text())))
    return out


def _observed(game: str) -> dict:
    return json.loads((OBSERVED / f"{game}-observed.json").read_text())


def _samples(game: str) -> list[tuple[int, int, int]]:
    return [tuple(int(v) for v in n.split("|")) for n in _observed(game)["nodes"]]


@pytest.fixture(scope="module")
def atlases() -> list[tuple[str, dict]]:
    got = _atlases()
    if not got:
        pytest.skip("no Gen 5 atlas rendered yet")
    return got


@pytest.fixture(scope="module")
def roms():
    render_gen5maps = pytest.importorskip("render_gen5maps")
    out = {}
    for game in GEN5_GAMES:
        if (REPO_ROOT / render_gen5maps.GAMES[game]).exists():
            out[game] = render_gen5maps.Gen5Rom(game)
    if not out:
        pytest.skip("no Gen 5 cartridge present")
    return out


# -- 1. the key on the wire ----------------------------------------------------

def test_every_gen5_atlas_key_is_the_route_wire_encoding(atlases):
    """`"389:0"`, not `"389"` — `src/app/route.py:_sample_tile` pads a single-id
    game's key to two slots, and the atlas is keyed on the padded string."""
    for game, atlas in atlases:
        assert atlas["key_shape"] == "id"
        for key in atlas["maps"]:
            head, _, tail = key.partition(":")
            assert head.isdigit() and tail == "0", f"{game}: {key!r} is not <id>:0"


def test_the_atlas_covers_exactly_the_maps_our_runs_stood_on(atlases):
    """Subset would be the easy migration and the wrong one: a map the runs name
    and the atlas lacks is a hole the viewer draws nothing into."""
    for game, atlas in atlases:
        want = {f"{m}:0" for m, _x, _y in _samples(game)}
        assert set(atlas["maps"]) == want, game


# -- 2. registration -----------------------------------------------------------

def test_every_sample_resolves_to_the_map_the_run_recorded(roms):
    """The whole point. A global tile is in world-matrix cell (x>>5, y>>5), and
    the zone that cell names must be the zone the run said it was on."""
    for game, rom in roms.items():
        bad, outdoor = [], 0
        for z, x, y in _samples(game):
            if rom.outdoor(z):
                outdoor += 1
                if rom.zone_at(x >> 5, y >> 5) != z:
                    bad.append((z, x, y))
            else:
                m = rom.matrix_for(z)
                if not ((x >> 5) < m["w"] and (y >> 5) < m["h"]):
                    bad.append((z, x, y))
        assert not bad, f"{game}: {len(bad)} samples land on another map: {bad[:5]}"
        # Without this the check is one-sided and a whole class of mutation gets
        # through for free: anything that makes `outdoor()` answer False for
        # every zone — reading the wrong matrix plane does exactly that — sends
        # all of them down the interior branch, which only bounds-checks and
        # cannot fail. Measured: 375 of Black's 433 samples are outdoor and 237
        # of Black 2's 256, so a collapse to zero is not a close call.
        assert outdoor > len(_samples(game)) // 2, (
            f"{game}: only {outdoor} of {len(_samples(game))} samples took the "
            f"world-matrix branch, so this check barely ran")


def test_registration_check_rejects_a_shifted_sample(roms):
    """The mutation control for the test above, in the test file.

    A registration check is only worth its passing count if it can fail, and the
    way it would silently not fail is by resolving every cell to whatever the
    sample claims. Shifting one cell right must change the answer for at least
    one real sample — if it does not, the check is reading a constant.
    """
    for game, rom in roms.items():
        moved = sum(1 for z, x, y in _samples(game)
                    if rom.outdoor(z) and rom.zone_at((x >> 5) + 1, y >> 5) != z)
        assert moved, f"{game}: shifting every sample one cell east changed nothing"


def test_every_world_zone_spawns_inside_a_cell_that_names_it(roms):
    """The cartridge checking itself, independently of our runs.

    Our two runs between them stood in eight distinct world cells; the zone
    header table carries a spawn position for every zone in the game, and each
    one must land in a cell the world matrix gives to that same zone. This is
    the check that would catch a matrix whose width happened to fit our handful
    of samples and nothing else.
    """
    for game, rom in roms.items():
        bad = []
        for z in sorted(rom.world_zones):
            x, y = rom.spawn(z)
            if rom.zone_at(x >> 5, y >> 5) != z:
                bad.append((z, x, y))
        assert not bad, f"{game}: {len(bad)} zones spawn outside themselves: {bad[:5]}"
        assert len(rom.world_zones) >= 40, f"{game}: only {len(rom.world_zones)} world zones"


def test_every_chunk_model_is_named_for_the_cell_we_put_it_in(roms):
    """The cartridge's own label, and the strongest evidence here.

    A world chunk's terrain model is named `map<col>_<row>` — Nuvema Town's is
    `map24_23`. Nothing in the renderer derived those numbers: they are a string
    written by the people who built the game, in a part of the file (the model
    header) that has nothing to do with the matrix this is checking. If the
    matrix decode were off by a column, every one of these would disagree.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    for game, rom in roms.items():
        got = render_gen5maps.check_chunk_names(rom)
        assert not got["mismatch"], f"{game}: {got['bad'][:5]}"
        assert got["named"] >= 100, (
            f"{game}: only {got['named']} chunks carried a positional name, so "
            f"this check barely ran")


def test_the_chunk_name_check_rejects_a_shifted_matrix(roms):
    """Mutation control for the test above: reading the matrix one column over
    must break the agreement, or the names are not really being compared."""
    for game, rom in roms.items():
        m = rom.world
        agree = 0
        for i, z in enumerate(m["zone"]):
            if z == 0xFFFFFFFF or i + 1 >= len(m["land"]):
                continue
            nxt = m["land"][i + 1]
            if nxt >= len(rom.chunks):                # the next cell is empty
                continue
            col, row = i % m["w"], i // m["w"]
            b = rom.chunks[nxt]                       # the NEXT cell's chunk
            offs = __import__("struct").unpack_from("<4I", b, 4)
            if b[offs[0]:offs[0] + 4] != b"BMD0":
                continue
            from ds3d import nsbmd
            try:
                models, _ = nsbmd.load_models(b[offs[0]:offs[1]])
            except Exception:
                continue
            if models[0].name == f"map{col}_{row}":
                agree += 1
        assert agree == 0, (
            f"{game}: {agree} chunks still matched after shifting one cell, so "
            f"the name check is not keyed on position")


# -- 3. the window -------------------------------------------------------------

def test_every_walked_tile_falls_inside_the_window_the_atlas_ships(atlases):
    """Read off the COMMITTED atlas, not recomputed: this is the check that sees
    an interior cropped to the wrong box, and it needs no cartridge."""
    for game, atlas in atlases:
        tile_px = atlas["tile_px"]
        assert tile_px > 0
        bad = []
        for z, x, y in _samples(game):
            m = atlas["maps"][f"{z}:0"]
            ox, oy = m.get("origin", [0, 0])
            if not (ox <= x < ox + m["width"] and oy <= y < oy + m["height"]):
                bad.append((z, x, y, ox, oy, m["width"], m["height"]))
        assert not bad, f"{game}: {len(bad)} walked tiles outside their map: {bad[:5]}"


def test_the_window_check_rejects_a_map_moved_one_cell(atlases):
    """Mutation control for the test above. Moving a map 32 tiles must put at
    least one of its own walked tiles outside it; a map whose window is so much
    bigger than its route that nothing falls out is a check that cannot bite."""
    for game, atlas in atlases:
        escaped = 0
        for z, x, y in _samples(game):
            m = atlas["maps"][f"{z}:0"]
            ox, oy = m.get("origin", [0, 0])
            ox += 32
            if not (ox <= x < ox + m["width"] and oy <= y < oy + m["height"]):
                escaped += 1
        assert escaped, f"{game}: moving every map 32 tiles east lost no route tile"


# -- 4. the atlas against the pixels -------------------------------------------

def test_every_entry_matches_the_image_actually_on_disk(atlases):
    """A renderer that exits before writing leaves the previous PNGs in place,
    and an atlas that describes them is a lie no consumer can detect.

    A map's PNG is NOT `width * tile_px` square. It was until the camera was
    pitched, and asserting that was only ever right by coincidence: under a
    pitch the two axes have different pixel scales, and the picture carries a
    margin around the map's own ground rectangle for the roofs and canopies
    the camera lifts off their tiles. Both numbers are published — `matrix[0]`
    and `matrix[3]` are the scales, per-map `png_pad` is the margin — so this
    reconstructs the size from what the atlas SAYS and compares it to the
    pixels, which is the claim a consumer actually depends on.

    Deliberately the viewer's arithmetic and not the renderer's: `mapatlas.js`
    reads `matrix[0]` and `matrix[3]` and never `tile_px` twice, so computing
    it any other way here would check a number nothing loads.
    """
    for game, atlas in atlases:
        sx, sy = atlas["camera"]["matrix"][0], atlas["camera"]["matrix"][3]
        for key, m in atlas["maps"].items():
            png = MAPS_ROOT / game / m["file"]
            assert png.is_file(), f"{game}:{key} names a missing {m['file']}"
            left, top, right, bottom = m.get("png_pad", [0, 0, 0, 0])
            want = (int(round(m["width"] * sx)) + left + right,
                    int(round(m["height"] * sy)) + top + bottom)
            with Image.open(png) as im:
                assert im.size == want, (
                    f"{game}:{key} claims {m['width']}x{m['height']} tiles at "
                    f"{sx}x{sy} px/tile plus pad {m.get('png_pad')} = {want}, "
                    f"but the PNG is {im.size}")
            assert png.stat().st_size == m["bytes"], f"{game}:{key} byte count is stale"


def test_the_png_size_check_would_notice_a_map_rendered_at_the_wrong_scale(atlases):
    """Mutation control for the test above, and it needs one for a specific
    reason: the test reads BOTH sides of its comparison out of the same build,
    so the way it silently stops working is by becoming an identity.

    Two mutations, because the pitch added two independent terms. Dropping the
    pad must break it, and reading the vertical scale off `tile_px` — the
    square-PNG assumption the pitch invalidated, and the exact thing this test
    used to assert — must break it too.
    """
    for game, atlas in atlases:
        sy = atlas["camera"]["matrix"][3]
        no_pad = flat_scale = 0
        for m in atlas["maps"].values():
            left, top, right, bottom = m.get("png_pad", [0, 0, 0, 0])
            if left or top or right or bottom:
                no_pad += 1
            if int(round(m["height"] * sy)) != m["height"] * atlas["tile_px"]:
                flat_scale += 1
        assert no_pad, f"{game}: no map declares a pad, so dropping it changes nothing"
        assert flat_scale, (
            f"{game}: the vertical scale is still {atlas['tile_px']} px/tile for "
            f"every map, so the square-PNG assumption would still pass")


def test_the_png_frame_is_declared_and_outdoor_maps_carry_their_origin(atlases):
    """Gen 5 outdoor coordinates are global, so the PNG cannot be in route-tile
    space without padding Nuvema Town out to tile (0, 0). It is map-local, and
    `png_origin` is what `RouteMap.svelte` subtracts from the source rect.

    The render kind is checked against the renderer's OWN table rather than
    against a list written here. That is not tidiness: an allow-list answers
    "is this a kind somebody once thought of", and the question worth asking
    is "is this the kind THIS renderer would emit for the camera the atlas
    declares". Those came apart the day the camera was pitched — a hard-coded
    `("3d-ortho", "collision")` passed a stale atlas that still said
    `3d-ortho` while every map in it was drawn straight down by a renderer
    that no longer does that, and adding `"3d-pitched"` to the tuple would
    have kept passing it. Keyed on the pair, a `render` that disagrees with
    `camera.kind` cannot pass at all, which is the staleness this section is
    for.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    from ds3d import camera as ds3d_camera

    kinds = render_gen5maps.RENDER_KIND_FOR
    for game, atlas in atlases:
        assert atlas["png_frame"] == "map-local", game
        tiers = {t for (t, _c), k in kinds.items() if k == atlas["render"]}
        assert len(tiers) == 1, f"{game}: {atlas['render']!r} is not one tier's output"
        tier = tiers.pop()
        cam = atlas["camera"]
        assert kinds[(tier, cam["kind"])] == atlas["render"], (
            f"{game}: an atlas that says {atlas['render']!r} under a "
            f"{cam['kind']!r} camera is one this renderer would not write — it "
            f"would write {kinds[(tier, cam['kind'])]!r}")
        # `tile_px` is per tier and the viewer takes it from the atlas, never
        # from the route: 16 is the DS's own world units per tile, so the 3D
        # tier is one pixel per world unit; the silhouette has no sub-tile
        # detail and 4 loses nothing.
        assert atlas["tile_px"] == render_gen5maps.TILE_PX[tier], game
        # And the projection itself, not just its name: the viewer places every
        # tile with `matrix`, so a published matrix that is not the one this
        # camera produces puts the route off the artwork.
        assert cam == ds3d_camera.atlas_camera(
            ds3d_camera.camera_for(cam["kind"], atlas["tile_px"])), game
        for key, m in atlas["maps"].items():
            if m.get("origin", [0, 0]) != [0, 0]:
                assert m.get("png_origin") == m["origin"], f"{game}:{key}"


# -- 5. walkability ------------------------------------------------------------

def test_every_walked_tile_is_passable_except_warp_tiles(roms):
    """The collision grid's own check.

    A doorway is flagged impassable because the game warps you off it before you
    could stand there, and our sampler catches the frame where you are on it —
    so a flagged tile is only forgiven when the observed graph independently
    calls it a warp endpoint. Anything else is the collision decode being wrong.
    """
    for game, rom in roms.items():
        warps = set()
        for a, b, _n in _observed(game)["warps"]:
            warps.add(a)
            warps.add(b)
        bad = []
        for z, x, y in _samples(game):
            m = rom.matrix_for(z)
            if not ((x >> 5) < m["w"] and (y >> 5) < m["h"]):
                continue
            g = rom.cell_grid(z, x >> 5, y >> 5)
            if g is None:
                continue
            if g[y & 31, x & 31, 3] & 1 and f"{z}|{x}|{y}" not in warps:
                bad.append((z, x, y))
        assert not bad, f"{game}: {len(bad)} walked tiles are walls: {bad[:5]}"


def test_the_walkability_flag_is_not_constant(roms):
    """Mutation control for the test above, and for the trap it sits next to: a
    detector that never fires passes a one-sided check for free. Bit 0 must be
    set on a real share of each game's tiles, or "passable" means nothing."""
    for game, rom in roms.items():
        g = rom.cell_grid(*_first_outdoor(rom, game))
        blocked = int((g[..., 3] & 1).sum())
        assert 0 < blocked < g[..., 3].size, (
            f"{game}: the impassable flag is constant over a whole chunk "
            f"({blocked} of {g[..., 3].size})")


def _first_outdoor(rom, game):
    for z, x, y in _samples(game):
        if rom.outdoor(z):
            return z, x >> 5, y >> 5
    pytest.skip(f"{game} has no outdoor sample")


# -- 6. the textured tier ------------------------------------------------------

def test_every_route_tile_lands_on_real_geometry(roms):
    """The failure only the 3D tier can have, and the reason `check_artwork`
    measures opacity rather than presence: a chunk missing from a multi-cell
    stitch leaves a hole the route runs through, and the page just shows its own
    background there."""
    render_gen5maps = pytest.importorskip("render_gen5maps")
    for game, rom in roms.items():
        got = render_gen5maps.check_artwork(rom)
        assert got["maps"], f"{game}: no map rendered in 3D at all"
        assert not got["bare_route"], f"{game}: {got['bare'][:5]}"
        assert got["route"] > 100, f"{game}: only {got['route']} route tiles checked"


def test_the_geometry_check_would_see_a_missing_chunk(roms):
    """Mutation control for the test above.

    Blanking the coverage test's own subject must fail it. This is the control
    the bare-route check needs because its healthy answer is zero, and a check
    that reports zero because it is looking at nothing reports the same zero.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    import numpy as np
    for game, rom in roms.items():
        win = render_gen5maps.map_window(rom, render_gen5maps.map_ids(game)[0])
        art = render_gen5maps.Field3D(rom, tile_px=16)
        px = art.pixels(win)
        assert px is not None
        holed = px.copy()
        holed[..., 3] = 0
        cov = holed[..., 3].reshape(win.h, 16, win.w, 16).mean((1, 3)) >= 128
        assert not cov.any(), (
            f"{game}: a fully transparent render still counted as covered, so "
            f"the coverage test cannot see a missing chunk")


# The placements no prop archive of their own map can answer, by name. Two, and
# they arrived with Black 2's five newly-walked maps rather than with any change
# to the renderer. Pinned as an identity rather than as a count of zero, because
# zero is no longer true and a count alone cannot tell the two failures apart:
# `prop_area` picking the wrong archive would strand whole maps' worth of
# furniture, and that must still fail here.
#
# `(map, prop id, the model the id means, the area that holds it)`.
KNOWN_PROP_GAPS = {
    "black-us": set(),
    "black2-us": {(437, 442, "c12_cloud01", 52),
                  (437, 443, "c12_sky", 52)},
}


def test_every_placement_resolves_to_a_prop_model(roms):
    """One number out of the zone header picks both the texture set and the
    prop archive. If that arithmetic were wrong, placements would resolve to
    nothing (or, worse, to another area's furniture).

    Black 2's map 437 carries two placements its own archive cannot answer:
    they name area 52's `c12_sky` and `c12_cloud01`, the backdrop of the city
    on map 427 next door. The arithmetic is not what is wrong — 439 and 446
    have the same zone header value, resolve to the same area 53, and resolve
    every placement they have (that is the next test) — the chunk simply
    references the area next door. The renderer says so per placement rather
    than counting, so the day this set changes it is readable.

    This bites in both directions. A new unresolved placement fails it, and so
    does one of these two starting to resolve — which is what would happen if
    somebody "fixed" the lookup by searching every area. A prop id is global,
    so that search succeeds almost everywhere no matter how wrong `prop_area`
    is, and it would take this whole check down with it.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    for game, rom in roms.items():
        got = render_gen5maps.check_props(rom)
        assert got["resolved"] > 40, f"{game}: only {got['resolved']} placements"
        gaps = {(u["map"], u["prop_id"], u["model"], u["found_in_area"])
                for u in got["unresolved"]}
        assert gaps == KNOWN_PROP_GAPS[game], (
            f"{game}: the unresolved placements are not the known set.\n"
            f"  got  {sorted(gaps)}\n  want {sorted(KNOWN_PROP_GAPS[game])}")


def test_a_map_that_shares_an_areas_props_resolves_all_of_them(roms):
    """The guard that stops the pin above from hiding a broken `prop_area`.

    Forgiving two named placements is only safe while the reason for them is
    "this chunk points at the area next door" and not "this map is reading the
    wrong archive". Those look identical from a count. They come apart on the
    maps that SHARE an archive: Black 2's 437, 439 and 446 all carry zone
    header value 214 and so all resolve to prop area 53, and if that number
    were wrong they would all be full of holes together. So every area with a
    gap in it must also be an area some OTHER map empties completely.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    for game, rom in roms.items():
        gaps = KNOWN_PROP_GAPS[game]
        if not gaps:
            continue
        got = render_gen5maps.check_props(rom)
        holed = {u["map"] for u in got["unresolved"]}
        areas = {rom.prop_area(m)[1] for m in holed}
        for area in areas:
            siblings = [i for i in render_gen5maps.map_ids(game)
                        if i not in holed and rom.prop_area(i)[1] == area]
            assert siblings, (
                f"{game}: area {area} has an unresolved placement and no other "
                f"map uses it, so nothing independently confirms the arithmetic")
            for i in siblings:
                win = render_gen5maps.map_window(rom, i)
                m = rom.matrix_for(i)
                lut = rom.prop_set(i)[0]
                for col, row in sorted(win.cells):
                    land = m["land"][row * m["w"] + col]
                    if land >= len(rom.chunks):
                        continue
                    for *_xyz, pid in rom.placements(land):
                        assert pid in lut, (
                            f"{game}: map {i} shares area {area} with a holed map "
                            f"and is holed too ({pid}) — this is the arithmetic, "
                            f"not a cross-area reference")


def test_the_unresolved_props_would_not_have_been_visible_anyway(roms):
    """What licenses skipping them: they change not one shipped pixel.

    Backdrop geometry, not furniture. `c12_sky` is a thousand world units
    across and sits between 29 and 370 units up; the tallest thing map 437
    actually draws is 176 and stands on the ground. Both of them project
    clear of the map's own rectangle, so resolving them — which is possible,
    the models are right there in area 52 — adds fifty-two triangles and
    produces a byte-identical PNG.

    Measured rather than argued, and measured through the real renderer
    rather than by reprojecting the bounding box here, because a second
    implementation of the projection would be checking itself. The donor
    archive is reached as map 427's own prop set, which IS area 52.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    import numpy as np
    from ds3d import camera as ds3d_camera

    for game, rom in roms.items():
        gaps = KNOWN_PROP_GAPS[game]
        if not gaps:
            continue
        donor_of = {rom.prop_area(i)[1]: i for i in render_gen5maps.map_ids(game)}
        by_map: dict[int, dict] = {}
        for map_id, pid, _name, area in gaps:
            donor = donor_of.get(area)
            assert donor is not None, f"{game}: no shipped map uses area {area}"
            by_map.setdefault(map_id, {})[pid] = rom.prop_set(donor)[0][pid]

        real = type(rom).prop_set
        cam = ds3d_camera.camera_for(render_gen5maps.DEFAULT_CAMERA, 16)
        for map_id, extra in by_map.items():
            win = render_gen5maps.map_window(rom, map_id)
            before = render_gen5maps.Field3D(rom, tile_px=16, cam=cam).pixels(win)

            def patched(self, zone, _map_id=map_id, _extra=extra):
                got = real(self, zone)
                if got is None or zone != _map_id:
                    return got
                return ({**got[0], **_extra}, got[1])

            try:
                type(rom).prop_set = patched
                art = render_gen5maps.Field3D(rom, tile_px=16, cam=cam)
                after = art.pixels(win)
                drawn = art.stats[win.key]["missing"]
            finally:
                type(rom).prop_set = real

            assert drawn == 0, f"{game}: map {map_id} still unresolved after patching"
            assert before.shape == after.shape, (
                f"{game}: map {map_id} changes SIZE when the backdrop resolves "
                f"{before.shape} -> {after.shape} — it is inside the picture")
            differing = int((np.abs(before.astype(int) - after.astype(int))
                             .sum(2) > 0).sum())
            assert differing == 0, (
                f"{game}: map {map_id} changes {differing} pixels when the two "
                f"backdrop props are resolved, so they are NOT invisible and "
                f"skipping them is dropping artwork")


def test_props_stand_on_the_walls_the_collision_grid_declares(roms):
    """The oracle that fixed the placement z axis, kept as a test.

    A building is a wall, and the collision grid says where the walls are —
    recovered from a different section of a different file than the placement
    list, so this is evidence and not a restatement. The CONTROL is the same
    measurement with z as stored: it must be clearly worse, or the axis
    convention is not actually being tested.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    for game, rom in roms.items():
        good = render_gen5maps.check_props(rom)
        control = render_gen5maps.check_props(rom, flip_z=True)
        assert good["total"] > 100, f"{game}: only {good['total']} prop tiles drawn"
        assert good["pct"] > 75, f"{game}: only {good['pct']:.1f}% of prop pixels on a wall"
        assert good["pct"] > control["pct"] + 15, (
            f"{game}: z negated {good['pct']:.1f}% vs as stored {control['pct']:.1f}% "
            f"— the oracle does not separate the two conventions")


# -- 7. the wrap mode, which is shared with gen 4 ------------------------------

def test_a_gen5_terrain_texture_is_tiled_and_not_smeared(roms):
    """The same `ds3d/nsbmd.py` off-by-eight that banded Platinum's routes.

    Gen 5 grass is a 64x64 texture of sixteen greens laid over the terrain at
    one texel per world unit, so a 32-tile chunk repeats it eight times each
    way. With the wrap bits read from the NSBTX (where they are always clear)
    every UV outside the first tile clamped to an edge texel and a whole chunk
    came out as ONE flat green — and because neighbouring chunks clamp to
    different corners, the 32-tile matrix grid showed as tone steps across the
    map.

    Measured on the scene rather than on a colour: the grass material must ask
    for repeat in both directions, the renderer must give it repeat, and the
    pixels the renderer then produces over a patch of open ground must carry
    more than one colour.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    import numpy as np
    from ds3d import scene as ds3d_scene

    seen = 0
    for game, rom in roms.items():
        zone = render_gen5maps.map_ids(game)[0]
        win = render_gen5maps.map_window(rom, zone)
        sc, st = render_gen5maps.Field3D(rom, tile_px=16).scene(win)
        assert sc is not None and st["terrain"], (game, st)
        wraps = {w[:2] for _v, _uv, _tex, w in sc.tris}
        assert (1, 1) in wraps, \
            f"{game}: not one triangle repeats in both directions"
        # and the material is where that answer came from: the NSBTX disagrees
        mapts = rom.texture_set(zone, 0)
        m = rom.matrix_for(zone)
        col, row = sorted(win.cells)[0]
        model = rom.chunk_model(m["land"][row * m["w"] + col])
        pairs = model.texture_pairs()
        disagree = sum(
            1 for mi, _pi in model.bind_draw()
            if (name := pairs.get(mi, (None,))[0]) in mapts.textures
            and (mapts.tex_params(name) >> 16) & 0xF
            != (model.material_texparams(mi) >> 16) & 0xF)
        assert disagree > 5, \
            f"{game}: only {disagree} materials disagree with their NSBTX — near-vacuous"
        seen += 1
    assert seen


# How much of one side's pixel mass must use a colour the other side also uses.
#
# A BAND RULE, fixed before it was applied to anything. Measured over all ten
# chunk seams in the two shipped Gen 5 atlases on 2026-09-21, every healthy
# seam scores between 0.595 and 0.998, and the same ten seams with one side
# replaced by its own mean colour — the clamp bug this test exists for — score
# 0.000, all ten. There is no overlap to trade off against: anything in the
# middle is the answer to a question neither case asks. 0.40 sits below every
# healthy seam measured and far above every clamped one.
SEAM_SHARED_MASS = 0.40


def _seam_bands(atlas, m, image, width=64):
    """Each internal 32-tile chunk boundary of one map, as two pixel bands.

    Located from the atlas rather than written down: under a pitched camera
    the two axes have different pixel scales and the picture carries a pad, so
    a chunk seam is at `pad + 32k * matrix[0]` and NOT at `32k * tile_px`. The
    old spelling of this test had the seam hard-coded at x = 512, which was
    right for the straight-down render and points 32 pixels and a different
    vertical scale away from the seam now.
    """
    import numpy as np

    sx, sy = atlas["camera"]["matrix"][0], atlas["camera"]["matrix"][3]
    pad = m.get("png_pad", [0, 0, 0, 0])
    for axis, tiles, scale, start in (("x", m["width"], sx, pad[0]),
                                      ("y", m["height"], sy, pad[1])):
        limit = image.shape[1] if axis == "x" else image.shape[0]
        for tile in range(32, tiles, 32):
            cut = int(round(start + tile * scale))
            if cut - width < 0 or cut + width > limit:
                continue
            if axis == "x":
                lo, hi = image[:, cut - width:cut], image[:, cut:cut + width]
            else:
                lo, hi = image[cut - width:cut, :], image[cut:cut + width, :]
            lo = lo[..., :3][lo[..., 3] > 250]
            hi = hi[..., :3][hi[..., 3] > 250]
            if len(lo) < 5000 or len(hi) < 5000:
                continue
            yield f"{axis}{tile}", lo, hi


def _shared_mass(lo, hi) -> tuple[float, float]:
    """For each side, the fraction of its pixels drawn in a colour the OTHER
    side also uses. Symmetric pair, because one side going flat only collapses
    that side's own score."""
    from collections import Counter

    a, b = Counter(map(tuple, lo)), Counter(map(tuple, hi))
    sa, sb = set(a), set(b)
    return (sum(v for k, v in a.items() if k in sb) / sum(a.values()),
            sum(v for k, v in b.items() if k in sa) / sum(b.values()))


def test_two_chunks_of_one_map_meet_without_a_step_in_tone(atlases):
    """Black's `317:0` is four matrix cells, and the boundary between two of
    them ran the full height of the map as a hard line between two flat greens.

    Read off the shipped PNG, because that is the artifact the viewer loads.

    NOT by comparing the mean colour of a patch either side, which is what
    this did and which does not survive contact with real maps. Mean colour
    answers "is there the same AMOUNT of light here", and a chunk boundary is
    a line map authors like to put things on: Black 2's `446:0` has a cliff
    running down its x = 32 seam, so the means either side differ by 45 and
    nothing whatever is wrong. The old patch also reported a step of 23 on
    `317:0` — on the RED channel alone, with green and blue steady, which is
    not what a lighting seam looks like and was the tell that the patch had
    slid onto a brown path when the camera was pitched.

    The property the clamp bug actually destroys is that the two chunks draw
    the SAME GRASS: it repainted one whole chunk in a single colour taken from
    one corner of the texture, and its neighbour in a different one. So this
    compares PALETTES, not brightness. Two sides showing different amounts of
    the same materials still share nearly all their pixel mass; two sides each
    flooded with one flat colour share none of it. That distinction is immune
    to what the map happens to depict, which mean colour is not.
    """
    import numpy as np

    checked = []
    for game, atlas in atlases:
        for key, m in atlas["maps"].items():
            if m.get("indoor"):
                continue
            png = MAPS_ROOT / game / m["file"]
            image = np.asarray(Image.open(png).convert("RGBA"))
            for where, lo, hi in _seam_bands(atlas, m, image):
                for name, side in (("low", lo), ("high", hi)):
                    n = len(np.unique(side, axis=0))
                    assert n > 10, (
                        f"{game}:{key} {where}: the {name} side of the seam is "
                        f"{n} colour(s) — that is the clamped-texture signature")
                a, b = _shared_mass(lo, hi)
                assert min(a, b) >= SEAM_SHARED_MASS, (
                    f"{game}:{key} {where}: the two sides of the chunk seam "
                    f"share only {a:.3f}/{b:.3f} of their pixel mass (floor "
                    f"{SEAM_SHARED_MASS}) — they are not drawing the same "
                    f"materials, which is what a per-chunk texture clamp does")
                checked.append(f"{game}:{key} {where}")
    assert "black-us:317:0 x32" in checked, (
        f"the seam this test was written for is not among the {len(checked)} "
        f"checked: {checked}")
    assert len(checked) >= 8, f"only {len(checked)} seams checked: {checked}"


def test_the_seam_check_would_see_a_chunk_painted_one_flat_colour(atlases):
    """Mutation control for the test above, and the one it cannot do without.

    Its healthy answer is "the two sides match", and a comparison of a thing
    with itself matches too — so the way this silently stops working is by
    comparing something that cannot differ. Reproduce the actual bug: repaint
    one side of every seam in its own mean colour, which is exactly what
    clamping a wrapped texture to one edge texel did, and require every single
    seam to fail. Measured, all ten score 0.000 against a 0.40 floor.
    """
    import numpy as np

    survived, seen = [], 0
    for game, atlas in atlases:
        for key, m in atlas["maps"].items():
            if m.get("indoor"):
                continue
            png = MAPS_ROOT / game / m["file"]
            image = np.asarray(Image.open(png).convert("RGBA"))
            for where, lo, hi in _seam_bands(atlas, m, image):
                seen += 1
                flat = np.repeat(lo.mean(0).astype(np.uint8)[None, :], len(lo), 0)
                a, b = _shared_mass(flat, hi)
                if min(a, b) >= SEAM_SHARED_MASS or len(np.unique(flat, axis=0)) > 10:
                    survived.append((game, key, where, a, b))
    assert seen >= 8, f"only {seen} seams to control with"
    assert not survived, (
        f"{len(survived)} seams still passed with one side painted flat, so "
        f"the seam check cannot see the clamp bug: {survived[:3]}")


# -- 8. the doors --------------------------------------------------------------
#
# Gen 5 event data is still not decoded, so a door here is a map transition one
# of our own runs made (`render_gen5maps.observed_doors`). Everything in this
# section is read off the COMMITTED atlas unless it needs the cartridge, because
# the atlas is what the browser loads and a derivation that is right in Python
# and wrong on disk is still an interior nobody can click into.

EXPECTED_DOORS = {
    # game: {map key: {(x, y, destination, via)}}
    "black-us": {
        "319:0": {(761, 649, "320:0", "landed")},
        "389:0": {(776, 757, "392:0", "walked"),
                  (777, 740, "396:0", "walked"),
                  (782, 748, "390:0", "landed")},
        "397:0": {(768, 649, "320:0", "walked"),
                  (796, 657, "398:0", "landed")},
    },
    # Black 2's graph grew from 4 maps and 10 warps to 9 and 26 when a much
    # longer run landed, and the door set grew with it: 437 and 439 reached the
    # page, 438 turned out to be a gate joining 427 and 437, and 427's door
    # into 435 MOVED — see `test_an_outdoor_door_tile_is_a_tile_the_collision
    # _grid_calls_a_wall` for why that one is the interesting change.
    "black2-us": {
        "427:0": {(43, 750, "429:0", "walked"),
                  (47, 761, "428:0", "landed"),
                  (48, 739, "435:0", "landed"),
                  (53, 710, "438:0", "walked")},
        "437:0": {(54, 703, "438:0", "landed")},
        "439:0": {(105, 693, "443:0", "walked")},
    },
}

EXPECTED_EXITS = {
    "black-us": {
        "320:0": {(1, 6, "319:0", "walked"), (15, 6, "397:0", "landed")},
        "390:0": {(2, 2, "391:0", "landed"),
                  (5, 10, "389:0", "walked"), (6, 10, "389:0", "walked")},
        "391:0": {(8, 2, "390:0", "walked")},
        "392:0": {(6, 10, "389:0", "walked")},
        "396:0": {(3, 11, "389:0", "walked")},
        "398:0": {(8, 19, "397:0", "walked")},
    },
    "black2-us": {
        "428:0": {(5, 10, "427:0", "walked"), (6, 10, "427:0", "walked")},
        "429:0": {(6, 11, "427:0", "walked")},
        # Was `(7, 19, "427:0", "landed")`: nobody had ever left 435, so its
        # exit had to be read off the tile a run arrived on. A run has now
        # walked back out, one tile east.
        "435:0": {(8, 19, "427:0", "walked")},
        "438:0": {(5, 14, "427:0", "landed"), (6, 1, "437:0", "walked")},
        "443:0": {(7, 19, "439:0", "walked")},
    },
}


def _tiles(m, field) -> set:
    return {(d["x"], d["y"], d["to"], d["via"]) for d in m.get(field, [])}


def test_the_shipped_doors_are_exactly_the_ones_we_derived(atlases):
    """The pin. Every door and every exit on disk, by tile, destination and
    which end of the warp it came from.

    A literal rather than a recomputation on purpose: recomputing the
    derivation and comparing it to itself would pass under every mutation of
    the derivation, which is the one thing worth guarding here. This fails if
    a tile moves, a destination changes, a door appears or disappears, or a
    `walked` tile silently becomes a `landed` one.
    """
    for game, atlas in atlases:
        if game not in EXPECTED_DOORS:
            continue
        got_doors = {k: _tiles(m, "doors") for k, m in atlas["maps"].items()
                     if m.get("doors")}
        got_exits = {k: _tiles(m, "exits") for k, m in atlas["maps"].items()
                     if m.get("exits")}
        assert got_doors == EXPECTED_DOORS[game], game
        assert got_exits == EXPECTED_EXITS[game], game


def test_a_warp_inside_one_map_is_never_a_door(atlases):
    """A staircase, a warp pad, a ledge the field code resolves by teleporting
    — all of those mint a warp between two tiles of the SAME map, and Black 2's
    `427|36|715 -> 427|36|718` is one of them. A door leads somewhere else.
    """
    for game, atlas in atlases:
        for key, m in atlas["maps"].items():
            for d in m.get("doors", []) + m.get("exits", []):
                assert d["to"] != key, f"{game}:{key} has a door to itself"


def test_dropping_the_same_map_rule_would_put_self_doors_on_the_page(atlases):
    """Mutation control for the test above, which needs one: it passes for free
    on a corpus with no same-map warps in it, and "no door leads to itself" is
    exactly the shape of assertion that is vacuous without a count.

    So: re-derive with the same-map filter taken out, and require that it
    produces at least one door from a map to itself. Black 2's ten warps
    include five inside `427` alone.
    """
    for game, _atlas in atlases:
        warps = _observed(game)["warps"]
        same = [(a, b) for a, b, _n in warps if a.split("|")[0] == b.split("|")[0]]
        assert same, f"{game}: no same-map warp in the corpus, so the rule is untested"
        # what the unfiltered derivation would emit, in the atlas's own spelling
        self_doors = {(f"{a.split('|')[0]}:0", f"{b.split('|')[0]}:0") for a, b in same}
        assert any(src == dst for src, dst in self_doors), game


def test_every_interior_is_reachable_from_a_door_on_another_map(atlases):
    """The whole point of this section. A popup nobody can open is artwork
    nobody sees, and before the doors were derived that was every Gen 5
    interior: rendered, shipped, listed, unclickable.

    Reachable AS A BUILDING, like SoulSilver's 2F: the viewer opens every floor
    from the one marker, so Black's 391 counts as reachable through 390.
    """
    for game, atlas in atlases:
        doors = {d["to"] for m in atlas["maps"].values() for d in m.get("doors", [])}
        interiors = [k for k, m in atlas["maps"].items() if m.get("indoor")]
        assert interiors, f"{game}: no interior in the atlas, so this checks nothing"
        for key in interiors:
            m = atlas["maps"][key]
            floors = m.get("floors") or []
            assert m.get("building") and key in floors, (game, key)
            assert doors & set(floors), \
                f"{game}:{key} is in a building with no door into any of its floors"


def test_a_door_is_written_in_the_frame_of_the_map_it_sits_on(atlases):
    """The silent one. `mapatlas.drawWindow` subtracts a map's own `origin`
    from a door's tile exactly as it does from a route step, so an outdoor
    door is GLOBAL and an interior exit is LOCAL. Get it backwards and the
    marker lands off the map, and the viewer reports nothing.

    NOT decided by magnitude, which is why this is a rectangle test: Black 2's
    world map starts at x = 32 and its interiors reach x = 17, so a "big
    coordinate means global" rule has 15 tiles of daylight and would be a
    coin toss on the x axis.
    """
    for game, atlas in atlases:
        seen = 0
        for key, m in atlas["maps"].items():
            ox, oy = m.get("origin", [0, 0])
            for d in m.get("doors", []) + m.get("exits", []):
                assert ox <= d["x"] < ox + m["width"], (game, key, d)
                assert oy <= d["y"] < oy + m["height"], (game, key, d)
                seen += 1
        assert seen, f"{game}: no door checked"


def test_the_frame_check_rejects_a_door_written_on_the_wrong_side(atlases):
    """Mutation control for the test above. Writing each door's tile on its
    DESTINATION's rectangle instead — the exact mistake of mixing the two
    frames — must put it outside, or the check above cannot see the error.

    Scoped to the doors that CROSS the two frames, which is the mistake being
    controlled for. Two interiors are both local and their rectangles overlap
    almost completely, so a stairway written on the wrong floor stays in
    bounds and no bounds check will ever catch it; claiming otherwise here
    would be a control that passes for the wrong reason.
    """
    for game, atlas in atlases:
        survived, checked = [], 0
        for key, m in atlas["maps"].items():
            for d in m.get("doors", []) + m.get("exits", []):
                t = atlas["maps"][d["to"]]
                if bool(t.get("indoor")) == bool(m.get("indoor")):
                    continue
                checked += 1
                ox, oy = t.get("origin", [0, 0])
                if (ox <= d["x"] < ox + t["width"]
                        and oy <= d["y"] < oy + t["height"]):
                    survived.append((key, d))
        assert checked, f"{game}: no frame-crossing door to control with"
        assert not survived, \
            f"{game}: {len(survived)} doors would still be in bounds on the far map"


def test_a_direction_nobody_walked_says_so(atlases):
    """Half a door is still emitted, and labelled.

    Every run STARTS inside the player's house, so the door into it is the one
    door nobody ever opened: Black 2's `428` and Black's `390` were left and
    never entered. Dropping those would leave the interior every Black 2 run
    begins in unreachable, so the tile is read off where the run LANDED coming
    out and marked `via: landed`. Pinned here from the corpus rather than from
    the atlas: a direction with NO cross-map warp in it must be labelled
    `landed`, because there was nothing else to read it from, and a tile
    labelled `walked` must have a warp that actually left it.

    (The converse does not hold and must not be asserted: a walked direction
    can still be labelled `landed`, because the collision grid overrides a
    walked tile that is not a door — Black's 397 -> 398 is exactly that.)
    """
    for game, atlas in atlases:
        if game not in EXPECTED_DOORS:
            continue
        walked_pairs, walked_tiles = set(), set()
        for a, b, _n in _observed(game)["warps"]:
            (az, ax, ay), bz = a.split("|"), b.split("|")[0]
            if az != bz:
                walked_pairs.add((f"{az}:0", f"{bz}:0"))
                walked_tiles.add((f"{az}:0", int(ax), int(ay), f"{bz}:0"))
        never, checked = [], {"walked": 0, "landed": 0}
        for key, m in atlas["maps"].items():
            for d in m.get("doors", []) + m.get("exits", []):
                assert d["via"] in checked, d
                if d["via"] == "walked":
                    assert (key, d["x"], d["y"], d["to"]) in walked_tiles, \
                        f"{game}:{key} ({d['x']},{d['y']}) -> {d['to']} was not walked"
                if (key, d["to"]) not in walked_pairs:
                    never.append((key, d["to"]))
                    assert d["via"] == "landed", (game, key, d)
                checked[d["via"]] += 1
        assert checked["walked"] and checked["landed"], (game, checked)
        assert never, \
            f"{game}: no one-way door in the corpus, so the fallback is untested"


def test_two_rooms_are_one_building_only_when_a_door_joins_them(atlases):
    """A cartridge ships map ids and no names, so the name-prefix rule that
    groups Platinum's floors has nothing to work on and the floors are grouped
    by the SHAPE of the doors instead: an interior whose door leads to another
    interior is a floor of that building.

    Black's 391 opens onto 390 and nothing else, so the two are one house. The
    mutation this guards is the tempting alternative — grouping interiors by
    the town they sit in — which would put all six of Black's rooms in one
    popup: assert the rooms that share no door are in different buildings.
    """
    for game, atlas in atlases:
        joined = {(k, d["to"]) for k, m in atlas["maps"].items() if m.get("indoor")
                  for d in m.get("exits", []) if atlas["maps"][d["to"]].get("indoor")}
        for key, m in atlas["maps"].items():
            if not m.get("indoor"):
                continue
            for other in m["floors"]:
                if other == key:
                    continue
                assert (key, other) in joined or (other, key) in joined, \
                    f"{game}: {key} and {other} share a building with no door between them"
    black = dict(atlases).get("black-us")
    if black:
        assert black["maps"]["391:0"]["floors"] == ["390:0", "391:0"]
        assert black["maps"]["392:0"]["floors"] == ["392:0"]


def test_the_atlas_says_its_doors_came_from_our_runs_and_not_from_events(atlases):
    """The label is half the claim. These are the doors that were USED, not the
    doors that exist, and the atlas has to say so in the same word SoulSilver
    uses so a reader can tell the two provenances apart. The old note said Gen 5
    doors were impossible; that sentence must be gone, not merely outnumbered.
    """
    for game, atlas in atlases:
        assert atlas.get("doors") == "observed-transitions", game
        note = atlas["map_set"].get("doors", "")
        assert "observed transitions" in note, game
        assert not note.startswith("none"), game
        assert "no door to open it from" not in note, game


def test_an_outdoor_door_tile_is_a_tile_the_collision_grid_calls_a_wall(roms):
    """The cartridge's own opinion of where a door is, which is independent of
    our runs: you never WALK onto an outdoor door, the field code warps you off
    it, so its collision bit is set. That is the same exception
    `test_every_walked_tile_is_passable_except_warp_tiles` has to make.

    It is also the tie-break the derivation uses when a pair has both a walked
    and a landed candidate, so the CONTROL matters: the tiles those doors were
    chosen over — every other tile our runs stood on — must be overwhelmingly
    passable, or "blocked" is not discriminating anything.
    """
    render_gen5maps = pytest.importorskip("render_gen5maps")
    atlas_by_game = dict(_atlases())
    blocked_doors = passable_doors = 0
    for game, rom in roms.items():
        atlas = atlas_by_game.get(game)
        if atlas is None:
            continue
        wins = {i: render_gen5maps.map_window(rom, i)
                for i in render_gen5maps.map_ids(game)}

        def at(map_id, x, y):
            w = wins[map_id]
            return bool(w.blocked[y - w.oy, x - w.ox])

        doors = set()
        for key, m in atlas["maps"].items():
            if m.get("indoor"):
                continue
            for d in m.get("doors", []):
                doors.add((int(key.split(":")[0]), d["x"], d["y"]))
        for map_id, x, y in doors:
            if at(map_id, x, y):
                blocked_doors += 1
            else:
                passable_doors += 1
        # the control: every OTHER outdoor tile a run stood on
        walls = sum(1 for z, x, y in _samples(game)
                    if not wins[z].indoor and (z, x, y) not in doors and at(z, x, y))
        outside = sum(1 for z, x, y in _samples(game)
                      if not wins[z].indoor and (z, x, y) not in doors)
        assert outside > 100, f"{game}: only {outside} control tiles"
        assert walls / outside < 0.02, (
            f"{game}: {walls} of {outside} non-door outdoor tiles are walls too, so "
            f"the collision bit does not pick doors out")
    assert blocked_doors >= 5, blocked_doors
    # This was 1, and the exception was Black 2's 427 -> 435: nobody had ever
    # walked back out of 435, so the only candidate was the tile the run stood
    # on going IN, two tiles short of the wall. The note here said the number
    # would drop the day a run walked that return trip.
    #
    # 2026-09-21: it did. The rebuilt graph took Black 2 from 4 maps and 10
    # warps to 9 and 26, and one of the new warps leaves 435. The derivation
    # could then read the door off where that run LANDED — (48, 739), which
    # the collision grid does call a wall — instead of off the walked tile
    # (49, 741), which it does not. So 427's door to 435 moved one tile and
    # flipped from `walked` to `landed`, 435's own exit flipped the other way
    # from `landed` to `walked`, and the exception closed on its own.
    #
    # Every outdoor door in both Gen 5 atlases is now a tile the cartridge
    # independently calls impassable. Zero is the strong form of this claim,
    # so it is asserted as zero rather than as "at most one" — a passable
    # outdoor door reappearing is now a finding, not a tolerated case.
    assert passable_doors == 0, (
        f"{passable_doors} outdoor door tile(s) are passable — the 427 -> 435 "
        f"exception closed on 2026-09-21 and nothing should have reopened it")


# -- 9. the colour depth the artwork is shipped at -----------------------------

def test_no_shipped_pixel_is_a_colour_the_console_could_not_have_shown(atlases):
    """The artwork tier is quantised to the DS's own five bits per channel by
    `ds3d/pngout.py`, which is where about a third of the file went.

    Pinned as the PROPERTY and not as a file size. A byte count is a proxy: it
    moves when a map is added, when the camera changes, when zlib changes its
    mind, and it says nothing about what was actually done to the pixels. The
    property is exact and has a reason — a DS channel has 32 levels, so an
    eight-bit value off this grid is precision the hardware never had and the
    renderer invented. A render that quietly stopped quantising would keep
    every other test in this file green and would show up here immediately.

    The collision tier is excluded on purpose, and not as an exemption: its
    two tones are a UI palette this repo chose rather than colour read off a
    cartridge, three of their six channel values are off the grid, and the
    argument for quantising simply does not apply to a colour we invented.
    """
    import numpy as np
    from ds3d import pngout

    seen = 0
    for game, atlas in atlases:
        for key, m in atlas["maps"].items():
            if m.get("render", atlas["render"]) == "collision":
                continue
            png = MAPS_ROOT / game / m["file"]
            a = np.asarray(Image.open(png).convert("RGBA"))
            bad = ~pngout.on_ds_grid(a)
            if bad.any():
                off = sorted(set(np.unique(a[bad]).tolist()))
                raise AssertionError(
                    f"{game}:{key} has {int(bad.sum())} channel samples the DS "
                    f"cannot express, e.g. {off[:8]} — the 32 it can are "
                    f"{pngout.ds_grid_values().tolist()[:6]}...")
            seen += 1
    assert seen >= 15, f"only {seen} artwork PNGs checked"


def test_the_colour_depth_check_rejects_an_eight_bit_pixel(atlases):
    """Mutation control for the test above.

    "Every sample is on a 32-value grid" is satisfied by a great many things,
    including an image of one colour and an empty array, so the check needs to
    be shown failing on the thing it is meant to reject. One channel of one
    pixel nudged by one — the smallest possible departure, and the one a
    half-applied quantisation would leave behind — must be caught.
    """
    import numpy as np
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
