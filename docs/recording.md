# Recording a run to MP4

Any run can be recorded to `<run_dir>/recording.mp4`. You pick **which interface**
to capture and **how the model's thinking time is treated**, at the moment you
start the run.

```bash
# From the CLI, standalone
pokemon run --model "claude-opus-4.7(medium)" --turns 50 \
            --record simple --record-speed cut-thinking

# Into the control center's queue
pokemon queue add --record simple --record-speed realtime claude-opus-4.7

# From the UI: "+ New run" → tick "Record this run to MP4"
```

Nothing is recorded unless you ask for it.

---

## It does not capture your browser

This is the design constraint everything else follows from: **the recording is
independent of what you are doing.** You can be on the leaderboard, on a report
page, on another desktop, or have the app closed entirely — the video is the
same either way, and nothing you click can spoil a take.

That rules out every in-page capture route. `MediaRecorder`, a canvas grab, or a
screen recorder pointed at your window are all hostage to your tab: your route,
your window size, your focus, and Chrome's background throttling (which stops
animation frames outright on a minimised window).

So the recorder never touches your browser. It launches **its own headless
Chrome**, loads a pinned URL, and streams frames out of it over the DevTools
protocol into ffmpeg:

```
headless Chrome ──Page.startScreencast──▶ latest JPEG frame
                                              │
                       sampler @ N fps ───────┤   (skipped while the gate is shut)
                                              ▼
                                 ffmpeg -f image2pipe ──▶ H.264 MP4
```

The pinned URL is `/spectate?record=1&view=<view>&run=<run_id>`. The `run` id is
pinned rather than read from `/api/emulator/status`, because that field goes null
the instant a run ends — a recorder that followed it would cut away for the last
seconds of every video.

**Requirements:** Chrome (or Chromium) and `ffmpeg` on `PATH`. Both are checked
*before* the run starts: `pokemon run --record` exits, and the API returns 400,
rather than letting you discover a missing encoder 200 turns later.

---

## `--record simple` vs `--record detailed`

| | `simple` | `detailed` |
|---|---|---|
| What's in frame | The 1:1 recording view: game screen + one box (turn number, GBA button glyphs, the model's reasoning) | The whole wide spectate panel — stats row, screen, live trace, memory, gates |
| Resolution | 1080×1080 | 1920×1080 |
| Good for | Posting unedited — square crops to Shorts / Reels / a LinkedIn card | Showing how the harness works |

The simple view's stage is `min(100vw, 100vh)`, so a **square** viewport makes it
fill the frame exactly — the video *is* the 1:1 view, with no cropping step and
no letterbox bars. It also sidesteps the box-geometry drift the view has on a
wide monitor (`.stage`'s percentage padding resolves against viewport *width*, so
the box renders ~21% of frame instead of the intended 24% on a 16:9 window — at
1:1 it is the intended 24%).

## `--record-speed realtime` vs `cut-thinking`

**`realtime`** records continuously. Every pause is in the file at its true
length, model latency included.

**`cut-thinking`** records only each turn's *execution* window:

```
turn_start ─────── thinking ─────── llm_output ── pressing ── screen_settled ──┐
           └──────── NOT recorded ─────────────┘└──── recorded ───────────┘ +0.9s
```

The gate opens at `llm_output` (the model has answered; the turn starts
executing) and shuts a beat after `screen_settled` (the emulator has stopped
moving). The dead time is simply never sampled, so it does not exist in the
file — no post-hoc editing, no timestamp arithmetic, and the result is still a
plain constant-frame-rate MP4.

Those event names match `SimpleView`'s phase machine exactly. Note that
`button_sequence` is **not** used: it is logged *after* `press_button_list()`
returns, so it marks the END of pressing — keying on it would start each clip
after the action it is meant to show.

Measured on a synthetic 4-turn run (3s think + 2s execute per turn):
realtime **22.3s**, cut-thinking **11.8s** — 47% removed, and the 11.8s is within
2% of the predicted 4 × (2.0 + 0.9).

---

## Flags

| Flag | Values | Default |
|---|---|---|
| `--record` | `simple`, `detailed` | off |
| `--record-speed` | `realtime`, `cut-thinking` | `realtime` |
| `--record-fps` | 1–60 | `30` |

The same three ride on the queue API as a `record` object:

```json
{"kind": "casual", "model": "...", "record": {"view": "simple", "speed": "cut-thinking", "fps": 30}}
```

They are persisted on the queue item, so a run queued now and started in an hour
still records. A **continue** chooses its own recording — it is a fresh run dir,
and the source run's setting is deliberately not inherited.

---

## Where it lands

`<run_dir>/recording.mp4`, beside `events.jsonl` and `run_summary.json`. H.264 /
yuv420p / `+faststart`, no audio (game audio is not on the dashboard's wire).
A 1080×1080 30fps recording runs roughly 3–8 MB per minute of *kept* video.

## When it doesn't work

Recording never takes a run down with it. Every failure path prints a line and
leaves the run alone:

- `⚠ recording disabled: <reason>` at start — no Chrome, no ffmpeg, no bound
  dashboard port (a `pokemon run` that never started a server), or a malformed spec.
- `⚠ no recording written: <reason>` at the end — includes the tail of ffmpeg's
  own stderr, which is the only thing that explains an empty encode.

Two traps worth knowing about, both found the hard way on 2026-08-01:

- **`--window-size` is not the viewport.** Asking Chrome for 1080×1080 produced a
  1080×**993** content area. That is not square (so the 1:1 view would have
  letterboxed) and the odd height made libx264 exit `-22` *before writing a single
  packet* — a zero-byte MP4 at the end of the run, with no warning. The recorder
  sets the viewport with `Emulation.setDeviceMetricsOverride` instead, and keeps
  an `scale=trunc(iw/2)*2:trunc(ih/2)*2` filter as a safety net so a surprise
  viewport costs one pixel rather than the whole recording.
- **Screencast frames are change-driven.** A static page yields ~1 frame per
  several seconds. That is why the recorder *samples* the newest frame on a fixed
  clock rather than forwarding frames as they arrive — it is what makes the
  output constant-frame-rate and wall-clock-faithful, and what makes both speed
  modes the same mechanism.

## Verifying a change to the recorder

`tests/test_recorder.py` covers spec normalisation, the cut-thinking gate (a pure
state machine with an injected clock), and the wiring that carries a queued run's
spec to the one place a recorder is started. It launches neither Chrome nor
ffmpeg.

The end-to-end path — real server, real SPA, real Chrome, real encode — is not in
the suite because it needs both binaries and ~25s per arm. The shape that proved
it: register a synthetic `RunSession`, drive it with a real-shaped
`turn_start`/`llm_output`/`screen_settled` cycle at known wall-clock times, record
it both ways, and compare `ffprobe` durations against the arithmetic. Then **look
at an extracted frame** — the durations were already correct on a run whose box
rendered completely empty, because the fixture invented `explanation` /
`button_sequence` fields instead of the real `args` JSON string.
