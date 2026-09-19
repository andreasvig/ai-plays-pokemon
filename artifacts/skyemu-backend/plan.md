# Swapping mGBA for SkyEmu — analysis

> Source: conversation 2026-09-19 (Andreas + Marvin). Every ✅ below was run on this
> machine against real ROMs; nothing is quoted from a page.
> Status: **analysis only.** Nothing built, no branch cut, no decision taken. The
> open decisions are in §8 and belong in chat, not here.

Andreas's framing, and it reframes the whole document (2026-09-19):

> "this is not the goal of just an exchange. the long running goal is the full v2
> benchmark"

So this is **not a backend swap that happens to enable NDS**. It is the first branch
of v2, and the backend swap is the part of v2 that can be started today. That changes
what "done" means: the bar is not "mGBA's behaviour, reproduced" but "a benchmark we
would want to run", and where the two disagree, v2 wins.

FireRed first, main stays main, **small misalignment is acceptable**.

The experiment this builds on lives in [`v2-experiments/`](../../v2-experiments/) —
`emulator-research.md` for the backend contract, `harness/` for a working client.

---

## 0. Decisions taken (Andreas, 2026-09-19)

| # | Question | Decision |
|---|---|---|
| A | Merge the seam dark into main early, or keep it on the branch? | **Keep it on the branch.** Not an exchange — the branch is v2's beginning, and a seam merged into main invites main to grow v2-shaped compromises before v2 has earned them. |
| B | Pacing default | **Fast.** Headless speed is a real benefit of the design; a spectated run opts into realtime. |
| C | How much parity to buy | **Accept the drift.** Measure it in the paired run, label the arm. Do not tune v2 toward v1's artefacts. |
| C′ | OCR | **Deferred, not ported.** "we could wait with the OCR service as i am not happy with that anyway." v2 starts with `ocr.enabled: false`. See §3.3 — this deletes the one architectural change the port had. |
| D | First branch scope | **Carry NDS.** The stylus verb and the stacked frame land in the same branch, because they are v2 features, not backend details. |

---

## 0.1 What is already proven, and what is not

| Claim | Status | Evidence |
|---|---|---|
| SkyEmu runs headless on arm64 | ✅ | two-line patch, `v2-experiments/skyemu-headless-arm64.patch` |
| GB / GBC / GBA / NDS all boot and render | ✅ | FireRed title + intro captured here; Platinum, SoulSilver, Black 2, Crystal earlier |
| Screenshots, inputs, frame step, save/load, read, **write** | ✅ | `harness/selftest.py`, 12 checks, mutation-shaped |
| `read_memory(addr, len)` — the referee's entire backend contract | ✅ | `referee.py:108`; implemented and checked against single-byte reads |
| **FireRed's referee map reads correctly through SkyEmu** | ✅ | `gSaveBlock1Ptr` at `0x03005008` dereferences to a live SaveBlock1; map, x/y and party read correctly from it |
| `TRACE` is reproducible without a patch | ✅ | it samples once per INPUT (`socketserver-1.lua:266`), not per frame |
| Per-frame tracing, if ever wanted | ✅ | step 1 + 64-byte read = 156 fps, 2.6× real time |
| A model can play through it | ✅ | gpt-6-astra low, 10 turns × 2 NDS titles, stylus included |
| MP4 of a run | ✅ | `harness/record.py`, same encoder as the dashboard |
| **mGBA savestates load in SkyEmu** | ❌ | `/load` on `configs/saves/pokebench-v1/emulator.state` → `failed` |
| **x/y moving as the player walks** | ✅ | from the P3 start state: `(6,6)` → three `down` → `(6,8)`, reload → `(6,6)` (2026-09-19) |
| **A FireRed start state exists on SkyEmu** | ✅ | `configs/saves/skyemu/firered-pokebench-v2/`, replayed from cold boot in 8,598 frames / 15.5 s |
| GBA accuracy / determinism vs mGBA over a long run | ⬜ **untested** | the main risk, §6 |
| Audio | ⬜ **untested** | spectate has a mute toggle; SkyEmu headless audio unknown |

