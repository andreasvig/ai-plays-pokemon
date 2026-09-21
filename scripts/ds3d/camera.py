"""Where a tile lands on the picture, under the camera the picture was drawn with.

Andreas, 2026-09-21, having looked at the straight-down DS atlases:

    "for gen 4 we shoudl defenlty not haev top down view, pelase make it teh
     real game like view."
    "both teh gen 4 and 5 views are actually really bad , bith are top down
     whcih dsont feel right"

So the DS tiers render at the field camera's own PITCH. `scene.py`'s docstring
and `render_sandgem.py`'s argue for straight down because an angled camera
z-buffers and a building therefore hides the route behind it; that argument
lost, and the occlusion it names is real and is measured rather than denied
(see `artifacts/game-map-render/notes/camera.md`).

WHICH ANGLED CAMERA — pitched orthographic, not the game's perspective one.

The field camera is a perspective camera with a half-FOV of 8.09 degrees at a
distance of 666.92 (`CAMERA_TYPE_DEFAULT`), and it frames ONE 32x32 chunk.
"Nearly orthographic" was the guess going in and the measurement says
otherwise: over a single chunk one tile is **13.26 px at the far edge and
19.52 px at the near one — 1.47x** — and the picture departs from its own
best-fit affine by a median 14.97 px (p95 40.8, max 62.3), rising to 22.7 /
74.1 / 115.0 px on 64x32 Route 201. It is a homography, not a rectangle.

That is the number that chose the pitch, and it costs three things a MAP
needs:

  * one tile is one size everywhere. Under the game's own camera the route's
    cable would be 1.47x fatter at the bottom of the same picture than at the
    top, and so would every texel of artwork.
  * the world-to-screen map stays AFFINE — a 2x3 matrix the browser carries in
    the atlas and inverts, instead of a homography plus a divide per point.
  * the canvas stays the map. Sandgem's 512x512-unit chunk needs a 900x800
    perspective canvas, 2.75x the pixels with 44% of them empty, because the
    map lands as a keystoned trapezoid; pitched it is 520x470.

The pictures are `artifacts/game-map-render/camera/`, at real size and with a
real run's route on them.

THE FRAME, once, because everything downstream assumes it.

World axes are `scene.py`'s: +x east, +y up, +z south, 16 units to a tile. A
`Camera` turns a world point into a PNG pixel with no divide:

    px = (X - x0) * s + pad_l
    py = (Z - z0) * s * sin(pitch) - Y * s * cos(pitch) + pad_t

`s` is `tile_px / TILE` pixels per world unit, `pitch` is 90 for straight
down (`sin` 1, `cos` 0 — the old projection exactly, which is why the topdown
tier keeps rendering byte-identical pixels through this module).

Ground is `Y = 0`, so a tile at altitude h is lifted `h * s * cos(pitch)`
pixels UP the picture. That is the whole of the height problem: the route is
drawn on the ground plane, and on a map with cliffs the ground plane is not
where the ground is. `ground_heights` samples the terrain mesh for the real
answer, and the atlas ships it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .scene import CAM_HALF_FOV, CAM_PITCH, DS_SCREEN_H, TILE

# The depth key is `base - (Y sin + Z cos)`, and the base is not free.
# `raster.draw_tri` interpolates 1/w, which is right for a perspective divide
# and wrong for an orthographic one; the texture error it makes goes as
# (spread/base)^2.
#
# Straight down the spread is the scene's HEIGHT, a hundred units or so, and
# `scene.ortho_projector` has always used 1000. Keeping that number is what
# makes `--camera topdown` reproduce the shipped atlas BYTE FOR BYTE — checked,
# and it is not a rounding-level agreement: at 1e5 instead of 1000 Sandgem Town
# comes out 6.6% different, max channel 138, because the old hyperbolic UV
# interpolation is baked into the pixels that shipped.
#
# Pitched, the spread is the scene's DEPTH — 790 units on a 96x32 map, and a
# base of 1000 would leave w as low as 210 and, on anything longer, negative.
# So the pitched tier takes a base far above any map: 1e5 puts the texture
# error at 2e-4 of a texel, under the rasteriser's own nearest-neighbour
# sampling.
ORTHO_DEPTH_BASE = 1000.0       # scene.ortho_projector's, kept for the control
DEPTH_BASE = 1.0e5

KINDS = ("topdown", "pitched")


@dataclass(frozen=True)
class Camera:
    """One projection, and the pixel grid it draws into.

    `kind` is what the atlas publishes and what the viewer switches on.
    `topdown` is pitch 90 and is bit-for-bit the projection that shipped before
    this module existed.
    """

    kind: str = "topdown"
    tile_px: int = 16
    pitch_deg: float = 90.0

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown camera kind {self.kind!r}; expected one of {KINDS}")
        if self.kind == "topdown" and self.pitch_deg != 90.0:
            raise ValueError("a topdown camera is pitch 90 by definition")
        if not 1.0 <= self.pitch_deg <= 90.0:
            raise ValueError(f"pitch {self.pitch_deg} is not a camera looking at the ground")

    # -- the three numbers the whole module is made of ------------------------

    @property
    def unit_px(self) -> float:
        """PNG pixels per world unit."""
        return self.tile_px / TILE

    @property
    def sin_p(self) -> float:
        """1.0 EXACTLY at pitch 90, not `sin(pi/2)` — see `cos_p`."""
        return 1.0 if self.pitch_deg == 90.0 else math.sin(math.radians(self.pitch_deg))

    @property
    def cos_p(self) -> float:
        """0.0 EXACTLY at pitch 90.

        `math.cos(math.radians(90))` is 6.1e-17, not zero, and straight down is
        the tier whose pixels already shipped. A residue that small changes
        nothing you could see and everything about whether the control can say
        "byte-identical" — which is the only way to know the switch back is a
        switch back and not a re-render.
        """
        return 0.0 if self.pitch_deg == 90.0 else math.cos(math.radians(self.pitch_deg))

    @property
    def depth_base(self) -> float:
        return ORTHO_DEPTH_BASE if self.kind == "topdown" else DEPTH_BASE

    @property
    def tile_px_x(self) -> float:
        """PNG pixels across one tile. `tile_px`, always — x is not foreshortened."""
        return float(self.tile_px)

    @property
    def tile_px_y(self) -> float:
        """PNG pixels DOWN one tile of ground. Foreshortened by sin(pitch)."""
        return self.tile_px * self.sin_p

    @property
    def height_px(self) -> float:
        """PNG pixels a world unit of altitude lifts a point UP the picture."""
        return self.unit_px * self.cos_p

    # -- tile <-> pixel, which is what the atlas has to carry -----------------

    def tile_to_px(self, tx: float, ty: float, height: float = 0.0,
                   pad: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
        """Map-local tile CORNER (tx, ty) at world altitude `height` -> PNG pixel.

        Tile centres are `tile_to_px(tx + .5, ty + .5)`; the half-tile is the
        caller's, exactly as it is in `RouteMap.place`.
        """
        return (tx * self.tile_px_x + pad[0],
                ty * self.tile_px_y - height * self.height_px + pad[1])

    def px_to_tile(self, px: float, py: float, height: float = 0.0,
                   pad: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
        """The inverse, on the plane `height`. Affine, so this is exact."""
        return ((px - pad[0]) / self.tile_px_x,
                (py - pad[1] + height * self.height_px) / self.tile_px_y)

    def matrix(self) -> list[float]:
        """The 2x3 affine, in the order the atlas publishes and the viewer reads.

        `[a, b, c, d, e, f]` with `px = a*tx + c*ty + e`, `py = b*tx + d*ty + f`
        — canvas `setTransform` order, and `e`/`f` are the pad. Height is NOT in
        here: it is a third column the viewer applies separately, because a
        tile's altitude comes from a different field.
        """
        return [self.tile_px_x, 0.0, 0.0, self.tile_px_y, 0.0, 0.0]

    # -- the rasteriser's projector -------------------------------------------

    def projector(self, ss: int = 1, pad: tuple[float, float] = (0.0, 0.0)):
        """`(N,3) world -> (N,3) screen x, y, depth`, at `ss` samples per pixel.

        World (x0, z0) = (0, 0) is PNG pixel `pad`, so the caller shifts the
        scene rather than the camera and a chunk keeps the origin `field.py`
        gives it.
        """
        s = self.unit_px * ss
        sin_p, cos_p = self.sin_p, self.cos_p
        base = self.depth_base
        ox, oy = pad[0] * ss, pad[1] * ss

        def proj(pts: np.ndarray) -> np.ndarray:
            x = pts[:, 0] * s + ox
            y = (pts[:, 2] * sin_p - pts[:, 1] * cos_p) * s + oy
            depth = base - (pts[:, 1] * sin_p + pts[:, 2] * cos_p)
            return np.stack([x, y, depth], 1)

        return proj


TOPDOWN = Camera("topdown", 16, 90.0)
#: The field camera's own pitch. `scene.CAM_PITCH` is the angle above the
#: horizon that `src/overlay005/field_camera.c` writes for CAMERA_TYPE_DEFAULT.
PITCHED = Camera("pitched", 16, CAM_PITCH)


def camera_for(kind: str, tile_px: int) -> Camera:
    """The camera a `--camera` flag names, at this tier's pixel density."""
    if kind == "topdown":
        return Camera("topdown", tile_px, 90.0)
    if kind == "pitched":
        return Camera("pitched", tile_px, CAM_PITCH)
    raise SystemExit(f"--camera {kind}: expected one of {', '.join(KINDS)}")


