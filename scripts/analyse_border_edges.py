#!/usr/bin/env python3
"""Decide, by measurement, which map edges get a fringe of the border block.

Replaces the hand-curated list in `src/dashboard/web/src/lib/borders.js`.
Andreas, 2026-09-20: "some routes on this map need the row of trees too, don't
you have a way to analyse and guestimate where we need a row to indicate a wall
such that I don't have to tell you each time? so basically all of the 'holes' in
the side of maps where roads don't go."

THE RULE, in one paragraph
-------------------------
A gen 2/3 map ships a border block that the game repeats beyond its bounds.
Drawing one row of it outside a closed edge makes the map end in a wall instead
of in black. Two things decide whether that is right for a given side:

  1. `closed`  — the part of the edge no neighbour covers, from the atlas's own
     `open` spans (pret's `connections`). A side that is entirely road has
     nothing to draw and can never be listed.

  2. `fit`     — of the closed edge tiles, the fraction whose OWN artwork is
     painted, to >= CELL_COVER of its pixels, out of the border block's palette.
     This is the water/cave objection made measurable. Littleroot's grass rim
     and the block's tree-and-grass palette are the same palette, so trees
     outside it look like more of the same place (fit 0.90-1.00); Route 104's
     sea edge and a block of trees are not (fit 0.47), and that is exactly the
     row of trees Andreas rejected. It is palette CONTAINMENT, not tile
     equality: the town's small trees are different tiles from the block's big
     ones and still score 1.00, while open water scores near zero.

  A block that is one flat colour over more than FLAT_VETO of its area is open
  water, not a wall — FireRed's Route 21 and Crystal's Cherrygrove both ship
  one — and a fringe of open water cannot do the job a fringe is for. Those
  sides are vetoed whatever they fit.

  At EXACTLY one colour the veto means something different and the report says
  so separately. A gen-1/2/3 map's border is a real block the cartridge repeats
  beyond the map's bounds; gen 4 and gen 5 have no such thing, so the DS
  renderers emit a 1x1 placeholder and every DS side is vetoed as `blank`.
  That is a property of those cartridges, not of how well the atlas is
  rendered: it stayed true when Platinum went from a collision silhouette to
  the full 3D render, and it will stay true after any further render work. If
  a DS map ever needs a fringe it has to come from somewhere other than a
  border block.

  fit >= DRAW  -> draw.  CAND <= fit < DRAW -> candidate, listed in borders.js
  as a commented line with its number so Andreas can switch it on. Below that,
  silent.

Outputs
-------
  (default)   the full table, the score against the hand list, and the
              black-region classification, on stdout
  --write     rewrite the generated block of borders.js in place
  --sheet     render the before/after world frames under
              artifacts/game-map-render/border-edges/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
MAPS = ROOT / "src/dashboard/web/public/maps"
BORDERS_JS = ROOT / "src/dashboard/web/src/lib/borders.js"
SHEETS = ROOT / "artifacts/game-map-render/border-edges"

SIDES = ("up", "down", "left", "right")

#: A tile counts as "painted out of the block's palette" at this coverage. The
#: measurement is flat between 0.80 and 0.95 — see `--sensitivity`.
CELL_COVER = 0.90
#: Draw at or above this fraction of the closed edge; offer as a candidate down
#: to CAND. The widest gap in the real data is 0.47 (the highest scoring side
#: Andreas rejected) to 0.85 (the lowest he asked for).
DRAW = 0.85
CAND = 0.50
#: A border block whose single commonest colour covers more than this is flat
#: open water, not a wall. Measured: water blocks 0.76 and 0.94, every tree or
#: rock block 0.51 or less.
FLAT_VETO = 0.60

#: The list Andreas built by hand, one map at a time, before asking for this
#: script. It is the validation set, not an input: nothing below reads it.
HAND = {
    ("firered-us", "3:0"): {"left", "right"},      # Pallet Town
    ("firered-us", "3:19"): {"left", "right"},     # Route 1
    ("firered-us", "3:1"): {"left"},               # Viridian City
    ("emerald-us", "0:9"): {"left", "right", "down"},   # Littleroot Town
    ("emerald-us", "0:16"): {"left", "right"},     # Route 101
    ("emerald-us", "0:17"): {"up", "down"},        # Route 102
    ("emerald-us", "0:0"): {"down"},               # Petalburg City
    ("emerald-us", "0:19"): {"right"},             # Route 104
    ("emerald-us", "0:10"): {"right"},             # Oldale Town
}


# ---------------------------------------------------------------- atlas reading


def games() -> list[str]:
    return sorted(p.name for p in MAPS.iterdir() if (p / "index.json").is_file())


def atlas(game: str) -> dict:
    return json.loads((MAPS / game / "index.json").read_text())


def drawable(m: dict) -> bool:
    """An outdoor map with artwork, a place in the world frame, and a block."""
    return (
        bool(m.get("file"))
        and not (m.get("popup") or m.get("indoor"))
        and isinstance(m.get("world"), list)
        and bool(m.get("border"))
    )


def edge_len(m: dict, side: str) -> int:
    return m["width"] if side in ("up", "down") else m["height"]


def closed_spans(m: dict, side: str) -> list[tuple[int, int]]:
    """The parts of `side` no neighbour covers, in this map's own tiles."""
    n = edge_len(m, side)
    out: list[tuple[int, int]] = []
    cur = 0
    for a, b in sorted((o["from"], o["to"]) for o in (m.get("open") or []) if o["side"] == side):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < n:
        out.append((cur, n))
    return out


