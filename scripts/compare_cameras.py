#!/usr/bin/env python3
"""Straight down against the field camera's own pitch, at real size, with a route.

The sheet that decided `ds3d/camera.py`. Andreas rejected the straight-down DS
atlases on sight — *"both teh gen 4 and 5 views are actually really bad, bith
are top down whcih dsont feel right"* — and this is the picture that says what
replaced them, on the same maps, at the same pixel size, under the same run.

Each row is ONE map drawn twice:

    BEFORE   straight-down orthographic, the atlas that shipped
    AFTER    orthographic at the field camera's own pitch (59.05 deg)

with the same run's route over both, drawn with `RouteMap.svelte`'s own stroke
width, halo, cable gap and colour wheel (`render_dsmaps.paint_route`). Over
the artwork, not beside it: a picture that reads fine bare can be useless
under a coloured polyline, and that is the whole question.

The route is placed through each panel's OWN camera, which is the claim the
atlas has to make good: `camera.matrix` plus the per-tile `heights` grid puts
a tile where the renderer drew it. If the two halves disagree about where a
road is, the sheet shows it.

    ./venv/bin/python scripts/compare_cameras.py
    ./venv/bin/python scripts/compare_cameras.py --maps platinum-us:418
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_dsmaps as R                                       # noqa: E402
import render_gen5maps as G                                     # noqa: E402
from ds3d import camera as ds3d_camera                          # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "artifacts" / "game-map-render" / "camera"

#: `game:map_id`. The cases the comparison has to answer for, and why each one
#: is here: a town whose buildings the pitch actually changes, the 64x32 case,
#: the cliff-and-water case, the 96x32 case (one frame across three chunks),
#: and one gen-5 map because gen 5 reads the same camera out of the same atlas.
DEFAULT_MAPS = ("platinum-us:418", "platinum-us:342", "platinum-us:391",
                "soulsilver-us:33", "black2-us:427")

CAMERAS = (("topdown", "BEFORE — straight down, the atlas that shipped"),
           ("pitched", "AFTER — the field camera's own pitch, 59.05 deg"))

#: The lighting sheet is a SECOND question and gets its own file. `scene._one`
#: shades a face by how far from vertical it is, so the four slopes of a hip
#: roof come out the same colour; straight down you only ever see one of them
#: and it never mattered. `--light` swaps in a directional lamp. It is off by
#: default and this is the picture that asks whether it should be.
LIGHTS = ((False, "the slope shade — one brightness per angle, whatever the direction"),
          (True, "--light — a directional lamp from the north-west, ground still 1.0"))


def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(
            f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf", size)
    except OSError:
        return ImageFont.load_default()


class Gen5Panel:
    """A gen-5 render wearing the four attributes `R.panel`/`R.paint_route` read.

    Duck-typed rather than shared by inheritance for the reason `RomAssets` is:
    the two renderers have a Window each and nothing else in common, and the
    route painter only ever asks for the map's origin, its frame, its ground
    and its pixels. One painter is the point — a sheet drawn by a second copy
    of the route code would be a picture of the second copy.
    """

    def __init__(self, win, rgba, kind, frame, heights) -> None:
        self.key, self.name = win.key, f"MAP_{win.zone}"
        self.ox, self.oy, self.w, self.h = win.ox, win.oy, win.w, win.h
        self.rgba, self.render, self.frame, self.heights = rgba, kind, frame, heights


def gen4_panels(game: str, map_id: int, walked: dict,
                arms=None) -> list[tuple[str, Image.Image, object]]:
    d = R.source_for(game, offline=False)
    out = []
    for kind, light in (arms or [(k, False) for k, _ in CAMERAS]):
        cam = ds3d_camera.camera_for(kind, R.TILE_PX["3d"])
        art = R.Field3D(d, tile_px=R.TILE_PX["3d"], cam=cam,
                        light=ds3d_camera.directional_shade() if light else None)
        r = R.render_map(d, map_id, art, cam=cam)
        out.append((kind, R.panel(r, walked, R.TILE_PX["3d"]), r))
    return out


def gen5_panels(game: str, map_id: int, walked: dict,
                arms=None) -> list[tuple[str, Image.Image, object]]:
    rom = G.Gen5Rom(game)
    win = G.map_window(rom, map_id)
    out = []
    for kind, light in (arms or [(k, False) for k, _ in CAMERAS]):
        cam = ds3d_camera.camera_for(kind, G.TILE_PX["3d"])
        art = G.Field3D(rom, tile_px=G.TILE_PX["3d"], cam=cam,
                        light=ds3d_camera.directional_shade() if light else None)
        rgba, rkind, frame, heights = G.render_map(rom, win, art, cam=cam)
        p = Gen5Panel(win, rgba, rkind, frame, heights)
        out.append((kind, R.panel(p, walked, G.TILE_PX["3d"]), p))
    return out


def walked_for(game: str) -> dict:
    if game in G.GAMES:
        out: dict[int, list] = {}
        for z, x, y in G.samples(game):
            row = out.setdefault(z, [])
            if not row or row[-1] != (x, y):
                row.append((x, y))
        return out
    return R.route_tiles(game)


def build(specs: list[str], path: Path, *, arms=None, captions=None,
          title=None, blurb=None) -> Path:
    PAD, GAP, LABEL, HEAD, ROWGAP = 28, 20, 40, 118, 34
    captions = captions or [c for _, c in CAMERAS]
    rows = []
    for spec in specs:
        game, sid = spec.split(":")
        map_id = int(sid)
        walked = walked_for(game)
        maker = gen5_panels if game in G.GAMES else gen4_panels
        try:
            pair = maker(game, map_id, walked, arms)
        except SystemExit as exc:
            print(f"{spec}: {exc}", file=sys.stderr)
            continue
        rows.append((spec, pair, len(walked.get(map_id, []))))
        print(f"{spec}: " + "  ".join(
            f"{k} {im.width}x{im.height}" for k, im, _ in pair))

    # A wide map's two halves go one ABOVE the other, a tall map's side by
    # side. Always side by side made the sheet as wide as twice Route 219's
    # 96 tiles and left every other row two thirds empty, and the comparison
    # that matters — did the road move? — is the one across the short axis.
    def stacked(pair):
        return pair[0][1].width > pair[0][1].height

    def box(pair):
        w = [im.width for _, im, _ in pair]
        h = [im.height for _, im, _ in pair]
        if stacked(pair):
            return max(w), sum(h) + LABEL + GAP
        return sum(w) + GAP, max(h)

    width = PAD * 2 + max(box(pair)[0] for _, pair, _ in rows)
    height = (PAD + HEAD + sum(box(pair)[1] + LABEL + ROWGAP for _, pair, _ in rows)
              + PAD)
    out = Image.new("RGBA", (width, height), (18, 20, 26, 255))
    g = ImageDraw.Draw(out)
    g.text((PAD, PAD), title or "DS map artwork — straight down against the game's own camera",
           font=font(22, True), fill=(240, 242, 248, 255))
    g.text((PAD, PAD + 32),
           "Every panel is at REAL SIZE (16 px per tile across) and carries a real run's "
           "route, drawn with RouteMap.svelte's own stroke width, halo, cable gap and "
           "colour wheel.",
           font=font(13), fill=(150, 158, 174, 255))
    g.text((PAD, PAD + 52),
           "The route is placed through each panel's OWN camera — the atlas's 2x3 matrix "
           "plus its per-tile `heights` grid — so a road the cable misses is a bug in the "
           "schema, not in the drawing.",
           font=font(13), fill=(150, 158, 174, 255))
    g.text((PAD, PAD + 72),
           blurb or ("Pitched is orthographic, NOT the game's perspective: over one 32x32 "
                     "chunk the real field camera makes a tile 13.26 px at the far edge "
                     "and 19.52 px at the near one (1.47x)."),
           font=font(13), fill=(150, 158, 174, 255))

    y = PAD + HEAD
    for spec, pair, n in rows:
        r = pair[0][2]
        g.text((PAD, y), f"{spec}  {r.name}", font=font(16, True),
               fill=(222, 228, 240, 255))
        g.text((PAD, y + 19),
               f"{r.w}x{r.h} tiles · origin {r.ox},{r.oy} · {n} samples walked",
               font=font(12), fill=(150, 158, 174, 255))
        down = stacked(pair)
        x, top = PAD, y + LABEL
        for i, ((kind, im, rr), cap) in enumerate(zip(pair, captions)):
            g.text((x, top), cap, font=font(12),
                   fill=(225, 150, 120, 255) if i == 0 else (130, 200, 150, 255))
            g.text((x, top + 15), f"{im.width}x{im.height} px · {rr.render}"
                   + (f" · pad {list(rr.frame.pad)}" if rr.frame else ""),
                   font=font(11), fill=(120, 128, 144, 255))
            out.alpha_composite(im, (x, top + 32))
            g.rectangle([x - 1, top + 31, x + im.width, top + 32 + im.height],
                        outline=(58, 62, 70, 255))
            if down:
                top += im.height + 32 + GAP
            else:
                x += im.width + GAP
        y += box(pair)[1] + LABEL + ROWGAP

    path.parent.mkdir(parents=True, exist_ok=True)
    out.convert("RGB").save(path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maps", default=",".join(DEFAULT_MAPS),
                    help="comma-separated game:map_id")
    ap.add_argument("--out", default=None)
    ap.add_argument("--light", action="store_true",
                    help="the LIGHTING sheet instead: both arms pitched, one lit")
    args = ap.parse_args(argv)
    specs = [s for s in args.maps.split(",") if s]
    if args.light:
        out = Path(args.out or OUT / "camera-lighting.png")
        print("wrote", build(specs, out,
                             arms=[("pitched", lit) for lit, _ in LIGHTS],
                             captions=[c for _, c in LIGHTS],
                             title="Pitched, with and without a directional light",
                             blurb=("`scene._one` shades a face by its ANGLE from vertical, "
                                    "so a hip roof's four slopes are one colour and the ridge "
                                    "is whatever the texture shows. Straight down you see one "
                                    "slope; pitched you see three.")))
        return 0
    print("wrote", build(specs, Path(args.out or OUT / "camera-comparison.png")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