---

## 1. The shape of the problem: one inversion, everything else follows

v1 and v2 differ in **one** thing, and almost every consequence below is that thing
seen from a different angle:

> **mGBA runs free and Python watches it. SkyEmu is frozen and Python advances it.**

mGBA is a GUI app running at 60 fps on its own clock; Python presses buttons, sleeps
in wall-clock, and samples asynchronously from three threads. SkyEmu headless does
nothing at all until `/step`, and answers one HTTP request at a time.

That is mostly an **upgrade**, because every place v1 sleeps against a wall clock is a
place where a slow machine and a fast machine see different games:

| v1 | v2 |
|---|---|
| `press_button_list` sends `SEQ:` then `time.sleep(total_frames/60 + 0.5)` | `step(total_frames)` — exact, and faster than real time |
| `WAIT` = `time.sleep(5.0)` | `step(300)` |
| `wait_for_stable_screen` polls captures every 0.3 s until frames repeat | step N, compare, repeat — deterministic |
| OCR thread captures every 0.4 s of wall clock | sample every N frames |

It is a **problem** in exactly one place, and that place is §3.3.

---

## 2. The coupling surface is small — and it is not where you'd expect

### 2.1 The client interface: 10 methods, two attributes, and six that nothing calls

*(Re-measured during P0, 2026-09-19. The earlier draft of this section said
"12 methods" and listed six more as part of the dependency that in fact have
**zero** call sites in `src/`. `src/emulator/backends/base.py` is now the
authority; these numbers are derived from it.)*

`EmulatorClient` exposes 18 public methods. Counting actual call sites across `src/`:

```
read_memory  8   disconnect  4   start_server  3   wait_for_connection  3
load_state   3   capture_screenshot  3   save_state  2   ping  2
press_button_list  1   wait_for_stable_screen  1
attributes:  trace_spec (2 writes)   facing (1 write)
optional:    fetch_trace  (1, reached via getattr at turn.py:1897)
```

Two things the first draft missed. **The attributes are load-bearing** — a backend
that implements every method and neither `trace_spec` (written at `runner.py:599`,
`launch.py:96`) nor `facing` (`turn.py:1818`) still breaks a run. And **six public
methods have no caller at all**: `connect`, `resync`, `pause`, `unpause`,
`press_button`, `press_sequence`. A v2 backend does not have to implement them,
which matters more than it sounds — see §4.1.