def open_spans(m: dict, side: str) -> list[tuple[int, int]]:
    return [(o["from"], o["to"]) for o in (m.get("open") or []) if o["side"] == side]


# ------------------------------------------------------------------- the measure


def palette(img: np.ndarray) -> tuple[set[tuple[int, ...]], float]:
    """The distinct colours of an image, and its commonest colour's share."""
    cols, cnt = np.unique(img.reshape(-1, 3), axis=0, return_counts=True)
    return ({tuple(int(x) for x in c) for c in cols}, float(cnt.max() / cnt.sum()))


def edge_cell(img: np.ndarray, m: dict, side: str, i: int, t: int) -> np.ndarray:
    """The map's own OUTERMOST tile at position `i` along `side`."""
    w, h = m["width"] * t, m["height"] * t
    if side == "up":
        return img[0:t, i * t:(i + 1) * t]
    if side == "down":
        return img[h - t:h, i * t:(i + 1) * t]
    if side == "left":
        return img[i * t:(i + 1) * t, 0:t]
    return img[i * t:(i + 1) * t, w - t:w]


def coverage(cell: np.ndarray, pal: set) -> float:
    """Share of this tile's pixels painted in a colour the block also uses."""
    cols, cnt = np.unique(cell.reshape(-1, 3), axis=0, return_counts=True)
    hit = sum(int(k) for c, k in zip(cols, cnt) if tuple(int(x) for x in c) in pal)
    return hit / float(cnt.sum())


def map_name(key: str, m: dict) -> str:
    """A name to print for one map. Gen 4 and gen 5 have no decomp to read map
    names out of, so their atlas entries carry `name: null` — every row of this
    report used to crash on the first one of those. The key without its `:0`
    matches how the gen-5 renderer already spells a nameless map (`building:
    "Map428"`), so the two surfaces agree rather than inventing a second form."""
    return m.get("name") or f"Map{key.split(':')[0]}"


@dataclass
class Edge:
    game: str
    key: str
    name: str
    side: str
    closed: int
    total: int
    fit: float
    flat: float
    verdict: str          # draw | candidate | skip | water | blank | road
    covers: list = field(default_factory=list)

    @property
    def hand(self) -> bool:
        return self.side in HAND.get((self.game, self.key), set())


def score(game: str, cell_cover: float = CELL_COVER) -> list[Edge]:
    a = atlas(game)
    t = int(a.get("tile_px", 16))
    out: list[Edge] = []
    for key, m in a["maps"].items():
        if not drawable(m):
            continue
        img = np.asarray(Image.open(MAPS / game / m["file"]).convert("RGB"))
        blk = np.asarray(Image.open(MAPS / game / m["border"]["file"]).convert("RGB"))
        pal, flat = palette(blk)
        for side in SIDES:
            spans = closed_spans(m, side)
            closed = sum(b - a_ for a_, b in spans)
            n = edge_len(m, side)
            if not closed:
                out.append(Edge(game, key, map_name(key, m), side, 0, n, 0.0, flat, "road"))
                continue
            cov = [coverage(edge_cell(img, m, side, i, t), pal)
                   for a_, b in spans for i in range(a_, b)]
            fit = float(np.mean([c >= cell_cover for c in cov]))
            if flat >= 0.999:
                v = "blank"      # a single flat colour: no artwork to draw
            elif flat >= FLAT_VETO:
                v = "water"
            elif fit >= DRAW:
                v = "draw"
            elif fit >= CAND:
                v = "candidate"
            else:
                v = "skip"
            out.append(Edge(game, key, map_name(key, m), side, closed, n, fit, flat, v, cov))
    out.sort(key=lambda e: (e.key, SIDES.index(e.side)))
    return out


