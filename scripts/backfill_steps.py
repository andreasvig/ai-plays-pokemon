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
  frame range per turn. Those change points alone are not enough, though: a
  continued run's ``recording.mp4`` is a splice of its source run's video and
  its own segment, and every seam in the chain repaints the counter without
  advancing it (a 457-turn chain gives 460 change points for 456 boundaries).
  The counter is therefore *read*: ``tesseract`` OCRs one crop per segment -
  cheap, because the crops are already in memory from the change scan - and the
  reading, not the change count, assigns the turn. Unreadable segments are
  interpolated between agreeing neighbours; a segment that disagrees with a
  legible counter makes the map inexact and is reported.

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
  sprite must not, or one step gets split across the break and lost. A turn that
  ends in an encounter stops being read at the transition: Gen-1 flashes the
  whole screen to one flat palette colour before the wipe, and everything after
  that flash (wipe, flat battle backdrop) matches translations that never
  happened - on the pilot run that was the only source of over-count.

A turn's raw detection is ``detected_scroll``. It is split into
``overworld_steps`` - what the board's movement efficiency divides by - and
``scripted_scroll``, by whether the turn's input list contains any direction: a
cutscene scrolls the camera for the model (pilot turn 14, 26 tiles on six `a`
presses) and that is not movement the model chose.

Writes ``<run_dir>/steps_backfill.json``:

    {"run_id":..., "video":{...}, "canvas":{...}, "totals":{...},
     "turns":[{"turn":N,"overworld_steps":K,"scripted_scroll":S,
               "detected_scroll":K+S,"frames":{...},"method_notes":"..."}],
     "validation":{"check1_displacement":..., "check2_no_direction":...,
                   "check3_battle_ab":...}}

All three validation checks run against ``detected_scroll``: they exist to
validate the detector, and check 2 would be vacuous against a field that is zero
on no-direction turns by construction.

The per-turn canvas calibration is the slow half (~45 s) and is cached in the
workdir as ``canvas_<run_id>.json``, keyed on the turn bounds it was measured
against, so a re-run with the same ``--workdir`` skips it. Nothing is cached in
the run dir.

Usage: venv/bin/python scripts/backfill_steps.py <run_dir> [--workdir DIR]
                                                 [--no-ocr-check]
