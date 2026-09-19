"""Stitch a map atlas out of the frames real runs saw.

    venv/bin/python scripts/stitch_maps.py local/runs/*/
    venv/bin/python scripts/stitch_maps.py local/runs/*/ --verify
    venv/bin/python scripts/stitch_maps.py local/runs/*/ --calibrate

Option C of ``artifacts/game-map-render/per-game-plan.md``: the only source of
map pixels that works for gen 4, gen 5 and ROM hacks, because it needs nothing
but the machine we already drive. FireRed is the game it is BUILT on rather
than the game it is FOR — it is the one with a render from pret's own tilesets
to check against (``--verify``), so every rule can be proven here before it
meets a game where nothing can check it.

Output (gitignored, this is a derived artifact): ``local/stitched/<game>/``
holding one PNG per map plus ``index.json`` with, per map, the canvas origin,
how many frames were placed, what share of the map they covered and how much
the frames disagreed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.app.stitch import (  # noqa: E402
    GBA_FIRERED, MapCanvas, ScreenSpec, canvas_for, fit_colour_map, has_trace, keep_mask, samples,
    to_native, window_origin,
)

MAPS_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"
OUT_DIR = REPO_ROOT / "local" / "stitched"
TOLERANCE = 1          # 8-bit values either side of a 5-bit GBA colour


def collect(run_dirs: list[Path], spec: ScreenSpec, only: str | None,
            require_trace: bool = True) -> tuple[dict[str, list], list[str]]:
    """Every placeable frame of every run, grouped by map, and who was turned away."""
    by_map: dict[str, list] = defaultdict(list)
    refused: list[str] = []
    for run in run_dirs:
        if require_trace and not has_trace(run):
            refused.append(run.name)
            continue
        for s in samples(run, require_trace=False):
            if only and s.map_key != only:
                continue
            by_map[s.map_key].append(s)
    return by_map, refused


def stitch(by_map: dict[str, list], spec: ScreenSpec, threshold: float,
           report: bool = True, histogram: bool = False) -> dict[str, MapCanvas]:
    """Accumulate a map, counting RUNS rather than frames.

    Two levels of vote, and the second one is the one that matters:

    1. **Within a run**, every frame votes, and a frame that disagrees with what
       its own run settled on is dropped and the run re-counted. That removes a
       menu, a fade, a cutscene — anything the run saw once.
    2. **Across runs**, each run gets ONE vote per pixel, whatever it saw.

    Counting frames instead lets one run's long sit-down decide a pixel: Pewter
    Gym came out with a battle screen across two thirds of it (2026-09-19),
    because one run fought Brock for 91 turns from one tile and six of the nine
    runs that reached the gym predate the per-input trace that flags a battle.
    91 frames of Bulbasaur beat three runs' worth of floor.

    One vote per run fixes it for a reason that generalises past battles:
    **wrong content disagrees with itself and right content does not.** Two runs
    fighting in the same doorway see different Pokemon at different HP, so their
    votes split; two runs walking over the same floor tile see the identical
    floor. Anything that belongs to one visit rather than to the map — a battle,
    an NPC's beat, weather, and in gen 5 a season — loses to anything that does
    not.
    """
    keep = keep_mask(spec)
    out: dict[str, MapCanvas] = {}
    for key, group in sorted(by_map.items()):
        origins = {id(s): window_origin(spec, s.x, s.y) for s in group}
        master = canvas_for(spec, list(origins.values()))
        by_run: dict[str, list] = defaultdict(list)
        for s in group:
            by_run[s.run].append(s)
        dropped = blind = 0
        for run, items in sorted(by_run.items()):
            frames = []
            for s in items:
                with Image.open(s.path) as img:
                    frames.append(to_native(img, spec))
            provisional = MapCanvas(*master.origin, master.shape[1], master.shape[0])
            for frame, s in zip(frames, items):
                provisional.add(frame, *origins[id(s)], keep)
            settled, votes = provisional.image()
            kept = []
            for frame, s in zip(frames, items):
                score = agreement(settled, votes, provisional.origin, frame, *origins[id(s)], keep)
                if score is None:
                    blind += 1
                if score is None or score >= threshold:
                    kept.append((frame, s))
                else:
                    dropped += 1
            final = MapCanvas(*master.origin, master.shape[1], master.shape[0])
            for frame, s in kept:
                final.add(frame, *origins[id(s)], keep)
            rgb, seen = final.image()
            master.add(rgb, *master.origin, seen > 0)      # one vote, for the whole run
        out[key] = master
        if report:
            print(f"{key:>6}  {len(group):5d} frames from {len(by_run)} runs  "
                  f"canvas {master.shape[1]}x{master.shape[0]}  dropped {dropped}"
                  + (f"  (unjudgeable {blind})" if blind else ""))
        if histogram:
            print(f"        runs voting: {sorted(len(v) for v in by_run.values())}")
    return out


def agreement(settled: np.ndarray, votes: np.ndarray, origin: tuple[int, int],
              frame: np.ndarray, ox: int, oy: int, keep: np.ndarray) -> float | None:
    """Share of a frame's pixels that match the settled map where others have voted.

    None when no pixel of this frame has a second opinion.
    """
    rgb = settled
    h, w = frame.shape[:2]
    x0, y0 = ox - origin[0], oy - origin[1]
    win = rgb[y0:y0 + h, x0:x0 + w].astype(np.int16)
    judged = keep & (votes[y0:y0 + h, x0:x0 + w] >= 2)
    if not judged.any():
        return None
    diff = np.abs(win - frame.astype(np.int16)).max(axis=2)
    return float((diff[judged] <= TOLERANCE).mean())


def write(canvases: dict[str, MapCanvas], spec: ScreenSpec, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    index = {"version": 1, "game": spec.name, "tile_px": spec.tile, "source": "stitched", "maps": {}}
    for key, canvas in canvases.items():
        rgb, votes = canvas.image()
        seen = votes > 0
        Image.fromarray(rgb).save(out_dir / f"{key.replace(':', '-')}.png")
        index["maps"][key] = {
            "file": f"{key.replace(':', '-')}.png",
            "origin": list(canvas.origin),
            "width": int(canvas.shape[1]), "height": int(canvas.shape[0]),
            "frames": int(canvas.frames),
            "seen_px": int(seen.sum()),
            "coverage": round(float(seen.mean()), 4),
            "disputed_px": int((canvas.disputed() > 0).sum()),
        }
    (out_dir / "index.json").write_text(json.dumps(index, indent=1))
    return index


def verify(canvases: dict[str, MapCanvas], spec: ScreenSpec, maps_dir: Path) -> int:
    """Compare the stitch against the render from pret's tilesets.

    Only where a frame actually voted, and only inside the map's own area: the
    canvas also holds the border tiles the game draws outside a map, which the
    render does not have.
    """
    atlas = json.loads((maps_dir / "index.json").read_text())["maps"]
    bad = 0
    print(f"\n{'map':>6}  {'compared':>9}  {'equal':>7}  {'within 1':>8}  {'worst':>5}  via")
    for key, canvas in sorted(canvases.items()):
        entry = atlas.get(key)
        if entry is None:
            print(f"{key:>6}  (not in the pret atlas — nothing to check against)")
            continue
        ref = np.asarray(Image.open(maps_dir / entry["file"]).convert("RGB"), dtype=np.int16)
        rgb, votes = canvas.image()
        ox, oy = canvas.origin
        h, w = ref.shape[:2]
        # the map's own area, expressed in canvas coordinates
        y0, x0 = -oy, -ox
        if y0 < 0 or x0 < 0 or y0 + h > canvas.shape[0] or x0 + w > canvas.shape[1]:
            yy0, xx0 = max(y0, 0), max(x0, 0)
            yy1 = min(y0 + h, canvas.shape[0])
            xx1 = min(x0 + w, canvas.shape[1])
        else:
            yy0, xx0, yy1, xx1 = y0, x0, y0 + h, x0 + w
        got = rgb[yy0:yy1, xx0:xx1].astype(np.int16)
        seen = votes[yy0:yy1, xx0:xx1] > 0
        want = ref[yy0 - y0: yy1 - y0, xx0 - x0: xx1 - x0]
        if got.shape != want.shape or not seen.any():
            print(f"{key:>6}  (no overlap with the render)")
            continue
        diff = np.abs(got - want).max(axis=2)[seen]
        equal = float((diff == 0).mean())
        within = float((diff <= TOLERANCE).mean())
        via = "direct"
        if within < 0.90:
            # A runtime colour map (weather) transforms every colour and moves no
            # terrain. Fit one and measure the residual through it.
            mapped = fit_colour_map(want, got, seen)
            shifted = np.abs(got - mapped).max(axis=2)[seen]
            if float((shifted <= TOLERANCE).mean()) > within:
                diff, via = shifted, "colour map"
                equal = float((diff == 0).mean())
                within = float((diff <= TOLERANCE).mean())
        print(f"{key:>6}  {seen.sum():9d}  {equal:7.2%}  {within:8.2%}  {int(diff.max()):5d}  {via}")
        if within < 0.90:
            bad += 1
    return bad


def calibrate(by_map: dict[str, list], spec: ScreenSpec) -> None:
    """Re-derive the camera from motion alone, and say whether the spec matches.

    Two measurements, neither of which uses the render:

    - **tile size** — between two frames one tile apart the world shifts by
      exactly one tile, so the shift that minimises the mismatch IS the tile
      size. A pair whose best shift is not clearly better than the runner-up is
      discarded rather than voted with: a frame where a textbox opened, or a
      cutscene moved the camera, has no honest shift to give.
    - **the player's anchor** — where that shift FAILS. The world obeys it and
      the player does not, so the pixels that stay wrong after the world has
      been lined up are the player (an NPC fails it too, but a different one
      each time, while the player is in the same box every time).
    """
    keep = keep_mask(spec)                          # map pixels: grid, sprite, textbox out
    whole = keep_mask(spec, sprite=False)           # the same minus the sprite cut-out
    shifts: Counter = Counter()
    bad = np.zeros((spec.height, spec.width), dtype=np.int32)
    seen = np.zeros((spec.height, spec.width), dtype=np.int32)
    pairs = ambiguous = 0
    for key, group in sorted(by_map.items()):
        for a, b in zip(group, group[1:]):
            if a.map_key != b.map_key or b.turn != a.turn + 1:
                continue
            dx, dy = b.x - a.x, b.y - a.y
            if abs(dx) + abs(dy) != 1:
                continue
            with Image.open(a.path) as ia, Image.open(b.path) as ib:
                fa, fb = to_native(ia, spec), to_native(ib, spec)
            scored = sorted((_mismatch(fa, fb, dx * s, dy * s, keep), s) for s in range(4, 33))
            best_score, best = scored[0]
            runner_up = next((sc for sc, s in scored[1:] if abs(s - best) > 2), float("inf"))
            if not best_score < 0.5 * runner_up:
                ambiguous += 1
                continue                            # no unambiguous shift: discard the pair
            shifts[best] += 1
            pairs += 1
            _accumulate_residual(fa, fb, dx * best, dy * best, whole, bad, seen)
    if not pairs:
        print(f"no unambiguous one-tile pairs ({ambiguous} discarded) — nothing to calibrate from")
        return
    tile, votes = shifts.most_common(1)[0]
    print(f"\ncalibration from {pairs} one-tile moves ({ambiguous} ambiguous pairs discarded), "
          f"no render involved:")
    print(f"  tile size    {tile} px  ({votes}/{pairs} pairs agree)   spec says {spec.tile}")
    rate = np.divide(bad, np.maximum(seen, 1), dtype=float)
    always = (rate >= 0.9) & (seen >= pairs * 0.5)
    ys, xs = np.where(always)
    if len(xs):
        left, right, top, bottom = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        print(f"  screen-fixed box  x {left}..{right}  y {top}..{bottom}  ({len(xs)} px)")
        # The sprite is drawn INSIDE its tile and usually a pixel or two clear of
        # the edges, so this places the player's tile to within about 2 px — enough
        # to confirm a spec, not to replace one. On a game with no render to check
        # against, that residual shifts the whole atlas by up to 2 px: invisible in
        # the artwork, and under a sixth of a tile, so the tile grid still lands
        # on the right tiles.
        print(f"  => player column  {left // spec.tile}  (box starts {left % spec.tile} px "
              f"into it)   spec says {spec.cam_x_tiles}")
        print(f"  => tile top row   {bottom + 1 - spec.tile}  +/- 2          "
              f"spec says {spec.cam_y_px}")
    else:
        print("  no pixel disagreed with the world shift in 90% of pairs")


def _mismatch(a: np.ndarray, b: np.ndarray, sx: int, sy: int, keep: np.ndarray) -> float:
    """Mean channel difference between ``a`` and ``b`` shifted by (sx, sy)."""
    h, w = a.shape[:2]
    ax0, bx0 = max(0, sx), max(0, -sx)
    ay0, by0 = max(0, sy), max(0, -sy)
    ww, hh = w - abs(sx), h - abs(sy)
    va = a[ay0:ay0 + hh, ax0:ax0 + ww].astype(np.int16)
    vb = b[by0:by0 + hh, bx0:bx0 + ww].astype(np.int16)
    m = keep[ay0:ay0 + hh, ax0:ax0 + ww] & keep[by0:by0 + hh, bx0:bx0 + ww]
    if not m.any():
        return float("inf")
    return float(np.abs(va - vb).max(axis=2)[m].mean())


def _accumulate_residual(a: np.ndarray, b: np.ndarray, sx: int, sy: int,
                         mask: np.ndarray, bad: np.ndarray, seen: np.ndarray) -> None:
    """Count, in B's frame, where A lined up on the world shift still disagrees."""
    h, w = a.shape[:2]
    ax0, bx0 = max(0, sx), max(0, -sx)
    ay0, by0 = max(0, sy), max(0, -sy)
    ww, hh = w - abs(sx), h - abs(sy)
    va = a[ay0:ay0 + hh, ax0:ax0 + ww].astype(np.int16)
    vb = b[by0:by0 + hh, bx0:bx0 + ww].astype(np.int16)
    m = mask[ay0:ay0 + hh, ax0:ax0 + ww] & mask[by0:by0 + hh, bx0:bx0 + ww]
    diff = np.abs(va - vb).max(axis=2) > TOLERANCE
    seen[by0:by0 + hh, bx0:bx0 + ww] += m
    bad[by0:by0 + hh, bx0:bx0 + ww] += (diff & m)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--only", help="one map key, e.g. 3:0")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--maps", type=Path, default=MAPS_DIR)
    ap.add_argument("--allow-untraced", action="store_true",
                    help="include runs with no per-input trace — their battle frames cannot be filtered")
    ap.add_argument("--threshold", type=float, default=0.6,
                    help="a frame agreeing with less than this share of the settled map is dropped")
    ap.add_argument("--scores", action="store_true", help="print the per-frame agreement histogram")
    ap.add_argument("--verify", action="store_true", help="compare against the pret render")
    ap.add_argument("--calibrate", action="store_true", help="re-derive the camera from motion")
    args = ap.parse_args()

    spec = GBA_FIRERED
    runs = [d for d in args.run_dirs if (d / "events.jsonl").exists()]
    by_map, refused = collect(runs, spec, args.only, require_trace=not args.allow_untraced)
    total = sum(len(v) for v in by_map.values())
    print(f"{len(runs) - len(refused)} runs, {total} placeable frames, {len(by_map)} maps")
    if refused:
        print(f"{len(refused)} runs refused: no per-input trace, so a battle frame cannot be "
              f"told from a map frame (--allow-untraced to include them anyway)")
    if not total:
        return 1
    if args.calibrate:
        calibrate(by_map, spec)
        return 0
    canvases = stitch(by_map, spec, args.threshold, histogram=args.scores)
    index = write(canvases, spec, args.out or OUT_DIR / spec.name)
    seen = sum(m["seen_px"] for m in index["maps"].values())
    print(f"\nwrote {len(index['maps'])} maps, {seen} pixels seen")
    if args.verify:
        return 1 if verify(canvases, spec, args.maps) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