# ----------------------------------------------------------- the two black kinds


def black_regions(game: str) -> dict:
    """Split the black on screen into the two things that cause it.

    A fringe fixes ONE of them. The other is a neighbour that exists in pret and
    connects to this map, but that no run has entered, so it has no artwork and
    nothing at all is drawn there. Only rendering fixes that.
    """
    a = atlas(game)
    drawn = {k: m for k, m in a["maps"].items() if drawable(m)}
    # every world tile some rendered map occupies, per frame
    filled: dict[str, set] = {}
    for m in drawn.values():
        s = filled.setdefault(m.get("frame") or "", set())
        wx, wy = m["world"]
        for y in range(wy, wy + m["height"]):
            for x in range(wx, wx + m["width"]):
                s.add((x, y))

    res = {"closed_edges": 0, "closed_tiles": 0, "unrendered_edges": 0,
           "unrendered_tiles": 0, "unrendered_to": []}
    for key, m in drawn.items():
        wx, wy = m["world"]
        fr = filled.get(m.get("frame") or "", set())
        for side in SIDES:
            for a_, b in closed_spans(m, side):
                res["closed_edges"] += 1
                res["closed_tiles"] += b - a_
            for a_, b in open_spans(m, side):
                # the world tile immediately beyond the middle of this road
                mid = (a_ + b) // 2
                if side == "up":
                    p = (wx + mid, wy - 1)
                elif side == "down":
                    p = (wx + mid, wy + m["height"])
                elif side == "left":
                    p = (wx - 1, wy + mid)
                else:
                    p = (wx + m["width"], wy + mid)
                if p not in fr:
                    res["unrendered_edges"] += 1
                    res["unrendered_tiles"] += b - a_
                    res["unrendered_to"].append(f"{key} {map_name(key, m)} {side}")
    return res


# ------------------------------------------------------------------- borders.js

GEN_OPEN = "// >>> generated by scripts/analyse_border_edges.py — do not hand-edit"
GEN_CLOSE = "// <<< end generated"


def hand_sides() -> dict[str, dict[str, set]]:
    """The BEFORE arm: the list Andreas built by hand, which this replaces.

    Read from HAND, not from borders.js — borders.js is the thing being
    rewritten, so parsing it would make the control move with the subject.
    """
    out: dict[str, dict[str, set]] = {}
    for (game, key), sides in HAND.items():
        out.setdefault(game, {})[key] = set(sides)
    return out


