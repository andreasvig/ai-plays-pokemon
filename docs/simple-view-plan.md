# Simple View — build plan

> **Plan document.** Written 2026-08-01 from a design session with a working
> mock. Archive to `agent_brain/projects/ai-plays-pokemon/mechanics/` once
> shipped; it is not a permanent doc.
>
> Reference implementation: `docs/simple-view-mock.html` (+ its data,
> `docs/simple-view-mock-turns.json` — 20 real turns from run
> `2026-08-01_12-20-01_config-4.0__gemini-3-6-flash-high`). A standalone,
> self-contained mock of the finished behaviour.
> **Port from it; do not re-invent the timings, geometry, or SVG paths.**
>
> Serve it, don't open it as a `file://` — it fetches its data:
> `python3 -m http.server 8791 --bind 127.0.0.1` from `docs/`, then
> <http://127.0.0.1:8791/simple-view-mock.html>. Screenshots load from the
> control center on :3420, so the game images only appear when `pokemon app`
> is up; everything else works without it.

---

## 1. What this is

A second, recording-optimised presentation of a live run. The full Spectate view
is an instrument panel — stats, gates, memory, trace feed. The simple view is
the opposite: a square frame holding the game screen and one box, designed to be
screen-recorded and posted with no editing.

**Scope: presentation only.** No new backend events, no new API fields, no
change to the run loop. Everything it needs is already on the wire.

---

## 2. Locked decisions

Settled with Andreas during the design session. Do not relitigate these.

| Decision | Value | Why |
|---|---|---|
| Layout | Stacked — screen above, box below | Chosen over side-by-side and overlay |
| Aspect | **1:1 only** | Single frame, no presets |
| Theme | **Paper** — ink on warm white | Chosen over terminal / DMG / slate |
| Typeface | Monospace throughout | Andreas's stated preference |
| Emoji | **None, anywhere** | Replaced by SVG of real GBA controls |
| Field labels | Cut | "Output"/"Action" headings are redundant |
| Success pill | Cut | `✓ succeeded` / `✗ failed` removed entirely |
| Press repeats | One glyph per press, flowing | No `×4` collapsing |
| Truncation | **Never** | Text auto-fits instead |
| Box height | 24% of frame | Tunable; 20% clips, verified |
| Order inside box | Turn number → presses → reasoning | Presses above the text |

**Palette** (from the mock, keep exact):

```
--paper #f2efe9   stage background
--card  #fbf9f5   box fill
--rule  #ddd7cc   borders
--ink   #1f1c17   turn number, glyphs
--body  #3b362e   reasoning text
--faint #9a9184   the word "TURN", pending box text
```

---

## 3. The one open call, now decided

Text size varies per turn because the box is fixed and nothing may be truncated.
Measured across all 20 turns of a real run at box=24%: **6.9px – 12.2px**, a 1.8×
swing that reads as the text "breathing" during playback.

**Decision: ratchet down (option B).** Andreas deferred to my recommendation.

- Start each run at the size ceiling.
- When a turn needs smaller, adopt that size and **keep it for the rest of the
  run**. Never grow back.
- Converges within a handful of turns; still never truncates.
- State is per-run: reset the ratchet when `activeRunId` changes.

Implementation: keep `ratchetPx` in component state. After `fitSay()` returns a
size for the new turn, set `ratchetPx = Math.min(ratchetPx ?? fitted, fitted)`
and apply `ratchetPx`. Re-fit and reset on resize (the ceiling is
frame-relative, so a resized window invalidates the ratchet).

---

## 4. Architecture

### 4.1 New files

| File | Contents |
|---|---|
| `src/dashboard/web/src/components/SimpleView.svelte` | The whole view: stage, screen, box, pending box, phase machine, streaming |
| `src/dashboard/web/src/components/Action.svelte` | One GBA control glyph. Props: `token`, optional `delay` (ms, for the stagger) |
| `src/dashboard/web/src/lib/prose.js` | `splitParas()` / `renderParas()` — the paragraph-flow logic |

### 4.2 Modified files

| File | Change |
|---|---|
| `src/dashboard/web/src/components/Spectate.svelte` | Toggle button in `.bar`; renders `<SimpleView>` instead of `.layout` when active |
| `src/dashboard/web/src/lib/format.js` | **Delete `actionEmoji()` and `ACTION_EMOJI`** |
| `src/dashboard/web/src/components/TraceFeed.svelte` | import + **1** call site (`:99`) → `<Action>` |
| `src/dashboard/web/src/components/Report.svelte` | import + **4** call sites (`:132`, `:139`, `:446`, `:457`) → `<Action>` |

