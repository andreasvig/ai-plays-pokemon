"""Draw a run's route on the FireRed pixel map — the proof that the per-input
trace is drawable (artifacts/route-fidelity/plan.md, 2026-09-15).

    venv/bin/python scripts/render_route.py local/runs/<run_id> [--out route.png] [--scale 4]

Outdoor maps sit in the walk graph's shared world frame (graph version 2,
Pallet Town top-left = (0, 0)); indoor maps the run visited are drawn as insets
on the right. Passable tiles grey, the route coloured from blue (start) to red
(end), warps as hollow circles, scripted jumps (Oak's escort) as the
shortest-path fill in a lighter line, samples taken in battle as red dots.
``--scale 16`` is the game's own tile size (1 tile = 16 px).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from src.app import route as route_mod  # noqa: E402
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph  # noqa: E402

BG = (250, 250, 247)
TILE = (222, 222, 216)
BORDER = (150, 150, 150)
TEXT = (60, 60, 60)
FILL_LINE = (190, 190, 230)
BATTLE = (220, 40, 40)
WARP = (30, 30, 30)
MARGIN = 6  # tiles of padding around the outdoor world


def turn_colour(t: float) -> tuple[int, int, int]:
    """0 → blue, 0.5 → green, 1 → red."""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        k = t / 0.5
        return int(40 + 0 * k), int(80 + 140 * k), int(220 - 140 * k)
    k = (t - 0.5) / 0.5
    return int(40 + 180 * k), int(220 - 170 * k), int(80 - 60 * k)


def render(run_dir: Path, out: Path, scale: int = 4) -> Path:
    graph = WalkGraph.load(REPO_ROOT / DEFAULT_GRAPH_PATH)
    r = route_mod.load_route(run_dir, graph)
    if r is None:
        raise SystemExit(f"{run_dir.name}: no per-input trace — nothing to draw")
    visits = r["visits"]
    turns = [v[0] for v in visits]
    t0, t1 = min(turns), max(max(turns), min(turns) + 1)

    # --- outdoor world bounds: every map with a frame, so runs share one canvas
    outdoor = {k: m for k, m in graph.maps.items() if m.get("world")}
    xs = [m["world"][0] for m in outdoor.values()] + [m["world"][0] + m["width"] for m in outdoor.values()]
    ys = [m["world"][1] for m in outdoor.values()] + [m["world"][1] + m["height"] for m in outdoor.values()]
    wx0, wy0 = min(xs) - MARGIN, min(ys) - MARGIN
    ww, wh = (max(xs) - min(xs) + 2 * MARGIN), (max(ys) - min(ys) + 2 * MARGIN)

    # --- indoor insets: visited maps without a frame, stacked on the right
    indoor_keys = [k for k in r["maps"] if not (graph.maps.get(k) or {}).get("world")]
    inset_w = max([(graph.maps.get(k) or {}).get("width") or 12 for k in indoor_keys] + [0]) + 2 * 2
    inset_positions: dict[str, tuple[int, int]] = {}  # map key → tile offset of its (0,0) on the canvas
    cy = MARGIN
    for k in indoor_keys:
        m = graph.maps.get(k) or {}
        inset_positions[k] = (ww + 2, cy + 2)
        cy += (m.get("height") or 12) + 5
    label_px = max([len((graph.maps.get(k) or {}).get("name") or k) for k in indoor_keys] + [0]) * 6
    total_w = (ww + (inset_w + 2 if indoor_keys else 0)) * scale + (max(0, label_px - inset_w * scale) if indoor_keys else 0)
    total_h = max(wh, cy) * scale + 40

    img = Image.new("RGB", (total_w, total_h), BG)
    draw = ImageDraw.Draw(img)

    def canvas_xy(tile) -> tuple[float, float] | None:
        g, m, x, y = tile
        key = f"{g}:{m}"
        w = graph.world_xy(g, m, x, y)
        if w is not None:
            return (w[0] - wx0 + 0.5) * scale, (w[1] - wy0 + 0.5) * scale
        if key in inset_positions:
            ox, oy = inset_positions[key]
            return (ox + x + 0.5) * scale, (oy + y + 0.5) * scale
        return None

    # passable tiles + map borders
    for n in range(len(graph)):
        c = graph.coord(n)
        p = canvas_xy(c)
        if p is None:
            continue
        x, y = p
        draw.rectangle([x - scale / 2, y - scale / 2, x + scale / 2 - 1, y + scale / 2 - 1], fill=TILE)
    for k, m in outdoor.items():
        x, y = (m["world"][0] - wx0) * scale, (m["world"][1] - wy0) * scale
        draw.rectangle([x, y, x + m["width"] * scale - 1, y + m["height"] * scale - 1], outline=BORDER)
        draw.text((x + 2, y + 1), m["name"], fill=TEXT)
    for k, (ox, oy) in inset_positions.items():
        m = graph.maps.get(k) or {}
        x, y = ox * scale, oy * scale
        draw.rectangle([x - 1, y - 1, x + (m.get("width") or 12) * scale, y + (m.get("height") or 12) * scale], outline=BORDER)
        draw.text((x, y - 12), m.get("name") or k, fill=TEXT)

    # route
    width = max(1, scale // 2)
    for i in range(len(visits) - 1):
        a, b = visits[i], visits[i + 1]
        ta, tb = tuple(a[2:6]), tuple(b[2:6])
        colour = turn_colour((a[0] - t0) / (t1 - t0))
        fill = r["fills"].get(str(i))
        chain = [ta] + ([tuple(t) for t in fill] if fill else []) + [tb]
        for u, v in zip(chain, chain[1:]):
            pu, pv = canvas_xy(u), canvas_xy(v)
            if pu is None or pv is None:
                continue
            same_map = u[:2] == v[:2]
            adjacent = same_map and abs(u[2] - v[2]) + abs(u[3] - v[3]) == 1
            wu, wv = graph.world_xy(*u), graph.world_xy(*v)
            seam = (not same_map and wu is not None and wv is not None and abs(wu[0] - wv[0]) + abs(wu[1] - wv[1]) == 1)
            if adjacent or seam:
                draw.line([pu, pv], fill=FILL_LINE if fill else colour, width=width)
            else:  # warp: mark both ends, no chord across the canvas
                for p in (pu, pv):
                    draw.ellipse([p[0] - scale, p[1] - scale, p[0] + scale, p[1] + scale], outline=WARP)
        if a[6]:
            draw.ellipse([canvas_xy(ta)[0] - width, canvas_xy(ta)[1] - width,
                          canvas_xy(ta)[0] + width, canvas_xy(ta)[1] + width], fill=BATTLE)
    # start / end markers
    for tile, colour in ((tuple(visits[0][2:6]), (40, 80, 220)), (tuple(visits[-1][2:6]), (220, 50, 20))):
        p = canvas_xy(tile)
        if p is not None:
            draw.rectangle([p[0] - scale, p[1] - scale, p[0] + scale, p[1] + scale], outline=colour, width=2)

    tr = r["transitions"]
    legend = (f"{run_dir.name}   turns {t0}-{max(turns)}   tiles visited {len(visits)}   tiles moved {r['tiles_moved']}   "
              f"coverage {r['coverage']:.0%}   steps {tr.get('step', 0)}  seams {tr.get('seam', 0)}  warps {tr.get('warp', 0)}  "
              f"jumps {tr.get('jump', 0)}  breaks {tr.get('break', 0)}")
    draw.text((4, total_h - 32), legend, fill=TEXT)
    draw.text((4, total_h - 18), "blue -> red = turn order; hollow circles = warp ends; light line = scripted walk fill; red dots = in battle",
              fill=TEXT)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="PNG path (default: <run_dir>/route.png)")
    ap.add_argument("--scale", type=int, default=4, help="pixels per tile (16 = the game's own)")
    args = ap.parse_args()
    out = args.out or (args.run_dir / "route.png")
    print(render(args.run_dir, out, args.scale))


if __name__ == "__main__":
    main()
