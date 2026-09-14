"""Count overworld steps per turn from a run's dashboard screen recording.

Pilot for the movement half of ``artifacts/battle-and-movement-fidelity``
(2026-09-14). A run's ``recording.mp4`` is a 1920x1080 / 30 fps capture of the
benchmark dashboard (``src/dashboard/recorder.py``): a headless Chrome renders
the run page and a sampler pastes the raw emulator frame into the page's game
rectangle. Neither the turn number nor the step count is in the video metadata,
so both are recovered from pixels. Three facts about this recording drive the
design, all verified on the pilot run:

* **The video is not wall-clock linear.** The recorder's gate is open only while
  a turn executes (``cut-thinking``), so the model's thinking time does not exist
  in the file - 2532 s of run become 1168 s of video. ``turn_start`` timestamps
  therefore cannot be mapped onto the video timeline. The TURN counter box
  painted top-left can: its crop is byte-identical inside a turn and changes
  exactly once per turn boundary, so its change points segment the video into one
  frame range per turn. ``tesseract``, when present, reads a few segments back as
  a self-check; the mapping itself needs no OCR.

* **The game rectangle is not fixed.** The dashboard is responsive and the game
  card shrinks as the memory-dictionary panel below it grows, so the canvas moves
  and resizes mid-run (1080x720 at x=21,y=211 early; 896x596 at x=112,y=165 once
  memory fills up). A fixed crop silently starts measuring dashboard text. The
  rect is therefore calibrated **per turn** against that turn's own
  ``screenshots/NNNNN_turn_N.png`` - ground truth for what the canvas showed at
  that video frame - seeded from the page geometry and from rects already seen.

* **A step is exactly one tile, and the camera rests on tile boundaries.** With
  a correct rect the canvas reduces to the native 240x160 GBA raster, where an
  overworld step scrolls the whole background by exactly 16 px over ~9 video
  frames. Consecutive frames are matched by brute-force integer translation along
  each axis (movement is never diagonal). A pair is accepted as a translation
  only when the best shift explains the change far better than the null shift, so
  fades, text boxes and battle animation contribute nothing. Per-axis
  accumulators emit one step per 16 px, and whatever sub-tile residue is left
  when the screen settles is rounded to the nearest whole tile - a settled camera
  is always tile-aligned, so that rounding is a free correction rather than a
  guess. Only a LARGE non-translation resets an accumulator mid-step; a blinking
  sprite must not, or one step gets split across the break and lost.

Writes ``<run_dir>/steps_backfill.json``:

    {"run_id":..., "video":{...}, "canvas":{...}, "totals":{...},
     "turns":[{"turn":N,"overworld_steps":K,"frames":{...},"method_notes":"..."}],
     "validation":{"check1_displacement":..., "check2_no_direction":...,
                   "check3_battle_ab":...}}

Usage: venv/bin/python scripts/backfill_steps.py <run_dir> [--workdir DIR]
                                                 [--no-ocr-check]
Read-only except for the one JSON it writes in the run dir.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

GBA_W, GBA_H = 240, 160
TILE = 16  # GBA px per map tile == px per tile in the 240x160 raster

# Turn-counter digits, relative to the dashboard's top-left. This box does not
# move with the responsive layout - it is in the fixed stat-card row.
TURNBOX = (26, 80, 110, 40)  # x, y, w, h

# Frame-pair classification, in mean-absolute-difference of 8-bit grey.
STATIC_MAD = 0.8  # below this the pair is identical bar sprite animation
ACCEPT_MAD = 5.0  # a real translation leaves a small residual (measured 0.8-3.6)
ACCEPT_RATIO = 0.45  # ...and must beat the null shift by this factor (msrd <0.2)
MAX_SHIFT = 8  # px per frame pair; a step is ~2 px/frame, 8 covers dropped ones
RESET_MAD = 6.0  # only a change this big is a scene break worth forgetting a
# partial tile over. Sprite blinks and NPC animation land at 0.8-2.5 and must
# NOT reset the accumulator: on the pilot run they were splitting single steps.
REST_FRAMES = 3  # consecutive static pairs that prove the camera has settled

CALIB_REDO_MAD = 15.0  # above this, the seeded rect search is not trusted
RECT_SNAP_PX = 8  # rects within this of a known one are treated as the same


# --------------------------------------------------------------------------- io


def probe(video: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {"width": s["width"], "height": s["height"],
            "fps": float(num) / float(den),
            "duration_s": float(d["format"]["duration"])}


def grab_frame(video: Path, frame_idx: int, meta: dict) -> np.ndarray:
    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{frame_idx / meta['fps']:.4f}",
         "-i", str(video), "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(meta["height"], meta["width"])


# ------------------------------------------------------------- frame -> turn map


def turn_segments(video: Path, work: Path, n_turns: int) -> tuple[list[int], dict]:
    """Segment the video into one frame range per turn via the TURN counter."""
    x, y, w, h = TURNBOX
    raw = work / "turnbox.raw"
    with raw.open("wb") as fh:
        subprocess.run([FFMPEG, "-v", "error", "-i", str(video),
                        "-vf", f"crop={w}:{h}:{x}:{y},fps=30",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       stdout=fh, check=True)
    a = np.memmap(raw, dtype=np.uint8, mode="r")
    n_frames = a.size // (w * h)
    a = a[: n_frames * w * h].reshape(n_frames, h, w)
    prev = a[0].astype(np.float32)
    changes, mads = [], []
    for i in range(1, n_frames):
        cur = a[i].astype(np.float32)
        m = float(np.abs(cur - prev).mean())
        mads.append(m)
        if m > 0.5:
            changes.append(i)
        prev = cur
    mads = np.asarray(mads)
    diag = {
        "n_frames": n_frames,
        "n_changes": len(changes),
        "expected_changes": n_turns - 1,
        # pairs that changed too little to count but not nothing: if this is
        # large, the 0.5 threshold is sitting inside a real distribution.
        "ambiguous_pairs": int(((mads > 0.05) & (mads <= 0.5)).sum()),
        "exact": len(changes) == n_turns - 1,
    }
    del a
    os.unlink(raw)
    return [0] + changes + [n_frames], diag


def ocr_check(video: Path, meta: dict, bounds: list[int],
              turns: list[int]) -> list[dict]:
    """Read the counter on a few segment midpoints - a self-check, not the map."""
    if not shutil.which("tesseract"):
        return []
    x, y, w, h = TURNBOX
    out = []
    with tempfile.TemporaryDirectory() as td:
        for t in turns:
            if t > len(bounds) - 1:
                continue
            mid = (bounds[t - 1] + bounds[t]) // 2
            g = grab_frame(video, mid, meta)[y:y + h, x:x + w]
            p = Path(td) / f"t{t}.png"
            Image.fromarray(g).resize((w * 4, h * 4), Image.LANCZOS).save(p)
            r = subprocess.run(
                ["tesseract", str(p), "stdout", "--psm", "7",
                 "-c", "tessedit_char_whitelist=0123456789"],
                capture_output=True, text=True)
            got = r.stdout.strip()
            out.append({"turn": t, "frame": mid, "ocr": got, "ok": got == str(t)})
    return out


# ------------------------------------------------------------------ canvas rect


def point_sample(img: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    """Reconstruct the 240x160 GBA raster from the canvas by point sampling.

    The recorder pastes the emulator frame into the DOM rectangle with a NEAREST
    resize, so the canvas holds each GBA pixel replicated an integer (but not
    constant) number of times. Sampling at the GBA pixel centres inverts that,
    and - unlike an area downscale - it is cheap enough to run inside the rect
    search, which is the only place this is used.
    """
    x0, y0, w, h = rect
    xi = (x0 + (np.arange(GBA_W) + 0.5) * w / GBA_W).astype(np.int32)
    yi = (y0 + (np.arange(GBA_H) + 0.5) * h / GBA_H).astype(np.int32)
    return img[np.ix_(yi, xi)]


def geometric_seed(g: np.ndarray) -> tuple[int, int, int] | None:
    """Rough canvas rect from the page alone: the one big non-background block.

    The dashboard paints flat near-white behind and around the cards. Eroding the
    "not near-white" mask removes glyph strokes and card hairlines, leaving the
    canvas as the only wide solid run. Returns None when the game itself is
    mostly light (a full-screen white flash) and the block is not 3:2.
    """
    H, W = g.shape
    m = g < 224
    m[:, int(W * 0.60):] = False  # live-trace panel lives on the right
    m[:110, :] = False  # header + stat cards
    er = m.copy()
    for d in (1, 2, 3, 4):
        er &= np.roll(m, d, 0) & np.roll(m, -d, 0)
    m2 = er.copy()
    for d in (1, 2, 3, 4):
        er &= np.roll(m2, d, 1) & np.roll(m2, -d, 1)

    def longest(mask: np.ndarray) -> np.ndarray:
        pad = np.zeros((mask.shape[0], 1), bool)
        r = np.concatenate([pad, mask, pad], axis=1)
        out = np.zeros(mask.shape[0], np.int32)
        for i in range(mask.shape[0]):
            idx = np.flatnonzero(r[i][1:] != r[i][:-1])
            if idx.size:
                out[i] = (idx[1::2] - idx[0::2]).max()
        return out

    rr = np.nonzero(longest(er) > 0.32 * W)[0]
    cc = np.nonzero(longest(er.T) > 0.28 * H)[0]
    if not len(rr) or not len(cc):
        return None
    # the erosion ate 4 px off every side
    x0, y0 = int(cc.min()) - 4, int(rr.min()) - 4
    w, h = int(cc.max() - cc.min()) + 9, int(rr.max() - rr.min()) + 9
    if not 1.30 < w / max(h, 1) < 1.75:
        return None
    return x0, y0, w


def refine_rect(img: np.ndarray, ref: np.ndarray,
                seed: tuple[int, int, int]) -> tuple[float, tuple[int, int, int]]:
    """Coordinate-descent (x0, y0, w) to minimise |point_sample - screenshot|."""
    H, W = img.shape

    def mad(x0: int, y0: int, w: int) -> float:
        h = int(round(w * GBA_H / GBA_W))
        if x0 < 0 or y0 < 0 or w < 400 or x0 + w > W or y0 + h > H:
            return 1e9
        return float(np.abs(point_sample(img, (x0, y0, w, h)).astype(np.float32)
                            - ref).mean())

    x0, y0, w = seed
    cur = mad(x0, y0, w)
    for _ in range(8):
        improved = False
        for i, rng in ((0, 10), (1, 10), (2, 14)):
            for d in range(-rng, rng + 1):
                if d == 0:
                    continue
                q = [x0, y0, w]
                q[i] += d
                m = mad(*q)
                if m < cur - 1e-9:
                    cur, improved = m, True
                    x0, y0, w = q
        if not improved:
            break
    return cur, (x0, y0, w)


def global_rect(img: np.ndarray, ref: np.ndarray) -> tuple[int, int, int]:
    """Coarse grid over the whole plausible canvas placement (last resort)."""
    H, W = img.shape
    best = None
    for w in range(700, 1200, 10):
        h = int(round(w * GBA_H / GBA_W))
        for x0 in range(0, 230, 10):
            for y0 in range(100, 330, 10):
                if x0 + w > W or y0 + h > H:
                    continue
                d = point_sample(img, (x0, y0, w, h)).astype(np.float32)
                m = float(np.abs(d[::2, ::2] - ref[::2, ::2]).mean())
                if best is None or m < best[0]:
                    best = (m, (x0, y0, w))
    return best[1]


def calibrate_turns(video: Path, meta: dict, bounds: list[int], run_dir: Path,
                    ev: dict, n_turns: int) -> tuple[list[tuple], list[dict]]:
    """One canvas rect per turn, calibrated against that turn's screenshot."""
    rects: list[tuple[int, int, int, int]] = []
    diag: list[dict] = []
    library: list[tuple[int, int, int]] = []
    prev: tuple[int, int, int] | None = None
    for t in range(1, n_turns + 1):
        shot = resolve_screenshot(run_dir, ev, t)
        if shot is None:
            rects.append(rects[-1] if rects else
                         (0, 0, meta["width"], int(meta["width"] * 2 / 3)))
            diag.append({"turn": t, "source": "carried", "mad": None,
                         "rect": list(rects[-1])})
            continue
        ref = np.asarray(Image.open(shot).convert("L")
                         .resize((GBA_W, GBA_H), Image.BOX)).astype(np.float32)
        g = grab_frame(video, bounds[t - 1], meta)
        seeds = [s for s in [geometric_seed(g), prev, *library] if s]
        best = None
        for s in seeds:
            m, r = refine_rect(g, ref, s)
            if best is None or m < best[0]:
                best = (m, r)
        source = "seeded"
        if best is None or best[0] > CALIB_REDO_MAD:
            m, r = refine_rect(g, ref, global_rect(g, ref))
            if best is None or m < best[0]:
                best, source = (m, r), "global"
        mad, rect3 = best
        # snap to an already-seen rect so near-identical calibrations do not
        # split the decode into dozens of groups
        for known in library:
            if max(abs(a - b) for a, b in zip(known, rect3)) <= RECT_SNAP_PX:
                rect3 = known
                break
        else:
            library.append(rect3)
        prev = rect3
        x0, y0, w = rect3
        h = int(round(w * GBA_H / GBA_W))
        # ffmpeg's crop filter silently rounds an odd x/y offset DOWN to even on
        # yuv420p, which would shift the crop 1 px away from what was calibrated.
        # Snap here instead, and report the MAD of the rect actually used.
        rect = (x0 - x0 % 2, y0 - y0 % 2, w - w % 2, h - h % 2)
        rects.append(rect)
        snapped = float(np.abs(point_sample(g, rect).astype(np.float32)
                               - ref).mean())
        diag.append({"turn": t, "source": source, "mad": round(snapped, 2),
                     "mad_unsnapped": round(mad, 2), "rect": list(rect)})
    return rects, diag


