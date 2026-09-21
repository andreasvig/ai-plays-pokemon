"""The DS map camera (`scripts/ds3d/camera.py`).

Three things fail silently here and each has a test that bites:

  1. **The topdown tier has to stay bit-for-bit what shipped.** The switch to a
     pitched camera moved every DS render through a new projector, and a
     projector that is "the same modulo rounding" is a re-render nobody asked
     for. `cos(radians(90))` is 6.1e-17 rather than 0 and the depth base the
     rasteriser divides by is load-bearing, so both are pinned.
  2. **A tile's ground is not the ground plane.** Gen 4 puts an outdoor map's
     walkable surface 16 world units up, which under this pitch is 8.2 px —
     half a tile — of route displacement on every map. The ground sampler has
     to read the LAND and not the roof standing on it.
  3. **The atlas has to say enough for a browser to redo the arithmetic.** The
     matrix, the height scale and the pad are the whole contract; the JS side
     of it is `tests/js/camera.test.mjs`, which hand-builds the same numbers.

The checks that need the decomp cache (`local/pret-cache-platinum`) skip
without it.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ds3d import camera as C                                    # noqa: E402
from ds3d import scene as S                                     # noqa: E402

CACHE = REPO_ROOT / "local" / "pret-cache-platinum"
MAPS = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"


def quad(y: float, x0: float, x1: float, z0: float, z1: float):
    """One flat, axis-aligned surface at altitude `y`, as two triangles."""
    tex = np.zeros((1, 1, 4), np.uint8)
    tex[0, 0] = (255, 0, 0, 255)
    uv = np.array([[0.5, 0.5]] * 3)
    a = np.array([x0, y, z0])
    b = np.array([x1, y, z0])
    c = np.array([x1, y, z1])
    d = np.array([x0, y, z1])
    return [(np.array([a, b, c]), uv, tex, (1, 1, 0, 0)),
            (np.array([a, c, d]), uv, tex, (1, 1, 0, 0))]


# -- 1. the straight-down camera is the identity, exactly ----------------------

def test_the_topdown_camera_is_the_identity_and_not_nearly_the_identity():
    cam = C.camera_for("topdown", 16)
    assert cam.sin_p == 1.0
    # NOT `pytest.approx`: math.cos(math.radians(90)) is 6.1e-17 and a residue
    # that small is the difference between "the control reproduced the shipped
    # atlas" and "the control re-rendered it".
    assert cam.cos_p == 0.0
    assert cam.tile_px_x == 16.0
    assert cam.tile_px_y == 16.0
    assert cam.height_px == 0.0
    assert cam.matrix() == [16.0, 0.0, 0.0, 16.0, 0.0, 0.0]


def test_the_topdown_projector_keeps_the_depth_base_the_shipped_pixels_were_drawn_with():
    """`raster.draw_tri` interpolates 1/w, so the base is in the PIXELS.

    Not a style point. Rendering Sandgem Town through the same projector at
    1e5 instead of 1000 changes 6.6% of its pixels, max channel 138, because
    the hyperbolic UV interpolation the old base produced is baked into the
    artwork that shipped.
    """
    assert C.camera_for("topdown", 16).depth_base == C.ORTHO_DEPTH_BASE == 1000.0
    assert C.camera_for("pitched", 16).depth_base == C.DEPTH_BASE
    pts = np.array([[0.0, 7.0, 0.0]])
    assert C.camera_for("topdown", 16).projector()(pts)[0, 2] == 1000.0 - 7.0


def test_a_pitched_camera_needs_a_depth_base_above_any_map_it_can_be_pointed_at():
    """The base has to clear the map's own DEPTH spread or w goes small, then
    negative.

    Straight down the spread is the scene's height, a hundred units or so.
    Pitched it is its depth: Black 2's map 427 is 64 tiles of z, and
    `z * cos(pitch)` over that is 527 — over half the straight-down base of
    1000, before a longer map is drawn at all.
    """
    cam = C.camera_for("pitched", 16)
    far = 64 * S.TILE * cam.cos_p
    assert far == pytest.approx(527, abs=1)
    assert far > 0.5 * C.ORTHO_DEPTH_BASE, "the old base would not have cleared it"
    assert C.DEPTH_BASE > 20 * far


def test_a_camera_refuses_a_kind_or_a_pitch_it_cannot_mean():
    with pytest.raises(ValueError):
        C.Camera("isometric", 16, 45.0)
    with pytest.raises(ValueError):
        C.Camera("topdown", 16, 59.0)        # topdown IS pitch 90
    with pytest.raises(ValueError):
        C.Camera("pitched", 16, 120.0)       # not looking at the ground
    with pytest.raises(SystemExit):
        C.camera_for("sideways", 16)


# -- 2. the projection, and its inverse ---------------------------------------

def test_the_pitch_is_the_field_cameras_own_and_foreshortens_only_y():
    cam = C.camera_for("pitched", 16)
    assert cam.pitch_deg == S.CAM_PITCH
    assert cam.tile_px_x == 16.0
    assert cam.tile_px_y == pytest.approx(16 * math.sin(math.radians(S.CAM_PITCH)))
    assert cam.tile_px_y == pytest.approx(13.722081, abs=1e-5)


def test_altitude_lifts_a_tile_up_the_picture_and_leaves_its_column_alone():
    cam = C.camera_for("pitched", 16)
    ground = cam.tile_to_px(3, 4, 0.0)
    raised = cam.tile_to_px(3, 4, 16.0)
    assert raised[0] == ground[0]
    assert ground[1] - raised[1] == pytest.approx(16 * cam.height_px)
    # The number the whole `heights` field exists for: gen 4's outdoor ground
    # is 16 world units, so ignoring it is half a tile of error everywhere.
    assert 16 * cam.height_px == pytest.approx(8.228, abs=1e-3)


@pytest.mark.parametrize("kind", ["topdown", "pitched"])
@pytest.mark.parametrize("h", [0.0, 16.0, -2.0])
def test_px_to_tile_inverts_tile_to_px_on_whatever_plane_it_is_given(kind, h):
    cam = C.camera_for(kind, 16)
    pad = (4.0, 31.0)
    for tx, ty in ((0, 0), (3.5, 7.25), (95.5, 31.5)):
        px = cam.tile_to_px(tx, ty, h, pad)
        back = cam.px_to_tile(px[0], px[1], h, pad)
        assert back[0] == pytest.approx(tx)
        assert back[1] == pytest.approx(ty)


def test_the_published_matrix_is_the_projection_and_not_a_second_copy_of_it():
    """The atlas ships `matrix`; the renderer uses `tile_to_px`. One answer."""
    for kind in ("topdown", "pitched"):
        cam = C.camera_for(kind, 16)
        a, b, c, d, e, f = cam.matrix()
        for tx, ty in ((0, 0), (2, 5), (31.5, 12.5)):
            want = cam.tile_to_px(tx, ty, 0.0)
            assert (a * tx + c * ty + e, b * tx + d * ty + f) == pytest.approx(want)


def test_the_projector_and_tile_to_px_agree_about_where_a_tile_centre_lands():
    """The rasteriser and the atlas must not be two opinions.

    `projector` takes WORLD points and is what draws the pixels; `tile_to_px`
    takes tiles and is what the atlas publishes. A tile centre put through
    both has to come out at the same pixel, or the route is drawn against a
    map that was rendered against something else.
    """
    cam = C.camera_for("pitched", 16)
    pad = (4.0, 31.0)
    proj = cam.projector(1, pad)
    for tx, ty, h in ((0, 0, 0.0), (5, 9, 16.0), (31, 31, 8.0)):
        world = np.array([[(tx + 0.5) * S.TILE, h, (ty + 0.5) * S.TILE]])
        got = proj(world)[0, :2]
        want = cam.tile_to_px(tx + 0.5, ty + 0.5, h, pad)
        assert got[0] == pytest.approx(want[0])
        assert got[1] == pytest.approx(want[1])


# -- 3. the frame: the pad, the crop --------------------------------------------

def test_a_frame_is_its_ground_rectangle_plus_its_declared_pad():
    cam = C.camera_for("pitched", 16)
    fr = C.Frame(cam, 32, 32, (4, 31, 4, 0))
    assert fr.width == 512 + 8
    assert fr.height == round(32 * cam.tile_px_y) + 31
    assert fr.origin == (4.0, 31.0)
    # tile (0,0)'s corner sits exactly at the pad, which is the anchor the
    # viewer uses to place the image.
    assert fr.tile_px(0, 0, 0.0) == (4.0, 31.0)


def test_a_topdown_frame_has_no_pad_and_is_the_rectangle_it_always_was():
    fr = C.Frame(C.camera_for("topdown", 16), 24, 20, (0, 0, 0, 0))
    assert (fr.width, fr.height) == (24 * 16, 20 * 16)
    assert fr.tile_px(3, 4) == (48.0, 64.0)


def test_a_cut_is_always_exactly_the_size_the_cropped_frame_declares():
    """Where the rounding bites.

    `tile_px_y` is 13.722, so `round(by0 * t) + round(h * t)` is not
    `round((by0 + h) * t)` — the two disagree by a pixel for some offsets, and
    a slice that came back one row short would ship a map one row short with
    nothing raising. Every offset, both axes.
    """
    cam = C.camera_for("pitched", 16)
    fr = C.Frame(cam, 32, 32, (8, 16, 8, 8))
    rgba = np.full((fr.height, fr.width, 4), 200, np.uint8)
    for by0 in range(0, 29):
        for bx0 in (0, 7, 28):
            sub = fr.crop(bx0, by0, 4, 4)
            assert fr.cut(rgba, bx0, by0, 4, 4).shape[:2] == (sub.height, sub.width)


def test_a_cut_off_the_end_of_the_canvas_is_transparent_rather_than_short():
    """Defensive, and the reason `cut` exists rather than a bare slice."""
    cam = C.camera_for("pitched", 16)
    fr = C.Frame(cam, 32, 32, (8, 16, 8, 8))
    small = np.full((40, 40, 4), 200, np.uint8)      # nothing like the frame
    cut = fr.cut(small, 20, 20, 4, 4)
    sub = fr.crop(20, 20, 4, 4)
    assert cut.shape[:2] == (sub.height, sub.width)
    assert cut[-1, -1, 3] == 0


def test_measure_pad_is_zero_straight_down_and_finds_what_rises_when_pitched():
    """A roof over the map's TOP row is the only thing that leaves the frame.

    Which is the whole subtlety: the same roof in the middle of the map is
    still inside the ground rectangle, because a pitched camera pushes
    everything down the picture as z grows. The pad is measured, not assumed,
    for exactly that reason.
    """
    cam = C.camera_for("pitched", 16)
    top = S.Scene()
    top.tris = quad(0.0, 0, 512, 0, 512) + quad(64.0, 100, 200, 0, 32)
    assert C.measure_pad(top, C.camera_for("topdown", 16), 32, 32) == (0, 0, 0, 0)
    pad = C.measure_pad(top, cam, 32, 32)
    assert pad[1] == math.ceil(64 * cam.height_px)
    assert pad[0] == pad[2] == 0

    middle = S.Scene()
    middle.tris = quad(0.0, 0, 512, 0, 512) + quad(64.0, 100, 200, 200, 300)
    assert C.measure_pad(middle, cam, 32, 32) == (0, 0, 0, 0)


def test_the_pad_is_capped_so_one_runaway_placement_cannot_size_the_canvas():
    wild = S.Scene()
    wild.tris = quad(0.0, 0, 512, 0, 512) + quad(9000.0, 0, 16, 0, 16)
    cam = C.camera_for("pitched", 16)
    pad = C.measure_pad(wild, cam, 32, 32)
    assert pad[1] == int(C.MAX_PAD_TILES[1] * 16)
    # MUTATION CONTROL: without the cap this is 4630 px of empty picture above
    # a 439 px map.
    assert 9000 * cam.height_px > 4000


# -- 4. the ground under a tile -------------------------------------------------

def test_the_ground_sampler_reads_the_surface_under_each_tile_centre():
    sc = S.Scene()
    # A 4x4-tile plane at 16, with its right half raised to 24.
    sc.tris = quad(16.0, 0, 32, 0, 64) + quad(24.0, 32, 64, 0, 64)
    h = C.ground_heights(sc.tris, 4, 4)
    assert h.shape == (4, 4)
    assert (h[:, :2] == 16.0).all()
    assert (h[:, 2:] == 24.0).all()


def test_a_tile_no_geometry_covers_reads_as_nan_rather_than_as_zero():
    sc = S.Scene()
    sc.tris = quad(16.0, 0, 32, 0, 32)
    h = C.ground_heights(sc.tris, 4, 4)
    assert np.isfinite(h[:2, :2]).all()
    assert np.isnan(h[3, 3])


def test_asking_a_whole_scene_reads_the_ROOF_which_is_why_the_caller_slices():
    """The trap this API shape exists to prevent.

    Measured on the cartridge: Sandgem Town's walkable ground is 16.0 world
    units everywhere, and the same sampler over the scene WITH its props
    reports up to 108 — the top of a house.
    """
    sc = S.Scene()
    ground = quad(16.0, 0, 64, 0, 64)
    roof = quad(108.0, 0, 32, 0, 32)
    sc.tris = ground + roof
    spans = [(0, len(ground))]
    assert C.ground_heights(sc.tris, 4, 4)[0, 0] == 108.0
    assert C.ground_heights(C.terrain_only(sc, spans), 4, 4)[0, 0] == 16.0


def test_terrain_only_slices_per_chunk_because_props_sit_between_the_chunks():
    """`field.add_chunk` appends terrain THEN props, per chunk, so the terrain
    of a two-chunk map is two runs and not one prefix."""
    sc = S.Scene()
    sc.tris = ["t0", "t1", "p0", "t2", "t3", "t4", "p1", "p2"]
    assert C.terrain_only(sc, [(0, 2), (3, 3)]) == ["t0", "t1", "t2", "t3", "t4"]


def test_walkable_plane_takes_the_ground_from_walkable_tiles_and_fills_the_rest():
    h = np.array([[16.0, 16.0], [52.7, np.nan]])
    walk = np.array([[True, True], [False, False]])
    got = C.walkable_plane(h, walk)
    assert (got == 16.0).all(), "the canopy and the hole both take the median"


def test_walkable_plane_on_a_map_with_nothing_walkable_is_flat_zero():
    h = np.array([[52.7, 52.7]])
    assert (C.walkable_plane(h, np.array([[False, False]])) == 0.0).all()


# -- 5. what the atlas carries --------------------------------------------------

def test_the_run_length_encoding_round_trips():
    grid = [[16, 16, 16, 8], [8, 8, 0, 0]]
    runs = C.rle(grid)
    assert runs == [3, 16, 3, 8, 2, 0]
    assert C.unrle(runs, 4, 2) == grid


def test_a_flat_map_costs_two_integers_and_a_terraced_one_costs_its_terraces():
    flat = C.atlas_heights(np.full((32, 32), 16.0))
    assert flat == [1024, 16]
    stepped = np.full((4, 4), 8.0)
    stepped[:, 2:] = 16.0
    assert C.atlas_heights(stepped) == [2, 8, 2, 16, 2, 8, 2, 16, 2, 8, 2, 16, 2, 8, 2, 16]


def test_unrle_refuses_a_run_list_that_is_not_the_grid_it_claims_to_be():
    with pytest.raises(ValueError):
        C.unrle([3, 16], 4, 2)


def test_the_atlas_camera_publishes_the_matrix_and_the_height_scale_and_no_alias():
    cam = C.camera_for("pitched", 16)
    rec = C.atlas_camera(cam)
    assert rec["kind"] == "pitched"
    assert rec["pitch_deg"] == S.CAM_PITCH
    assert rec["height_unit"] == "world"
    assert rec["matrix"][0] == 16.0
    assert rec["matrix"][3] == pytest.approx(cam.tile_px_y, abs=1e-6)
    assert rec["height_px"] == pytest.approx(cam.height_px, abs=1e-6)
    # `tile_px_x`/`tile_px_y` are NOT published beside the matrix: two spellings
    # of one number is a derivation change waiting to move only one of them.
    assert set(rec) == {"kind", "pitch_deg", "matrix", "height_px", "height_unit"}


def test_a_topdown_atlas_still_publishes_a_usable_camera_record():
    rec = C.atlas_camera(C.camera_for("topdown", 4))
    assert rec["matrix"] == [4.0, 0.0, 0.0, 4.0, 0.0, 0.0]
    assert rec["height_px"] == 0.0


# -- 6. the camera that was rejected, and the number that rejected it ----------

def test_the_games_own_camera_is_not_nearly_orthographic_over_a_whole_map():
    """The measurement that chose the pitch over the perspective.

    "Nearly orthographic at 8 degrees of FOV" was the guess. Over ONE 32x32
    chunk the field camera makes a tile 1.47x bigger at the near edge than at
    the far one, so the route's cable — and every texel of artwork — would
    change size down the same picture.
    """
    w = h = 32
    tx, ty = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    pts = np.stack([tx.ravel() * S.TILE, np.zeros(tx.size), ty.ravel() * S.TILE], 1)
    proj = C.perspective_projector(1000, 1000, 1, centre=(w * S.TILE / 2, h * S.TILE / 2))
    scr = proj(pts)[:, :2].reshape(h, w, 2)
    far = np.abs(scr[0, 1:, 0] - scr[0, :-1, 0]).mean()
    near = np.abs(scr[-1, 1:, 0] - scr[-1, :-1, 0]).mean()
    assert near / far == pytest.approx(1.47, abs=0.02)

    # The pitched camera's answer to the same question, which is the point.
    cam = C.camera_for("pitched", 16)
    assert cam.tile_to_px(1, 0)[0] - cam.tile_to_px(0, 0)[0] == 16.0
    assert cam.tile_to_px(1, 31)[0] - cam.tile_to_px(0, 31)[0] == 16.0


# -- 7. the control: straight down still renders the atlas that shipped --------

@pytest.mark.skipif(not (CACHE / "SHA").is_file(),
                    reason="no local/pret-cache-platinum")
@pytest.mark.skipif(not (MAPS / "platinum-us" / "index.json").is_file(),
                    reason="no Platinum atlas rendered yet")
def test_camera_topdown_reproduces_the_shipped_platinum_pngs_byte_for_byte():
    """The only way to know `--camera topdown` is a switch BACK.

    Every DS render now goes through `ds3d/camera.py`, including the
    straight-down one. "Looks the same" would hide a depth base, a rounding
    rule or a 6.1e-17 cosine; this is the assertion that does not.
    """
    from PIL import Image

    import render_dsmaps as R

    d = R.Decomp(offline=True)
    art = R.Field3D(d, tile_px=16, cam=C.camera_for("topdown", 16))
    checked, bad = 0, []
    for png in sorted((MAPS / "platinum-us").glob("*-0.png")):
        map_id = int(png.stem.split("-")[0])
        r = R.render_map(d, map_id, art, cam=art.cam)
        if r.render != "3d-ortho":
            continue
        got = R.full_pixels(r, 16)
        want = np.array(Image.open(png).convert("RGBA"))
        checked += 1
        if got.shape != want.shape or not (got == want).all():
            bad.append(png.name)
    assert checked >= 8, f"only {checked} maps compared"
    assert not bad, f"{len(bad)} of {checked} PNGs changed: {bad}"


GEN5_ROM = REPO_ROOT / "roms" / "Pokemon - Black Version 2 (USA, Europe) (NDSi Enhanced).nds"


@pytest.mark.skipif(not GEN5_ROM.is_file(), reason="no Black 2 cartridge")
@pytest.mark.skipif(not (MAPS / "black2-us" / "index.json").is_file(),
                    reason="no Black 2 atlas rendered yet")
def test_camera_topdown_reproduces_the_shipped_black2_pngs_byte_for_byte():
    """The same control on the other renderer.

    Gen 5 got the camera on the same day and through the same module, and it
    has its own `render_map`, its own `to_png` density rule and its own
    silhouette fallback. One of the two reproducing the shipped atlas says
    nothing about the other.
    """
    from PIL import Image

    import render_gen5maps as G

    rom = G.Gen5Rom("black2-us")
    art = G.Field3D(rom, tile_px=16, cam=C.camera_for("topdown", 16))
    checked, bad = 0, []
    for map_id in G.map_ids("black2-us"):
        win = G.map_window(rom, map_id)
        got, kind, _frame, _h = G.render_map(rom, win, art, cam=art.cam)
        if kind != "3d-ortho":
            continue
        want = np.array(Image.open(MAPS / "black2-us" / f"{map_id}-0.png").convert("RGBA"))
        checked += 1
        if got.shape != want.shape or not (got == want).all():
            bad.append(map_id)
    assert checked >= 3, f"only {checked} maps compared"
    assert not bad, f"{len(bad)} of {checked} PNGs changed: {bad}"


@pytest.mark.skipif(not (CACHE / "SHA").is_file(),
                    reason="no local/pret-cache-platinum")
def test_the_pitched_atlas_says_where_every_walked_tile_is_and_it_is_on_the_map():
    """The end-to-end claim: matrix + pad + heights put a route tile inside the
    picture the renderer wrote, for every tile a real run stood on."""
    import render_dsmaps as R

    d = R.Decomp(offline=True)
    cam = C.camera_for("pitched", 16)
    walked = R.route_tiles("platinum-us")
    ids, _ = R.placeable(d, R.observed_maps("platinum-us"))
    art = R.Field3D(d, tile_px=16, cam=cam)
    seen = 0
    for map_id in ids:
        r = R.render_map(d, map_id, art, cam=cam)
        if r.frame is None:
            continue
        px = R.full_pixels(r, 16)
        for x, y in walked.get(map_id, []):
            tx, ty = x - r.ox, y - r.oy
            h = float(r.heights[ty, tx])
            fx, fy = r.frame.tile_px(tx + 0.5, ty + 0.5, h)
            assert 0 <= fx < px.shape[1] and 0 <= fy < px.shape[0], (
                f"{r.key} tile {x},{y} projects to {fx:.1f},{fy:.1f} outside "
                f"{px.shape[1]}x{px.shape[0]}")
            seen += 1
    assert seen > 1000, f"only {seen} walked tiles checked"
