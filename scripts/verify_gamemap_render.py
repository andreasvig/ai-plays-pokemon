"""Check a rendered map against a real screenshot from a run.

    venv/bin/python scripts/verify_gamemap_render.py local/runs/<run_id>
    venv/bin/python scripts/verify_gamemap_render.py local/runs/<run_id> --turn 13

The control from artifacts/game-map-render/plan.md §4: the render is only
trustworthy if the terrain under the player matches what the emulator drew.
Every run folder holds ``screenshots/<n>_turn_<n>.png`` at 6x the GBA's
240x160, and ``referee_position`` gives the player's tile for the same turn, so
the 15x10-tile window is known and the two can be compared pixel for pixel.

Three things are expected to differ, and only these three:

- the **grid overlay** the harness itself draws over every screenshot
  (``emulator._draw_grid_overlay``: semi-transparent red at every tile edge,
  2 native px wide, horizontals offset half a tile);
- **sprites** — the player, NPCs — which are object events, not layout (M4);
- the **textbox**, which covers the bottom three tile rows whenever one is open
  — so only the rows above it are compared;
- **animated tiles**, which render at frame 0 by decision M5: the flower beds
  of Pallet Town are the visible case, and they are why a clean turn still
  scores a few percent rather than zero;
- **weather colour maps**. Viridian Forest is ``WEATHER_SHADE``: the game
  darkens every palette entry at runtime, so a correct render of it differs
  from the screenshot in colour on nearly every pixel. A colour map is a
  per-colour lookup by construction, so the check fits one from the frame
  (render colour -> the screenshot colour it most often sits under) and
  measures the residual through it. A render that is structurally wrong cannot
  be rescued that way: no lookup moves a tree onto a roof.

Everything else must agree within 1 per channel, which is the GBA's 5-bit
colour rounding and nothing else. A larger residual means the render is wrong
(the palette split, the tile order, or the layer composition) — not the
screenshot.

One pairing matters: the screenshot the model sees on turn N is taken BEFORE
turn N's buttons, so it shows the tile the referee recorded at turn **N-1**.
Pairing it with turn N's own position compares the frame against where the run
ended up, which on a turn that moved is a different place entirely (90% of the
pixels differ, measured 2026-09-15).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# The same fit the stitcher uses, from the other direction — one map, many
# frames there; one frame, one map here.
from src.app.stitch import fit_colour_map  # noqa: E402

MAPS_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"
GBA_W, GBA_H = 240, 160
TILE = 16
# The camera puts the player at column 7 and half-way down row 4 — the offset
# the harness's own grid overlay also uses (8 px, half a tile).
CAM_X_TILES, CAM_Y_PX = 7, 4 * TILE + 8
TOLERANCE = 1                      # 8-bit values either side of a 5-bit colour
TEXTBOX_TOP = 112                  # the message box owns everything below this


def positions(run_dir: Path) -> dict[int, tuple[int, int, int, int]]:
    out: dict[int, tuple[int, int, int, int]] = {}
    with (run_dir / "events.jsonl").open() as fh:
        for line in fh:
            if '"referee_position"' not in line:
                continue
            e = json.loads(line)
            if e.get("type") == "referee_position" and e.get("x") is not None:
                out[int(e["turn"])] = (int(e["map_group"]), int(e["map_num"]), int(e["x"]), int(e["y"]))
    return out


def battle_turns(run_dir: Path) -> set[int]:
    """Turns whose per-input trace saw a battle — their frame is not the map."""
    out: set[int] = set()
    with (run_dir / "events.jsonl").open() as fh:
        for line in fh:
            if '"turn_input_trace"' not in line:
                continue
            e = json.loads(line)
            if e.get("type") != "turn_input_trace":
                continue
            if any(isinstance(s, dict) and s.get("in_battle") for s in (e.get("samples") or [])):
                out.add(int(e["turn"]))
    return out


def screenshots(run_dir: Path) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for p in sorted((run_dir / "screenshots").glob("*.png")):
        m = re.search(r"turn_(\d+)", p.name)
        if m:
            out[int(m.group(1))] = p
    return out


def compare(shot: Path, rendered: Path, px: int, py: int) -> dict:
    """Residual between the screenshot's terrain and the same window of the map."""
    img = Image.open(shot).convert("RGB")
    small = np.asarray(img.resize((GBA_W, GBA_H), Image.NEAREST), dtype=np.int16)
    full = np.asarray(Image.open(rendered).convert("RGB"), dtype=np.int16)

    ox, oy = px * TILE - CAM_X_TILES * TILE, py * TILE - CAM_Y_PX
    if ox < 0 or oy < 0 or ox + GBA_W > full.shape[1] or oy + GBA_H > full.shape[0]:
        return {"skipped": "the camera window runs off the map (the game draws border tiles there)"}
    win = full[oy: oy + GBA_H, ox: ox + GBA_W]

    X, Y = np.meshgrid(np.arange(GBA_W), np.arange(GBA_H))
    grid = (np.minimum(X % TILE, (TILE - X) % TILE) <= 1) | (np.minimum((Y - 8) % TILE, (8 - Y) % TILE) <= 1)
    sprite = (X >= CAM_X_TILES * TILE - 4) & (X < (CAM_X_TILES + 1) * TILE + 4) & (Y >= CAM_Y_PX - 28) & (Y < CAM_Y_PX + 8)
    keep = ~(grid | sprite) & (Y < TEXTBOX_TOP)

    direct = np.abs(win - small).max(axis=2)[keep]
    out = {"compared": int(keep.sum()), "max": int(direct.max()),
           "over": int((direct > TOLERANCE).sum()), "window": [ox, oy], "via": "direct"}
    if out["over"] <= 0:
        return out
    shifted = np.abs(fit_colour_map(win, small, keep) - small).max(axis=2)[keep]
    if int((shifted > TOLERANCE).sum()) < out["over"]:
        out.update(max=int(shifted.max()), over=int((shifted > TOLERANCE).sum()), via="colour map")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--turn", type=int, action="append", help="check only these turns")
    ap.add_argument("--maps", type=Path, default=MAPS_DIR)
    ap.add_argument("--max-bad-frac", type=float, default=0.06,
                    help="a turn fails above this share of differing terrain px (NPCs and animated tiles)")
    args = ap.parse_args()

    index = json.loads((args.maps / "index.json").read_text())["maps"]
    pos, shots, fights = positions(args.run_dir), screenshots(args.run_dir), battle_turns(args.run_dir)
    turns = args.turn or sorted({t + 1 for t in pos} & set(shots))
    checked = failed = 0
    worst: list[tuple[float, int, dict]] = []
    for t in turns:
        # the frame shown ON turn t is the state the referee polled AFTER t-1
        if t - 1 not in pos or t not in shots:
            continue
        if t in fights or t - 1 in fights:
            continue                       # a battle frame draws no map at all
        g, n, x, y = pos[t - 1]
        entry = index.get(f"{g}:{n}")
        if entry is None:
            continue
        r = compare(shots[t], args.maps / entry["file"], x, y)
        if "skipped" in r:
            continue
        checked += 1
        frac = r["over"] / r["compared"]
        worst.append((frac, t, r))
        if frac > args.max_bad_frac:
            failed += 1
    worst.sort(reverse=True)
    for frac, t, r in worst[:8]:
        print(f"turn {t:4d}  {frac:7.3%} of {r['compared']} terrain px differ "
              f"(max {r['max']}, via {r['via']})")
    clean = sum(1 for f, _, _ in worst if f == 0)
    print(f"{checked} turns checked ({len(fights)} battle turns skipped), "
          f"{clean} pixel-identical, {failed} over {args.max_bad_frac:.0%}")
    if not fights:
        print("note: this run has no per-input trace, so its battle frames could not be "
              "skipped — every fight is counted as a failing turn")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
