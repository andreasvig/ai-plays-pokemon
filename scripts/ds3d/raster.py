"""CPU triangle rasteriser: z-buffer, perspective-correct UV, alpha test.

Small enough scenes (a few thousand triangles) that a per-triangle numpy
inner loop is fast; no GL, no native deps.
"""
import numpy as np


class Framebuffer:
    def __init__(self, w, h, bg=(0, 0, 0, 0)):
        self.w, self.h = w, h
        self.color = np.zeros((h, w, 4), np.float32)
        self.color[..., :] = bg
        self.depth = np.full((h, w), np.inf, np.float32)

    def to_rgba8(self):
        return np.clip(self.color, 0, 255).astype(np.uint8)


def sample(tex, u, v):
    """Nearest-neighbour wrap sample. u,v in texels."""
    h, w = tex.shape[:2]
    iu = np.mod(np.floor(u).astype(np.int64), w)
    iv = np.mod(np.floor(v).astype(np.int64), h)
    return tex[iv, iu]


def draw(fb, scr, uv, tex, shade=1.0, alpha_test=True):
    """scr: (3,3) float x,y,w  (w = view-space depth, >0)
       uv:  (3,2) texel coords
       tex: (H,W,4) uint8"""
    x = scr[:, 0]; y = scr[:, 1]; wv = scr[:, 2]
    minx = max(int(np.floor(x.min())), 0)
    maxx = min(int(np.ceil(x.max())) + 1, fb.w)
    miny = max(int(np.floor(y.min())), 0)
    maxy = min(int(np.ceil(y.max())) + 1, fb.h)
    if minx >= maxx or miny >= maxy:
        return
    area = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
    if abs(area) < 1e-9:
        return
    px = np.arange(minx, maxx) + 0.5
    py = np.arange(miny, maxy) + 0.5
    gx, gy = np.meshgrid(px, py)
    w0 = ((x[1] - x[0]) * (gy - y[0]) - (gx - x[0]) * (y[1] - y[0])) / area
    w1 = ((gx - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (gy - y[0])) / area
    l2, l1 = w0, w1
    l0 = 1.0 - l1 - l2
    inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
    if not inside.any():
        return
    invw = l0 / wv[0] + l1 / wv[1] + l2 / wv[2]
    depth = 1.0 / np.where(invw == 0, 1e-9, invw)
    sub = fb.depth[miny:maxy, minx:maxx]
    closer = inside & (depth < sub)
    if not closer.any():
        return
    u = (l0 * uv[0, 0] / wv[0] + l1 * uv[1, 0] / wv[1] + l2 * uv[2, 0] / wv[2]) * depth
    v = (l0 * uv[0, 1] / wv[0] + l1 * uv[1, 1] / wv[1] + l2 * uv[2, 1] / wv[2]) * depth
    rgba = sample(tex, u[closer], v[closer]).astype(np.float32)
    if alpha_test:
        keep = rgba[:, 3] > 8
    else:
        keep = np.ones(len(rgba), bool)
    idx = np.where(closer)
    iy, ix = idx[0][keep], idx[1][keep]
    rgba = rgba[keep]
    rgba[:, :3] *= shade
    fb.color[miny + iy, minx + ix] = rgba
    fb.depth[miny + iy, minx + ix] = depth[closer][keep]


def make_view(pitch_deg, yaw_deg=0.0):
    """World -> camera rotation. Camera looks down -Z_cam.
    World: +x east, +y up, +z south. pitch is degrees below horizontal."""
    p = np.radians(pitch_deg)
    ya = np.radians(yaw_deg)
    cy, sy = np.cos(ya), np.sin(ya)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], np.float64)
    cp, sp = np.cos(p), np.sin(p)
    # rotate so the camera, sitting up and to the +z side, looks down at origin
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], np.float64)
    return Rx @ Ry


def project_persp(pts, R, cam_pos, focal, cx, cy):
    """pts (N,3) world -> (N,3) screen x, y, view-depth."""
    v = (pts - cam_pos) @ R.T
    z = -v[:, 2]
    z = np.where(z < 1e-3, 1e-3, z)
    return np.stack([cx + focal * v[:, 0] / z,
                     cy - focal * v[:, 1] / z, z], 1)


def project_ortho(pts, R, scale, cx, cy, origin):
    v = (pts - origin) @ R.T
    return np.stack([cx + scale * v[:, 0],
                     cy - scale * v[:, 1],
                     1000.0 - v[:, 2]], 1)
