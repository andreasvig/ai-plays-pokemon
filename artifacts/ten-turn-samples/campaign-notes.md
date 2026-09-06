# Ten-turn real-gameplay samples — live campaign notes

Started 2026-09-06 10:50 by Codex; main queue ended 12:02; Gemma reruns ended 12:14; continued by Claude Code from ~11:15 after Codex ran out of usage.
Campaign runner: `test_scripts/run_ten_turn_samples.py` (parent PID 92456, one real mGBA emulator, models run sequentially).
Design: same canonical FireRed bedroom save (`configs/saves/pokebench-v1`, sha256 7512323c…), compaction after turn 5,
agent-process + emulator restart after turn 7 (resume from savepoint), 10 turns per profile, referee observing (not enforcing),
max spend $5 per run. Raw manifest: `local/ten-turn-samples/manifest.json`. Run dirs under `local/runs/`.

Rollup script (run after the campaign): `test_scripts/summarize_ten_turn_samples.py local/ten-turn-samples/manifest.json`
→ `artifacts/ten-turn-samples/results.{md,json}`.

## Results so far (updated as runs finish)

| Profile | Turns | Furthest checkpoint (turn) | Cost | Notes |
|---|---:|---|---:|---|
| gemma-guidance (rerun, fixed parser) | 10/10 | none | $0.0058 | Fix verified: turn-6 post-handover request, the exact spot that crashed before, parsed cleanly. Zero request errors. Gameplay: 10 turns of 1–4 button nudges around the staircase without ever triggering the warp. Very cheap and fast (3–15 s, one 52 s turn). |
| gemma-guidance (attempt 1) | 5/10 | none | $0.0042 | Crashed at turn 6: post-handover output was valid action JSON without the `result` wrapper; harness rejected it 3×. Pre-fix. **Rerun pending.** |
| gemma-replay (rerun, fixed parser) | 10/10 | none | $0.0044 | Zero request errors; handover at t5 took 28 s this time (vs 4+ min in attempt 1). Reasoning replay verified intact every turn (2→10 blocks growing to compaction, reset after). Gameplay: pressed `up` into a wall for turns 2–5 while claiming to be below the stairs, then tried `down`. Never left the bedroom in 10 turns. After the t7 restart, replay stayed intact (4/6/8 blocks at t8–t10). |
| gemma-replay (attempt 1) | 5/10 | none | $0.0029 | Handover request took ~4+ min, then same missing-wrapper error. Process had loaded the old parser; Codex sent SIGINT to let the queue move on. **Rerun pending.** |
| google/gemini-3.8-flash | 10/10 | Chose a starter (t9) | $0.1266 | Left bedroom t1, outside t3, Oak's Lab t6, starter t9. Zero request errors. Compaction t5 and restart t7 both clean. |
| anthropic/claude-opus-5 | 10/10 | Stepped outside in Pallet Town | $0.4992 | 5 turns in the bedroom; left bedroom only at t6. Compaction needed 3 attempts: attempt 1 returned truncated JSON (`Expecting ',' delimiter` at char 4086, cost $0.08 wasted), attempt 2 hit an SSL `bad record mac` transport error, attempt 3 succeeded. Restart at t7 clean. |
| anthropic/claude-fable-5.1 | 10/10 | Entered Oak's Lab | $0.6219 | Left bedroom t3, outside t5, Oak's Lab after restart. Zero request errors; compaction t5 and restart t7 clean. Most expensive run so far. |
| qwen/qwen3.8-flash | 10/10 | Entered Oak's Lab (t9) | $0.0466 | Left bedroom t2, outside t5. Compaction fired after turns 2, 3, 4 and 6 via `context_threshold` (see finding 4), so the every-5 schedule never applied. Two compaction attempts returned unwrapped JSON (finding 5); retries recovered. Gameplay latency 55–80 s per turn, 3.4k–6.7k reasoning tokens per turn. |
| z-ai/glm-5.3-flash | 10/10 | Left the bedroom (t10) | $0.0070 | Left the bedroom only at t10, after the restart. Still in the bedroom after 7 turns, circling the staircase (up/left/right nudges each turn). Turn-1 attempt 1 came back empty after 181 s with OpenRouter `native_finish_reason=network_error`; retry succeeded. Otherwise fast (11–45 s) and very cheap; 80–1100 reasoning tokens per turn. Compaction at t5 by schedule, clean. |
| deepseek/deepseek-v4-flash-vision-exp | 10/10 | Left the bedroom (t5) | $0.0151 | Went south first (wrong), found the stairs by t5. Then spent t6–t7 trying to talk to Mom instead of leaving. Zero request errors; compaction t5 by schedule. Fast: 6–36 s per turn. |
| x-ai/grok-4.6 | 10/10 | Entered Oak's Lab (t10) | $0.3996 | Systematic wall-sweeps: left bedroom t4, outside t7. Zero request errors; compaction t5 by schedule. Slow and expensive: 14–138 s per turn, 2k–7k reasoning tokens; strong cache hits (up to 22k cached tokens at t5). Second-priciest after the Claudes. |
| moonshotai/kimi-k3 | 10/10 | Stepped outside in Pallet Town (t6) | $0.1644 | Left bedroom t3, outside t6, then walked north toward Route 1 to trigger Oak's cutscene (the real speedrun route). At t8 it accidentally re-entered the house, then spent t9–t10 blocked by a tree at the town's north edge. Zero request errors; compaction t5 by schedule. Fast (10–30 s) with very little reasoning (0–330 tokens). Mid-priced. |
| meta/muse-spark-1.3 | excluded | | | OpenRouter account requires age attestation. |