# ------------------------------------------------------------------ step counter


def axis_shifts(cur: np.ndarray, prev: np.ndarray) -> tuple[int, int, float, float]:
    """Best axis-aligned integer translation of `cur` relative to `prev`.

    Overworld movement is never diagonal, so only (dx,0) and (0,dy) are searched.
    Returns (dx, dy, mad_at_best, mad_at_zero).
    """
    mad0 = float(np.abs(cur - prev).mean())
    best = (mad0, 0, 0)
    for d in range(1, MAX_SHIFT + 1):
        for k in (d, -d):
            if k > 0:
                m = float(np.abs(cur[k:, :] - prev[:-k, :]).mean())
            else:
                m = float(np.abs(cur[:k, :] - prev[-k:, :]).mean())
            if m < best[0]:
                best = (m, 0, k)
            if k > 0:
                m = float(np.abs(cur[:, k:] - prev[:, :-k]).mean())
            else:
                m = float(np.abs(cur[:, :k] - prev[:, -k:]).mean())
            if m < best[0]:
                best = (m, k, 0)
    return best[1], best[2], best[0], mad0


def group_turns(rects: list[tuple]) -> list[tuple[int, int, tuple]]:
    """Consecutive turns sharing a canvas rect - one ffmpeg decode each."""
    groups = []
    start = 0
    for i in range(1, len(rects) + 1):
        if i == len(rects) or rects[i] != rects[start]:
            groups.append((start + 1, i, rects[start]))  # 1-based, inclusive
            start = i
    return groups