def render_generated(all_edges: dict[str, list[Edge]]) -> str:
    lines = [GEN_OPEN,
             f"//   fit >= {DRAW:.2f} draws; {CAND:.2f}-{DRAW:.2f} is offered as a commented",
             "//   candidate; a flat (open-water) block is vetoed whatever it fits.",
             "//   \"no border block\" below means the cartridge has none to tile: gen 4",
             "//   and gen 5 do not have the concept, so their renderers emit a 1x1",
             "//   placeholder and every DS side is held back for that one reason.",
             "export const MEASURED = {"]
    for game, edges in all_edges.items():
        rows = [e for e in edges if e.verdict in ("draw", "candidate", "water", "blank")]
        if not rows:
            continue
        block: list[str] = [f"  '{game}': {{"]
        lines_before = lines
        lines = block
        by_key: dict[str, list[Edge]] = {}
        for e in rows:
            by_key.setdefault(e.key, []).append(e)
        for key, es in by_key.items():
            drawn = [e for e in es if e.verdict == "draw"]
            held = [e for e in es if e.verdict in ("candidate", "water", "blank")]
            if drawn:
                sides = ", ".join(f"'{e.side}'" for e in drawn)
                fits = " ".join(f"{e.side} {e.fit:.2f}" for e in drawn)
                lines.append(f"    '{key}': [{sides}],".ljust(44)
                             + f"// {es[0].name} — fit {fits}")
            # The held sides are written as ONE line carrying the whole list it
            # would become, never as a second `'key': [...]` — two entries for
            # the same key in an object literal is the last one silently winning.
            if held:
                why = {"water": "block is open water (%.2f flat)" % held[0].flat,
                       "blank": "no border block",
                       }.get(held[0].verdict, f"under {DRAW:.2f}")
                nums = ", ".join(f"{e.side} {e.fit:.2f}" for e in held)
                allsides = ", ".join(f"'{e.side}'" for e in drawn + held)
                lines.append(f"    // {es[0].name} held back, {why}: {nums}")
                how = ("to add, REPLACING the line above" if drawn
                       else "nothing is drawn for this map; to draw it anyway")
                lines.append(f"    // {how}: '{key}': [{allsides}],")
        lines.append("  },")
        block, lines = lines, lines_before
        # A game whose every side is held back is written out COMMENTED, not as
        # an empty object: `'platinum-us': {}` in the shipped list reads as a
        # cartridge someone deliberately switched off, and it is really a
        # cartridge with no tile artwork yet.
        if any(e.verdict == "draw" for e in rows):
            lines.extend(block)
        else:
            lines.append(f"  // {game}: nothing drawn, every side held back —")
            lines.extend("  " + ln.lstrip() if ln.lstrip().startswith("//")
                         else "  // " + ln.strip() for ln in block[1:-1])
    lines.append("}")
    lines.append(GEN_CLOSE)
    return "\n".join(lines)


def write_borders(all_edges: dict[str, list[Edge]]) -> None:
    src = BORDERS_JS.read_text()
    i, j = src.index(GEN_OPEN), src.index(GEN_CLOSE) + len(GEN_CLOSE)
    BORDERS_JS.write_text(src[:i] + render_generated(all_edges) + src[j:])


# ----------------------------------------------------------------- the sheet


def world_origin(game: str) -> tuple[int, int, int, int, int]:
    a = atlas(game)
    t = int(a.get("tile_px", 16))
    ms = {k: m for k, m in a["maps"].items() if drawable(m)}
    bleed = max(2, max(m["border"]["w"] for m in ms.values()),
                max(m["border"]["h"] for m in ms.values()))
    xs = [m["world"][0] for m in ms.values()] + [m["world"][0] + m["width"] for m in ms.values()]
    ys = [m["world"][1] for m in ms.values()] + [m["world"][1] + m["height"] for m in ms.values()]
    return min(xs) - bleed - 1, min(ys) - bleed - 1, t, bleed, 0