# ---------------------------------------------------------------- lighting

#: The direction TOWARD the light, in world axes (+x east, +y up, +z south).
#: High, and from the north-west, so it comes over the viewer's left shoulder
#: — which is the direction Pokémon's own building art is drawn lit from.
LIGHT_DIR = np.array([-0.38, 0.88, -0.28])
#: What a surface facing away from the light keeps. Not zero: the DS field has
#: no shadowing model and a black north face would read as a hole in a roof.
AMBIENT = 0.74


def directional_shade(light_dir=LIGHT_DIR, ambient: float = AMBIENT):
    """A Lambert `normal -> brightness`, normalised so flat GROUND is 1.0.

    The problem it solves, which only exists once the camera is angled: gen
    4's terrain and props are drawn with a slope shade that depends on how far
    from vertical a face is (`scene._slope_shade`), so the four slopes of a hip
    roof — same angle, four different directions — all come out the same
    colour and the ridge between them is whatever the texture happens to show.
    Straight down you never see more than one slope at a time and it does not
    matter. Pitched you see three, and a roof reads as a flat teal lozenge.

    Normalised on the UP direction rather than on the light's own maximum so
    that flat ground is exactly 1.0: the ground is most of every map, this
    replaces a shade that gave it 1.0, and a lighting model that darkened
    every map by 3% would be a recolour of all seven games wearing a roof fix.

    OFF BY DEFAULT (`--light` on both renderers). It repaints every DS map, so
    it is Andreas's call with the picture in front of him, not mine.
    """
    l = np.asarray(light_dir, np.float64)
    l = l / np.linalg.norm(l)
    up = float(l[1])

    def shade(n):
        # Two-sided: back-face culling is not implemented (every gen-4 material
        # declares front-face only, and nothing enforces it), so a polygon can
        # reach here wound away from the camera. Lighting it by the signed
        # normal would paint one arbitrary half of the props black.
        d = abs(float(np.dot(n, l)))
        return min(1.0, ambient + (1.0 - ambient) * d / max(up, 1e-6))

    return shade