Andreas asked for the emoji to be replaced **in the regular system too**, so
`actionEmoji` dies rather than coexisting. **5** call sites plus 2 imports are
the full set — re-verified 2026-08-01 with an unrestricted grep of
`src/dashboard/web/src/`. (An earlier revision of this plan said 4; it missed
one of Report's. Re-grep rather than trusting this count.)

Two of Report's sites are awkward: `:132` and `:139` sit inside a `$derived`
that returns a **string**, not markup, so they cannot simply become a
component. Either return the token list and render `<Action>` at the consuming
site, or keep a plain-text `actionTokens()` helper for those two and use
`<Action>` only where markup is renderable. Decide in-band; note which in the
commit message.

### 4.3 Where the toggle lives

Spectate's existing `.bar` (top strip, currently: back / live pill / mute /
kind badge / model / connection indicators). Add one button there.

Inside the simple view the bar is gone; **exit is a small `✕` in the top-left
of the stage**, `opacity:0` until the mouse moves, hidden again after 2s. The
same idle timer hides everything else, so the recording frame stays clean.

State lives in Spectate (`let simple = $state(false)`), not in the route —
`/spectate` stays a single URL. Persist to `localStorage` so it survives a
reload mid-recording.

---

## 5. The phase machine

**This is the heart of the feature.** Andreas's requirement: the pending box must
appear *only* when the model actually begins thinking — never while buttons are
executing and the screen is settling.

### 5.1 Verified event mapping — no new backend events needed

Confirmed in `src/agent/turn.py`; all four are already handled or ignored in
Spectate's `ingestEvent`:

| Event | Emitted at | Simple view does |
|---|---|---|
| `turn_start` | `turn.py:1815`, top of the turn, **before** the vision + LLM call | **thinking** — show the pending box for `evt.turn`, start the dots |
| `llm_output` | when the decision arrives; `args.inputs` + `args.reasoning` | **promote** — morph, then stream presses, then text |
| `screen_settled` | `turn.py:1613`, after `wait_for_stable_screen()` | **executing → idle** — pending box stays hidden |
| `button_sequence` | `turn.py:1609` — **after** `press_button_list()` returns | *not used*; it marks the end of pressing, not the start |

The resulting cycle: `turn_start(N)` → thinking → `llm_output(N)` → promote +
stream → executing (no pending box) → `turn_start(N+1)` → thinking again.

That falls out of the existing stream exactly as specified. Note the trap
`button_sequence` sets: its name suggests "presses starting" but it is logged
*after* the presses complete. Do not key execution start on it.

### 5.2 Timings (from the mock; all tuned by eye with Andreas)

```
morph      520ms   cubic-bezier(.32,.72,0,1)   pending box → main box
old turn   520ms   translateY(-62%) + fade     runs concurrently
press      85ms    per glyph, staggered via CSS animation-delay
pop         260ms  cubic-bezier(.2,1.4,.4,1)   scale .7 → 1 per glyph
text       ~850ms  total, word-by-word, independent of length
dots       340ms   per step: none → . → .. → ... → none
```

### 5.3 The morph

The pending box does not slide — it **becomes** the main box.

1. Clone the pending box into an absolutely-positioned `.morph` at its exact
   current rect (relative to `.stage`).
2. Hide the real pending box (it has vacated its slot).
3. Add `.out` to the retiring slot — `translateY(-62%)` + fade.
4. `requestAnimationFrame` → animate the clone's `top` and `height` onto the
   main box's rect; border dashed→solid; background transparent→`--card`.
5. After 520ms, write the new turn into the real box and remove the clone.

Seamlessness depends on one thing: **`.pending`, `.morph`, and `.slot` must
share identical padding (`1em 1.3em`) and top alignment**, so the "TURN n" label
never shifts during the handoff. If the label jumps, that's the cause.

### 5.4 Streaming

- **Fit before streaming.** Render the full reasoning, run the fit, apply the
  ratcheted size, *then* empty the element and stream into it. Fitting
  progressively would shrink the type as words arrive — reads as a layout bug.
- Presses: all glyphs written at once, each with `animation-delay: n*85ms`.
  CSS does the stagger; no per-glyph timer.
- Text: word-by-word on a 16ms tick, `step` words per tick so the total lands
  near 850ms regardless of length. A blinking caret trails the last word and is
  dropped when the paragraph settles.

---

## 6. Text layout — the non-obvious part

The model's reasoning carries **~18 hard newlines**. Rendering with
`white-space: pre-wrap` made 946 characters demand 195px where 129px existed —
this looked like a box-sizing bug and is not one.