def compose(game: str, sides_of, label: str) -> Image.Image:
    """The world frame, drawn the way RouteMap.svelte draws it.

    Pass 1 is the fringe — one row of the block outside the map, with every
    `open` span punched back out — and pass 2 is the artwork over it, so a
    neighbour's real ground always wins and a road is never painted.
    """
    a = atlas(game)
    t = int(a.get("tile_px", 16))
    ms = {k: m for k, m in a["maps"].items() if drawable(m)}
    bleed = max(2, max(m["border"]["w"] for m in ms.values()),
                max(m["border"]["h"] for m in ms.values()))
    xs = [m["world"][0] for m in ms.values()] + [m["world"][0] + m["width"] for m in ms.values()]
    ys = [m["world"][1] for m in ms.values()] + [m["world"][1] + m["height"] for m in ms.values()]
    x0, y0 = min(xs) - bleed - 1, min(ys) - bleed - 1
    W, H = (max(xs) - min(xs) + 2 * bleed + 2), (max(ys) - min(ys) + 2 * bleed + 2)
    out = Image.new("RGB", (W * t, H * t), (0, 0, 0))

    for key, m in ms.items():
        sides = sides_of(game, key, m)
        if not sides:
            continue
        b = max(1, m["border"]["w"], m["border"]["h"])
        blk = Image.open(MAPS / game / m["border"]["file"]).convert("RGB")
        wT, hT = m["width"] + 2 * b, m["height"] + 2 * b
        patch = Image.new("RGBA", (wT * t, hT * t), (0, 0, 0, 0))
        tile = Image.new("RGB", (wT * t, hT * t))
        for yy in range(0, tile.height, blk.height):
            for xx in range(0, tile.width, blk.width):
                tile.paste(blk, (xx, yy))
        B = b * t
        mask = Image.new("L", patch.size, 0)
        d = ImageDraw.Draw(mask)
        if "up" in sides:
            d.rectangle([0, 0, patch.width, B], fill=255)
        if "down" in sides:
            d.rectangle([0, patch.height - B, patch.width, patch.height], fill=255)
        if "left" in sides:
            d.rectangle([0, 0, B, patch.height], fill=255)
        if "right" in sides:
            d.rectangle([patch.width - B, 0, patch.width, patch.height], fill=255)
        for o in m.get("open") or []:
            s, p = o["side"], B + o["from"] * t
            ln = (o["to"] - o["from"]) * t
            if s == "up":
                d.rectangle([p, 0, p + ln, B], fill=0)
            elif s == "down":
                d.rectangle([p, patch.height - B, p + ln, patch.height], fill=0)
            elif s == "left":
                d.rectangle([0, p, B, p + ln], fill=0)
            elif s == "right":
                d.rectangle([patch.width - B, p, patch.width, p + ln], fill=0)
        patch.paste(tile, (0, 0), mask)
        out.paste(patch, ((m["world"][0] - b - x0) * t, (m["world"][1] - b - y0) * t), patch)

    for key, m in ms.items():
        out.paste(Image.open(MAPS / game / m["file"]).convert("RGB"),
                  ((m["world"][0] - x0) * t, (m["world"][1] - y0) * t))

    bar = Image.new("RGB", (out.width, 34), (16, 17, 21))
    ImageDraw.Draw(bar).text((10, 11), label, fill=(235, 235, 200))
    sheet = Image.new("RGB", (out.width, out.height + 34), (16, 17, 21))
    sheet.paste(bar, (0, 0))
    sheet.paste(out, (0, 34))
    return sheet


