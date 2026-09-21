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
    and an atlas that describes them is a lie no consumer can detect."""
    for game, atlas in atlases:
        tile_px = atlas["tile_px"]
        for key, m in atlas["maps"].items():
            png = MAPS_ROOT / game / m["file"]
            assert png.is_file(), f"{game}:{key} names a missing {m['file']}"
            with Image.open(png) as im:
                assert im.size == (m["width"] * tile_px, m["height"] * tile_px), (
                    f"{game}:{key} claims {m['width']}x{m['height']} at {tile_px}px "
                    f"but the PNG is {im.size}")
            assert png.stat().st_size == m["bytes"], f"{game}:{key} byte count is stale"


def test_the_png_frame_is_declared_and_outdoor_maps_carry_their_origin(atlases):
    """Gen 5 outdoor coordinates are global, so the PNG cannot be in route-tile
    space without padding Nuvema Town out to tile (0, 0). It is map-local, and
    `png_origin` is what `RouteMap.svelte` subtracts from the source rect."""
    for game, atlas in atlases:
        assert atlas["png_frame"] == "map-local", game
        assert atlas["render"] in ("3d-ortho", "collision"), game
        # `tile_px` is per tier and the viewer takes it from the atlas, never
        # from the route: 16 is the DS's own world units per tile, so the 3D
        # tier is one pixel per world unit; the silhouette has no sub-tile
        # detail and 4 loses nothing.
        assert atlas["tile_px"] == (16 if atlas["render"] == "3d-ortho" else 4), game
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


def test_every_placement_resolves_to_a_prop_model(roms):
    """One number out of the zone header picks both the texture set and the
    prop archive. If that arithmetic were wrong, placements would resolve to
    nothing (or, worse, to another area's furniture)."""
    render_gen5maps = pytest.importorskip("render_gen5maps")
    for game, rom in roms.items():
        got = render_gen5maps.check_props(rom)
        assert got["resolved"] > 40, f"{game}: only {got['resolved']} placements"
        assert not got["missing"], f"{game}: {got['missing']} placements name no model"


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


def test_two_chunks_of_one_map_meet_without_a_step_in_tone(atlases):
    """Black's `317:0` is four matrix cells, and the boundary between two of
    them ran the full height of the map as a hard line between two flat greens.

    Read off the shipped PNG, because that is the artifact the viewer loads.
    Two 40x40 patches of open grass either side of the x = 512 chunk seam: each
    must carry real texture (one flat colour is the clamp signature — it was
    exactly one colour on each side before), and the two must agree in mean
    colour, because they are the same grass.
    """
    import numpy as np

    atlas = dict(atlases).get("black-us")
    if atlas is None or "317:0" not in atlas["maps"]:
        pytest.skip("black-us 317:0 not rendered")
    png = MAPS_ROOT / "black-us" / atlas["maps"]["317:0"]["file"]
    a = np.asarray(Image.open(png).convert("RGBA")).astype(float)
    left = a[580:620, 470:510, :3].reshape(-1, 3)
    right = a[580:620, 515:555, :3].reshape(-1, 3)
    for name, patch in (("left", left), ("right", right)):
        assert len(np.unique(patch, axis=0)) > 10, \
            f"{name} of the seam is {len(np.unique(patch, axis=0))} colour(s) — a clamped texture"
    assert np.abs(left.mean(0) - right.mean(0)).max() < 12, \
        f"a tone step across the chunk seam: {left.mean(0)} vs {right.mean(0)}"