# ---------------------------------------------------------------- the canvas

@dataclass(frozen=True)
class Frame:
    """A camera plus the PNG it fills: how big, and where the ground sits in it.

    `pad` is the margin in PNG pixels around the map's own ground rectangle.
    It is not a guess and not a constant: a pitched camera lifts a roof up the
    picture and a tree's canopy off the edge of its own tile, so the pad is
    measured from the scene's projected bounding box and then DECLARED, per
    map, in the atlas. The viewer anchors the image by it.
    """

    cam: Camera
    tiles_w: int
    tiles_h: int
    pad: tuple[int, int, int, int] = (0, 0, 0, 0)     # left, top, right, bottom

    @property
    def ground_w(self) -> float:
        return self.tiles_w * self.cam.tile_px_x

    @property
    def ground_h(self) -> float:
        return self.tiles_h * self.cam.tile_px_y

    @property
    def width(self) -> int:
        return int(round(self.ground_w)) + self.pad[0] + self.pad[2]

    @property
    def height(self) -> int:
        return int(round(self.ground_h)) + self.pad[1] + self.pad[3]

    @property
    def origin(self) -> tuple[float, float]:
        """PNG pixel of the ground rectangle's top-left corner."""
        return (float(self.pad[0]), float(self.pad[1]))

    def tile_px(self, tx: float, ty: float, height: float = 0.0) -> tuple[float, float]:
        return self.cam.tile_to_px(tx, ty, height, self.origin)

    def crop(self, bx0: int, by0: int, w: int, h: int) -> "Frame":
        """The sub-window starting at tile (bx0, by0), keeping the same pad.

        An interior is cropped to the room's own box (`map_window`), and the
        crop is stated in TILES on the ground plane. The pad travels with it:
        a wall behind the room's top row still has to be in the picture.
        """
        return Frame(self.cam, w, h, self.pad)

    def crop_box(self, bx0: int, by0: int, w: int, h: int) -> tuple[int, int, int, int]:
        """`(x0, y0, x1, y1)` in this frame's PNG pixels, for `crop(...)`'s window."""
        x0 = int(round(bx0 * self.cam.tile_px_x))
        y0 = int(round(by0 * self.cam.tile_px_y))
        return (x0, y0,
                x0 + int(round(w * self.cam.tile_px_x)) + self.pad[0] + self.pad[2],
                y0 + int(round(h * self.cam.tile_px_y)) + self.pad[1] + self.pad[3])

    def cut(self, rgba: np.ndarray, bx0: int, by0: int, w: int, h: int) -> np.ndarray:
        """`crop_box` applied, and PADDED with transparency where it runs off.

        The box can leave the rendered canvas: a room cropped against the
        right-hand edge of its own block still wants its pad, and there are no
        pixels there. Slicing alone would silently hand back a SMALLER image
        than the frame declares, which is the shape of bug that ships as a map
        drawn one pixel column short and nobody sees it.
        """
        x0, y0, x1, y1 = self.crop_box(bx0, by0, w, h)
        out = np.zeros((y1 - y0, x1 - x0, 4), rgba.dtype)
        sx0, sy0 = max(x0, 0), max(y0, 0)
        sx1, sy1 = min(x1, rgba.shape[1]), min(y1, rgba.shape[0])
        if sx1 > sx0 and sy1 > sy0:
            out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = rgba[sy0:sy1, sx0:sx1]
        return out


