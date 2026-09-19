# The paired comparison, fixed before it is run

> Source: conversation 2026-09-19 (Andreas + Marvin). Written at the START of the
> branch, deliberately: `plan.md` §8 question 3 says this wants fixing **before**
> P0, "because a comparison designed after seeing its first result is not a
> comparison". At the time of writing **no SkyEmu run has been scored at all** —
> the only v2 plays that exist are the 10-turn NDS experiments in
> `v2-experiments/plays/`, which have no referee, no checkpoints and no score.
> Everything below is therefore a prediction, not a rationalisation.

## 1. The confound that decides the design

Decision C′ turns OCR off in v2. v1's scored config has it on
(`configs/config-5.1.yaml:61`). So a naive two-arm comparison — "v1 as it is
today" against "v2 as it will be" — measures **the backend and the text channel
at once**, and cannot say which moved the number.

That is not a reason to re-port OCR. It is a reason to run a third arm:

| Arm | Backend | OCR | What the arm is for |
|---|---|---|---|
| **A1** | mGBA | on | The board's actual configuration. The thing v2 is compared against. |
| **A2** | mGBA | **off** | The control. Isolates the OCR channel with the backend held fixed. |
| **A3** | SkyEmu | off | v2 as decision C′ defines it. |

Then the decomposition is arithmetic rather than argued:

```
A1 → A2   the OCR effect      (backend held fixed)
A2 → A3   the backend effect  (text channel held fixed)
A1 → A3   the total drift     — the number §5 of the plan is about
```

Without A2, "accept the drift" would mean accepting a number nobody can
attribute. Decision C says do not *tune* v2 toward v1; it explicitly does not say
do not *know*.

## 2. Model and length — chosen for the reason, not the vibe

**Model: `gpt-6-astra-low`.** Three reasons, in order:

1. It is the only model with **two completed FireRed runs already on disk**, four
   days apart, so the v1 arm's own run-to-run spread is measured rather than
   assumed — and a backend gap only means something measured against it.
2. Andreas has already authorised spending on astra runs at this length.
3. It finishes. Both runs reached Brock (138 and 145 turns), so the early ladder
   is not a model failing to play.

**Length: a 30-turn cap.** From the two existing runs, turn 30 sits just past
`viridian_reached` — seven of the twelve gates, through the bedroom, the lab, the
starter, a real battle, an overworld route and a town. It exercises navigation,
battle and dialogue, which is everything a backend could plausibly break, and it
costs about **$1.60 a run** (v1 run 2: $7.30 for 138 turns).

Nine runs at that length is roughly **$15 for the whole comparison**. The full
ladder to Brock would be $70 and would add its variance, not its signal.

## 3. The two statistics, and why the primary one is the boring gate

The v1 arm's gate turns, read off the two runs on disk plus `astra-medium` as a
third reference point:

| Gate | astra-low (09-11) | astra-low (09-15) | astra-medium (09-11) |
|---|---|---|---|
| left_bedroom | 3 | 2 | 2 |
| left_house | 5 | 4 | 4 |
| oaks_lab_entered | 7 | 6 | 7 |
| starter_chosen | 10 | 10 | 9 |
| rival1_done | 14 | 15 | 13 |
| **route1_reached** | **16** | **16** | **15** |
| viridian_reached | 26 | 29 | 28 |
| pokedex_received | 52 | 47 | 50 |
| viridian_forest_reached | 84 | 69 | 82 |
| pewter_reached | 124 | 124 | 135 |
| brock_defeated | ~145 | 138 | 150 |

**Primary statistic: turns to `route1_reached`.** 16, 16, 15 across three runs,
including a run at a different reasoning effort. That stability is not the model
being consistent — it is FireRed railroading the opening: the game demands a
near-fixed number of inputs to get out of the house and past Oak, so the gate
mostly measures *how many inputs the harness had to spend*, which is exactly the
quantity a backend swap can move. A gate that is nearly deterministic under the
model is the most sensitive instrument available for the thing being tested.

**Secondary statistic: gates latched within 30 turns.** v1 reaches seven in both
runs. This one is coarse but it is the one that answers "does the referee work at
all on the new backend", which is P5's actual question.

Both statistics are reported per run. No averaging across arms before the per-run
numbers are shown.

## 4. The decision rule, written down now

n = 3 per arm (A1 gets one fresh run; its two runs on disk are cited as prior
observations, not as arm members, because they were cut from a different commit —
*a measurement must see one code version*).

The arms are **aligned** if both hold:

- The A3 median turns-to-`route1_reached` is within **±3 turns** of A2's median.
  (±3 is the observed v1 spread of 15–16 widened by one turn in each direction.
  It is set here so it cannot be set later.)
- A3 latches **≥ 6 of the 7 early gates within 30 turns in at least 2 of 3 runs**.

If aligned: the backends are close enough that merging the arms becomes a
decision with evidence behind it, per plan §5.

If not aligned: **that is a result, not a failure.** The number gets published
with the branch and the arms stay separate. The one outcome that is not
acceptable is adjusting this rule after seeing A3.

**A1 → A2 is reported whatever it says.** If turning OCR off moves the primary
statistic more than the backend does, the headline of this whole branch changes,
and that possibility is named here before it can be quietly dropped.

## 5. What this does not cover, stated up front

- **30 turns is not 30 hours.** Nothing here speaks to GBA accuracy over a long
  run, which is risk #1 in plan §6. That needs a full ladder run and it is not
  this experiment.
- **One model.** A backend difference that only bites a model with different
  timing habits would not show up.
- **One game.** FireRed only. NDS carries no referee yet.
- **Different start states.** A3 begins from a re-created SkyEmu savestate
  (plan §3.4), not a copy of v1's. Two states that look identical on screen can
  differ in RNG state, and no part of this design can rule that out.

## 6. Operational note — the v1 arms are blocked on this machine (2026-09-19)

A1 and A2 both need mGBA, and mGBA's harness binds TCP port **8888** (slot 1,
`src/cli/slots.py`). That port is held by Andreas's own long-running
`pokemon app --no-browser` control center — up for 2½ days at the time of
writing. Starting a v1 run would either fail on `Address already in use` (it
did: `emulator.py:101`, verified) or require stopping his app, which is not a
thing to do unattended.

So **A3 comes first**, and A1/A2 are run when the machine is free. This does not
weaken the design — the decision rule in §4 was fixed before any arm ran, which
is the property that mattered — but it does mean the first SkyEmu numbers will
land with nothing to compare them against yet, and they must not be reported as
a comparison until A2 exists.