def count_steps(video: Path, meta: dict, bounds: list[int],
                rects: list[tuple]) -> tuple[list[dict], dict]:
    n_turns = len(rects)
    per_turn = [{"turn": i + 1, "overworld_steps": 0, "steps_x": 0, "steps_y": 0,
                 "frames": {"start": bounds[i], "end": bounds[i + 1] - 1,
                            "count": bounds[i + 1] - bounds[i],
                            "moving": 0, "rejected": 0, "static": 0},
                 "rect": list(rects[i])} for i in range(n_turns)]
    groups = group_turns(rects)
    fsize = GBA_W * GBA_H
    total_rejected = 0
    for first, last, rect in groups:
        x, y, w, h = rect
        f0, f1 = bounds[first - 1], bounds[last]
        proc = subprocess.Popen(
            [FFMPEG, "-v", "error", "-ss", f"{f0 / meta['fps']:.4f}",
             "-i", str(video), "-frames:v", str(f1 - f0),
             "-vf", f"crop={w}:{h}:{x}:{y},scale={GBA_W}:{GBA_H}:flags=area,fps=30",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            stdout=subprocess.PIPE, bufsize=fsize * 8)
        acc_x = acc_y = 0.0
        static_run = 0
        prev = None
        idx = f0
        seg = first - 1  # 0-based index into per_turn
        while True:
            buf = proc.stdout.read(fsize)
            if len(buf) < fsize:
                break
            cur = np.frombuffer(buf, np.uint8).reshape(
                GBA_H, GBA_W).astype(np.float32)
            while seg + 1 < last and idx >= bounds[seg + 1]:
                seg += 1
            if prev is not None:
                rec = per_turn[seg]
                dx = dy = 0
                if float(np.abs(cur - prev).mean()) < STATIC_MAD:
                    rec["frames"]["static"] += 1
                    static_run += 1
                    # The camera only ever comes to rest on a tile boundary, so
                    # a settled screen is a free calibration: whatever sub-tile
                    # residue the per-frame shifts accumulated is measurement
                    # error and the true travel is the nearest whole tile. Doing
                    # this only at rest (never at a rejection) is what keeps a
                    # 15 px reading from silently cancelling the next step.
                    if static_run == REST_FRAMES and (acc_x or acc_y):
                        for ax in ("x", "y"):
                            acc = acc_x if ax == "x" else acc_y
                            n = int(abs(acc) / TILE + 0.5)
                            rec[f"steps_{ax}"] += n
                            rec["overworld_steps"] += n
                        acc_x = acc_y = 0.0
                else:
                    static_run = 0
                    dx, dy, mad, mad0 = axis_shifts(cur, prev)
                    if (dx or dy) and mad < ACCEPT_MAD and mad < ACCEPT_RATIO * mad0:
                        rec["frames"]["moving"] += 1
                    else:
                        dx = dy = 0
                        rec["frames"]["rejected"] += 1
                        total_rejected += 1
                        if mad0 >= RESET_MAD:
                            # a big non-translation (text box, fade, warp) breaks
                            # camera continuity: drop any partial tile in flight.
                            # Rounding it up instead was measured on the pilot
                            # run: it recovers one real step but invents four
                            # (turns 14/67/84/149), so dropping wins.
                            acc_x = acc_y = 0.0
                if dx:
                    acc_x += dx
                    while abs(acc_x) >= TILE:
                        acc_x -= TILE * (1 if acc_x > 0 else -1)
                        rec["steps_x"] += 1
                        rec["overworld_steps"] += 1
                if dy:
                    acc_y += dy
                    while abs(acc_y) >= TILE:
                        acc_y -= TILE * (1 if acc_y > 0 else -1)
                        rec["steps_y"] += 1
                        rec["overworld_steps"] += 1
            prev = cur
            idx += 1
        proc.stdout.close()
        proc.wait()
    return per_turn, {"groups": [{"turns": [g[0], g[1]], "rect": list(g[2])}
                                 for g in groups],
                      "rejected_pairs": total_rejected}


# ------------------------------------------------------------------- run events


def load_events(run_dir: Path) -> dict:
    positions: dict[int, tuple] = {}
    actions: dict[int, list] = {}
    shots: dict[int, str] = {}
    for line in (run_dir / "events.jsonl").open():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t == "referee_position":
            positions[e["turn"]] = (e["map_group"], e["map_num"], e["x"], e["y"])
        elif t == "turn_explanation":
            actions[e["turn"]] = list(e.get("explanation", {}).get("action") or [])
        elif t == "screenshot" and str(e.get("label", "")).startswith("turn_"):
            shots[int(e["label"].split("_")[1])] = e["file"]
    return {"positions": positions, "actions": actions, "screenshots": shots}


def resolve_screenshot(run_dir: Path, ev: dict, turn: int) -> Path | None:
    rel = ev["screenshots"].get(turn)
    if rel:
        p = Path(rel)
        if p.exists():
            return p
        p = run_dir / "screenshots" / p.name
        if p.exists():
            return p
    hits = sorted((run_dir / "screenshots").glob(f"*_turn_{turn}.png"))
    return hits[0] if hits else None


def is_battle_screen(png: Path) -> bool | None:
    """Dark-navy share of the bottom 28% > 0.08 == a battle text box is up."""
    if not png.exists():
        return None
    a = np.asarray(Image.open(png).convert("RGB")).astype(np.int16)
    band = a[int(a.shape[0] * 0.72):]
    r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    navy = (b > r + 20) & (b > g + 10) & (b < 120)
    return bool(navy.mean() > 0.08)


DIRECTIONS = {"up", "down", "left", "right"}
PASSIVE = {"a", "b", "wait"}


def validate(per_turn, ev, run_dir: Path) -> dict:
    steps = {t["turn"]: t["overworld_steps"] for t in per_turn}
    pos, act = ev["positions"], ev["actions"]

    # check 1 - per-turn grid displacement must not exceed detected steps
    viol, checked, map_changes = [], 0, []
    for turn in sorted(steps):
        a, b = pos.get(turn - 1), pos.get(turn)
        if a is None or b is None:
            continue
        if a[:2] != b[:2]:
            map_changes.append(turn)
            continue
        checked += 1
        dist = abs(a[2] - b[2]) + abs(a[3] - b[3])
        if dist > steps[turn]:
            viol.append({"turn": turn, "displacement": dist,
                         "detected": steps[turn], "from": list(a), "to": list(b),
                         "inputs": act.get(turn, [])})
    c1 = {"turns_checked": checked, "map_change_turns": map_changes,
          "violations": len(viol),
          "agreement_rate": round(1 - len(viol) / checked, 4) if checked else None,
          "violating_turns": viol}

    # check 2 - no direction input in the action list => expect ~0 steps
    no_dir = [t for t in sorted(steps) if not (set(act.get(t, [])) & DIRECTIONS)]
    c2 = {"turns": no_dir, "n": len(no_dir),
          "n_with_steps": sum(1 for t in no_dir if steps[t]),
          "nonzero": [{"turn": t, "detected": steps[t], "inputs": act.get(t, [])}
                      for t in no_dir if steps[t]]}

    # check 3 - battle-start screenshot and an all-passive action list => 0 steps
    c3_turns, c3_bad = [], []
    for t in sorted(steps):
        a = act.get(t, [])
        if not a or set(a) - PASSIVE:
            continue
        p = resolve_screenshot(run_dir, ev, t)
        if p is None or is_battle_screen(p) is not True:
            continue
        c3_turns.append(t)
        if steps[t]:
            c3_bad.append({"turn": t, "detected": steps[t]})
    c3 = {"turns": c3_turns, "n": len(c3_turns), "n_with_steps": len(c3_bad),
          "nonzero": c3_bad}

    return {"check1_displacement": c1, "check2_no_direction": c2,
            "check3_battle_ab": c3}


# ------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--no-ocr-check", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    video = run_dir / "recording.mp4"
    if not video.exists():
        print(f"no recording.mp4 in {run_dir}", file=sys.stderr)
        return 2
    work = Path(args.workdir or tempfile.mkdtemp(prefix="steps_"))
    work.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    meta = probe(video)
    ev = load_events(run_dir)
    n_turns = len(ev["actions"])

    bounds, seg_diag = turn_segments(video, work, n_turns)
    if not seg_diag["exact"]:
        print(f"WARNING: {seg_diag['n_changes']} counter changes for {n_turns} "
              f"turns - the frame->turn map is not exact", file=sys.stderr)
        while len(bounds) - 1 > n_turns:
            bounds.pop()
        bounds[-1] = seg_diag["n_frames"]
        while len(bounds) - 1 < n_turns:
            bounds.insert(-1, bounds[-1])
    t_seg = time.time()

    ocr = [] if args.no_ocr_check else ocr_check(
        video, meta, bounds,
        sorted({1, 2, max(2, n_turns // 3), max(3, 2 * n_turns // 3), n_turns}))
    t_ocr = time.time()

    rects, calib = calibrate_turns(video, meta, bounds, run_dir, ev, n_turns)
    t_canvas = time.time()

    per_turn, step_diag = count_steps(video, meta, bounds, rects)
    t_steps = time.time()

    for rec in per_turn:
        a = ev["actions"].get(rec["turn"], [])
        rec["method_notes"] = (
            f"{rec['frames']['count']} frames "
            f"({rec['frames']['moving']} translating, "
            f"{rec['frames']['rejected']} non-translation, "
            f"{rec['frames']['static']} static); "
            f"steps x={rec['steps_x']} y={rec['steps_y']}; "
            f"canvas={rec['rect']}; "
            f"inputs={'+'.join(a) if a else 'none'}")

    val = validate(per_turn, ev, run_dir)
    total = sum(r["overworld_steps"] for r in per_turn)
    mads = [c["mad"] for c in calib if c["mad"] is not None]
    out = {
        "run_id": run_dir.name,
        "generated_by": "scripts/backfill_steps.py",
        "video": {**meta, "raster": f"{GBA_W}x{GBA_H}", "px_per_tile": TILE},
        "frame_to_turn": {"source": "TURN counter change points", **seg_diag,
                          "ocr_spotchecks": ocr},
        "canvas": {"distinct_rects": sorted({tuple(r) for r in rects}),
                   "calibration_mad": ({"median": round(float(np.median(mads)), 2),
                                        "max": round(float(np.max(mads)), 2)}
                                       if mads else None),
                   "per_turn_calibration": calib},
        "detector": {"static_mad": STATIC_MAD, "accept_mad": ACCEPT_MAD,
                     "accept_ratio": ACCEPT_RATIO, "max_shift_px": MAX_SHIFT,
                     **step_diag},
        "totals": {"turns": len(per_turn), "overworld_steps": total,
                   "turns_with_steps": sum(1 for r in per_turn
                                           if r["overworld_steps"]),
                   "wall_seconds": round(t_steps - t0, 1),
                   "timing": {"turn_map_s": round(t_seg - t0, 1),
                              "ocr_check_s": round(t_ocr - t_seg, 1),
                              "canvas_s": round(t_canvas - t_ocr, 1),
                              "step_count_s": round(t_steps - t_canvas, 1)}},
        "turns": [{"turn": r["turn"], "overworld_steps": r["overworld_steps"],
                   "frames": r["frames"], "steps_x": r["steps_x"],
                   "steps_y": r["steps_y"], "method_notes": r["method_notes"]}
                  for r in per_turn],
        "validation": val,
    }
    dst = run_dir / "steps_backfill.json"
    dst.write_text(json.dumps(out, indent=1, default=list))
    c1 = val["check1_displacement"]
    print(f"{run_dir.name}: {total} steps over {len(per_turn)} turns "
          f"({out['totals']['turns_with_steps']} with steps) in "
          f"{out['totals']['wall_seconds']}s")
    print(f"  check1 {c1['violations']} violations / {c1['turns_checked']} "
          f"checked (agreement {c1['agreement_rate']})")
    print(f"  check2 {val['check2_no_direction']['n_with_steps']}/"
          f"{val['check2_no_direction']['n']} no-direction turns show steps")
    print(f"  check3 {val['check3_battle_ab']['n_with_steps']}/"
          f"{val['check3_battle_ab']['n']} battle+passive turns show steps")
    print(f"  -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