#: A pad is measured, but not without a ceiling: one bad placement (a prop the
#: cartridge scales by 40, a model whose origin is in another chunk) would
#: otherwise size the canvas off it and every map would ship a mostly-empty
#: PNG. Eight tiles up is taller than anything gen 4 or gen 5 puts on a map —
#: measured at 3.4 tiles for Platinum's tallest, Jubilife's buildings.
#: Horizontally the cap is moot — see `measure_pad`, which forces those to
#: zero — and it stays as the belt to that braces.
MAX_PAD_TILES = (2.0, 8.0, 2.0, 2.0)


def in_footprint(tris, tiles_w: int, tiles_h: int, origin=(0.0, 0.0)):
    """The triangles with any part of their x/z footprint inside the map's cell.

    A GEN-4 TERRAIN MODEL IS NOT BOUNDED BY ITS OWN 32x32 CELL. Measured:
    Twinleaf Town's land block reaches 36 world units — 2.25 tiles — past its
    cell to the south-east and 16 units past it to the north-west, and 20 of
    its triangles lie WHOLLY outside; Route 201's reach one tile past. They
    are the map's own terrain model, not a neighbour's: in the game the
    adjacent cell draws over them, and in a map-local render there is no
    adjacent cell, so they hang in the air.

    Straight down they were never a problem — a triangle outside the cell
    projects outside the canvas and is clipped for free. Pitched, a pad opens
    a band around the canvas and they appear in it: a two-tile sliver of
    forest floating off Twinleaf's bottom-right corner, attached to nothing.

    Dropped rather than clipped to the boundary, which keeps a triangle that
    STRADDLES the edge — a tree half in the map — drawn exactly as the
    straight-down tier draws it, cut off at the frame.

    A FACE LYING IN THE BOUNDARY PLANE IS INSIDE, and needs saying because
    the obvious test gets it wrong. "Touching is outside" (`max <= lo`) is
    right for a triangle with real extent on that axis: a quad from z = 512
    to z = 528 on a cell that ends at 512 has no area inside it, and keeping
    it put 178 pixels of the next cell's grass into Route 201 — see
    `test_geometry_wholly_outside_the_cell_is_dropped_rather_than_padded_for`.
    But a VERTICAL face has no extent on one axis at all: its footprint is a
    line, and a wall standing on the cell's own northern edge has
    `z.min() == z.max() == 0`, which the same test reads as "outside" and
    throws the wall away. Black 2's map 438 lost the two triangles of its
    reception counter to exactly that, leaving a hole through the room and a
    walked tile standing on nothing.

    So the rule is per axis: a footprint with extent must OVERLAP the cell, a
    degenerate one need only lie within it. Equality is exact on purpose —
    the case is a face whose vertices share a coordinate literally, not one
    that lands near a boundary, and a tolerance here would start eating the
    Route 201 sliver back in from the other side.
    """
    gx = tiles_w * TILE + origin[0]
    gz = tiles_h * TILE + origin[1]

    def outside(lo_v: float, hi_v: float, lo: float, hi: float) -> bool:
        if lo_v == hi_v:                    # a line, not a box: edge-on face
            return lo_v < lo or lo_v > hi
        return hi_v <= lo or lo_v >= hi

    keep = []
    for tri in tris:
        v = tri[0]
        if outside(v[:, 0].min(), v[:, 0].max(), origin[0], gx):
            continue
        if outside(v[:, 2].min(), v[:, 2].max(), origin[1], gz):
            continue
        keep.append(tri)
    return keep