**Rule: a blank line is a paragraph; a lone newline is the model's own wrapping
and becomes a space.** Same words, ~5 flowing lines instead of 18. This is what
lets the box be small. Do not restore `pre-wrap`.

Auto-fit bounds are **fractions of frame height**, not pixels —
`MIN 0.0055 × H`, `MAX 0.019 × H` — so the design holds identically at 1000px
and at 4K. A binary search on font-size finds the largest that fits.

If even the minimum overflows, the view must **surface it** (the mock writes
`CLIPPED` to a readout and `data-clipped="true"`), never silently clip. Keep an
equivalent signal — a silent clip violates the locked "never truncate" rule and
would be invisible in review.

---

## 7. GBA control glyphs

Inline SVG, `currentColor`, `em`-sized, `viewBox="0 0 24 24"` (34 wide for
START/SELECT). Copy the paths verbatim from the mock.

- **D-pad**: the real cross outline at 45% opacity, plus a solid triangle in the
  pressed arm. Direction is readable at a glance — an early version at 1.8em was
  too small and had to be enlarged to **2.35em**.
- **A / B**: outlined circle, letter in mono.
- **START / SELECT**: capsule rotated `-16°`, label inside.
- **wait**: clock face.
- Unknown token: fall through to bold mono text (never crash on a new button).

---

## 8. Entering mid-run

Toggling on at turn 40 must not show an empty box. Seed from Spectate's existing
`turnBoxes` accumulator — it already holds the last turn's `output` and `action`
boxes. Render the most recent turn immediately, no animation, then let the phase
machine take over at the next event.

---

## 9. Verification

**The project has no JS test runner** (`package.json` has only dev/build/preview),
so browser behaviour cannot be unit-tested. Use the loop that caught three real
bugs during design:

1. The component publishes `data-phase`, `data-fit`, `data-clipped` on `<body>`.
2. Drive headless Chrome (`--headless --dump-dom --virtual-time-budget=…`) and
   assert those attributes rather than eyeballing screenshots.
3. Screenshot for visual checks only.

**Required checks before calling it done:**

- [ ] Worst-case turn (longest reasoning **and** most presses — turn 9 of run
      `2026-08-01_12-20-01_config-4.0__gemini-3-6-flash-high`) renders with
      `data-clipped="false"` at box heights 24 / 27 / 30%.
- [ ] All turns of one run audited for clipping, not a sample.
- [ ] Phase trace shows `thinking → presses → text → executing → thinking`, and
      the pending box is absent for the whole `executing` window.
- [ ] Ratchet: size is non-increasing across a run, and resets on run change.
- [ ] Bundle builds (`npm run build`) with no new warnings.
- [ ] Existing Python suite unchanged: **326 pass / 8 fail** (all 8 pre-existing
      — see `agent_brain/tasks/land-ai-plays-pokemon-config-4-0.md`).
- [ ] Emoji are gone: `grep -rn "actionEmoji\|⬅️\|🅰️" src/dashboard/web/src/`
      returns nothing.

Regenerate mock data if needed:

```bash
curl -s "http://localhost:3420/api/runs/<run_id>/trace" -o trace.json
# then map tasks[].turns[] -> {turn, action, reasoning, shot, cost}
```

---

## 10. Orchestrator breakdown

Four tracks. **A and B are independent and can run in parallel**; C depends on
both; D is a gate.

**A — Glyph component + emoji removal**
`Action.svelte`; port the SVG paths; convert the 4 `actionEmoji` call sites;
delete `actionEmoji`/`ACTION_EMOJI`. Verify TraceFeed and Report still render
actions, and the grep above is clean.

**B — SimpleView component**
`prose.js`, the stage/box/pending markup, paper CSS, auto-fit + ratchet, morph,
phase machine, streaming, exit affordance. Consumes `<Action>` from track A —
stub it locally if A hasn't landed.

**C — Wiring**
Toggle in Spectate's `.bar`; `localStorage` persistence; mid-run seeding from
`turnBoxes`; make sure the existing screen/event sockets are shared, not
reopened, when toggling.

**D — Verification**
The checklist in §9. Must run against a live run, not fixtures.

**Do not** promote the simple view to its own route, add cost/token/memory
readouts, restore the success pill, or add aspect presets. All were considered
and cut.

---

## 11. Known risk

The morph and the phase machine are timing-dependent and cannot be unit-tested
in this project. Budget a live-run pass with Andreas watching before declaring
it done — the same gate that
`agent_brain/tasks/land-ai-plays-pokemon-config-4-0.md` carries for config-4.0,
and for the same reason.
