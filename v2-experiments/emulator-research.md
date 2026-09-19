# Emulator research — one backend for GBA + NDS?

> 2026-09-18. Tested on this machine (macOS 25.6, arm64). Every ✅/❌ below marked
> "verified" was run here, not read off a page.

## The contract a backend has to satisfy

From `lua/socketserver-1.lua` — what the harness actually uses today:

| Command | Purpose |
|---|---|
| `CAP` | screenshot to a file |
| `PRESS:<btn>` / `SEQ:<btn>;<btn>` | inputs, held across a configurable frame count |
| `CONFIG:hold_frames=` / `gap_frames=` | input timing |
| `SAVE:<path>` / `LOAD:<path>` | savestates — the unit the whole start-state + continue system is built on |
| `READMEM:<addr>:<len>` | arbitrary memory read (the referee) |
| `TRACESPEC:` / `TRACE` | **per-frame** memory sampling, with pointer dereference |
| `PAUSE` / `UNPAUSE` / `PING` | lifecycle |
| *(v2 adds)* `WRITEMEM:` | savestate authoring / PKHax |

`TRACE` is the demanding one: it samples **every frame** from inside mGBA's frame
callback. Anything driven over a network request-per-frame can't reproduce that
cheaply — it needs the sampling to live inside the emulator's frame loop.

## Candidates

| Backend | Systems | Screenshot | Input | Savestate | Read | **Write** | Frame step | macOS arm64 |
|---|---|---|---|---|---|---|---|---|
| **mGBA + Lua** *(current)* | GB, GBC, GBA | ✅ | ✅ | ✅ | ✅ | ➕ ~5 lines | frame callback | ✅ **verified running** |
| **SkyEmu** HTTP | GB, GBC, GBA, **NDS** | `/screen` | `/input` | `/save` `/load` | `/read_byte` | ✅ `/write_byte` | ✅ `/step?frames=N` | ✅ **verified, patched** (2026-09-18) |
| **RetroArch** + melonDS DS | everything | `SCREENSHOT` | ⚠️ separate channel | `SAVE_STATE` | `READ_CORE_MEMORY` | ✅ `WRITE_CORE_MEMORY` | `FRAMEADVANCE` | ✅ cask available |
| melonDS + Lua fork | NDS | — | ✅ incl. touch | ❌ | ❌ | ❌ | frame callback | PR #1671 unmerged |
| DeSmuME | NDS | Lua | Lua | Lua | Lua | Lua | ✅ | ⚠️ brew cask **disabled** |
| BizHawk | everything | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ x86_64 Win/Linux only |

## What was actually tested here

**mGBA (control).** Launches and runs FireRed from this environment. Establishes that
graphics/WindowServer access is available, so any other emulator's failure is its own.

**SkyEmu v5** (`SkyEmu-v5-macOS.dmg`, released 2026-02-12) — the prebuilt binary:
- Universal binary, **native arm64**, ad-hoc signed (needs `xattr -dr com.apple.quarantine`).
- **GUI mode runs FireRed fine.** ✅
- **`./SkyEmu http_server <port> <rom>` crashes instantly:**
  `Assertion failed: (_sg.valid), function sg_make_image, file sokol_gfx.h, line 14665`.
  The HTTP port never binds — connection refused.