def measure_pad(sc, cam: Camera, tiles_w: int, tiles_h: int) -> tuple[int, int, int, int]:
    """How far the map's own ground rectangle has to grow to hold its geometry.

    Straight down it is zero by construction — nothing is outside its own
    footprint when the footprint IS the projection. Pitched, a roof rises out
    of the top of the ground rect and the picture has to hold it or a town is
    drawn with its rooftops sliced off.

    **Horizontally the answer is always zero, and that is a proof rather than
    a measurement.** The projection has no yaw: `px = tile_px * tx`, with no
    `ty` term and no divide. Nothing whose x lies inside the map can project
    outside it, so a horizontal pad has nothing legitimate to hold — every
    pixel it ever held was geometry outside the cell (see `in_footprint`).
    Twinleaf shipped a 32 px right pad holding 2,268 opaque pixels of exactly
    that, and it read as a sliver of forest hanging off the corner.

    **Vertically it is measured, and only from geometry the map owns.** The
    bound is taken over vertices INSIDE the cell rectangle, so a tree one tile
    south of the map cannot buy a band for itself at the bottom — while a
    pond bed at -16 world units, which is the map's own and does project
    down, still can.
    """
    if not sc.tris or cam.kind == "topdown":
        return (0, 0, 0, 0)
    proj = cam.projector(1, (0.0, 0.0))
    gx, gz = tiles_w * TILE, tiles_h * TILE
    lo = np.inf
    hi = -np.inf
    for v, _uv, _tex, _wrap in in_footprint(sc.tris, tiles_w, tiles_h):
        inside = ((v[:, 0] >= -1e-6) & (v[:, 0] <= gx + 1e-6)
                  & (v[:, 2] >= -1e-6) & (v[:, 2] <= gz + 1e-6))
        # A triangle covering the cell with every vertex outside it would
        # otherwise contribute nothing; take all three, which is the
        # conservative direction (a pad too large shows background, a pad too
        # small cuts artwork off).
        py = proj(v)[inside if inside.any() else slice(None), 1]
        lo = min(lo, float(py.min()))
        hi = max(hi, float(py.max()))
    if not np.isfinite(lo):
        return (0, 0, 0, 0)
    caps = [t * cam.tile_px for t in MAX_PAD_TILES]
    raw = (0.0, max(0.0, -lo), 0.0, max(0.0, hi - tiles_h * cam.tile_px_y))
    return tuple(int(min(math.ceil(r), c)) for r, c in zip(raw, caps))


