# A3 — the first SkyEmu arm

> Source: 2026-09-19. Three runs of `gpt-6-astra(low)` on `config-v2-firered.yaml`
> (SkyEmu, `ocr.enabled: false`), 40-turn cap, `--stop-at viridian_reached`, from
> `configs/saves/skyemu/firered-pokebench-v2`. Run dirs
> `local/runs/2026-09-19_09-{36-10,40-14,44-02}_config-v2-firered__gpt-6-astra-low`.
> Analysis rules were fixed in `paired-comparison.md` before these ran.

## 1. It works end to end

A model played the real v1.1 FireRed ladder on SkyEmu through the unmodified
benchmark harness: start state, backend, turn loop, referee, walkgraph, input
census, savepoints. Six gates latched per run. **$1.92–$1.94 a run**, close to the
$1.9 the revised spec predicted for 40 turns.

Speed: ~5.6 s/turn against v1's measured 27.8 s/turn.

## 2. The numbers

| gate | v1 09-11 | v1 09-15 | v2 run1 | v2 run2 | v2 run3 | Δ median |
|---|---|---|---|---|---|---|
| left_bedroom | 3 | 2 | 2 | 2 | 2 | 0 |
| left_house | 5 | 4 | 4 | 4 | 4 | 0 |
| oaks_lab_entered | 7 | 6 | 7 | 7 | 7 | 0 |
| starter_chosen | 10 | 10 | 11 | 11 | 11 | +1 |
| rival1_done | 14 | 15 | 22 | 22 | 22 | **+8** |
| route1_reached | 16 | 16 | 29 | 28 | 29 | **+13** |
| viridian_reached | 26 | 29 | — | — | — | not reached in 40 |

**Primary statistic (requested inputs to `route1_reached`):**

| | v1 | v2 |
|---|---|---|
| values | 188, 185 | 238, 230, 252 |
| median | 186.5 | **238** |
| sd | 2.12 | **11.1** |
| | | **+28%** |

**Input loss** (executed census): v2 11.2% / 9.6% / 10.1%, v1 8.6%. Comparable —
**this is not a backend eating presses.** That mattered more than anything else here,
because the statistic was chosen precisely to catch a lossy backend, and it says no.

## 3. Two things worth noticing about the shape

**v2 is more reproducible than v1.** The three v2 runs are *identical* on five of six
gates — 2, 4, 7, 11, 22 — and differ by one turn on the sixth. v1's pair differs on
four of six. n is small on both sides, but it is the direction plan §1 predicted from
the emulator being frozen between steps.

**The divergence has a location.** Gates 1–3 match v1 exactly. Everything appears at
and after the rival battle: +8 turns to get through it, +5 more on the walk from the
lab to Route 1. Whatever the cause is, it is not present in the opening's walking and
dialogue and is present in everything after.

The measured correlate is **batch size**: v2 spends 8.2 / 8.2 / 8.8 inputs per turn
over the first 28 turns against v1's 11.8 and 10.8. Same work, smaller steps, more
turns.

## 4. What this does NOT establish

**This is A1 → A3: the total drift.** It confounds the backend with the OCR channel,
the re-created start state, and v1's free-running frames. It is not the backend
effect. `paired-comparison.md` §4 requires A2 (mGBA, OCR off) to separate them, and
A2 has not run — it needs port 8888, held by the control center (§6).

So the honest statement is: **the total drift on the primary statistic is +28%, and
nothing here attributes it.**

### A mechanism that was proposed and does not survive

v1's emulator runs free at 60 fps while the model thinks (plan §4.1), so a natural
guess is that v1 gets battle animations completed for free and v2 has to spend `wait`
inputs on them. **Measured, and it does not hold.** `wait` share in the battle window
is 12.7% for v2 against 5.7–11.2% for v1 — overlapping, not a gap. Over the first 30
turns: 6.1% vs 4.3–5.9%. Too small to explain a doubling.

### A second mechanism, and why its evidence was thrown out

The prompt tells the model to lengthen sequences when a prediction holds and shorten
them when it fails (`config-5.1.yaml:136,153`), so shorter batches should mean more
failed predictions — which a vision-only model would have.