- That is **open issue [#576](https://github.com/skylersaleh/SkyEmu/issues/576)**, filed
  2026-08-26, macOS arm64, no maintainer fix.

**→ RESOLVED 2026-09-18 by building from source with a two-line patch. See below.**

**Homebrew:** `melonds` and `desmume` casks were **disabled 2026-09-01** for failing the
macOS Gatekeeper check. `retroarch` (1.22.2) is available.

## Reading the candidates

### SkyEmu — the only single-backend answer, and the API is better than ours

One emulator covering **GB, GBC, GBA and NDS**, with a REST API that maps onto our
contract one-for-one and improves two things:

- **`/step?frames=N`** is deterministic frame control. Today's input timing is a
  frame-callback hold counter tuned by `hold_frames`/`gap_frames`; with explicit
  stepping, "press A for 8 frames" becomes exactly that.
- **`/write_byte?ADDR=VAL`** means **PKHax needs no emulator patching at all** — flags,
  party, box and position are writable over HTTP on day one.
- `/read_byte` takes a `map` parameter for NDS (7 = ARM7, 0/9 = ARM9), so the dual-CPU
  address space is already modelled.
- `/status` returns JSON with run mode, ROM path, save path and every input's state.

Both of the risks this section used to list have now been tested rather than reasoned
about, and both came out in SkyEmu's favour — see the next section.

### RetroArch + melonDS DS — mature NDS, assembled interface

Memory is solid: `READ_CORE_MEMORY` / `WRITE_CORE_MEMORY` over UDP `127.0.0.1:55355`,
and the non-contiguous-descriptor bug (#13664) was fixed in 2022. RetroAchievements
works on the melonDS DS core, which is direct evidence NDS memory is properly exposed.

The problem is **inputs**: the network control interface has **no button command** at
all. Buttons come from a *second*, unrelated mechanism — the Remote RetroPad receiver on
UDP `55400 + player`, needing `network_remote_enable`. So a run would be assembled from
three channels (commands, retropad, screenshot-to-file), with no per-frame trace path
and no security on the retropad port.

Workable as an **NDS-only** backend. Poor as the single unified one.

### The rest

- **melonDS Lua fork** — the documented API is input + GUI drawing only; memory
  read/write appears in the PR conversation but not the docs, and the PR is unmerged
  after two years. Too immature to build a benchmark on.
- **DeSmuME** — Lua is Windows-centric, accuracy is weaker, cask disabled here.
- **BizHawk** — the obvious answer on paper (multi-system, mature Lua, TAS-grade
  determinism) and **unavailable**: x86_64 Windows/Linux only, no macOS build.

## What this means for the game list

**Five of the nine games need no new emulator at all.** mGBA already covers GB and GBC,
so **Red and Crystal run on the harness as it stands** — they need ROM dumps and their
own referee address maps, not a backend. FireRed, Emerald and the rest of Gen 3 are
already there.

Only **Platinum, HGSS, BW, B2W2** force the question. So the backend decision can be
deferred behind real work rather than blocking it — and it should be, because the
answer depends on an experiment nobody has run yet.

## Three experiments RUN — 2026-09-18. SkyEmu is the backend.

### Experiment 1 — headless on arm64: **works, with a two-line patch**

**Issue #576's diagnosis is wrong.** It blames `se_get_image()`; that function only
allocates a deferred-free list node and never touches the GPU. The crash report
(`~/Library/Logs/DiagnosticReports/SkyEmu-2026-09-18-1511*.ips`) gives the real chain:

```
sokol_main → headless_mode → se_init → se_load_settings
           → se_reload_theme → se_load_theme_from_image → sg_make_image → assert(_sg.valid)
```

It is the **UI theme**. `headless_mode()` calls `se_init()` from `sokol_main()`, before
sapp's `init_cb` has run `sg_setup()` — and settings-loading eagerly uploads the theme
atlas's mipmaps as GPU textures. A headless server never draws a theme.

Fix: gate that one upload loop on `sg_isvalid()`. Everything else in the function is
CPU-side parsing and still runs, so settings load completes normally.

### Experiment 2 — does NDS render? **Yes, cleanly, including 3D**

Verified by screenshot over `/screen`, not by reputation:

- **Platinum** — boot logo → Pokémon logo over the intro scene → the starter montage
  with a **3D Torterra**. Correct tiles, sprites, palettes and text throughout.
- **SoulSilver** (the case flagged as hardest) — full title screen with the animated
  **3D Lugia** underwater, "TOUCH TO START", then the intro menus. No glitching.
- **Crystal** (GBC) on the same binary — 160×144, boots.

Captures in [`shots/`](shots/): [`platinum-intro.png`](shots/platinum-intro.png),
[`soulsilver-title.png`](shots/soulsilver-title.png), and the two-tap control
([`y=0.22`](shots/soulsilver-tap-y022-control-info.png) ·
[`y=0.80`](shots/soulsilver-tap-y080-skipped.png)).

`/screen` returns **256×384**: both DS screens stacked vertically in one PNG, which is
convenient — one capture is the whole console, and the vision pipeline's "one frame"
assumption survives with a taller frame rather than a second channel.

### The second patch: `/input` had no touch coordinates

`Tap Screen (NDS)` is only the pen-DOWN flag. The position lives in `joy.touch_pos`,
which the GUI fills from the mouse and which **nothing over HTTP could set** — so every
tap over the API landed at (0,0). "TOUCH TO START" accepts that; no other DS menu does.

Added `touch_x` / `touch_y` (normalised 0..1, clamped) to `/input`, applied while the pen
is down. **Controlled**: from one savestate of SoulSilver's intro menu, a tap at
`touch_y=0.22` opens "I'll explain this game's controls" and a tap at `touch_y=0.80`
skips past — two different screens from the same state with only Y changed. Without the
patch both are the (0,0) tap.

That also answers the README's "the action space has no stylus verb": the harness needs
one new action carrying (x, y), not a new input channel.

### Verified contract

| Harness command | SkyEmu | Verified |
|---|---|---|
| `CAP` | `/screen` | ✅ 256×384 PNG (NDS), 160×144 (GBC) |
| `PRESS` / `SEQ` | `/input?A=1` | ✅ reflected in `/status` |
| *(new)* touch | `/input?touch_x=&touch_y=` | ✅ two coordinates, two outcomes |
| `SAVE` / `LOAD` | `/save?path=` `/load?path=` | ✅ round-trip: wrote `a5`, saved, wrote `7e`→`11`, loaded, read `a5` |
| `READMEM` | `/read_byte?addr=` | ✅ multi-address, `map=7` for ARM7 |
| **`WRITEMEM`** | `/write_byte?ADDR=VAL` | ✅ **PKHax needs no emulator patching** |
| frame step | `/step?frames=N` | ✅ deterministic |
| `PING` | `/ping` → `pong` | ✅ |
| `READMEM` range | `/read_byte` x N in one request | ✅ ~4,800 req/s at 64 B; `read_memory(addr, len)` |
| **`TRACE`** | step-and-read between inputs | ✅ **not a gap — see the correction below** |

#### Correction (2026-09-19): `TRACE` is not a gap, and never needed per-frame sampling

Everything above this line said `TRACE` "samples every frame from inside mGBA's frame
callback", so reproducing it over HTTP would mean one request per frame. **That was
wrong, and it was wrong about our own code.** `lua/socketserver-1.lua:266` calls
`trace_sample()` in exactly one place — at the end of an input's gap, in the
`waiting` branch of the input state machine. The state machine ticks every frame;
the sampling does not. `src/referee/trace.py`'s own docstring says so in its first
line: "after EVERY button of a turn". One row per input, not per frame.

So the SkyEmu equivalent is: step the input, step the gap, read the ranges. That is
what the backend already does, with no patch and no new command.

Per-frame sampling turns out to be affordable anyway, which is the second half of the
correction. Measured on Platinum:

| | rate | vs real time |
|---|---|---|
| `/read_byte`, 1 address | ~5,400 requests/s | — |
| `/read_byte`, 64 addresses in one request | ~4,800 requests/s (307 KB/s) | — |
| `/step?frames=1` + a 64-byte read, repeated | **156 frames/s** | **2.6x** |

A trace sampling *every frame* over HTTP runs faster than the console does. The
original note's reasoning — "anything driven by a network request per frame can't
reproduce that cheaply" — assumed a request costs what a network request costs, and
on loopback to a process that is not waiting on anything, it does not.

`/read_byte` takes **repeated `addr` parameters** and answers with the bytes
concatenated as bare hex, so a range is one round trip rather than one per byte. The
`map` parameter must come before them in the query string: the handler walks the
parameters in order and a `map` seen after an `addr` arrives too late for it.

### Using it

- Patch: [`skyemu-headless-arm64.patch`](skyemu-headless-arm64.patch) — 3 hunks, against
  upstream `01516d6`. Two changes: the theme guard, and the touch coordinates.
- Binary: `~/Applications/SkyEmu.app` (Release, arm64). Rebuild with
  `cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j8` after applying
  the patch to a fresh clone. Needs `cmake` (installed 2026-09-18) and SDL2.
- Run: `SkyEmu.app/Contents/MacOS/SkyEmu http_server <port> <rom>`

Things that cost time to find out, so the next person does not repeat them:

- **Headless starts PAUSED.** `/status` reports `"run-mode": "PAUSE"` and nothing
  advances until you `/step`. That is a feature for a benchmark — every frame is
  accounted for — but a first probe looks dead otherwise.
- **`/step?frames=N` is the whole clock.** Booting an NDS title to its title screen is
  roughly 4,000–5,000 frames; Platinum's starter montage is around 2,400. Step in
  chunks of ~600 with a generous curl timeout: stepping runs as fast as it can, not in
  real time, and a large N blocks the HTTP response until it finishes.
- **A tap needs three parameters, and two of them are the patch.**
  `/input?Tap%20Screen%20(NDS)=1&touch_x=0.5&touch_y=0.8`, then step ~20 frames, then
  send the same input with `=0`. Holding it down across a step is what registers; a
  press and release inside one call does nothing.
- **`touch_x`/`touch_y` are normalised over the TOUCH screen**, not over the 256×384
  capture. The touch screen is the bottom half of that image, so an on-screen feature at
  image row `r` is at `touch_y = (r - 192) / 192`.
- **Writes land in the running machine, not the save file.** `/write_byte` then `/save`
  is the authoring loop; `/load` restores whatever was in RAM at `/save` time. Verified
  by writing three distinct markers around a save and reading back the right one.
- **The `.sav` beside the ROM is created on load.** SkyEmu allocated a 512 KB `.sav` next
  to the Platinum ROM in `roms/` on first run. Harmless, but it means the ROM directory
  is not read-only in practice — worth knowing before that directory is shared or
  checksummed.
- **Text replies carry a trailing NUL.** `/ping` answers `pong\0`, `/save` answers
  `ok\0`. `bytes.strip()` removes ASCII whitespace and *not* NUL, so `== b"pong"`
  is false forever and a client sits in its connect loop reporting a backend
  that is running and answering as dead. Cost 20 minutes here before the log
  showed the byte.
- **Kill the process explicitly.** A headless instance does not exit when its client
  goes away; one from a crashed session was still holding a ROM hours later.

### Experiment 3 — a model playing through it: **works, both screens, stylus included**

The first two experiments proved the backend answers. This one asks the question
that actually matters: can a model *see* an NDS frame and act on it. Run with
`harness/play.py`, gpt-6-astra at effort low, 10 turns each, cold-booted rather
than loaded from a prepared state.

**Platinum** (3,000 boot frames → the opening cinematic). Ten turns took it
through the title screen, the black transition, Rowan's "Hello there!", the whole
"world of Pokémon" introduction and onto the CONTROL INFO / ADVENTURE INFO / NO
INFO NEEDED menu — [`platinum-agent-turn10.png`](shots/platinum-agent-turn10.png).
Every turn's reasoning names something actually on screen; no turn describes a
screen that is not there.

**SoulSilver** (4,500 boot frames → the title screen). This is the one that needed
the stylus, and it is the discriminating case, because a button press cannot
produce the outcome:

- **Turn 1** — "the title screen shows TOUCH TO START" → `tap(0.50, 0.79)`. Past it.
- **Turn 3** — the three-button topic menu, and the model picked `tap(0.51, 0.79)`
  for NO INFO NEEDED. That button's centre sits at image row 1037; through
  `image_row_to_touch_y` that is **0.793**. The model, given only the prompt's
  description of the coordinate system and the picture, computed the same number
  to two decimals and hit the third of three buttons
  ([`soulsilver-agent-menu-stacked.png`](shots/soulsilver-agent-menu-stacked.png)).
  Turn 4 shows Oak's intro, which is where NO INFO NEEDED goes and where the other
  two buttons do not.

That is the whole v2 capability chain — stacked capture → prepared frame → model →
normalised tap → emulator — closing on a target only the correct coordinate
reaches.

**Cost and pace:** 10 turns ≈ 42k prompt + 1k completion tokens, **$0.53**, 40 s of
wall clock. Most of the prompt is pixels: the frame is 768×1156 and only the three
most recent stay in the conversation. Not comparable with a board run — this loop
has no OCR, no referee, no compaction, no gates.

### The two frame changes

**No grid overlay.** v1 paints a red lattice every 16 native px with an 8 px
vertical offset — both numbers are GBA viewport facts (FireRed's camera sits half
a tile off the tile grid). On an NDS frame they describe nothing, and they land
hardest on the bottom screen, which is exactly where the model has to aim. Gone,
and `frame.py` has no code path that can draw one. (Andreas, 2026-09-18: "remove
the overlay grid graphic. no doubt.")

**The two screens, stacked.** Already true and now guaranteed: `/screen` on NDS is
`framebuffer_top` memcpy'd above `framebuffer_bottom` (`se_screenshot`, main.c) —
a fixed layout in the source, not a GUI setting, so it cannot drift with a
preference. `frame.py` splits and re-stacks anyway and raises on any geometry it
does not recognise, because "the capture happens to be right today" is a weaker
claim than "the image the model sees is a vertical stack". The one mark drawn is a
4 px seam between the screens: two dark DS screens abut invisibly, and the prompt
tells the model the lower half is touchable — a fact it cannot use if it cannot
see where the halves divide. `--no-divider` removes it.

### The harness

`harness/` — deliberately outside `src/`, because the question was whether a model
can play through this backend at all, and answering it inside the real turn loop
would have meant changing the real turn loop before knowing.

| File | What it is |
|---|---|
| `skyemu.py` | the HTTP client — lifecycle, `/step`, press/tap, save/load, `read_memory` |
| `frame.py` | what the model is shown: geometry check, stack, seam, no grid, row→`touch_y` |
| `play.py` | the turn loop: capture → model → actions → repeat, with a JSONL log per run |
| `record.py` | MP4 of a run — emulator frames into ffmpeg, same encoder as the board |
| `selftest.py` | the backend contract, no model and no API key, ~1 minute |

`selftest.py` is the piece worth keeping honest: each check is written to **fail if
the mechanism is missing**, not merely to pass when it is present. The savestate
check writes `a5`, saves, overwrites with `7e`, loads, and demands `a5` back — a
round trip nothing can fake. 12 checks pass on Platinum and 8 on Crystal
(2026-09-18).

Running it on Crystal as a **control** is what made it worth trusting, because the
GBC run failed two checks the NDS run had passed — and both were the test's fault,
not the backend's:

- `before != after` across 1,200 frames is a weak oracle. Crystal's screen at
  frame 1,200 was byte-identical to frame 0; the NDS case had been passing on the
  luck of an animated intro. Now it steps in chunks and passes at the first
  change, which only a running machine can produce.
- The savestate check read `0x02000000` — main RAM on the NDS's ARM9 and EWRAM on
  GBA, and **not memory at all** on a Game Boy. The write appeared to land and the
  savestate did not carry it, which presents as a broken savestate rather than a
  bad address. The address is now chosen per console.

### Recording a run

`--video game` / `--video realtime` writes `run.mp4` beside the turn log. Verified
on a 6-turn SoulSilver run: H.264, 768x1152, 30 fps, yuv420p, `+faststart` — the
same encoder settings as `src/dashboard/recorder.py`, so the two files look like
each other in a player.

It is only that recorder's second half. The board's recorder launches its **own
headless Chrome** against the dashboard and composites the emulator into the game
rectangle, because the requirement (2026-08-01) is a file independent of where the
viewer is, and because the presentation card is part of the subject. Headless has
no browser and no dashboard, so what this produces is the game and nothing around
it: no card, no typography, no detailed instrument-panel view.

Two things the board's recorder needs machinery for are free here:

- **Cut-thinking needs no gate.** There the emulator runs continuously and the
  recorder opens and closes a gate on `llm_output` / `screen_settled` to keep the
  model's thinking out of the file. Here the machine is frozen unless something
  calls `/step`, so thinking is not in the frame stream to begin with. `game` is
  the default for that reason.
- **Constant frame rate needs no sampler thread.** Frames exist because the turn
  loop asked for them, so N frames written IS N/30 seconds of game time, with no
  clock to drift against. Checked on a 3-turn control: a tap (20 hold + 24 gap)
  plus waits of 90, 120 and 120 is 374 emulator frames, and the file came out at
  exactly 187 samples / 6.2 s.

`realtime` adds the one constructed part — `hold()` repeats the frozen frame for
as long as the call took, because the machine genuinely was stopped. The 6-turn
run: 38.3 s of file over 23 s of thinking and 15.3 s of game.

Sampling is interleaved, not concurrent: SkyEmu's HTTP server handles one request
at a time, so a `/screen` sent from another thread during a long `/step` does not
answer until the step finishes. `step()` therefore chunks and captures, which is
also why one hook catches presses, taps and waits alike — nothing else can move
the machine. Measured 54 captures/s, so a 30 fps recording renders at ~1.8x real
time.

The video carries the **native** capture upscaled once in ffmpeg (NEIGHBOR, no
blur on a 1 px font) and therefore has **no seam** — the seam is a prompt aid on
the model's frame, not part of the console.

### Reading game state while it runs (2026-09-19)

Yes — and the referee needs exactly one method from a backend to work. `Referee` is
constructed with "an emulator-like object exposing `read_memory(addr, length) ->
bytes`" (`src/referee/referee.py:108`). That is the whole contract. `SkyEmu.read_memory`
now implements it, so a v2 referee is a swap of the object, not a rewrite.

Checked rather than assumed: a 300-byte read agrees byte-for-byte with the same
addresses read one at a time, and the values are plausible memory (43 distinct 8-byte
windows across 64 samples, carrying ARM instruction encodings where the ARM9 binary
was copied at boot).

**Reads are live.** A dense 64 KB block at `0x02100000` changes across a 60-frame step
while the screen changes over the same interval.

That took a second probe, because the first one said the opposite and was wrong. It
read **64 bytes at each 64 KB boundary** — 0.1% coverage, landing on page starts,
which in this ROM are the static ARM9 binary — and reported 0 of 64 samples changing
over 600 frames while the screen plainly moved. The sample could not contain the
thing it was looking for. A contiguous block found it immediately. *A memory probe has
to be dense enough to hit changing memory; aligned spot-checks sample exactly the
addresses least likely to move.*

Three things this does **not** settle:

- **The address maps are the work, and they are per game.** Everything the referee
  knows is FireRed: `GSAVEBLOCK1_PTR = 0x03005008` is GBA IWRAM and means nothing on a
  DS. Checkpoints, route tracking and the efficiency buckets are all expressed in those
  addresses. The backend delivers bytes; which bytes to ask for is unbuilt for Gen 4/5,
  and that is the cost `roms/MANIFEST.md` has named from the start — unchanged by any
  of this.
- **"While executing" means between steps, not during one.** SkyEmu answers one HTTP
  request at a time, so a read issued during a long `/step` waits for the step to
  finish. In a headless benchmark that is the right shape anyway — the machine only
  moves when the loop asks — but it rules out a watcher thread sampling a free-running
  emulator.
- **The `map` parameter routes to `nds7_byte_read` / `nds9_byte_read` in source
  (`main.c:2934`), but no address probed here shows the two views differing.** Main RAM
  is shared between the CPUs, so agreement there is correct and not evidence either
  way. Untested, not verified.

### What this settles

One backend covers **all nine games** — GB, GBC, GBA and NDS — so the architectural fork
the README described does not have to happen. RetroArch + melonDS DS stays as the
fallback if a specific Gen 4/5 title turns out to render badly, but nothing seen so far
suggests it will.

The per-game **referee address maps** remain the real cost, exactly as
[`roms/MANIFEST.md`](roms/MANIFEST.md) said — and that cost is unchanged by this result.

## Determinism — 2026-09-19. Replays are reproducible; each system has one defect

Risk #2 in [the backend plan](../artifacts/skyemu-backend/plan.md) §6: v1 (mGBA driven
by wall-clock sleeps) is not reproducible, v2 *ought* to be because `/step?frames=N` is
an exact frame count and not a `time.sleep`, and "ought to" had never been measured.
[`determinism.py`](determinism.py) is the measurement — one control and four
experiments, no API key, about five minutes per system. Run here on **FireRed** (GBA)
and **Platinum** (NDS).

What is compared: EWRAM `0x02000000`+`0x40000` and IWRAM `0x03000000`+`0x8000` on GBA
(288 KB); two 256 KB windows of ARM9 main RAM at `0x02000000` and `0x02100000` on NDS
(512 KB). By SHA-256 first and, on any mismatch, byte by byte — "not identical" is a
much weaker claim than "10 bytes differ, all inside a 378-byte window", and only the
second one tells you what to do about it.

The headline: **replaying a savestate is bit-exact on both systems**, and each system
has exactly one defect, and they are mirror images. On **GBA**, `/load` is not an exact
inverse of `/save` — a reloaded machine diverges from the live one, and the gap grows.
On **NDS**, `/load` is exact but two *cold processes* end up ten bytes apart.

### The control, and the two times it bit

Two *different* input sequences of the *same* frame count from the *same* savestate. The
equal frame count is the point: everything that advances per frame — the RNG, vblank
counters, animation timers — reaches the same value in both arms, so every byte that
differs differs because of the **inputs**. If the arms came out equal, the snapshot
region would not be where the game lives and every "identical" below would be vacuous.

| | arms | result |
|---|---|---|
| FireRed | `START, A×8` vs `START, A, A, B×6` | **7,463 / 294,912 differ (2.53%)** — 4,891 EWRAM in 717 runs, 2,572 IWRAM in 660 runs |
| Platinum | `START, A×8` vs `B×9` | **20,977 / 524,288 (4.00%)**, 427 runs |

It bit twice, and both were the test's fault rather than the backend's — which is the
only reason the passes below are worth anything:

- **At 3,000 boot frames Platinum is still inside its opening cinematic** — the Pokémon
  logo over a town scene. Nine presses there changed **6 of 524,288 bytes**: the machine
  was running, and nothing the player did mattered. Measured in 600-frame steps, the
  title art is up by **6,000** frames and the PRESS START prompt by **7,200**, which is
  where the experiments now boot to. The "Using it" figure above — 4,000–5,000 frames to
  a title screen — is low for this game.
- **FireRed's second arm is a no-op on Platinum.** `START, A, A, B×6` came out
  **byte-identical** to `START, A×8` — 0 of 524,288 — because B advances Pokémon dialogue
  exactly as A does. A control arm *chosen* to diverge on one game silently stopped being
  a control on another. Platinum's arm withholds START instead, so arm B sits on the
  copyright card while arm A reaches "My name is Rowan."

### E1 — savestate round trip: two claims, and they disagree

"Does a savestate replay?" is two questions, and rolling them together hides the result:

| | FireRed | Platinum |
|---|---|---|
| **replay vs replay** — load, run, load, run | **0 / 294,912** | **0 / 524,288** |
| **live vs replay** — run on from `/save`, vs the same sequence after `/load` | **55 / 294,912** (12 EWRAM, 43 IWRAM, 40 runs) | **0 / 524,288** |
| same, at 31,200 frames | **3,131** (28 EWRAM, **3,103 IWRAM**, 119 runs) | **0** |

The first row is what the benchmark needs and it holds everywhere: two episodes started
from the same start-state get byte-identical machines.

The second row is a real defect on GBA. **`/load` does not restore the machine exactly.**
The evidence that it is `/load` and not `/save` or stepping: the live arm's digest equals
the cold-booted arm's digest in E2 and again at 33,600 frames, so cold boot and
run-on-from-save agree with each other and only the reloaded machine differs. It is
deterministic — the same 55 bytes every time, and replay-vs-replay stays at 0 — and it
**grows**, 55 → 3,131 over 31,200 frames, almost all of it in IWRAM. The `/screen` PNG
is byte-identical at both horizons, so nothing about it is visible.

What this costs: a v2 run resumed from a savestate is not a continuation of the run that
wrote it — it is a *different* run that starts at the same screen. For comparing models
against each other that is fine, because every episode takes the same `/load` path. For
"resume this exact run where it stopped", it is not, and 3 KB of IWRAM is enough to
change an RNG stream. **Which 55 bytes these are has not been identified**, and no test
here shows whether the divergence changes any observable game outcome.

### E2 — two cold processes

Two SkyEmu processes, each from a cold ROM load, each running boot frames plus the
identical sequence. Each arm gets its own directory with a symlink to the ROM and a
**private copy of the `.sav`**: SkyEmu writes that battery file, and on FireRed a `.sav`
beside the ROM changes the boot path, so two arms sharing one would have a channel
between them shaped exactly like nondeterminism.

- **FireRed: 0 / 294,912 bytes differ**, `/screen` PNG byte-identical. A GBA run is
  reproducible from the ROM, not merely resumable from RAM.
- **Platinum: 7–10 / 524,288 differ**, `/screen` PNG still byte-identical. Six stable
  locations inside the 378-byte window `0x02101d2c`–`0x02101ea5`.

The NDS residue is one value, not six faults. Within a pair every differing location is
off by the *same* constant — 0x44, 0x50 and 0x28 in three measured pairs — including a
16-bit little-endian field at `0x02101ea4`. Three more things measured: it appears with
**no inputs at all** (boot frames only); it does **not** scale with wall-clock distance
between launches (90 s apart gave a *smaller* offset than back-to-back); and the
later-launched process was lower every time. **What the value is has not been
identified.** The shape fits a host-dependent seed latched at boot — Gen 4 derives its
initial RNG seed from the DS clock and a boot delay — but that is a hypothesis with
nothing behind it yet, and the address has not been matched to a Platinum map.

### E3 — the savestate file, and why a file diff is the wrong instrument

A SkyEmu savestate is a **PNG** (FireRed ~73 KB, a 1200×800 RGBA image; Platinum
~5.9 MB). Change one byte early in a zlib stream and everything after it moves, so a raw
file comparison reports nearly the whole file as different for nearly any cause. Another
agent measured exactly that on this backend — "roughly 178 KB of a 180 KB file" — while
the emulated machine was identical. `determinism.py` decompresses to the pixel payload
first, and brackets the comparison with three controls:

| | FireRed | Platinum |
|---|---|---|
| **writer** — two `/save` from one *frozen* machine, 2 s apart | **byte-identical file** | **byte-identical file** |
| E2's two files, payload | 107–169 of 3,840,800, tens of runs, inside 5 of 800 image rows, every delta ≤ 4 | ~1.03 M of 25,168,896 |
| **frame 0** — 4 cold processes, `/save` at **0 frames emulated** | 16–24 bytes apart; 7–15 offsets differ in every pair | 23–29 apart; ~15 every pair |
| **propagation** — reload both, run 3,864 further identical frames | 0 → 0 | 10 → 10 |

The writer is deterministic: no timestamp, no nondeterministic compression, and no path
dependence — two processes handed the *identical* ROM path still differ. The savestate
file is nevertheless **not byte-reproducible across processes** on either system, and the
difference is already there with **zero frames emulated**, so it is not accumulated
drift. On FireRed it never reaches game memory at all. Platinum's ~1 MB is not a separate
finding: that machine genuinely differs by E2's ten bytes, so the file is expected to
differ and the number means nothing until E2 is clean.

**A `.state` file diff is not a determinism instrument here.** The load-bearing
comparisons are emulated memory and the `/screen` capture.

### E4 — ten minutes of console time

E1 and E2 again, over 200 presses across six buttons plus a 12,000-frame tail.

| | FireRed, 33,600 frames (9.3 min) | Platinum, 38,400 frames (10.7 min) |
|---|---|---|
| cold process vs cold process | **0 / 294,912** | **7 / 524,288**, the same six locations as at 3,264 frames |
| replay vs replay | **0** | **0** |
| live vs replay | **3,131**, up from 55 | **0** |

So of the three residues, two are bounded — NDS's cold-start offset does not spread, and
the savestate file's does not reach memory — and one is not: GBA's `/load` gap grows with
the horizon.

### The verdict, and what it does not cover

**Reproducibility, in the sense the benchmark needs, holds on both systems.** Two
episodes launched the same way from the same start-state produce byte-identical
machines, and on GBA that extends to two independent cold boots, framebuffer included.
That is strictly better than v1, where a wall-clock `sleep` decides how many frames a
press lasts.

**Two defects are now named rather than suspected**, and they sit on opposite sides:

- **GBA — `/load` is not an exact inverse of `/save`.** 55 bytes at 864 frames, 3,131 at
  31,200. Harmless for model-vs-model comparison, wrong for "resume this run".
- **NDS — a cold boot carries ten bytes of host-dependent state.** Fixed location, does
  not spread, invisible on screen. Avoidable entirely by starting NDS episodes from a
  savestate, which is what the start-state system does anyway.

What none of this covers, stated plainly:

- **38,400 frames.** A 30-hour FireRed run is ~6.5 million — two orders of magnitude
  further out. Nothing here bounds drift that needs a long horizon to appear, and one of
  the three residues already grows within the range that *was* tested.
- **One host, one build.** All of it is this machine and one SkyEmu binary.
  Cross-machine reproducibility is untested, and is the harder claim.
- **No battery save, no battle, no RTC.** FireRed has no real-time clock; **Emerald
  does**, and an RTC is exactly the sort of host input that shows up on NDS here.
  Emerald is untested.
- **RAM only.** VRAM, OAM, palette and audio are not snapshotted. They show through
  `/screen`, which agreed byte for byte in every comparison above, and no further.
- **No observable outcome is tested.** Every number here is memory. Whether any of these
  residues changes an encounter, a damage roll or a checkpoint is unmeasured.
- **Nothing about the model.** Emulator reproducibility is not run reproducibility; the
  model's sampling is the other half and is out of scope here.

## `/load` resumes one frame late — 2026-09-19. The divergent bytes are load-bearing

The section above found that on FireRed a reloaded machine drifts from a live one and
stopped at the byte count, noting that "**which 55 bytes these are has not been
identified**, and no test here shows whether the divergence changes any observable game
outcome". [`load_divergence.py`](load_divergence.py) is that step. It reproduced the 55
bytes exactly — same count, same 40 runs, the same address set on two independent
repetitions — and then named them.

### The headline: `gRngValue` is one of them

`0x03005000`–`0x03005003` is in the divergent set every time, with wholly different
values (`0xdcb64200` live against `0xeee85e65` reloaded — not a drift, a different
number). That address is FireRed's RNG, established by measurement rather than by a
symbol table this repo does not carry: stepping one frame and searching all 8,192 IWRAM
words for one obeying the Gen-3 LCG `x' = 0x41C64E6D·x + 0x6073` returns **exactly one
hit**, at `0x03005000`, twice over. It also sits 8 bytes below `gSaveBlock1Ptr`
(`0x03005008`, `src/referee/referee.py`), which is where pret's common-symbol order puts
`gRngValue` and `gRng2Value`.

The other 51 bytes are almost all downstream of it. The save blocks are DMA-shuffled to
an offset that differs by `0x5c` between the two arms, and that one offset accounts for
`gSaveBlock1Ptr`, `gSaveBlock2Ptr`, the word at `0x03005010` (pret's common-symbol
order puts `gPokemonStoragePtr` there; not independently verified), a table of
copies at `0x030053b0`–`0x03005418`, and five cached EWRAM pointers at
`0x0203988c`–`0x020398ac` — every one of them differing by exactly `0x5c`. So the 55
bytes are roughly: 4 bytes of RNG, ~30 bytes of "the save blocks moved", one byte of
`gMain+0x24`, and a dozen unidentified singletons.

### What `/load` actually gets wrong: nothing, except *when* it resumes

The defect is not corruption. Comparing the reloaded machine at frame *k* against the
live machine at frame *k+1* over the whole 288 KB gives **zero bytes differing, at every
k, on both a cold title screen and the benchmark's own start state**:

| | live+0 | live+1 | live+2 | live+3 |
|---|---|---|---|---|
| **reloaded+0** | 522 | **0** | 537 | 963 |
| **reloaded+1** | 942 | 537 | **0** | 489 |
| **reloaded+2** | 1379 | 963 | 489 | **0** |

`/load` restores the machine perfectly and resumes it **one frame ahead** of where
`/save` was taken. Consistent with that, `TM0CNT_L` and `TM1CNT_L` differ across a
`/load` (by 1 and by ~0x4941) while `DISPSTAT` and `VCOUNT` do not — and FireRed seeds
`gRngValue` from `REG_TM1CNT_L | REG_TM2CNT_L << 16`.

**That does not make it benign.** With nothing but `/step`, the one-frame shift stays a
pure shift — phase-corrected difference 0 at 864 frames. With a single button press it
does not: the harness counts input frames from the resume point, so a machine one frame
further along receives every press at a different point in its own frame, and the two
machines genuinely part company (phase-corrected difference 60 bytes at 864 frames, up
from 0). A resumed run is therefore not a delayed copy of the run it resumes; it is a
different run.

### The outcome test, which the section above did not have

Both arms take the **same** inputs from the **same** savepoint, and every comparison
carries a control — a second `/load` of the same file — which came out at zero
everywhere.

- **`/screen` is not identical.** Standing where nothing moves it is, which is what the
  single-frame check above measured. At a Pallet Town vantage chosen for motion (30
  distinct frames per 600, against 1 at a still spot), **16 of 121 sampled frames differ**
  between live and resumed. The differing pixels are the water animation at the south
  edge, so this is visible but cosmetic on its own.
- **A battle is fought differently.** Playing the opening to the rival battle at the lab
  door — Charmander 19 HP against Squirtle 19 HP — and then taking 160 identical `A`
  presses on each arm: the traces disagree from turn 36, **no shift from −3 to +3 aligns
  them** (121–124 of ~160 turns disagree at every shift, so this is not a timing
  artifact), and the HP trajectories are different numbers throughout — live
  19→14→9→5→3, resumed 19→16→13→10→9→7. Charmander wins both times but finishes on
  **5 HP live and 9 HP resumed**. The damage rolls differ; a critical hit landing on the
  other side of a knockout would flip the result outright.

The recipe that reaches that battle is in `load_divergence.py` as a fixed input script
with `expect_*` assertions at each landmark (routes computed from
`data/firered-walkgraph.json`), so it fails loudly rather than drifting into a state that
looks fine and tests nothing.

### The workaround, measured

If the live run **also** does a `/save` immediately followed by a `/load` at every
savepoint, both arms have paid the same one-frame cost and agree by construction. Run as
a fourth arm of the outcome test: **0 bytes, 0 screens, 0 RNG samples differing** across
the whole observation window. This is a harness-side fix and needs no change to SkyEmu —
but it has to be applied when the savepoint is *written*, not when it is read, so it
belongs next to `save_savepoint`, and it changes the live run (by one frame) rather than
leaving it alone.

### What this corrects in the section above

- "**the defect is in `/load`**" — right about which call, wrong about what it does.
  `/load` restores state exactly; it resumes one frame late.
- "**it grows, 55 → 3,131**" — the growth is real but it is not accumulating corruption;
  it is two machines that parted company at the first press and then diverged like any
  two machines running different RNG streams.
- "**The `/screen` PNG is byte-identical at both horizons, so nothing about it is
  visible**" — false as a general claim. It holds only for a static scene.
- "**3 KB of IWRAM is enough to change an RNG stream**" — understated. 4 bytes of it
  *are* the RNG stream, from the first frame.

### Still not covered

- **One frame, always?** Measured as exactly one at two different save points. Not
  measured across a wider variety of states, and not measured on NDS (where `/load` is
  already exact, which is itself a hint that the one-frame skip is GBA-specific).
- **Where the frame is lost.** Whether `/save` writes a state one frame ahead of the
  machine or `/load` emulates a frame on the way in is not distinguished here; the two
  are indistinguishable from the HTTP side and the harness consequence is identical.
- **The battle test is n=1.** One battle, one savepoint. The control (a second `/load`)
  is clean and the alignment sweep rules out a timing artifact, but a second battle from
  a different savepoint has not been run.

## Sources

- [SkyEmu](https://github.com/skylersaleh/SkyEmu) · [HTTP Control Server docs](https://github.com/skylersaleh/SkyEmu/blob/dev/docs/HTTP_CONTROL_SERVER.md) · [issue #576](https://github.com/skylersaleh/SkyEmu/issues/576) · [releases](https://github.com/skylersaleh/SkyEmu/releases)
- [RetroArch Network Control Interface](https://docs.libretro.com/development/retroarch/network-control-interface/) · [Remote RetroPad](https://docs.libretro.com/library/remote_retropad/) · [issue #13664](https://github.com/libretro/RetroArch/issues/13664)
- [melonDS DS libretro core](https://docs.libretro.com/library/melonds_ds/) · [melonDS Lua PR #1671](https://github.com/melonDS-emu/melonDS/pull/1671) · [melonDS-lua docs](https://github.com/NPO-197/melonDS-lua/blob/master/tools/LuaScripts/Lua_Docs.md)
- [BizHawk](https://github.com/tasemulators/bizhawk) · [mGBA scripting API](https://mgba.io/docs/scripting.html)