That is the whole dependency. **A backend swap is an adapter behind this interface**,
not a rewrite of the turn loop. The referee's own contract is a single method
(`referee.py:108`: "an emulator-like object exposing `read_memory(addr, length) ->
bytes`"), which is already implemented and verified.

### 2.2 `emulator.type` is decorative

`configs/config-5.1.yaml` says `emulator: {type: mgba, ...}` and **nothing reads it**.
There is no backend seam today. Creating one is P0 and it is the only change that
touches main's code path, which is why P0 ships with "no behaviour change" as its
acceptance test rather than a feature.

### 2.3 The real coupling is outside `src/emulator/`

This is the part that a naive estimate misses. mGBA is a **GUI application driven by
macOS Accessibility**, and that leaks into the app layer:

- `runner.py:285` `load_lua_script_in_mgba_for_pid` — drives *File → Load recent
  script* in mGBA's Scripting window, with a printed manual fallback.
- `runner.py` `load_rom_in_mgba_for_pid`, `set_mgba_mute_for_pid` — *File → Recent*,
  and audio muting, both by window automation.
- `supervisor.py:172` `switch_rom` — a two-mechanism dance (in-place menu drive vs
  relaunch) that exists *only* because re-loading a ROM in mGBA costs the Lua
  connection.
- `lua/socketserver-1.lua` — the wire protocol, and with it `resync()`, FIFO reply
  ordering, `QUEUED:`/`SEQUENCE_DONE` notification skipping, `stale_replies_skipped`,
  `ProtocolError`, and the threading lock that serialises the OCR poller against the
  turn loop.

**All of that deletes.** SkyEmu takes the ROM as `argv`, needs no window, no
Accessibility permission, no script loading, and answers one request per request with
no unsolicited notifications. `switch_rom` becomes "relaunch with a different path" —
a handful of lines replacing the most fragile subsystem in the run lifecycle.

That is the strongest argument for the branch **independent of NDS**: it removes the
single biggest source of run-start flakiness.

---

## 3. What actually has to be built

### 3.1 P0 — the seam (no behaviour change)

Make `emulator.type` mean something: `src/emulator/backends/mgba.py` (today's class,
moved) and `src/emulator/backends/skyemu.py`, both behind the existing method set,
selected by config. Main's configs keep `type: mgba`.

**Acceptance: the full existing suite passes unchanged, and a real FireRed run on
`type: mgba` produces a run dir indistinguishable from one cut before the branch.**
This phase earns nothing and de-risks everything after it.

### 3.2 P1 — the SkyEmu adapter

Port `v2-experiments/harness/skyemu.py` into the seam. Screens, inputs, `read_memory`,
save/load, lifecycle. Mostly done already; the work is shape, not discovery.

Two known gotchas, both recorded in `emulator-research.md`: text replies carry a
trailing NUL (`bytes.strip()` does not remove it), and `/read_byte`'s `map` parameter
must precede its `addr` parameters in the query string.

### 3.3 P2 — timing semantics (and the change that decision C′ deletes)

Three of the four rows in §1's table are local rewrites: `press_button_list` swaps a
wall-clock sleep for `step(total_frames)`, `WAIT` becomes `step(wait_seconds * 60)`,
and `wait_for_stable_screen` becomes step-N-compare instead of poll-and-sleep. All
three get *more* correct, not less — a frame count is the same on every machine and a
sleep is not.

The fourth was OCR, and it was the only place the port needed an architectural change
rather than a rewrite. `OCRRunner` is a daemon thread capturing every
`capture_interval` (0.4 s) of wall clock while the game runs — and on a stepped
emulator there is no "while". It would have had to stop driving itself and become a
consumer of frames the step loop hands it.

**Decision C′ removes that work from the branch.** OCR is already fully optional:
`runner.py:817` constructs an `OCRRunner` only when `ocr.enabled`, and every consumer
guards on `self.ocr and self.ocr.enabled` (`turn.py:1799`, `:1822`, `:2094`, with cost
accounting falling back to 0). So v2 starts with `ocr.enabled: false` and the thread
never exists.

Two consequences worth stating plainly:

- **v2 is vision-only at the start.** The model loses the OCR text channel entirely.
  That is a larger behavioural difference from v1 than anything else in this document,
  and it is deliberate — the mechanism was not trusted, so v2 does not inherit it.
- **When OCR (or its replacement) comes back, the frame hook is already there.** The
  step loop's sampler feeds the recorder and spectate; a text extractor becomes a third
  consumer of the same frames, sampled every N frames rather than every 0.4 s of
  whatever the machine was doing. Deferring it costs nothing structurally.

### 3.4 P3 — start states, which do not port *(built 2026-09-19)*

`configs/saves/pokebench-v1/emulator.state` is an mGBA savestate (a PNG with the state
in ancillary chunks). **SkyEmu refuses it** — verified, `/load` → `failed`. SkyEmu
writes its own PNG-embedded format and has BESS best-effort restore, but not for this.

Consequences:

- The canonical benchmark start has to be **re-created** on SkyEmu: boot FireRed, play
  to the same moment, `/save`. Done — `v2-experiments/make_start_state.py` replays cold
  boot to the bedroom in 8,598 frames (15.5 s wall clock, ~9.5x real time) and two
  independent runs produced identical emulated memory. Two facts made it reproducible
  without a human: the ROM is copied to a directory with **no `.sav` beside it** (a save
  file puts CONTINUE on the title screen and every later press lands on the wrong
  screen), and **START on an empty name field is the "accept the default" verb** — the
  player becomes KAY and the rival GREEN, constant across three different frame offsets
  into the naming screen, so it is a hardcoded default rather than an RNG draw.
- **`0x02025534` is not an address to hardcode.** FireRed DMA-shuffles its save blocks:
  the pointer read `0x0202552C` pre-game and `0x02025594` in the bedroom on the same
  boot. `referee.py` already dereferences `gSaveBlock1Ptr` fresh on every poll, so the
  referee is correct — but an earlier draft of this document quoted the resolved value
  as if it were the address, and it is not.
- **A savestate file is not a determinism instrument.** SkyEmu writes PNG containers;
  two saves of an identical machine state differ across ~178 KB of 180 KB. Compare
  emulated memory or the framebuffer, never the `.state` bytes.
- `configs/starts.yaml`, every `configs/saves/*` dir, and every run savepoint is in
  mGBA's format. **A SkyEmu run cannot `--continue` an mGBA run, or vice versa.** A
  savepoint needs a backend marker so the executor refuses the mismatch loudly instead
  of producing a `failed` deep in a run.
- The `.sav` (battery SRAM) **is** portable between emulators. It does not help here,
  because the canonical start is a savestate mid-intro, before the game has ever saved.

### 3.5 P3b — the NDS capability (decision D)

Carried on this branch because they are v2 features, not backend details. Two pieces,
already built and verified in `v2-experiments/harness/`:

- **The stacked frame.** `/screen` on NDS is 256x384, top screen above touch screen,
  fixed in SkyEmu's `se_screenshot`. The frame the model sees gets a seam between the
  halves and **no grid overlay** — v1's lattice is 16 px with an 8 px offset, both GBA
  viewport facts, and on a DS frame they land hardest on the touch screen where the
  model has to aim.
- **The stylus verb.** This is the part that is not a new enum member. Every existing
  action is one string; a tap is a verb plus two floats. It touches: `Button` in
  `agent/agent.py:12`, `valid_inputs` in every config (read in `config.py:437`,
  `agent.py:254` for the prompt's button list, and `emulator.py:219/423/511` for
  validation), the prompt's action description, and the per-input census in
  `projection.py:460`.

Neither is on FireRed's path, so P5's referee work does not wait for this.

### 3.6 P4 — spectate and video (§4)

### 3.7 P5 — prove the referee on FireRed

The address map already reads (§0.1). What is untested is the map *moving*: x/y changing
as the player walks, the in-battle bit flipping, the battle counter incrementing. The
first task is a start state in the overworld (P3 gives it), then a scripted walk
asserting the tile changes — the exact signal `overworld_steps` counts.

Then a real run with checkpoints latching, graded against a known-good mGBA run of the
same start: not the same turns, but the same checkpoints in the same order.

### 3.8 P6 — a paired comparison

One model, N runs on each backend from each backend's own canonical start. This is what
converts "it works" into a number we can say something about (§5).

---

## 4. Live spectate: yes, and cheaper than expected

The spectate feed's contract is **a PNG file that keeps changing**:

```
lua/socketserver-1.lua:346  →  /tmp/mgba_stream_1.png   (every frame)
screen_stream.py            →  polls mtime at 125 Hz, validates the IEND marker
server.py:447               →  WS /runs/{id}/ws/screen forwards each new PNG
```

Nothing in that chain knows which emulator wrote the file. The v2 sampler already
produces exactly this stream — it is what feeds the recorder — at a measured 54
captures/s.

Better: `recorder.py` takes `screen_source=session.streamer` (server.py:1281), so
**feeding the stream file gets the existing dashboard MP4 recorder as well**, card,
typography and all. The standalone `harness/record.py` stops being needed on this path.

Three things that are not free:

1. **Pacing — decided B: fast by default.** mGBA runs at real time; SkyEmu steps as
   fast as the host allows (147 frames/s vs 60), so a spectator would see execution at
   ~2.5× in bursts. `pace: fast | realtime` on the run, defaulting to **fast**: headless
   speed is a real benefit of the stepped design and a queue run should keep it. A
   spectated run opts into `realtime`, which costs the wall clock the throttle adds —
   about 10 s on a measured 41 s ten-turn run. Note this makes "is anyone watching?"
   affect run duration but *not* run content: the game sees identical frames either
   way, which is the whole point of the emulator being frozen between steps.
2. **30 fps, not 60.** At 54 captures/s the sampler cannot sustain 60, because every
   capture is a round trip and SkyEmu answers one at a time.
3. **Audio is unknown.** The Home and Spectate mute toggles drive mGBA via
   Accessibility. Whether SkyEmu headless produces audio at all is untested.

### 4.1 `pause_during_thinking` does nothing, and that is a v1 finding

An earlier draft of this section said the feed freezing while the model thinks is
"not a difference", because `config-5.1.yaml` sets `pause_during_thinking: true`.
**That was wrong, and the way it is wrong is worth more than the spectate question
it was answering.** Checked three ways on 2026-09-19:

- `grep -rn pause_during_thinking` across every `.py`, `.lua`, `.js`, `.svelte` and
  `.md` in the repo finds it **only in the five config files**. No code reads it.
- `EmulatorClient.pause()` and `.unpause()` have **zero call sites** in `src/`.
- `lua/socketserver-1.lua:112` — the `PAUSE` handler's entire body is
  `respond("OK:Paused")`. Even if something did call it, it would not pause mGBA.

So in v1 **the GBA runs free at 60 fps for the entire time the model is thinking.**
The key is at `config-5.1.yaml:44`, it has been in every config since 3.13, and it
has never done anything.

The consequence is not mainly about spectate. FireRed's RNG advances every frame, so
**the RNG state at the moment an input lands depends on how long the model took to
answer.** A model that thinks for 4 s and one that thinks for 40 s arrive at the
same screen with the emulator in different states. It is bounded — nothing advances
without input in dialogue, and no encounter fires while standing still — but it is
real, it is unmeasured, and it is exactly the class of wall-clock leakage §1 says v2
removes. On a stepped emulator it is structurally impossible: the machine does not
move between `/step` calls, so a slow model and a fast one see byte-identical games.

This should be surfaced to Andreas as a v1 defect in its own right, separately from
the branch. Fixing it in v1 is one line of Lua plus two call sites; deciding whether
to fix it is a benchmark-comparability question, because every run on the board today
was played with it broken.

---

## 5. What a SkyEmu v1.1 number would mean

Andreas accepts small misalignment. Being precise about what misaligns, because it
decides where the runs are allowed to appear:

1. **Different start state.** Re-created, not copied (§3.4).
2. **No OCR at all.** Not "different text" — decision C′ turns the channel off, so v2
   starts vision-only. This is the largest behavioural difference in the document.
3. **Different emulation.** Different core, different timing, different RNG. The same
   inputs do not produce the same game.

**Decision C: accept the drift.** (1) and (2) could in principle be tuned toward v1,
and deliberately will not be — tuning v2 toward v1's artefacts is how a rewrite inherits
the things it was meant to replace. (3) could not be tuned anyway: two emulators are two
games, and no amount of alignment makes a turn count on one comparable to a turn count
on the other at the resolution the leaderboard claims.

What "accept the drift" does **not** mean is "do not measure it". P6 exists to put a
number on the gap; accepting the drift is a decision about what to *change*, not about
what to *know*.

So the recommendation is blunt: **a SkyEmu run is a different arm, not a continuation
of the board.** It gets its own label, and it stays off the main leaderboard until P6
gives a paired measurement saying how far apart the two backends actually sit. If they
land within noise, merging the arms becomes a decision with evidence behind it. If they
do not, we will have learned something worth knowing before publishing it.

This is also why the branch is worth cutting even if it is never merged: the number it
produces is the thing that decides whether it should be.

---

## 6. Risks, in the order they would bite

1. **GBA accuracy over a long run.** Everything tested so far is minutes. FireRed has
   30-hour runs with battles, scripted events and saves. SkyEmu is a good emulator but
   mGBA is the reference. *Mitigation: P5's long run with the referee latching, before
   any of the surrounding work is polished.*
2. **Determinism.** v1 is not reproducible either (wall-clock sleeps), and v2 ought to
   be *more* so, but "ought to" is untested. *Mitigation: run the same scripted input
   sequence twice from one savestate and diff the memory. A cheap, decisive check —
   worth doing in P1, before anything is built on top.*
3. **One request at a time.** Every consumer — turn loop, OCR, referee poll, spectate,
   recorder — serialises through the step loop. In v1 a lock made this convenient; in
   v2 it is a hard constraint. Any future "watch it while it runs" feature has to be
   expressed as sampling, not as a thread.
4. **The start-state cliff.** Every existing savepoint becomes unloadable on the new
   backend. Without a format marker this surfaces as a `failed` mid-run. *Mitigation:
   the marker is P3, not later.*
5. **Branch drift, which is the price of decision A(b).** Nothing merges back until P6,
   so the branch lives against a moving main. P0 is the only phase touching shared code;
   every later phase is additive files behind `type: skyemu`. Rebase on a schedule
   rather than at the end, and keep P0 small enough to re-apply.
6. **Question 1 in §8 answering itself by default.** The branch is v2's first branch,
   and a long branch with momentum is how "what is v2" gets decided by whoever happens
   to be typing. The scope questions want an answer before P5, not after.

---

## 7. Branch strategy

**Decision A: everything stays on the branch.** Not merged dark. The reasoning is the
reframe at the top — if this were a backend exchange, a dark seam in main would be the
cautious choice. It is not: it is v2's first branch, and a seam sitting in main invites
main to grow v2-shaped compromises before v2 has earned any of them. Main keeps
`type: mgba` because main keeps *everything* it has today.

- Branch `skyemu-backend` off main. Nothing merges back until P6 reports.
- P0 is a pure refactor **on the branch**, with the existing suite as its oracle — so
  the seam is proven behaviour-neutral even though main never sees it.
- The cost of A(b) is drift: a long-lived branch rots against main. Mitigation is
  rebase discipline, and the fact that P0 is the only phase touching shared code — every
  later phase is additive files behind `type: skyemu`, which rebase cleanly.
- The existing uncommitted Pareto work is unrelated and should be committed before the
  branch is cut, so the diff is readable.

---

## 8. Still open — raised BY the decisions, not settled by them

§0's four are answered. These are the questions those answers created, and they are
chat questions, not doc questions.

1. **What is "the full v2 benchmark"?** The reframe gives the branch a destination this
   document does not contain. `v2-experiments/task-catalog.md` and `README.md` hold
   pieces of it; whether v2 keeps v1.1's checkpoint ladder, its scoring, and its
   leaderboard shape is undecided and much bigger than the backend. **This document
   should not be the place that quietly decides it.**
2. **Vision-only: destination or waypoint?** C′ defers OCR because the service is not
   trusted. Whether v2 ships with no text channel at all, or gets a different one, is a
   design question the backend does not answer. It is worth answering before P6, because
   P6's number is measured on whatever v2 is at that moment.
3. **The paired comparison's shape.** Which model, how many runs per arm, and what
   counts as "close enough". Worth fixing BEFORE P0, because a comparison designed after
   seeing its first result is not a comparison. (The registry's `observed` sample-turn
   convention and `n=1 is not a finding` both apply.)
4. **Does the v1.1 ladder transfer as-is?** FireRed checkpoints read the same addresses
   (§0.1), so mechanically yes. Whether the same gates are the right gates for v2 is
   question 1 wearing a smaller hat.