def crop_for(game: str, m: dict, side: str, spans: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    """A window on the world image around one edge: the closed run, 6 tiles of
    map behind it and 5 outside, so the fringe and what it butts against are
    both in the picture."""
    x0, y0, t, _, _ = world_origin(game)
    wx, wy = m["world"][0] - x0, m["world"][1] - y0
    a = min(s[0] for s in spans)
    b = max(s[1] for s in spans)
    IN, OUT = 6, 5
    if side in ("up", "down"):
        left, right = wx + a - OUT, wx + b + OUT
        top = wy - OUT if side == "up" else wy + m["height"] - IN
        bot = wy + IN if side == "up" else wy + m["height"] + OUT
    else:
        top, bot = wy + a - OUT, wy + b + OUT
        left = wx - OUT if side == "left" else wx + m["width"] - IN
        right = wx + IN if side == "left" else wx + m["width"] + OUT
    return left * t, top * t, right * t, bot * t


def sheet(all_edges: dict[str, list[Edge]]) -> list[Path]:
    shipped = hand_sides()
    SHEETS.mkdir(parents=True, exist_ok=True)
    paths = []
    for game, edges in all_edges.items():
        a = atlas(game)
        drawn: dict[str, set] = {}
        for e in edges:
            if e.verdict == "draw":
                drawn.setdefault(e.key, set()).add(e.side)
        before = compose(game, lambda g, k, m: shipped.get(g, {}).get(k, set()),
                         f"{game}  BEFORE - the hand list "
                         f"({sum(len(v) for v in shipped.get(game, {}).values())} sides)")
        after = compose(game, lambda g, k, m: drawn.get(k, set()),
                        f"{game}  AFTER - measured "
                        f"({sum(len(v) for v in drawn.values())} sides)")
        gap = 24
        cv = Image.new("RGB", (before.width + gap + after.width,
                               max(before.height, after.height)), (16, 17, 21))
        cv.paste(before, (0, 0))
        cv.paste(after, (before.width + gap, 0))
        p = SHEETS / f"{game}-world.png"
        cv.save(p)
        paths.append(p)

        # and the same two images cropped, 1:1, to every edge that CHANGED —
        # the world frame is 4000 px wide and a row of trees is 32 of them.
        rows = []
        for e in edges:
            was = e.side in shipped.get(game, {}).get(e.key, set())
            now = e.verdict == "draw"
            if was == now:
                continue
            m = a["maps"][e.key]
            box = crop_for(game, m, e.side, closed_spans(m, e.side))
            box = (max(0, box[0]), max(0, box[1] + 34),
                   min(before.width, box[2]), min(before.height, box[3] + 34))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            L, R = before.crop(box), after.crop(box)
            rows.append((f"{e.key} {e.name} {e.side}: closed {e.closed}/{e.total}, "
                         f"fit {e.fit:.2f}  [left = now, right = proposed "
                         f"{'ON' if now else 'OFF'}]", L, R))
        if rows:
            wide = max(r[1].width * 2 + 16 for r in rows)
            tall = sum(r[1].height + 20 for r in rows) + 10
            det = Image.new("RGB", (wide + 20, tall), (16, 17, 21))
            d = ImageDraw.Draw(det)
            y = 6
            for lbl, L, R in rows:
                d.text((8, y), lbl, fill=(235, 235, 160))
                det.paste(L, (8, y + 16))
                det.paste(R, (8 + L.width + 16, y + 16))
                y += L.height + 20
            p = SHEETS / f"{game}-changes.png"
            det.save(p)
            paths.append(p)
    return paths


# ------------------------------------------------------------------- reporting


def report(all_edges: dict[str, list[Edge]]) -> None:
    print(f"{'game':4} {'key':7} {'map':17} {'side':5} {'closed':>8} {'fit':>5} "
          f"{'flat':>5}  {'verdict':10} hand")
    for game, edges in all_edges.items():
        for e in edges:
            if e.verdict == "road":
                continue
            print(f"{game[:3]:4} {e.key:7} {e.name[:17]:17} {e.side:5} "
                  f"{str(e.closed) + '/' + str(e.total):>8} {e.fit:5.2f} {e.flat:5.2f}  "
                  f"{e.verdict:10} {'HAND' if e.hand else ''}")

    truth = {(g, k, s) for (g, k), ss in HAND.items() for s in ss}
    pred = {(e.game, e.key, e.side) for es in all_edges.values() for e in es
            if e.verdict == "draw"}
    known = {(g, k, s) for (g, k), ss in HAND.items() for s in SIDES
             if any(e.key == k and e.game == g for e in all_edges.get(g, []))}
    tp, fn, fp = truth & pred, truth - pred, pred - truth
    print(f"\nagainst the hand list: {len(tp)}/{len(truth)} recall, "
          f"precision {len(tp)}/{len(pred)} = {len(tp) / max(1, len(pred)):.2f}")
    for x in sorted(fn):
        print(f"  MISSED   {x}")
    for x in sorted(fp):
        print(f"  EXTRA    {x}")

    print("\nblack regions")
    for game in all_edges:
        b = black_regions(game)
        print(f"  {game:12} closed edge, no artwork beyond: "
              f"{b['closed_edges']:3} spans / {b['closed_tiles']:5} tiles   "
              f"unrendered neighbour: {b['unrendered_edges']:3} spans / "
              f"{b['unrendered_tiles']:5} tiles")
        for s in b["unrendered_to"]:
            print(f"                 -> {s}")


def sensitivity() -> None:
    """One control per open parameter: does the draw set move when they do?"""
    base = None
    for cc in (0.80, 0.85, 0.90, 0.95, 1.00):
        pred = set()
        for g in games():
            for e in score(g, cell_cover=cc):
                if e.verdict == "draw":
                    pred.add((e.game, e.key, e.side))
        if base is None:
            base = pred
        print(f"  CELL_COVER={cc:.2f}  draws={len(pred):3}  "
              f"delta vs 0.80: +{len(pred - base)} -{len(base - pred)}")
    print("  fit thresholds — the sorted fit values, so the gaps are visible:")
    vals = sorted({round(e.fit, 2) for g in games() for e in score(g)
                   if e.verdict != "road" and e.flat < FLAT_VETO})
    print("   ", vals)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="rewrite borders.js")
    ap.add_argument("--sheet", action="store_true", help="render the before/after sheet")
    ap.add_argument("--sensitivity", action="store_true", help="parameter controls")
    args = ap.parse_args()

    all_edges = {g: score(g) for g in games()}
    if args.sensitivity:
        sensitivity()
        return 0
    report(all_edges)
    if args.write:
        write_borders(all_edges)
        print(f"\nwrote {BORDERS_JS.relative_to(ROOT)}")
    if args.sheet:
        for p in sheet(all_edges):
            print(f"sheet {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