def pixels(sc, cam: Camera, tiles_w: int, tiles_h: int, ss: int,
           pad: tuple[int, int, int, int] | None = None,
           light=None) -> tuple[np.ndarray, Frame]:
    """Rasterise `sc` under `cam`. Returns the RGBA image and the frame it fills.

    Only the geometry inside the map's own cell is drawn (`in_footprint`).
    Straight down that is a no-op — a triangle outside the cell projects
    outside the canvas and the rasteriser already clips it — which is what
    keeps `--camera topdown` byte-identical to the shipped atlas.
    """
    from . import scene as _scene

    tris = in_footprint(sc.tris, tiles_w, tiles_h)
    if pad is None:
        pad = measure_pad(sc, cam, tiles_w, tiles_h)
    frame = Frame(cam, tiles_w, tiles_h, pad)
    shim = type("Clipped", (), {"tris": tris})()
    fb = _scene.render(shim, cam.projector(ss, frame.origin),
                       frame.width * ss, frame.height * ss, light=light)
    return _scene.downsample(fb, ss), frame


def tile_coverage(rgba: np.ndarray, frame: "Frame",
                  heights: np.ndarray | None = None) -> np.ndarray:
    """Mean alpha over each tile's own box in the picture, `(tiles_h, tiles_w)`.

    Straight down this is the plain `reshape(h, t, w, t).mean((1, 3))` the
    check used before there was a camera — a tile's box IS its pixels. Pitched
    it is the same box, foreshortened and lifted to the tile's own ground
    height, which is where the route is drawn and therefore the place worth
    asking about.
    """
    cam = frame.cam
    out = np.zeros((frame.tiles_h, frame.tiles_w), np.float64)
    a = rgba[..., 3]
    for ty in range(frame.tiles_h):
        for tx in range(frame.tiles_w):
            h = 0.0 if heights is None else float(heights[ty, tx])
            x0, y0 = frame.tile_px(tx, ty, h)
            x1 = int(round(x0 + cam.tile_px_x))
            y1 = int(round(y0 + cam.tile_px_y))
            x0i, y0i = int(round(x0)), int(round(y0))
            box = a[max(y0i, 0):max(y1, 0), max(x0i, 0):max(x1, 0)]
            out[ty, tx] = box.mean() if box.size else 0.0
    return out


