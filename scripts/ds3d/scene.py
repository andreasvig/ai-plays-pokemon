"""A triangle soup in world space, and the two ways to look at it.

The one implementation of the drawing loop. `scripts/render_dsmaps.py` (the
shipping map tier) and `v2-experiments/render_sandgem.py` (the spike that
proved it possible, kept for hero images) both call it, so there is no second
rasteriser to drift from the first.

WORLD AXES, once, because everything downstream assumes them:
  +x east, +y up, +z south. One tile is 16 units. A 32x32 chunk is 512 units
  square and its own model is centred on the origin, so chunk-local tile (0, 0)
  has its centre at (-248, -248) — see `tile_world`.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import raster
from .dlist import decode

TILE = 16.0                 # world units per tile
CHUNK = 32                  # tiles per land block
SPAN = TILE * CHUNK         # 512 world units

# CAMERA_TYPE_DEFAULT, verbatim from src/overlay005/field_camera.c. Not the map
# tier — an angled camera z-buffers, so a building HIDES the route behind it —
# but kept here because a hero image wants the game's own framing.
CAM_PITCH = 59.051513671875
CAM_HALF_FOV = 8.0914306640625
CAM_DIST = 666.922119140625
DS_SCREEN_H = 192


def tile_world(tx: float, ty: float, ox: float = 0.0, oz: float = 0.0):
    """Centre of chunk-local tile (tx, ty), in world units."""
    return (-SPAN / 2 + (tx + 0.5) * TILE + ox, -SPAN / 2 + (ty + 0.5) * TILE + oz)


class Scene:
    """`(verts (3,3) world, uv (3,2) texels, tex RGBA, wrap flags)` per triangle."""

    def __init__(self) -> None:
        self.tris: list[tuple] = []

    def add_model(self, model, texset, origin=(0, 0, 0), yaw: float = 0.0,
                  place_scale=(1.0, 1.0, 1.0)) -> int:
        """One NSBMD model, placed. Returns the triangle count it contributed.

        A material joins to the texture set BY NAME: a field terrain model
        carries no texture data of its own, and the name in the model is the
        name in the separate NSBTX.

        `place_scale` is the placement's own scale vector, which the field
        renderer passes to `Easy3D_DrawRenderObj`; `yaw` is NOT what it passes
        for a map prop — see the note in `field.py`.
        """
        scale = model.up_scale
        ps = np.asarray(place_scale, np.float64)
        pairs = model.texture_pairs()
        cy, sy = np.cos(yaw), np.sin(yaw)
        before = len(self.tris)
        for mi, pi in model.bind_draw():
            tname, pname = pairs.get(mi, (None, None))
            if tname is None or tname not in texset.textures:
                # An untextured material is a FLAT COLOUR, not a hole. Drawn as
                # a 1x1 texture of its own diffuse colour, which is where the
                # game gets it from too.
                tex = np.zeros((1, 1, 4), np.uint8)
                tex[0, 0, :3] = model.material_diffuse(mi)
                tex[0, 0, 3] = 255
                wrap = (1, 1, 0, 0)
            else:
                tex = texset.image(tname, pname)
                p = texset.tex_params(tname)
                wrap = ((p >> 16) & 1, (p >> 17) & 1, (p >> 18) & 1, (p >> 19) & 1)
            tris, _ = decode(model.piece_dl(pi))
            for t in tris:
                v = np.array([[a[0], a[1], a[2]] for a in t], np.float64) * scale * ps
                if yaw:
                    x, z = v[:, 0].copy(), v[:, 2].copy()
                    v[:, 0] = x * cy + z * sy
                    v[:, 2] = -x * sy + z * cy
                v += np.array(origin, np.float64)
                uv = np.array([[a[3], a[4]] for a in t], np.float64)
                self.tris.append((v, uv, tex, wrap))
        return len(self.tris) - before

    def add_quad(self, corners, color) -> None:
        """Four world points, as a flat-coloured 1x1 texture."""
        tex = np.zeros((1, 1, 4), np.uint8)
        tex[0, 0] = color
        uv = np.array([[0.5, 0.5]] * 3)
        a, b, c, d = [np.array(p, np.float64) for p in corners]
        for t in ((a, b, c), (a, c, d)):
            self.tris.append((np.array(t), uv, tex, (1, 1, 0, 0)))


def sample_wrapped(tex, u, v, wrap):
    """Nearest-neighbour sample honouring the NDS repeat/flip bits."""
    h, w = tex.shape[:2]
    rs, rt, fs, ft = wrap

    def axis(c, n, rep, flip):
        if rep:
            if flip:
                m = np.mod(np.floor(c).astype(np.int64), 2 * n)
                return np.where(m < n, m, 2 * n - 1 - m)
            return np.mod(np.floor(c).astype(np.int64), n)
        return np.clip(np.floor(c).astype(np.int64), 0, n - 1)

    return tex[axis(v, h, rt, ft), axis(u, w, rs, fs)]


def is_translucent(tex) -> bool:
    """True when the texture carries a partial alpha, not just on/off.

    The NDS alpha-tests a texel at 0 and blends everything between, and Gen 4
    uses that for exactly one thing that matters to a top-down map: water. Twinleaf
    Town's pond is a plane of alpha-36 texels over its own dark bed, so written
    opaquely it is a black rectangle in the middle of the town, and written
    blended it is a pond.
    """
    a = tex[..., 3]
    return bool(((a > 8) & (a < 250)).any())


def draw_tri(fb, scr, uv, tex, wrap, shade, *, blend: bool = False) -> None:
    x, y, wv = scr[:, 0], scr[:, 1], scr[:, 2]
    minx = max(int(np.floor(x.min())), 0)
    maxx = min(int(np.ceil(x.max())) + 1, fb.w)
    miny = max(int(np.floor(y.min())), 0)
    maxy = min(int(np.ceil(y.max())) + 1, fb.h)
    if minx >= maxx or miny >= maxy:
        return
    area = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
    if abs(area) < 1e-12:
        return
    gx, gy = np.meshgrid(np.arange(minx, maxx) + 0.5, np.arange(miny, maxy) + 0.5)
    l2 = ((x[1] - x[0]) * (gy - y[0]) - (gx - x[0]) * (y[1] - y[0])) / area
    l1 = ((gx - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (gy - y[0])) / area
    l0 = 1.0 - l1 - l2
    inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
    if not inside.any():
        return
    invw = l0 / wv[0] + l1 / wv[1] + l2 / wv[2]
    depth = 1.0 / np.where(np.abs(invw) < 1e-12, 1e-12, invw)
    sub = fb.depth[miny:maxy, minx:maxx]
    closer = inside & (depth < sub)
    if not closer.any():
        return
    u = (l0 * uv[0, 0] / wv[0] + l1 * uv[1, 0] / wv[1] + l2 * uv[2, 0] / wv[2]) * depth
    v = (l0 * uv[0, 1] / wv[0] + l1 * uv[1, 1] / wv[1] + l2 * uv[2, 1] / wv[2]) * depth
    rgba = sample_wrapped(tex, u[closer], v[closer], wrap).astype(np.float32)
    keep = rgba[:, 3] > 8
    iy, ix = np.where(closer)
    iy, ix = iy[keep], ix[keep]
    rgba = rgba[keep]
    rgba[:, :3] *= shade
    if not blend:
        fb.color[miny + iy, minx + ix] = rgba
        fb.depth[miny + iy, minx + ix] = depth[closer][keep]
        return
    # Source-over, and NO depth write: a translucent polygon on the DS is tested
    # against the depth buffer and does not fill it, so the thing underneath is
    # still drawable and still shows through.
    dst = fb.color[miny + iy, minx + ix]
    sa = rgba[:, 3:4] / 255.0
    da = dst[:, 3:4] / 255.0
    oa = sa + da * (1.0 - sa)
    out = np.empty_like(dst)
    out[:, :3] = np.where(oa > 0,
                          (rgba[:, :3] * sa + dst[:, :3] * da * (1.0 - sa)) / np.where(oa > 0, oa, 1.0),
                          0.0)
    out[:, 3:4] = oa * 255.0
    fb.color[miny + iy, minx + ix] = out


def render(scene: Scene, project, w: int, h: int, bg=(0, 0, 0, 0)):
    """Rasterise into a fresh framebuffer. `project` maps (N,3) world -> screen.

    Two passes, opaque then translucent, which is the DS's own order: a
    translucent polygon drawn into the depth buffer would hide the geometry it
    is supposed to be seen through.
    """
    fb = raster.Framebuffer(w, h, bg)
    seen: dict[int, bool] = {}
    late = []
    for tri in scene.tris:
        tex = tri[2]
        soft = seen.get(id(tex))
        if soft is None:
            soft = seen[id(tex)] = is_translucent(tex)
        if soft:
            late.append(tri)
        else:
            _one(fb, project, tri, blend=False)
    for tri in late:
        _one(fb, project, tri, blend=True)
    return fb


def _one(fb, project, tri, *, blend: bool) -> None:
    v, uv, tex, wrap = tri
    n = np.cross(v[1] - v[0], v[2] - v[0])
    ln = np.linalg.norm(n)
    # A flat 0.8 on non-up-facing faces, in place of the game's directional
    # lighting: a roof and the wall under it are otherwise the same colour.
    shade = 1.0
    if ln > 1e-9:
        up = abs(n[1] / ln)
        shade = 1.0 if up > 0.8 else 0.80 + 0.20 * up
    draw_tri(fb, project(v), uv, tex, wrap, shade, blend=blend)


def ortho_projector(scale: float = 1.0, ox: float = 0.0, oz: float = 0.0):
    """Straight down. `scale` pixels per world unit; world y becomes depth.

    THE map tier's camera, and the reason it is: looking straight down, nothing
    can hide behind anything, so a route that runs past the back of a building
    is still on the picture. Screen (0, 0) is world (ox, oz).
    """
    def proj(pts):
        return np.stack([(pts[:, 0] - ox) * scale,
                         (pts[:, 2] - oz) * scale,
                         1000.0 - pts[:, 1]], 1)
    return proj


def game_cam_projector(w: int, h: int, ss: int = 1, dist: float = CAM_DIST,
                       pitch: float = CAM_PITCH, px_per_unit: float = 1.0):
    """The game's own field camera. Hero images only — see the note above."""
    focal = (DS_SCREEN_H / 2) / np.tan(np.radians(CAM_HALF_FOV))
    focal *= px_per_unit * ss
    p = np.radians(pitch)
    cam = np.array([0.0, dist * np.sin(p), dist * np.cos(p)])
    fwd = -cam / np.linalg.norm(cam)
    right = np.array([1.0, 0.0, 0.0])
    up = np.cross(right, fwd)
    R = np.stack([right, up, -fwd])

    def proj(pts):
        v = (pts - cam) @ R.T
        z = np.maximum(-v[:, 2], 1e-3)
        return np.stack([w / 2 + focal * v[:, 0] / z,
                         h / 2 - focal * v[:, 1] / z, z], 1)
    return proj


def downsample(fb, ss: int) -> np.ndarray:
    """Box-filter the supersampled buffer down, RGBA uint8.

    ALPHA-WEIGHTED, not a plain mean of four channels. The background of a map
    chunk is transparent (a matrix cell nothing covers is nothing, the same
    claim the collision tier makes with `VOID`), and a plain mean over
    straight-alpha pixels drags every edge towards the background's colour —
    which for `(0,0,0,0)` is black. That is a dark fringe all the way round a
    coastline, on every map, and it looks like artwork rather than like a bug.
    """
    c = fb.to_rgba8()
    if ss == 1:
        return c
    h, w = c.shape[:2]
    f = c.reshape(h // ss, ss, w // ss, ss, 4).astype(np.float32)
    a = f[..., 3:4] / 255.0
    wsum = a.sum((1, 3))
    rgb = (f[..., :3] * a).sum((1, 3)) / np.where(wsum == 0, 1.0, wsum)
    out = np.empty((h // ss, w // ss, 4), np.float32)
    out[..., :3] = rgb
    out[..., 3] = f[..., 3].mean((1, 3))
    return np.clip(out, 0, 255).astype(np.uint8)


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr, "RGBA")