Read-only except for the one JSON it writes in the run dir.
"""

from __future__ import annotations

import argparse
import collections
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
# move with the responsive layout - it is in the fixed stat-card row. The first
# candidate is the one measured on the pilot; the rest are wider fallbacks tried
# only when the first one's crops do not OCR as numbers, so a run recorded
# against different dashboard chrome is detected rather than silently misread.
TURNBOX_CANDIDATES = [(26, 80, 110, 40), (20, 70, 150, 60), (20, 96, 150, 60)]
TURNBOX = TURNBOX_CANDIDATES[0]  # x, y, w, h
TURNBOX_OK_RATE = 0.80  # share of segments that must OCR to a plausible number

# Frame-pair classification, in mean-absolute-difference of 8-bit grey.
STATIC_MAD = 0.8  # below this the pair is identical bar sprite animation
ACCEPT_MAD = 5.0  # a real translation leaves a small residual (measured 0.8-3.6)
ACCEPT_RATIO = 0.45  # ...and must beat the null shift by this factor (msrd <0.2)
MAX_SHIFT = 14  # px per frame pair. A step is ~2 px/frame at a 30 Hz emulator
# paste, but the older recordings paste at ~7 Hz (see REST_FRAMES) and then show
# up to 11 px in one pair. The search stops below one tile on purpose: at 16 px
# a shift lands back on the tile grid and a repeating pattern (grass, water)
# matches itself, so a wider search would start inventing translations.
RESET_MAD = 6.0  # only a change this big is a scene break worth forgetting a
# partial tile over. Sprite blinks and NPC animation land at 0.8-2.5 and must
# NOT reset the accumulator: on the pilot run they were splitting single steps.
REST_FRAMES = 3  # consecutive static pairs that prove the camera has settled,
# at one emulator frame per video frame. The recorder has not always pasted that
# fast: the Sep-9/10 recordings update the canvas about every 4th video frame,
# which puts 3 identical frames *inside* every step and makes a moving camera
# look settled - the residue is then rounded to zero and most of the walk is
# lost (this run: 22 turns detecting fewer steps than the referee's grid moved).
# The rest threshold is therefore scaled by the paste period measured online
# from the gaps between changing pairs; at 30 Hz the period is 1 and nothing
# changes.
REST_GAPS = 24  # rolling window of inter-change gaps the period is read from
REST_MAX_GAP = 16  # a longer gap is a real pause, not the paste period

# Wild/trainer-encounter transition. Gen-1 opens an encounter by flashing the
# whole screen to one flat palette colour before the wipe; nothing else in the
# game paints a uniform mid-grey raster (a map fade scales every pixel, so its
# spread survives, and it bottoms out at black, not grey). Once that frame is
# seen the turn is over as far as the overworld is concerned - the queued inputs
# are eaten by the battle - and every "translation" the wipe and the flat battle
# backdrop produce afterwards is noise. On the pilot run exactly 6 turns contain
# such a frame and they are exactly its 6 encounters.
FLASH_PTP = 8  # max-min of the raster; a palette flash measures 0
FLASH_MEAN = (25, 215)  # ...and is neither a fade-to-black nor a fade-to-white

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
    """One frame by index. Seeking within a few frames of the end can decode
    nothing (the container's duration rounds up past the last packet), so step
    back until a frame comes out rather than crashing on an empty buffer."""
    want = meta["height"] * meta["width"]
    for back in (0, 2, 5, 12, 30, 90):
        idx = max(frame_idx - back, 0)
        raw = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{idx / meta['fps']:.4f}",
             "-i", str(video), "-frames:v", "1", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        if len(raw) >= want:
            return np.frombuffer(raw[:want], np.uint8).reshape(
                meta["height"], meta["width"])
        if idx == 0:
            break
    raise RuntimeError(f"no frame decodable at or before index {frame_idx}")


# ------------------------------------------------------------- frame -> turn map


def counter_segments(video: Path, box: tuple[int, int, int, int]
                     ) -> tuple[list[int], list[np.ndarray], int]:
    """Change points of the TURN-counter crop, streamed straight off ffmpeg.

    Returns the segment start frames, one representative crop per segment, and
    the frame count. Nothing is written to disk: the crop is 4 KB a frame but a
    long run is 200k frames, and a 1 GB scratch file per run is the one thing
    that stops several runs being processed side by side.
    """
    x, y, w, h = box
    fsize = w * h
    proc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", str(video),
         "-vf", f"crop={w}:{h}:{x}:{y},fps=30",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, bufsize=fsize * 64)
    starts, reps = [0], []
    prev = None
    rep = None
    n = 0
    while True:
        buf = proc.stdout.read(fsize)
        if len(buf) < fsize:
            break
        cur = np.frombuffer(buf, np.uint8).reshape(h, w)
        f = cur.astype(np.float32)
        if prev is not None and float(np.abs(f - prev).mean()) > 0.5:
            starts.append(n)
            reps.append(rep)
        rep = cur
        prev = f
        n += 1
    proc.stdout.close()
    proc.wait()
    if rep is not None:
        reps.append(rep)
    return starts, reps, n


def ocr_numbers(reps: list[np.ndarray]) -> list[int | None]:
    """Read every segment's counter crop. Returns None where it is not a number."""
    if not shutil.which("tesseract"):
        return [None] * len(reps)
    out: list[int | None] = []
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "seg.png"
        for r in reps:
            h, w = r.shape
            Image.fromarray(r).resize((w * 4, h * 4), Image.LANCZOS).save(png)
            got = subprocess.run(
                ["tesseract", str(png), "stdout", "--psm", "7",
                 "-c", "tessedit_char_whitelist=0123456789"],
                capture_output=True, text=True).stdout.strip()
            out.append(int(got) if got.isdigit() else None)
    return out


def anchor_turns(vals: list[int | None], n_turns: int) -> tuple[list[int], int]:
    """Assign a turn number to every segment, trusting the OCR where it agrees.

    A spliced recording (a continued run replays its source's video as a prefix)
    has more counter change points than turns: the seam repaints the box without
    advancing the number, and each continuation in the chain adds one. Counting
    change points therefore mis-segments those runs - the pilot's 457-turn chain
    produced 460 changes for 456 boundaries. The counter itself is unambiguous,
    so the reading drives the map and the change points only bound it.

    The trusted set is the longest non-decreasing run of in-range readings;
    everything else (a misread digit, an unreadable frame) is interpolated
    between its neighbours. Returns the per-segment turn numbers and the number
    of segments that had to be interpolated.
    """
    n = len(vals)
    ok = [i for i, v in enumerate(vals) if v is not None and 1 <= v <= n_turns]
    # longest non-decreasing subsequence over the readable values (O(k log k))
    import bisect as _bs
    tails: list[int] = []
    tail_idx: list[int] = []
    back: dict[int, int | None] = {}
    for i in ok:
        v = vals[i]
        j = _bs.bisect_right(tails, v)
        back[i] = tail_idx[j - 1] if j else None
        if j == len(tails):
            tails.append(v)
            tail_idx.append(i)
        else:
            tails[j] = v
            tail_idx[j] = i
    keep: list[int] = []
    cur = tail_idx[-1] if tail_idx else None
    while cur is not None:
        keep.append(cur)
        cur = back[cur]
    keep.reverse()
    anchors = {i: vals[i] for i in keep}
    if not anchors:  # no OCR at all - fall back to one segment per turn
        return [min(i + 1, n_turns) for i in range(n)], n

    out = [0] * n
    ks = sorted(anchors)
    first, last = ks[0], ks[-1]
    for i in range(n):
        if i in anchors:
            out[i] = anchors[i]
        elif i < first:
            out[i] = max(1, anchors[first] - (first - i))
        elif i > last:
            out[i] = min(n_turns, anchors[last] + (i - last))
        else:
            lo = max(k for k in ks if k < i)
            hi = min(k for k in ks if k > i)
            out[i] = min(anchors[lo] + (i - lo), anchors[hi])
    for i in range(1, n):  # monotone by construction, but be certain
        out[i] = max(out[i], out[i - 1])
    return out, n - len(anchors)


def turn_segments(video: Path, work: Path, n_turns: int) -> tuple[list[int], dict]:
    """Segment the video into one frame range per turn via the TURN counter."""
    global TURNBOX
    tried = []
    chosen = None
    for box in TURNBOX_CANDIDATES:
        starts, reps, n_frames = counter_segments(video, box)
        vals = ocr_numbers(reps)
        rate = (sum(1 for v in vals if v is not None and 1 <= v <= n_turns)
                / max(len(vals), 1))
        tried.append({"box": list(box), "segments": len(reps),
                      "ocr_read_rate": round(rate, 3)})
        if chosen is None or rate > chosen[0]:
            chosen = (rate, box, starts, reps, vals, n_frames)
        if rate >= TURNBOX_OK_RATE:
            break
    rate, box, starts, reps, vals, n_frames = chosen
    TURNBOX = box
    seg_turn, interpolated = anchor_turns(vals, n_turns)

    bounds = [None] * (n_turns + 1)
    for i, t in enumerate(seg_turn):
        if bounds[t - 1] is None:
            bounds[t - 1] = starts[i]
    bounds[n_turns] = n_frames
    missing = [t for t in range(1, n_turns + 1) if bounds[t - 1] is None]
    # a turn with no segment of its own gets an empty range at its successor's
    # start; it reports 0 frames and 0 steps, and is listed in the diagnostics.
    for t in range(n_turns, 0, -1):
        if bounds[t - 1] is None:
            bounds[t - 1] = bounds[t]
    for t in range(1, n_turns + 1):
        bounds[t - 1] = min(bounds[t - 1], bounds[t])

    # A reading that disagrees with the turn its segment was assigned is a
    # reading the anchoring outvoted. A lone one is a misread glyph ("50" read
    # as "58") and cannot move the map: its neighbours pin it from both sides.
    # Consecutive disagreements are different - they are a stretch of video the
    # counter says belongs somewhere else, which is what a splice seam that
    # replays abandoned frames looks like - so those make the map inexact.
    bad = [i for i in range(len(vals))
           if vals[i] is not None and vals[i] != seg_turn[i]]
    badset = set(bad)
    clustered = [i for i in bad if (i - 1) in badset or (i + 1) in badset]
    disagree = [{"segment": i, "ocr": vals[i], "assigned": seg_turn[i],
                 "isolated": i not in clustered} for i in bad]
    identity = (len(seg_turn) == n_turns
                and all(t == i + 1 for i, t in enumerate(seg_turn)))
    diag = {
        "n_frames": n_frames,
        "n_changes": len(starts) - 1,
        "expected_changes": n_turns - 1,
        "n_segments": len(reps),
        "turnbox": list(box),
        "turnbox_candidates_tried": tried,
        "ocr_read_rate": round(rate, 3),
        "segments_interpolated": interpolated,
        "ocr_disagreements": len(disagree),
        "ocr_disagreements_clustered": len(clustered),
        "ocr_disagreeing_segments": disagree[:20],
        "turns_without_frames": missing,
        "one_segment_per_turn": identity,
        "exact": not missing and (identity or not clustered),
    }
    return bounds, diag


def ocr_check(video: Path, meta: dict, bounds: list[int],
              turns: list[int]) -> list[dict]:
    """Re-read the counter at a few segment midpoints, straight from the video.

    The map is already OCR-anchored, so this is a check of the *bounds* - it
    seeks the video independently and confirms the frame in the middle of the
    range assigned to turn N really shows N.
    """
    if not shutil.which("tesseract"):
        return []
    x, y, w, h = TURNBOX
    out = []
    with tempfile.TemporaryDirectory() as td:
        for t in turns:
            if t > len(bounds) - 1 or bounds[t] <= bounds[t - 1]:
                continue  # no frames of its own - nothing to re-read
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
        # A turn whose action list was empty executes nothing, so the recorder's
        # gate never opens and the turn owns no video frame. There is nothing to
        # calibrate against and nothing to count: carry the neighbouring rect.
        if bounds[t] <= bounds[t - 1]:
            rects.append(rects[-1] if rects else
                         (0, 0, meta["width"], int(meta["width"] * 2 / 3)))
            diag.append({"turn": t, "source": "no_frames", "mad": None,
                         "rect": list(rects[-1])})
            continue
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
    per_turn = [{"turn": i + 1, "detected_scroll": 0, "steps_x": 0, "steps_y": 0,
                 "frames": {"start": bounds[i], "end": bounds[i + 1] - 1,
                            "count": bounds[i + 1] - bounds[i],
                            "moving": 0, "rejected": 0, "static": 0},
                 "encounter_cut_frame": None,
                 "rect": list(rects[i])} for i in range(n_turns)]
    groups = group_turns(rects)
    fsize = GBA_W * GBA_H
    total_rejected = 0
    n_cut = 0
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
        gaps: collections.deque[int] = collections.deque(maxlen=REST_GAPS)
        since_change = 0
        rest_at = REST_FRAMES
        prev = None
        idx = f0
        seg = first - 1  # 0-based index into per_turn
        cut_seg = None  # turn index whose encounter transition has already fired
        while True:
            buf = proc.stdout.read(fsize)
            if len(buf) < fsize:
                break
            u8 = np.frombuffer(buf, np.uint8)
            cur = u8.reshape(GBA_H, GBA_W).astype(np.float32)
            while seg + 1 < last and idx >= bounds[seg + 1]:
                seg += 1
            if cut_seg is not None and cut_seg != seg:
                cut_seg = None
            if cut_seg is None and int(u8.max()) - int(u8.min()) <= FLASH_PTP \
                    and FLASH_MEAN[0] < u8.mean() < FLASH_MEAN[1]:
                # encounter flash: bank whatever whole tile is in flight (the
                # camera is tile-aligned the moment an encounter fires) and stop
                # reading this turn.
                rec = per_turn[seg]
                for ax in ("x", "y"):
                    acc = acc_x if ax == "x" else acc_y
                    k = int(abs(acc) / TILE + 0.5)
                    rec[f"steps_{ax}"] += k
                    rec["detected_scroll"] += k
                acc_x = acc_y = 0.0
                static_run = 0
                rec["encounter_cut_frame"] = idx
                cut_seg = seg
                n_cut += 1
            if cut_seg is not None:
                prev = cur
                idx += 1
                continue
            if prev is not None:
                rec = per_turn[seg]
                dx = dy = 0
                since_change += 1
                if float(np.abs(cur - prev).mean()) < STATIC_MAD:
                    rec["frames"]["static"] += 1
                    static_run += 1
                    # The camera only ever comes to rest on a tile boundary, so
                    # a settled screen is a free calibration: whatever sub-tile
                    # residue the per-frame shifts accumulated is measurement
                    # error and the true travel is the nearest whole tile. Doing
                    # this only at rest (never at a rejection) is what keeps a
                    # 15 px reading from silently cancelling the next step.
                    if static_run >= rest_at and (acc_x or acc_y):
                        for ax in ("x", "y"):
                            acc = acc_x if ax == "x" else acc_y
                            n = int(abs(acc) / TILE + 0.5)
                            rec[f"steps_{ax}"] += n
                            rec["detected_scroll"] += n
                        acc_x = acc_y = 0.0
                else:
                    static_run = 0
                    if 1 <= since_change <= REST_MAX_GAP:
                        gaps.append(since_change)
                        if len(gaps) >= 6:
                            rest_at = max(REST_FRAMES,
                                          2 * int(np.median(gaps)) + 1)
                    since_change = 0
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
                        rec["detected_scroll"] += 1
                if dy:
                    acc_y += dy
                    while abs(acc_y) >= TILE:
                        acc_y -= TILE * (1 if acc_y > 0 else -1)
                        rec["steps_y"] += 1
                        rec["detected_scroll"] += 1
            prev = cur
            idx += 1
        proc.stdout.close()
        proc.wait()
    return per_turn, {"groups": [{"turns": [g[0], g[1]], "rect": list(g[2])}
                                 for g in groups],
                      "rejected_pairs": total_rejected,
                      "encounter_cuts": n_cut}


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
    # All three checks run against ``detected_scroll``, the raw count, not the
    # player-initiated ``overworld_steps``: they exist to validate the detector,
    # and check 2 in particular would be vacuous against a field that is zero on
    # no-direction turns by construction.
    steps = {t["turn"]: t["detected_scroll"] for t in per_turn}
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
    summary = {}
    sp = run_dir / "run_summary.json"
    if sp.exists():
        summary = json.loads(sp.read_text())
    n_turns = max(n_turns, len(summary.get("turns") or []))

    bounds, seg_diag = turn_segments(video, work, n_turns)
    # A continued run replays its source run's video as a prefix of its own
    # recording.mp4 (run_summary.json["recording_splice"] gives source_s +
    # segment_s == the file's duration), so one file covers turns 1..N and the
    # source run is never needed. Verified on all nine continued runs on the
    # board. If that ever stops being true the counter map starts above turn 1
    # and the run must be rebuilt from the source's recording - say so loudly.
    # A turn the model answered with an empty action list executes nothing, so
    # the recorder's gate never opens and the video holds no frame for it. That
    # is not a broken map - the turn really is zero steps - so only a frameless
    # turn that HAD inputs counts against exactness.
    empty_in = [t for t in seg_diag["turns_without_frames"]
                if not ev["actions"].get(t)]
    seg_diag["turns_without_frames_no_input"] = len(empty_in)
    seg_diag["turns_without_frames_with_input"] = [
        t for t in seg_diag["turns_without_frames"] if ev["actions"].get(t)]
    if not seg_diag["turns_without_frames_with_input"]:
        seg_diag["exact"] = (seg_diag["one_segment_per_turn"]
                             or not seg_diag["ocr_disagreements_clustered"])
    seg_diag["continued_from"] = summary.get("continued_from")
    seg_diag["recording_splice"] = summary.get("recording_splice")
    if seg_diag["turns_without_frames"][:1] == [1]:
        print("WARNING: recording.mp4 does not start at turn 1 - the early "
              "turns must come from the source run's recording", file=sys.stderr)
    if not seg_diag["exact"]:
        print(f"WARNING: frame->turn map not exact: "
              f"{seg_diag['ocr_disagreements_clustered']} of "
              f"{seg_diag['ocr_disagreements']} counter disagreements are "
              f"consecutive, "
              f"{len(seg_diag['turns_without_frames_with_input'])} turns with "
              f"inputs have no frames", file=sys.stderr)
    t_seg = time.time()

    ocr = [] if args.no_ocr_check else ocr_check(
        video, meta, bounds,
        sorted({1, 2, max(2, n_turns // 3), max(3, 2 * n_turns // 3), n_turns}))
    t_ocr = time.time()

    cache = work / f"canvas_{run_dir.name}.json"
    key = {"n_turns": n_turns, "n_frames": seg_diag["n_frames"],
           "bounds": bounds}
    cached = None
    if cache.exists():
        try:
            blob = json.loads(cache.read_text())
            if blob.get("key") == key:
                cached = blob["calibration"]
        except (json.JSONDecodeError, KeyError):
            cached = None
    if cached is not None:
        calib = cached
        rects = [tuple(c["rect"]) for c in calib]
        print(f"  canvas calibration reused from {cache}")
    else:
        rects, calib = calibrate_turns(video, meta, bounds, run_dir, ev, n_turns)
        cache.write_text(json.dumps({"key": key, "calibration": calib}))
    t_canvas = time.time()

    per_turn, step_diag = count_steps(video, meta, bounds, rects)
    t_steps = time.time()

    # Player-initiated vs scripted. A turn whose input list holds no direction
    # cannot have moved the player by walking, yet the camera may still scroll:
    # a cutscene walks the player for you (pilot turn 14, Oak's lab, scrolls 26
    # tiles on six `a` presses). That scroll is real and measured, but it is not
    # the model's movement, and ``overworld_steps`` is what the board's movement
    # efficiency divides by - so the scripted share is carried in its own field.
    for rec in per_turn:
        a = ev["actions"].get(rec["turn"], [])
        scripted = not (set(a) & DIRECTIONS)
        rec["overworld_steps"] = 0 if scripted else rec["detected_scroll"]
        rec["scripted_scroll"] = rec["detected_scroll"] if scripted else 0
        if scripted:
            rec["steps_x"] = rec["steps_y"] = 0
        cut = rec.pop("encounter_cut_frame")
        rec["method_notes"] = (
            f"{rec['frames']['count']} frames "
            f"({rec['frames']['moving']} translating, "
            f"{rec['frames']['rejected']} non-translation, "
            f"{rec['frames']['static']} static); "
            f"detected={rec['detected_scroll']}"
            f"{' (scripted - no direction input)' if scripted else ''}; "
            f"steps x={rec['steps_x']} y={rec['steps_y']}; "
            + (f"encounter transition at frame {cut}; " if cut is not None else "")
            + f"canvas={rec['rect']}; "
            f"inputs={'+'.join(a) if a else 'none'}")

    val = validate(per_turn, ev, run_dir)
    total = sum(r["overworld_steps"] for r in per_turn)
    scripted_total = sum(r["scripted_scroll"] for r in per_turn)
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
                   "scripted_scroll": scripted_total,
                   "detected_scroll": total + scripted_total,
                   "encounter_cut_turns": step_diag["encounter_cuts"],
                   "turns_with_steps": sum(1 for r in per_turn
                                           if r["overworld_steps"]),
                   "wall_seconds": round(t_steps - t0, 1),
                   "timing": {"turn_map_s": round(t_seg - t0, 1),
                              "ocr_check_s": round(t_ocr - t_seg, 1),
                              "canvas_s": round(t_canvas - t_ocr, 1),
                              "step_count_s": round(t_steps - t_canvas, 1)}},
        "turns": [{"turn": r["turn"], "overworld_steps": r["overworld_steps"],
                   "scripted_scroll": r["scripted_scroll"],
                   "detected_scroll": r["detected_scroll"],
                   "frames": r["frames"], "steps_x": r["steps_x"],
                   "steps_y": r["steps_y"], "method_notes": r["method_notes"]}
                  for r in per_turn],
        "validation": val,
    }
    dst = run_dir / "steps_backfill.json"
    dst.write_text(json.dumps(out, indent=1, default=list))
    c1 = val["check1_displacement"]
    print(f"{run_dir.name}: {total} player steps (+{scripted_total} scripted) "
          f"over {len(per_turn)} turns "
          f"({out['totals']['turns_with_steps']} with steps) in "
          f"{out['totals']['wall_seconds']}s")
    print(f"  frame->turn exact={seg_diag['exact']} segments="
          f"{seg_diag['n_segments']} ocr_rate={seg_diag['ocr_read_rate']} "
          f"interpolated={seg_diag['segments_interpolated']} "
          f"turnbox={seg_diag['turnbox']} "
          f"empty_turns={len(seg_diag['turns_without_frames'])}")
    print(f"  canvas mad {out['canvas']['calibration_mad']}; "
          f"{step_diag['encounter_cuts']} encounter cuts")
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
