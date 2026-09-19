# The paired comparison, fixed before it is run

> Source: conversation 2026-09-19 (Andreas + Marvin). Written at the START of the
> branch, per `plan.md` §8 question 3 — "a comparison designed after seeing its
> first result is not a comparison". **Revision 2, same day**: the first draft was
> put to an adversarial review before anything ran, and the review broke its
> primary statistic with a run from our own corpus. §3 is the replacement and §7
> records what was wrong, because a design doc that quietly absorbs its own
> refutation teaches nothing. At the time of writing **no SkyEmu run has been
> scored**.

## 1. The confound that decides the design

Decision C′ turns OCR off in v2. v1's scored config has it on
(`configs/config-5.1.yaml:61`). So a two-arm comparison would measure **the backend
and the text channel at once** and could not say which moved the number.

| Arm | Backend | OCR | What the arm is for |
|---|---|---|---|
| **A1** | mGBA | on | The board's actual configuration. |
| **A2** | mGBA | **off** | The control. Isolates OCR with the backend fixed. |
| **A3** | SkyEmu | off | v2 as decision C′ defines it. |

```
A1 → A2   the OCR effect      (backend held fixed)
A2 → A3   the backend effect  (text channel held fixed)
A1 → A3   the total drift     — the number plan §5 is about
```

**All three get an acceptance criterion in §4**, including A1 → A3. Revision 1 named
A1 → A3 as the headline and then only wrote a rule for A3-vs-A2.

## 2. Model and length

**Model: `gpt-6-astra(low)`** — the only model with two completed FireRed runs on
disk, so the arm's own run-to-run spread is measured rather than assumed; already
authorised for spend; and it finishes.

**Length: a 40-turn cap.** Revision 1 said 30. `viridian_reached` lands at 26, 28 and
29 across the three reference runs — one of them **one turn** under a 30-cap, so the
gate would be censored by a run that was slightly slow. 40 uncensors it. Cost below.

**Cost: $1.43 a run at 30 turns, ≈$1.9 at 40.** Revision 1 said $1.60, derived by
multiplying a whole-run average by 30. That is wrong in a knowable direction: per-turn
cost is a sawtooth on the 20-turn compaction period and the opening block is the
cheapest of the run — $0.374 for turns 1–10 against $0.561 for 11–20. The naive
extrapolation over-estimates by 12–15%. **Nine runs ≈ $17.**

## 3. The statistic — inputs, not turns

The reference runs, measured over `run_summary.turns[].action` (the model's requested
input list, present on every run in the corpus) and `turn_input_trace` (the executed
census, present on only 3 of 21):

| | astra-low 09-11 | astra-low 09-15 | astra-med 09-11 | gemini-3.8-flash(high) |
|---|---|---|---|---|
| turns to `route1_reached` | 16 | 16 | 15 | **13** |
| **inputs requested to that gate** | **188** | **185** | 170 | **291** |
| inputs/turn | 11.8 | 11.6 | 11.3 | 22.4 |
| inputs the census says moved nothing | *(no census)* | 18 of 180 (10.0%) | *(no census)* | **53 of 276 (19.2%)** |

**Primary statistic: emulator inputs requested to `route1_reached`.**

The replicate pair is **188 and 185** — sd 2.12, CV 1.1%, on a base of 186. That is the
same stability the turn count has (16 and 16) at **12× the resolution**.

**Why turns had to go.** Revision 1 made turns-to-gate the primary on the argument that
FireRed railroads the opening, so the gate mostly counts inputs spent. The corpus says
otherwise: inputs to that gate run 170 → 291 across models, batch size runs 11 → 22
inputs per turn, and there is no cap on sequence length (`src/agent/agent.py:57`) while
the prompt actively tells the model to lengthen sequences that pay off
(`config-5.1.yaml:136,153`). A turn is one decision whatever it contains, so **input-level
damage is absorbed inside a turn.**

The decisive case is `gemini-3-8-flash(high)`, 2026-09-15: **19.2% of its inputs moved
nothing**, and it latched the gate at **T13 — the fastest in the corpus, three turns
better than the astra baseline.** By turn 30 it had lost 159 of 572 inputs (27.8%) and
still reached `viridian_reached` three turns ahead of astra. On the turn statistic it
would have passed every alignment test comfortably.

So a backend that silently dropped one press in five would have scored **better than
baseline**. A design that cannot fail is not a design, and that one could be beaten by
the defect it exists to detect.

The input statistic inverts correctly. A press that does nothing has to be re-requested,
so a lossy backend drives requested inputs **up** — gemini's 291 against astra's 185 is
exactly that signal, correctly signed, while the turn count read it backwards.

**Companion statistic: `inputs_lost / inputs` over the capped run**, from the
`turn_input_trace` census. astra-low 09-15: 8.6%. gemini: 27.8%. The corpus brackets the
band — but thinly, from three runs, which is the weakest part of this document.

**`fetch_trace` is a precondition, not an option.** `turn.py:1897` reaches it through
`getattr` and `base.py` declares it off the required protocol, so an A3 run could pass
every criterion with the census **silently absent** — the most sensitive instrument
missing from the arm being judged. An arm run without a trace is void.

## 4. The decision rule

n = 3 per arm. A1's two runs on disk are **prior observations, not arm members** (cut
from a different commit), so A1 gets 3 fresh runs too: nine runs.

**The band is a rule fixed now, not a number fixed now.** Revision 1 pre-committed
±3 turns, justified as "the observed spread widened by one". The observed spread *within
the arm* was 0 — both astra-low runs read 16 — and the 15 that manufactured the width
came from a different reasoning effort. Worse, a simulation of that rule accepts a true
3-turn backend penalty 88% of the time. Pre-registering an unfounded number is not more
honest than pre-registering none; it is the same guess with a date on it.