## Final rollup

All 10 accessible profiles completed 10 real turns with a clean t5 compaction and a clean t7 process + emulator restart
(Qwen's compactions fired on the context-threshold path instead, see finding 4). Combined table with trace links:
`results.md` in this folder; per-attempt detail incl. the two failed Gemma attempts: `results.json`. Total reported spend
across every retained attempt: $1.90.

Progress tiers after 10 turns (referee checkpoints, not self-report):
- Chose a starter: gemini-3.8-flash
- Entered Oak's Lab: claude-fable-5.1, qwen3.8-flash, grok-4.6
- Outside in Pallet Town: claude-opus-5, kimi-k3
- Left the bedroom only: glm-5.3-flash, deepseek-v4-flash-vision-exp
- Never left the bedroom: gemma-4-31b-it (both profiles)

Guidance vs replay for Gemma: identical outcome (stuck in the bedroom), near-identical cost; replay's only visible
difference is the 69% vs 17% cache-read fraction and a longer handover. Ten turns cannot separate them on quality.

Gemini reported 0% cached input on Google AI Studio across all requests. Pricing confirms it: every request was billed at
exactly $0.75/M prompt tokens (the endpoint's second pricing tier; the base tier is $0.375/M, cache reads $0.075/M at the
tier that applied). Zero discount on any request means zero billed cache hits, not merely unreported ones. Method, usable
for any provider whose usage omits cache counts: `implied_cached = (n × P_full − billed_prompt_cost) / (P_full − P_cache)`,
after identifying the tier from `billed / n` on the first request. **Now built in**: the append agent snapshots the
endpoint list prices once per run (`conversation/endpoint-pricing.json`), the trace builder attaches
`implied_cache` to every usage event, Technical details show "Billed at" and "Discount implied by billing", and
`results.md` has an implied column. Validated on all 116 requests of this campaign: the implied figure matches the
provider-reported one on every request for all ten profiles (Anthropic needed the 1.25× cache-write premium removed per
tier before choosing the tier). The campaign run dirs were backfilled from the same-day catalog snapshot. Why implicit caching never hit (stable prefix, 2.4k–14.6k
tokens, requests seconds apart) is unexplained; worth a direct probe with and without the encrypted thought blocks.

## Harness findings (kept separate from model quality)

1. **Gemma unwrapped JSON after handover.** Fixed mid-campaign by Codex: `allow_unwrapped_json` profile flag
   (`configs/provider-profiles.yaml`, Gemma variants only) and parser fallback in `src/agent/append_agent.py`. Test added in
   `tests/test_provider_profiles.py` (invalid button names still rejected). 36 tests pass. Both Gemma rows are being rerun in
   `local/ten-turn-samples-gemma-rerun/` (launched 12:03) with `--only gemma-guidance --only gemma-replay` once the main campaign releases the emulator.
2. **Opus compaction malformed JSON (not truncation).** Attempt 1 finished with `finish_reason=stop` at 2021 completion tokens
   (cap 12288), but the JSON body was one closing brace short (`…]}}` vs the valid attempt's `…]}}}`). A model formatting slip,
   retried successfully by the existing retry loop. Cost of the wasted attempt: $0.081.
3. **Transient SSL error** on Opus attempt 2 — transport-level, retried successfully.
4. **FIXED** — **Context-threshold estimate conflates bytes with tokens** (`src/agent/append_agent.py` ~line 338). `growth` is the JSON
   byte length of the last two messages plus the OCR text. Those bytes include the base64 screenshot (~22 KB) and the
   assistant's replayed reasoning stored twice (`reasoning` + `reasoning_details`, ~18.5 KB each for Qwen). For Qwen the
   turn-3 estimate was ≈ 10.2k (last input) + 38k (reasoning bytes) + 22k (image bytes) + 4.1k (reserve) + 12.3k (max
   output) ≈ 88k, over the 85.2k threshold (131072 × 0.65), so compaction fired every turn. Gemini's same estimate was ≈ 52k.
   Consequence: any verbose-reasoning model compacts nearly every turn, reasoning replay across turns is never exercised for
   it, and cost/latency inflate. The real prompt was 10k tokens, far from any limit. Fixed after the campaign: `approx_tokens()` estimates in tokens (bytes ÷ 4 for text, `image_token_reserve` per image,
   reasoning counted once). Replayed against Qwen's real turn 3: old ≈ 88k (trip), new ≈ 36k (no trip); ~60k tokens of headroom.
5. **FIXED** — **Qwen also drops the `result` wrapper** on compaction (2 of 4 first attempts). The Gemma fix is profile-gated
   (`allow_unwrapped_json` only on the Gemma profiles), so Qwen paid a retry each time. Fixed: `allow_unwrapped_json` now defaults to true for
   every profile; schema validation of the action/handover remains the guard (test covers Gemma and Qwen, valid and invalid).
6. **FIXED** — **Provider network error surfaced as a format error.** GLM turn 1 attempt 1: OpenRouter returned an empty choice with
   `native_finish_reason: network_error` and no usage after 181 s. The harness logged it as `Expected exactly one gameplay
   call`, which reads as a model-output defect. Fixed: `native_finish_reason` in {network_error, error} (or finish_reason error) now raises
   `ProviderTransportError("Provider returned no completion (…)")` before any output parsing; it is retried like other transient errors.

## Caveats
Full suite after the fixes: 649 passed, 8 failed; the 8 are in model-registry, taskmaster and OCR tests that predate this
branch's work and do not import the append agent or provider profiles. The two touched suites pass 40/40, and each new
test was mutation-checked (reverting its fix makes it fail).

Ten turns cannot rank model quality. Checkpoint progress is from the independent referee (map reads), not model self-report.
Costs include OCR cleanup and failed attempts.

## Caching and reasoning-continuity overview (per profile)

Source: every `llm_request_usage` event's `continuity` block and OpenRouter usage. "Replay intact" means the harness
verified, before sending, that every archived reasoning block from the current segment was present and unchanged in the
outbound history; no provider reported back whether it *used* them (`provider_feedback: not_reported` for all ten).
Cache figures are the provider-reported cached-input share summed over all requests of the run.

| Profile | Cache mode | Cached input | Cache pattern | Reasoning format returned | Replay | Notes |
|---|---|---:|---|---|---|---|
| gemini-3.8-flash | implicit (AI Studio) | 0% | Never reported | text + **encrypted (signed)** | intact, 1 opaque block/turn | Google implicit caching is not surfaced in usage, so 0% is "unknown", not "none". |
| claude-opus-5 | automatic | 75% | 63% → 83% within a segment; 0% on the first request of a new segment; compaction retry hit 100% | text | intact | Textbook prefix cache: grows every turn, resets at segment start. |
| claude-fable-5.1 | automatic | 72% | 63% → 85%, same shape as Opus | text; **empty on 2 of 11 requests** (0 reasoning tokens) | intact | Mandatory-thinking model returned no thinking on t6 compaction and t8; replay count dropped accordingly (4/4 not 6/6) and stayed consistent. |
| qwen3.8-flash | system_breakpoint | 11% | Flat 1,060 tokens cached on every request (the explicit system prefix only) | text | intact | Alibaba honoured the explicit breakpoint but never cached the growing conversation. 3–7k reasoning tokens/turn, 55–80 s latency. |
| glm-5.3-flash | implicit | 67% | 61% → 83%; dipped to 22% on the 2nd request of segment 2, then recovered | text | intact | One provider-side `network_error` (t1, 181 s, empty completion). |
| deepseek-v4-flash-vision | implicit | **89%** | 90% → 96% from turn 2, in 64-token blocks | text | intact | Best cache behaviour of the set. |
| grok-4.6 | implicit | 46% | 35% → 79% in segment 1, then **0.4% on the 32k-token compaction request** and ~1% on t7–t8, recovering to 78% by t10 | **encrypted + summary** | intact, 1 opaque block/turn | xAI cache misses are unpredictable at larger prompts; the most expensive misses of the campaign. |
| kimi-k3 | implicit | 73% | 61% → 83%; 31% on the first request of segment 2 (system prefix still hot) | text; empty on t5 | intact | Very little reasoning (0–330 tokens) yet the best progress per dollar among mid-priced models. |
| gemma-4-31b guidance | implicit (DeepInfra) | 17% | 0% on 9 of 11 requests; 74–88% on t4–t5 only | text | **0 replayed by design**, 2→8 archived locally | Prior raw thoughts removed between turns (Google's guidance). |
| gemma-4-31b replay | implicit (DeepInfra) | 69% | 82–88% from turn 3 onward, both segments | text | intact, 2→10 blocks | Same provider, same sizes, far better cache hits than the guidance arm. n=1 each; do not attribute yet. |

**Was the replayed reasoning actually consumed?** The usage data can answer what `provider_feedback` cannot. Within a
segment, prompt growth from one gameplay request to the next should equal a stable per-turn cost (observation + image +
assistant content) *plus the previous turn's reasoning tokens* if the replayed reasoning is tokenized by the model.

| Profile | corr(prompt growth, prior reasoning) | residual after subtracting reasoning | Verdict |
|---|---:|---|---|
| gemini-3.8-flash | 1.00 | 1,242–1,330 (flat) | replayed reasoning billed → reached the model |
| claude-opus-5 | 0.99 | 1,973–2,079 | billed |
| claude-fable-5.1 | 0.98 | 1,977–2,110 | billed |
| qwen3.8-flash | 1.00 | 1,523–1,570 | billed (5.7k reasoning tokens at t1 show up 1:1 at t2) |
| glm-5.3-flash | 1.00 | 1,961–2,062 | billed |
| deepseek-v4-flash-vision | 1.00 | 508–591 | billed |
| grok-4.6 | 1.00 | 1,513–1,581 | billed (encrypted blocks) |
| kimi-k3 | 0.17 | growth flat at 2,025–2,169 while reasoning varied 85–326 | **not billed → dropped upstream** (moderate confidence; small reasoning) |
| gemma replay | 0.52 | growth flat at 348–390, identical to the guidance arm, while 81–334 reasoning tokens were sent | **not billed → dropped upstream** (high confidence) |
| gemma guidance | n/a | growth flat at 352–390 | nothing replayed by design |

Gemma's per-turn growth is ~365 tokens in both arms: a 256-token image plus ~110 tokens of text. At turn 10 the replay arm
sent 3,330 characters of prior reasoning (~800 tokens) and was billed 3,453 prompt tokens against guidance's 3,383. Either
OpenRouter strips `reasoning`/`reasoning_details` from assistant history before forwarding to DeepInfra, or DeepInfra's chat
template does not render them. Which of the two cannot be told from this data. Consequence: for Gemma on DeepInfra the
guidance/replay comparison is a comparison of identical prompts, and any difference between the arms (cache 69% vs 17%,
speed) is provider variance, not policy. The profile's `reasoning_use: unverified` for gemma-replay should read
`not_consumed_by_endpoint`. Same caveat, weaker, for Kimi on Moonshot.

What held everywhere: local replay was `intact` and the history prefix `unchanged` on all 118 successful requests, including
after every turn-7 restart (turn 8 replayed exactly the blocks archived before the restart). Session ids were stable per run.
The turn-5 handover reset replay to 0 and the archive kept growing, as designed.

What did not: no provider confirmed use of replayed reasoning (`reasoning_use: unverified` for all but Gemma guidance, which
is `not_preserved_between_user_turns` by declaration). Signed/encrypted formats (Gemini, Grok) are the only ones where
replay is at least *verifiable* by the provider; text replay (everyone else) is a plain prompt.

One replay-cost observation: for text-reasoning providers the gateway returns the same thoughts under both `reasoning` and
`reasoning_details`, and the harness archives and replays both. That doubled the byte count that tripped finding 4, and
doubles archive size. Whether the provider ignores one of the two is unknown. Not changed here; worth a probe.

## The two Gemma runs, explained

Both used google/gemma-4-31b-it on DeepInfra, temperature 0.3, prompted JSON output, same save, 10 turns each. The only
configured difference: guidance strips earlier raw thoughts from the history before each request; replay sends them back
unchanged until the handover.

**What the game asked of them.** The bedroom staircase in FireRed is entered from the *right*, by walking onto the stair
tile from the orange mat beside it. Every model that got out did that: Gemini's first action was right ×4, up ×4, left ×2
and it warped on turn 1; Kimi did the same in three turns. Approaching from below (the row with the stair feet) or from the
left (the white banister) is blocked.

**Guidance run.** Turns 1–3 walked up and right in 2–4 button nudges and ended just *left* of the banister. Turns 4–7
alternated up/left/right one tile at a time, repeatedly reporting `last_turn_succeeded: true` while not having moved. At
turn 8 it correctly named the railing as the blocker, then planned a route through invented coordinates ("(6,3), (6,4),
(6,5)… stairs at (7,3)") that the prompt explicitly tells it not to use, and never crossed to the mat side. Its turn-5
handover said "positioned directly below the stairs; next step move up", a confident wrong belief carried into segment 2.

**Replay run.** Turn 1 went up and right to the tile directly *below* the stair feet. Turns 2–5 pressed `up` into that
wall four times in a row, each turn seeing the same screen. With its own earlier reasoning in context it did not change
hypothesis; it escalated to "the screen has not updated" and "the game may be frozen", pressed Start on turn 8 to test
responsiveness, and finished with a 5× `up` into the same wall. Its handover was more honest than guidance's ("remained
stationary despite inputs; try right then up") but framed the cause as an invisible obstacle or failing inputs, not a wrong
entry side.

**What separates them, and what does not.**
- Outcome: identical (bedroom, no checkpoint). Cost: $0.006 vs $0.004. Speed: both 3–30 s per turn with occasional 50–86 s spikes.
- Behavioural texture (n=1 each, so a hypothesis, not a finding): guidance *varied* its approach every turn (up, left, right, down) without ever reasoning about why moves fail; replay *anchored* on one approach for four turns, then jumped to a system-level explanation. That is the pattern one would predict from "sees its own previous reasoning" vs "does not", but two ten-turn runs cannot confirm it.
- Neither run populated `map_notes` in the handover memory, and both handovers were 1–3 sentences. Gemma's handover quality is thin regardless of profile.
- Cache: replay 69% vs guidance 17% on DeepInfra. Same provider, near-identical request sizes. If it replicates, replay is *cheaper* in practice for Gemma despite sending more tokens, because guidance's history mutation may be defeating prefix caching. Needs 3+ runs per arm to say.
- Harness: the first attempt of each arm crashed on the same missing `result` envelope after the handover (fixed, now default-on for all providers). The first replay attempt also had a 4-minute handover request; the rerun's took 28 s, so that was provider latency, not the profile.

**Price and speed (answering "how can they cost the same if replay sends more tokens?").** They cost the same because the
extra tokens were never billed (table above): both arms were charged for ~29.2k prompt tokens over 11 requests. DeepInfra
does discount cache hits ($0.09/M uncached vs ~$0.055/M effective on requests with 70–88% cached), so the replay arm's
higher hit rate made its prompt side slightly cheaper; the rest of the $0.0058 vs $0.0044 gap is output tokens (6,469 vs
5,379). Speed: guidance took 256 s of model time for 11 requests (median ~11 s, spikes of 52, 38 and 86 s at 10–18 tok/s),
replay 145 s (median ~7 s, one 30 s spike). Latency tracked output length and DeepInfra throughput variance (10–63 tok/s
across requests), not prompt size; with identical prompts reaching the model, the speed gap is provider noise at n=1.

**Bottom line for Gemma 31B.** The model is not failing on format or continuity; it is failing on spatial reasoning in a way
neither replay policy changes. A longer benchmark can still separate the two profiles on cost and cache, but on progress
they will need a map-reading aid (or a smaller step budget per turn) before the comparison says anything.

## Provider probe: which endpoints consume replayed reasoning (2026-09-06, after the campaign)

Full table: `artifacts/provider-compatibility/reasoning-replay-probe.md`. Method: identical two-turn history sent with and
without the prior turn's reasoning (seven field variants, two padded), billed prompt tokens compared. $0.36 total.

- **Gemma 4 31B, 15 endpoints:** only `coreweave/fp4` tokenizes replayed reasoning (Δ = its own reasoning length, padded
  Δ ≈ +1,250). The other 14, DeepInfra's three tiers included, bill zero extra tokens whichever field carries it, so their chat
  templates discard assistant reasoning. `cerebras/fp16` returns no thinking at all.
- **Kimi K3, 17 reachable endpoints (makora rate-limited):** all drop replayed reasoning, Moonshot's own endpoint included.
  Effort sweep on Moonshot: 0 / 39 / 121 / 118 reasoning tokens at off / low / high / max. Thinking is on but brief, and
  `max` is not more than `high`. There is no OpenRouter endpoint on which Kimi's reasoning persists across turns.
- **Consequences:** the campaign's gemma-replay row and kimi-k3 row were no-replay runs in effect. Profile metadata updated
  (`gemma-replay` → `reasoning_use: not_consumed_by_endpoint`; Kimi note). New opt-in Gemma variants pair both arms on
  CoreWeave (`gemma-guidance-coreweave`, `gemma-replay-coreweave`) and add a bf16 control (`gemma-guidance-bf16`, Novita).
- **Quantization caveat (Andreas):** endpoints can serve degraded quants or sampling defaults; Artificial Analysis' Endpoint
  Accuracy Index tracks this (currently for gpt-oss-120b only). The only replay-consuming Gemma endpoint is fp4, which is why
  the mini-campaign below includes a bf16 control and repeats.

### Gemma endpoint mini-campaign (launched ~13:30)
`local/ten-turn-samples-gemma-endpoints/`: guidance and replay on CoreWeave fp4, guidance on Novita bf16, guidance on
DeepInfra turbo again, each ×2 (`--repeat 2`). Same save, same 10-turn protocol. Results appended below when done.

### Gemma endpoint mini-campaign — results (ended ~14:15, 8 runs, $0.043 total, zero request errors)

| Arm | Endpoint (quant) | Rep | Furthest checkpoint | Replay billed in-game (corr) | Cache | Latency med/max s | Reasoning tok/turn | Buttons/turn |
|---|---|---:|---|---:|---:|---|---:|---:|
| guidance | deepinfra/turbo | 1 | **Outside in Pallet Town (t8)** | n/a (0.35) | 55% | 6 / 18 | 182 | 4.5 |
| guidance | deepinfra/turbo | 2 | none | n/a (0.82) | 5% | 32 / 114 | 361 | 3.0 |
| guidance | coreweave/fp4 | 1 | none | n/a (0.69) | 50% | 12 / 46 | 254 | 2.5 |
| guidance | coreweave/fp4 | 2 | **Left the bedroom (t5)** | n/a (0.55) | 44% | 4 / 24 | 212 | 3.9 |
| replay | coreweave/fp4 | 1 | none | **yes (0.99)** | 55% | 5 / 63 | 199 | 2.3 |
| replay | coreweave/fp4 | 2 | none | **yes (0.98)** | 70% | 6 / 15 | 264 | 2.5 |
| guidance | novita/bf16 | 1 | none | n/a (0.51) | 0% | 9 / 16 | 353 | 3.0 |
| guidance | novita/bf16 | 2 | none | n/a (0.33) | 0% | 11 / 184 | 331 | 3.2 |

Per-run detail and trace links: `gemma-endpoints/results.md` (repeats kept apart as `#r1`/`#r2`).

What this says, with the honest caveat that every arm is n=2:
- **Gemma's staircase failure is stochastic, not deterministic.** Across all 10 Gemma runs today (2 original + 8 here), 2 left
  the bedroom and 1 left the house. Temperature 0.3 gives different first moves each run; the ones that happened to approach
  the stairs from the mat side got out, the rest nudged into the banister or the stair feet for the remaining turns.
- **Replay reached the model this time and did not help.** On CoreWeave, prompt growth tracked prior reasoning 1:1 (corr 0.98–0.99),
  so the replay arm was real. It went 0/2; guidance on the same endpoint went 1/2. No evidence that seeing its own earlier
  thoughts improves Gemma's navigation; the anchoring pattern from the first campaign (repeat the same failed move) appeared again.
- **Quantization did not rescue it.** bf16 on Novita went 0/2 with the same behaviour as fp4 and "turbo". Whatever the
  endpoint accuracy differences are, they are not what is blocking Gemma here. Novita also reports 0% cache on every request.
- **Gemma sends 2–4 buttons per turn** against Gemini's 10–15. Its plans are one or two tiles at a time, and it revises the
  hypothesis about where the warp tile is almost every turn. That, not format or continuity, is the model-level limitation.
- **Endpoint quality differences are about speed and cache, not gameplay.** CoreWeave fp4 was the fastest (median 4–12 s)
  and cached 44–70%; DeepInfra turbo swung from 6 s to 32 s medians between two runs with a 114 s spike; Novita bf16 had a 184 s
  spike and no caching. All at $0.004–0.006 per 10 turns.
- The in-game "replay billed" correlation is only decisive when it is near 1.0. Guidance arms show 0.3–0.8 because the
  visible `reasoning` field in the action JSON grows with thinking length; the summarizer should flag only corr ≥ 0.95 as
  consumed and treat the rest as not consumed.

### Decisions after the mini-campaign (Andreas, 2026-09-06)
- **Gemma: guidance only from now on.** The base profile (omit_prior) is the default; `gemma-replay*` variants are opt-in
  for re-probing and skipped by the campaign runner.
- **Novita bf16 never hit a cache because there is none to hit.** Every request reported `cached_tokens: 0` explicitly (not
  absent), was billed at the full $0.14/M list rate, and the endpoint advertises no cache-read price at all. Contrast
  CoreWeave fp4: 44–70% reported hits, but its cache-read price equals its prompt price ($0.10/M), so those hits saved
  nothing; DeepInfra turbo does discount ($0.05/M vs $0.09/M). Cache percentages are only worth money where the read price
  is lower, which the "Billed at" row now shows per request.

## Cache economics sort-out (2026-09-06, after the Gemma mini-campaign)

Every run now carries a cache-economics verdict (trace overview, summarizer column): *no cache offered*, *hits not
discounted*, *priced but no hits*, or *$ saved*. Across today's endpoints (`artifacts/provider-compatibility/cache-hit-probe.md`):

- **No cache offered:** novita/bf16. Nothing to fix; avoid for long runs.
- **Hits not discounted:** coreweave/fp4 (cache-read price = prompt price). Its 44–70% "hits" saved $0. Avoid unless replay
  consumption is the point.
- **Priced, no hits:** google-ai-studio (Gemini). 90% discount available, 0 hits in 11 requests. Probe: implicit caching never
  hits through OpenRouter, but an explicit `cache_control` marker on the system prompt caches that block; markers elsewhere
  cache nothing extra on AI Studio and double-bill on Vertex. **Fixed:** Gemini profile → `cache_mode: system_breakpoint`.
- **Under-hitting:** alibaba (Qwen). 89% discount, 11% hits because our explicit system marker *limited* caching to the
  system block; with no markers Alibaba caches the whole prefix implicitly (98%). **Fixed:** Qwen profile → `cache_mode: implicit`.
- Everyone else saves money: Anthropic ($0.43 Opus, $0.74 Fable on one run), Kimi $0.18, Grok $0.11, DeepSeek/GLM/DeepInfra small.

Verification run for the two fixes: `local/ten-turn-samples-cache-fix/` (Gemini + Qwen, 10 turns each). Numbers below.

### Cache-fix verification (10 turns each, same save and protocol; `cache-fix/results.md`)

| Model | Cache before → after | Prompt spend before → after | Total run cost before → after | Compactions before → after | Progress |
|---|---|---|---|---|---|
| gemini-3.8-flash (system marker) | 0% → 14.6% | $0.068 → $0.062 | $0.127 → $0.126 | 1 (schedule) → 1 (schedule) | Chose a starter both times |
| qwen3.8-flash (implicit, no markers) | 10.6% → 70.5% | $0.020 → $0.008 | $0.047 → $0.026 | 4 (context_threshold) → 1 (schedule) | Oak's Lab → outside in Pallet Town |

- Qwen: prompt spend down 59%, total down 44%, and the compaction schedule now behaves (the token-estimate fix, finding 4,
  confirmed live). One new retry: reasoning hit the 8,192 output-token cap on turn 3 (`finish_reason: length`); second attempt
  succeeded. Worth watching in longer runs; Qwen's thinking can exceed the gameplay output budget.
- Gemini: only the ~1.3k-token system block is cacheable through OpenRouter, so the saving is ~9% of prompt spend and <1% of
  total (Gemini's cost is mostly output). Moving more stable content into the system block is the only lever left there.

## Profile improvements applied (Andreas, 2026-09-06 late afternoon)

- **All harness-side output budgets removed for profiled models.** `transport.max_output_tokens` and
  `compaction.max_output_tokens` now equal the endpoint's advertised completion ceiling, and the early-compaction
  `context_token_limit` equals the endpoint's context length. Verified live: all 9 reachable endpoints accept `max_tokens`
  at their ceiling (65k Gemini … 943k Kimi). The early-compaction estimate reserves a realistic 12,288 output tokens instead
  of the ceiling so a 128k ceiling cannot force compaction every turn (test added). An explicit per-alias `max_tokens` is
  still honoured but may not exceed the ceiling. **Consequence to watch:** on Anthropic, OpenRouter derives the thinking
  budget from `max_tokens` (effort high ≈ 80%), so Opus/Fable may now think far longer than before if they choose to; today's
  runs used 36–1,300 reasoning tokens per turn, so the risk is cost variance, not correctness. `max_spend_usd` stays as the
  safety net.
- **Kimi K3:** `reasoning_replay: omit_prior`, `reasoning_use: not_consumed_by_endpoint` (no endpoint tokenizes replay).
- **Gemini system block:** measured a real request: system 1,160 tokens (cached), segment-start user message ~510 tokens
  (changes per segment), observations ~50 tokens + image. Nothing static is left outside the system block, so no prompt
  restructuring; the 15% cache share is the ceiling of this lever.
- **Summarizer:** new column "Replayed reasoning billed" per run (corr ≥ 0.95 → consumed; `omit_prior` → not replayed).
- **Run-start warning** (`endpoint_warning` event + terminal line) when the pinned endpoint lists no cache-read price or
  charges reads at the prompt price. Would have flagged Novita and CoreWeave before turn 1.
- Not done: dropping the duplicate `reasoning` string from replay (billing-neutral per the CoreWeave probe; archive size
  only) and the effort sweep on Opus/Fable/Grok (an experiment, ~$1.50, proposed for before the first-gym benchmark).
