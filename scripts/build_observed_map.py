#!/usr/bin/env python3
"""Draw the walk graph a run actually walked, per game.

    scripts/build_observed_map.py --runs local/runs --out local/observed

One sheet per game: every map the runs entered, drawn as the grid of tiles the
player stood on, with the moves they proved between them. Nothing here comes
from a decompilation, so it works on the six games that have none.

Read the sheet as: filled cell = stood there (darker = more often), line =
a move made in both directions, half-line = made one way only, ring = a tile a
warp left from or arrived at. Faint red ticks are presses that moved nothing —
EVIDENCE of a wall, never drawn as one, because without a textbox flag a press
eaten by dialogue looks identical (see src/app/observed.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app.observed import DIRECTIONS, ObservedGraph, build  # noqa: E402

# PIL's built-in bitmap font has no em dash (it renders a box) and is too small
# to read a map label at. A real face is worth the lookup; falling back is fine
# because the fallback only costs legibility, and every string here is ASCII so
# nothing turns into a box either way.
def _font(size: int):
    for path in ("/System/Library/Fonts/SFNSMono.ttf",
                 "/System/Library/Fonts/Supplemental/Menlo.ttc",
                 "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


F_TITLE = _font(15)
F_SUB = _font(11)
F_LABEL = _font(12)

CELL = 11          # px per tile
PAD = 26           # px around each map panel, leaving room for its label
GUTTER = 18
TARGET_W = 1500  # shelf width the packer aims for; a wider panel overrides it
COLS = 4
BG = (250, 249, 246)
INK = (28, 28, 30)
FAINT = (198, 196, 190)
EDGE = (58, 104, 168)
WARP = (196, 108, 32)
BLOCK = (200, 70, 70)


def _heat(n: int, most: int) -> tuple[int, int, int]:
    """Visited once -> pale; visited most -> saturated. Linear in the COUNT, not
    in its rank: a tile crossed forty times is a corridor and should look like
    one."""
    f = 0.25 + 0.75 * (n / most if most else 1)
    return (int(232 - 150 * f), int(238 - 96 * f), int(246 - 40 * f))


def draw_map(g: ObservedGraph, map_key: tuple) -> Image.Image:
    x0, y0, x1, y1 = g.bounds(map_key)
    w, h = (x1 - x0 + 1), (y1 - y0 + 1)
    label = f"{':'.join(str(v) for v in map_key)}  {len([1 for t in g.visits if t[0] == map_key])} tiles"
    caption = f"x {x0}-{x1}  y {y0}-{y1}"
    # BOTH strings, not just the title. Measuring only the title clipped the
    # axis caption on any narrow panel — Black's map 317 is one tile wide and
    # read "y 716-", losing the number that says how far the corridor runs.
    # The caption is the wider of the two whenever coordinates are global, so
    # on the DS games it is the one that sets the panel width.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_w = int(max(probe.textlength(label, font=F_LABEL),
                     probe.textlength(caption, font=F_SUB)))
    img = Image.new("RGB", (max(w * CELL, text_w) + 2 * PAD, h * CELL + 2 * PAD + 14), BG)
    d = ImageDraw.Draw(img)

    def px(x, y):
        return PAD + (x - x0) * CELL, PAD + (y - y0) * CELL

    # The empty lattice first, so unvisited ground inside the walked area reads
    # as "not seen" rather than as a hole in the map.
    for gx in range(w + 1):
        d.line([px(x0 + gx, y0), px(x0 + gx, y1 + 1)], fill=(238, 237, 233))
    for gy in range(h + 1):
        d.line([px(x0, y0 + gy), px(x1 + 1, y0 + gy)], fill=(238, 237, 233))

    most = max((n for t, n in g.visits.items() if t[0] == map_key), default=1)
    for (m, x, y), n in g.visits.items():
        if m != map_key:
            continue
        a, b = px(x, y)
        d.rectangle([a + 1, b + 1, a + CELL - 1, b + CELL - 1], fill=_heat(n, most))

    # Edges. A pair walked both ways is drawn full width; one-way (a ledge, or
    # simply never walked back) is drawn as a stub from its source, so the
    # asymmetry is visible rather than averaged away.
    for (src, dst), _n in g.edges.items():
        if src[0] != map_key:
            continue
        both = (dst, src) in g.edges
        ax, ay = px(src[1], src[2]); bx, by = px(dst[1], dst[2])
        ax, ay, bx, by = ax + CELL // 2, ay + CELL // 2, bx + CELL // 2, by + CELL // 2
        if not both:
            bx, by = (ax + bx) // 2, (ay + by) // 2
        d.line([ax, ay, bx, by], fill=EDGE, width=2 if both else 1)

    for (src, dst), _n in list(g.warps.items()):
        for t in (src, dst):
            if t[0] != map_key:
                continue
            a, b = px(t[1], t[2])
            d.ellipse([a + 1, b + 1, a + CELL - 1, b + CELL - 1], outline=WARP, width=2)

    for (t, direction), _n in g.blocked.items():
        if t[0] != map_key:
            continue
        a, b = px(t[1], t[2])
        cx, cy = a + CELL // 2, b + CELL // 2
        dx, dy = next(k for k, v in DIRECTIONS.items() if v == direction)
        d.line([cx + dx * 2, cy + dy * 2, cx + dx * (CELL // 2 - 1), cy + dy * (CELL // 2 - 1)],
               fill=BLOCK, width=1)

    label = f"{':'.join(str(v) for v in map_key)}  {len([1 for t in g.visits if t[0] == map_key])} tiles"
    d.text((PAD, 7), label, fill=INK, font=F_LABEL)
    d.text((PAD, PAD + h * CELL + 6), caption, fill=FAINT, font=F_SUB)
    return img


def sheet(g: ObservedGraph, title: str) -> Image.Image:
    panels = [draw_map(g, m) for m in sorted(g.maps, key=lambda m: -len(g.tiles_of(m)))]
    # SHELF packing, not a fixed grid of equal columns. Maps differ in size by
    # two orders of magnitude — a 222-tile town beside a 3-tile shop — so a
    # column sized for the widest one left roughly 60% of the Crystal sheet
    # empty and shrank every small map to a speck in the corner of its cell.
    # Each row instead fills left to right at natural widths until it reaches
    # the target, then wraps; per-ROW height is kept for the same reason.
    widest = max(p.width for p in panels)
    target = max(widest, TARGET_W)
    rows, row, row_w = [], [], 0
    for p_ in panels:
        w = p_.width + GUTTER
        if row and row_w + w > target:
            rows.append(row)
            row, row_w = [], 0
        row.append(p_)
        row_w += w
    if row:
        rows.append(row)
    row_h = [max(p_.height for p_ in r) + GUTTER for r in rows]
    grid_w = max(sum(p_.width + GUTTER for p_ in r) for r in rows)

    st = g.summary()
    caption = [
        f"{st['maps']} maps    {st['tiles']} tiles stood on    {st['edges']} moves proven"
        f"    {st['warps']} warps",
        f"{st['blocked_observations']} presses that moved nothing (evidence, not walls)"
        f"    {len(st['runs'])} run(s), {st['inputs']} inputs",
    ]
    # The HEADER can be wider than the panels — a one-map sheet was cutting its
    # own caption off at the right edge, because the width came only from the
    # grid. Measure the text and let whichever is wider set the sheet.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_w = max([probe.textlength(title, font=F_TITLE)]
                 + [probe.textlength(line, font=F_SUB) for line in caption])
    head = 34 + 15 * len(caption)
    width = max(grid_w, int(text_w) + GUTTER) + GUTTER

    img = Image.new("RGB", (width, head + sum(row_h) + GUTTER), BG)
    d = ImageDraw.Draw(img)
    d.text((GUTTER, 12), title, fill=INK, font=F_TITLE)
    for i, line in enumerate(caption):
        d.text((GUTTER, 34 + 15 * i), line, fill=FAINT, font=F_SUB)
    y = head
    for r, row in enumerate(rows):
        x = GUTTER
        for p_ in row:
            img.paste(p_, (x, y))
            x += p_.width + GUTTER
        y += row_h[r]
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", default="local/runs", help="directory of run dirs")
    ap.add_argument("--out", default="local/observed")
    ap.add_argument("--only", default=None, help="one game key, e.g. emerald-us")
    args = ap.parse_args()

    root = Path(args.runs)
    dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []
    if not dirs:
        raise SystemExit(f"No run dirs under {root}")
    graphs = build(dirs)
    if args.only:
        graphs = {k: v for k, v in graphs.items() if k == args.only}
    if not graphs:
        raise SystemExit("No run recorded a readable position. Every game needs a "
                         "contract in src/referee/contracts.py before it can.")

    # A game with samples but no readable tile is not a bad run — "0 maps" in a
    # table of results reads like one — so say by name WHICH of the two reasons
    # it is. They need different actions and used to print the same sentence:
    # the day Platinum got a contract, its older runs still said NO CONTRACT,
    # which was flatly untrue and pointed at the wrong fix.
    blind = {k: v for k, v in graphs.items() if v.contractless or not v.visits}
    graphs = {k: v for k, v in graphs.items() if not (v.contractless or not v.visits)}
    for game, g in sorted(blind.items()):
        why = ("NO CONTRACT (src/referee/contracts.py), so nothing here is a position"
               if g.contractless else
               "a contract exists, but these runs were RECORDED before it did — the "
               "spec is baked into the recording, so only a new run can be read")
        print(f"  {game:<13} {g.inputs:>5} inputs across {len(g.runs)} run(s) — {why}. "
              f"Not drawn.")
    if not graphs:
        raise SystemExit("No game had a readable position.")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for game, g in sorted(graphs.items()):
        (out / f"{game}-observed.json").write_text(json.dumps(g.to_dict(), indent=2))
        img = sheet(g, f"{game}: the walk graph these runs proved")
        img.save(out / f"{game}-observed.png")
        s = g.summary()
        print(f"  {game:<12} {s['maps']:>3} maps  {s['tiles']:>5} tiles  {s['edges']:>5} moves  "
              f"{s['warps']:>3} warps  from {len(s['runs'])} run(s)  -> {out}/{game}-observed.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