So, decided now:

> **Band = 3 × the within-arm standard deviation of A2's three runs, with a floor of
> ±6 requested inputs (3 × the 2.12 sd of the on-disk replicate pair).** A2 is run
> first, its sd sets the band, and the band is written down before A3 is run.

This resolves the tension the review could not: you cannot know σ without running, and
you cannot set a band without σ. The control arm estimates it, and A3 is judged against
a band it could not have influenced.

**Aligned** if all three hold:

1. **A2 → A3**: |median inputs(A3) − median inputs(A2)| ≤ band.
2. **A2 → A3**: A3's `inputs_lost` rate is within 3 percentage points of A2's.
3. **A1 → A3**: reported with the same band, and stated as a number whether or not it
   passes. This is the drift plan §5 accepts; the criterion exists so "accept" does not
   silently become "do not measure".

Revision 1's "≥ 6 of 7 gates within 30 turns" clause is **dropped**. Against a baseline
of 16 turns it allowed +88% of slack and could not bite unless the primary had already
failed catastrophically.

**If not aligned, that is a result.** The number is published with the branch and the
arms stay separate. The one unacceptable outcome is adjusting this rule after seeing A3.

## 5. Held fixed, and what cannot be

**Pin the frame budget and log it per arm.** This is the confound the first draft missed
entirely, and it is larger than the start state. `pause_during_thinking` is declared in
five configs and **read by nothing**; `pause()`/`unpause()` have zero call sites; and
`lua/socketserver-1.lua:112`'s PAUSE handler body is `respond("OK:Paused")`. So mGBA runs
free at 60 fps through the model's latency, the settle poll, the screenshot and the
referee poll. Over the first 30 turns of the astra runs, LLM latency (139 s) plus settle
(122 s) is **at least 15,660 frames of game time nobody asked for** — a floor, since the
measured 27.8 s/turn wall clock accounts for only 8.7 s/turn. On SkyEmu that number is
exactly zero unless someone steps it. Both arms therefore log frames-per-turn explicitly,
and the difference is reported rather than absorbed.

Two more, both fixable and both to be fixed before the arms run:

- **`press_button_list` slack.** mGBA sleeps `total_frames/60 + 0.5 s`; SkyEmu's
  `step(total_frames)` is exact — about 30 extra frames a turn on the mGBA side.
- **Screenshot post-processing lives inside the mGBA backend** (`backends/mgba.py:629–663`:
  ×6 upscale, the 16 px grid on an 8 px offset). GBA frames are 240×160 on both backends,
  so this is portable — but only if it is hoisted into a shared layer. Reimplemented, the
  model sees a different image and the comparison is of two prompts, not two backends.

Irreducible, and stated rather than fixed:

- **Different start state.** A3 begins from a re-created savestate (plan §3.4). Two states
  that look identical on screen can differ in RNG state.
- **Different emulation.** Two cores are two games.
- **The settle criterion is renderer-dependent.** `_capture_raw_frame` compares 120×80
  greyscale at a 0.98–1.00 threshold; two cores differ at the pixel level. Report the
  settle-frame distribution per arm as a result, not an implementation detail.
- **A1 → A2 is not only the text channel.** The OCR poller captures every **0.2 s**
  (`ocr.capture_interval`, not the 0.4 s plan §3.3 claims) through the same lock as the
  turn loop, so switching it off also removes a competing consumer and changes real input
  timing. Measurable — log press wall-clock in both arms — not removable.

## 6. Operational note — the v1 arms are blocked on this machine (2026-09-19)

A1 and A2 need mGBA, whose harness binds TCP port **8888** (`src/cli/slots.py`). That port
is held by Andreas's own long-running `pokemon app --no-browser` control center, up for
2½ days. Starting a v1 run fails on `Address already in use` (verified), and stopping his
app unattended is not a thing to do.

So **A3 runs first**, and A1/A2 follow when the machine is free. The decision rule above was
fixed before any arm ran, which is the property that mattered. But note the consequence: §4
requires A2's sd to set the band, so **A3's numbers are not a verdict until A2 exists** —
they are a measurement waiting for its control.

## 7. What revision 1 got wrong

Kept here deliberately. Four of these were caught by an adversarial review of this document
before it spent anything, and the rest by checking its own numbers against the corpus.

1. **The primary statistic could not fail.** Turns-to-gate rewards a backend that eats
   inputs (§3). This is the one that mattered.
2. **"The opening is railroaded" was false.** Inputs to the gate span 170–291 and batch
   size spans 11–22 per turn; the gate counts model decisions, not inputs the game demands.
3. **±3 turns was manufactured.** The within-arm spread was 0; the width came from a
   different reasoning effort, and simulation puts its acceptance of a real 3-turn penalty
   at 88%.
4. **The 30-turn cap censored `viridian_reached`** by one turn on a real run.
5. **The secondary criterion was inert** — +88% slack against a ±19% primary band.
6. **The cost was over-estimated by 12–15%**, by extrapolating a whole-run average across
   the cheapest block of the run.
7. **The free-running-frames confound was missing entirely**, and it is bigger than the
   start state that §5 did name.
8. **Internal incoherence**: the on-disk runs were excluded as arm members and then used to
   set the threshold and the cap; and A1 → A3 was called the headline but given no rule.

One correction in the other direction. The review reported the replicate pair as 188 and
185 executed inputs; checked against the corpus, **188 is a requested-input count and the
09-11 run has no execution census at all** — only 3 of 21 runs carry `turn_input_trace`.
The pair is 188/185 on one instrument (requested), which is what §3 now says, and the
execution census for 09-15 reads 180 against its own 185 requested. The sd survives; the
instrument had to be named.