def stretch(per_tile: np.ndarray, frame: "Frame") -> np.ndarray:
    """One flat block per tile, blown up to fill `frame`'s ground rectangle.

    The collision tier is an array of tiles, not a mesh, so it has no camera of
    its own — but it has to land in the SAME frame as the 3D tier or a game
    that falls back for one map draws that map's route somewhere else. Nearest
    neighbour, which is what the flat tier means: a block, not a gradient.
    """
    h, w = per_tile.shape[:2]
    gw = int(round(w * frame.cam.tile_px_x))
    gh = int(round(h * frame.cam.tile_px_y))
    ys = np.minimum((np.arange(gh) * h) // max(gh, 1), h - 1)
    xs = np.minimum((np.arange(gw) * w) // max(gw, 1), w - 1)
    out = np.zeros((frame.height, frame.width, 4), per_tile.dtype)
    t, l = frame.pad[1], frame.pad[0]
    out[t:t + gh, l:l + gw] = per_tile[np.ix_(ys, xs)]
    return out


# ---------------------------------------------------------------- height

def terrain_only(sc, spans: list[tuple[int, int]]):
    """The terrain triangles of a multi-chunk scene, in scene order.

    `spans` is `(start, count)` per chunk, which `field.add_chunk`'s
    `terrain_tris` gives the caller for free. Kept separate from
    `ground_heights` so the slicing rule is stated once and testable without a
    cartridge.
    """
    out = []
    for start, count in spans:
        out.extend(sc.tris[start:start + count])
    return out


def ground_heights(tris, tiles_w: int, tiles_h: int, origin=(0.0, 0.0)) -> np.ndarray:
    """World altitude of the ground under each tile CENTRE, as `(tiles_h, tiles_w)`.

    A ray straight down at the tile's centre, keeping the HIGHEST triangle it
    hits. The mesh's own answer, which is the only one there is: the collision
    grid carries no elevation, and gen 4's BDHC — the table the game itself
    reads a walking height out of — is not decoded here.

    `tris` is a TRIANGLE LIST, not a scene, because the caller has to hand in
    the TERRAIN triangles alone. Ask a whole scene and the ray hits the roof of
    the house standing on the tile: Sandgem Town's ground is 16 world units
    everywhere and its answer with the props in is 108, which is a rooftop.
    `field.add_chunk` returns each chunk's terrain count and its triangles come
    first WITHIN that chunk, so a multi-chunk map has to slice per chunk —
    `terrain_only` does it.

    NaN where nothing is under the tile at all — a matrix cell no map claims.
    """
    out = np.full((tiles_h, tiles_w), np.nan)
    if not len(tris):
        return out
    cx = (np.arange(tiles_w) + 0.5) * TILE + origin[0]
    cz = (np.arange(tiles_h) + 0.5) * TILE + origin[1]
    gx, gz = np.meshgrid(cx, cz)
    px, pz = gx.ravel(), gz.ravel()
    best = np.full(px.shape, -np.inf)
    for v, _uv, _tex, _wrap in tris:
        x0, x1, x2 = v[:, 0]
        z0, z1, z2 = v[:, 2]
        den = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2)
        if abs(den) < 1e-9:
            continue
        lo_x, hi_x = min(x0, x1, x2), max(x0, x1, x2)
        lo_z, hi_z = min(z0, z1, z2), max(z0, z1, z2)
        cand = (px >= lo_x - 1e-6) & (px <= hi_x + 1e-6) & (pz >= lo_z - 1e-6) & (pz <= hi_z + 1e-6)
        if not cand.any():
            continue
        qx, qz = px[cand], pz[cand]
        l0 = ((z1 - z2) * (qx - x2) + (x2 - x1) * (qz - z2)) / den
        l1 = ((z2 - z0) * (qx - x2) + (x0 - x2) * (qz - z2)) / den
        l2 = 1.0 - l0 - l1
        hit = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not hit.any():
            continue
        y = (l0 * v[0, 1] + l1 * v[1, 1] + l2 * v[2, 1])[hit]
        idx = np.where(cand)[0][hit]
        np.maximum.at(best, idx, y)
    best = best.reshape(tiles_h, tiles_w)
    out[np.isfinite(best)] = best[np.isfinite(best)]
    return out


#: Altitude ships in WORLD UNITS, rounded to whole ones, because gen 4's own
#: answers already are whole: the walkable ground of the eleven Platinum maps
#: our runs have entered reads 8, 12, 16, 17 and -2, never a fraction. Half a
#: unit of rounding is 0.26 px at 16 px a tile. 16 units is one tile, which is
#: also how far a route sits off the picture if the grid is ignored entirely:
#: a gen-4 outdoor map's ground plane is y = 16, not y = 0.


def rle(grid: list[list[int]]) -> list[int]:
    """Row-major run-length encoding, `[count, value, count, value, ...]`.

    A 96x32 map is 3072 tiles and its altitude is piecewise constant across
    whole terraces, so the raw array is ~12 KB of JSON and the runs are a few
    hundred bytes. The viewer decodes it once, on load.
    """
    out: list[int] = []
    for row in grid:
        for v in row:
            if out and out[-1] == v:
                out[-2] += 1
            else:
                out.extend((1, v))
    return out


def walkable_plane(heights: np.ndarray, walkable: np.ndarray) -> np.ndarray:
    """The ground a ROUTE can stand on, with everything else filled in flat.

    Two jobs, and both of them are corrections to the naive answer:

    * a ray straight down hits the TOP of a tree canopy, and gen 4 models a lot
      of its foliage in the terrain mesh itself. Sandgem Town's tiles report
      four distinct heights, of which 52.7 and 24.6 are the same clump of trees
      seen from above; its WALKABLE tiles report exactly one, 16.0.
    * a flat fill over the rest collapses the run-length encoding. The grid
      only has to be right where a route, a door marker or a battle icon can
      be, and the fill is the map's own median walkable height, so a viewer
      that reads a blocked tile gets the plane the map sits on rather than a
      rooftop.
    """
    ok = walkable & np.isfinite(heights)
    if not ok.any():
        return np.zeros_like(heights)
    return np.where(ok, heights, float(np.median(heights[ok])))


def atlas_camera(cam: Camera) -> dict:
    """What the atlas publishes about the projection, and nothing more.

    `matrix` is the authority for both pixel scales — a viewer reads
    `matrix[0]` and `matrix[3]`, never `tile_px` twice — and `height_px` is the
    third column it cannot hold, because a tile's altitude comes from the
    per-map `heights` grid rather than from its coordinates.
    """
    return {
        "kind": cam.kind,
        "pitch_deg": cam.pitch_deg,
        "matrix": [round(v, 6) for v in cam.matrix()],
        "height_px": round(cam.height_px, 6),
        "height_unit": "world",
    }


def atlas_heights(plane: np.ndarray) -> list[int]:
    """The per-tile ground grid as the atlas ships it: RLE of world units."""
    return rle([[int(round(v)) for v in row] for row in plane])


def unrle(runs: list[int], w: int, h: int) -> list[list[int]]:
    """`rle`'s inverse, for the tests that prove the pair is one thing."""
    flat: list[int] = []
    for i in range(0, len(runs), 2):
        flat.extend([runs[i + 1]] * runs[i])
    if len(flat) != w * h:
        raise ValueError(f"run length {len(flat)} is not {w}x{h}")
    return [flat[r * w:(r + 1) * w] for r in range(h)]


# ---------------------------------------------------------------- the rejected one

def perspective_projector(w: int, h: int, ss: int = 1, *, centre=(0.0, 0.0),
                          dist: float = None, pitch: float = CAM_PITCH,
                          px_per_unit: float = 1.0):
    """The game's own perspective field camera, for the comparison sheet only.

    Kept here rather than in `scene.py` because the comparison that rejected it
    is this module's evidence, and the number it produced is in the notes. It
    is `scene.game_cam_projector` with the scene's own centring folded in, so
    one map can be rendered both ways from one scene.
    """
    from .scene import CAM_DIST

    dist = CAM_DIST if dist is None else dist
    focal = (DS_SCREEN_H / 2) / math.tan(math.radians(CAM_HALF_FOV)) * px_per_unit * ss
    p = math.radians(pitch)
    cam = np.array([0.0, dist * math.sin(p), dist * math.cos(p)])
    fwd = -cam / np.linalg.norm(cam)
    right = np.array([1.0, 0.0, 0.0])
    up = np.cross(right, fwd)
    rot = np.stack([right, up, -fwd])
    shift = np.array([centre[0], 0.0, centre[1]])

    def proj(pts: np.ndarray) -> np.ndarray:
        v = ((pts - shift) - cam) @ rot.T
        z = np.maximum(-v[:, 2], 1e-3)
        return np.stack([w * ss / 2 + focal * v[:, 0] / z,
                         h * ss / 2 - focal * v[:, 1] / z, z], 1)

    return proj