A keyword count over the models' own reasoning gave v1 21–25% and v2 35–41%, which
appears to support it. **That number is discarded.** The classifier matched v2's
phrasing ("The prediction came true", "The prediction failed") and left **24 of 28**
v1 turns unclassified against 1–3 of v2's, so v1's rate was computed on n≈4. v1 says
the same thing in prose the matcher misses — "We did not exit", "Oak did not appear",
"The ball was Charmander, not Bulbasaur, so my ordering assumption was wrong". Hand
reading v1's first ten turns gives roughly 44–56% failures, i.e. **higher** than v2,
which would reverse the claim.

An instrument that fits one arm better than the other is not a measurement of the
arms. Recorded here rather than deleted, because the tempting version of this document
reports 21% vs 36% and calls the mechanism confirmed.

**So: the batch-size difference is real and measured; its cause is unattributed.**

## 5. What should happen next

1. **A2.** Everything in §4 waits on it. It needs the machine's mGBA port.
2. **Raise the cap past 40.** The spec raised it from 30 to uncensor `viridian_reached`;
   on this arm 40 does not reach it either. 55 would, on these three runs' slope.
3. **Do not tune v2 toward v1 on this evidence.** Decision C stands: the gap is a thing
   to measure, and one arm of a three-arm design is not a reason to change anything.

---

## 6. A2 landed, and it reverses §4 (2026-09-19, later the same day)

Andreas stopped the control center, so A2 (mGBA, `ocr.enabled: false`,
`configs/config-a2-mgba-noocr.yaml` — config-5.1 with one key changed) could run.
**n=1**, so this is a direction, not a measurement.

| arm | inputs to `route1_reached` | turn | battle span | inputs/turn | `blocked_by_actor` |
|---|---|---|---|---|---|
| A1 mGBA + OCR | 186 | 16 | 5–6 | 10.8–11.8 | 9 |
| **A2 mGBA − OCR** | **185** | 19 | 7 | 10.0 | 3 |
| A3 SkyEmu − OCR | 240 | 28–29 | 11 | 8.2–8.8 | 16 |

**A1 → A2 = −1 input. The OCR channel costs essentially nothing on the primary
statistic. A2 → A3 = +55 inputs, +30%, and that is the backend.**

### This is why the statistic was changed

On **turns**, the story is "OCR costs 3, the backend costs 9" — the effect looks
shared. On **inputs**, OCR costs nothing at all: without the text log the model
spreads the *same* presses over more turns, which is a change in how it batches, not
in how much work it does. Only the input statistic separates "thinks in smaller
chunks" from "has to press more buttons". The revision that `paired-comparison.md` §3
made on an adversarial reading, before any of this ran, is what made the attribution
possible.

### The mechanism, and it is the one Andreas named

`blocked_by_actor` — a directional press that moved nothing and was not a wall — is
**16 on SkyEmu against 3 on mGBA**, on comparable step counts (99 vs 107). Presses are
landing while the game is in a state that ignores them.

That has a specific, pre-identified cause. mGBA's `press_button_list` is
fire-and-forget: it sends the sequence and sleeps `total_frames/60 + **0.5 s**`. The
SkyEmu backend steps `total_frames` exactly. **That half-second — about 30 extra
frames after every sequence — was doing work nobody had named.** P1 hit it
independently while building: a single `up` on the stairs tile does *not* complete the
warp in 36 exact frames, and mGBA had been handing the game ~30 more.

So v2 is not slower at playing; **it is pressing buttons into moments the game is not
listening**, and then having to press them again. Andreas raised exactly this before
A2 ran: *"teh duration of button presses, too cirectly move aroudn and turn."*

### What this makes next

1. **Calibrate input timing empirically** — `cross-game-plan.md` §3 already specifies
   the sweep (smallest hold where x changes = the move threshold). It was written for
   porting to new games; it turns out FireRed on SkyEmu needs it too.
2. **Then re-run A3.** The +30% is a hypothesis about timing until a calibrated arm
   either closes the gap or does not.
3. **Do not conclude the backend is unfaithful.** Nothing here says SkyEmu emulates
   wrongly. It says the harness's input timing was tuned — accidentally — against
   mGBA's sleep, and the exact clock removed the padding.
4. **n=1.** A2 needs its other two runs before any of this is a number.
