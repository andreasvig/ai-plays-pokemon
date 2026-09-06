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

Gemini reported 0% cached input on Google AI Studio across all requests (implicit caching not surfaced in usage), so its
cache column is not comparable to the others.

## Harness findings (kept separate from model quality)

1. **Gemma unwrapped JSON after handover.** Fixed mid-campaign by Codex: `allow_unwrapped_json` profile flag
   (`configs/provider-profiles.yaml`, Gemma variants only) and parser fallback in `src/agent/append_agent.py`. Test added in
   `tests/test_provider_profiles.py` (invalid button names still rejected). 36 tests pass. Both Gemma rows are being rerun in
   `local/ten-turn-samples-gemma-rerun/` (launched 12:03) with `--only gemma-guidance --only gemma-replay` once the main campaign releases the emulator.
2. **Opus compaction malformed JSON (not truncation).** Attempt 1 finished with `finish_reason=stop` at 2021 completion tokens
   (cap 12288), but the JSON body was one closing brace short (`…]}}` vs the valid attempt's `…]}}}`). A model formatting slip,
   retried successfully by the existing retry loop. Cost of the wasted attempt: $0.081.
3. **Transient SSL error** on Opus attempt 2 — transport-level, retried successfully.
4. **Context-threshold estimate conflates bytes with tokens** (`src/agent/append_agent.py` ~line 338). `growth` is the JSON
   byte length of the last two messages plus the OCR text. Those bytes include the base64 screenshot (~22 KB) and the
   assistant's replayed reasoning stored twice (`reasoning` + `reasoning_details`, ~18.5 KB each for Qwen). For Qwen the
   turn-3 estimate was ≈ 10.2k (last input) + 38k (reasoning bytes) + 22k (image bytes) + 4.1k (reserve) + 12.3k (max
   output) ≈ 88k, over the 85.2k threshold (131072 × 0.65), so compaction fired every turn. Gemini's same estimate was ≈ 52k.
   Consequence: any verbose-reasoning model compacts nearly every turn, reasoning replay across turns is never exercised for
   it, and cost/latency inflate. The real prompt was 10k tokens, far from any limit. Fix candidates (after the campaign, not
   mid-measurement): estimate `growth` in tokens (bytes ÷ ~4 for text, a fixed image token cost), and count reasoning once.
5. **Qwen also drops the `result` wrapper** on compaction (2 of 4 first attempts). The Gemma fix is profile-gated
   (`allow_unwrapped_json` only on the Gemma profiles), so Qwen paid a retry each time. Same class of defect; suggests the
   unwrapped fallback should be default-on for all providers, with action validation still enforced.
6. **Provider network error surfaced as a format error.** GLM turn 1 attempt 1: OpenRouter returned an empty choice with
   `native_finish_reason: network_error` and no usage after 181 s. The harness logged it as `Expected exactly one gameplay
   call`, which reads as a model-output defect. Worth classifying `native_finish_reason in {network_error, error}` as a
   transport failure so error tallies separate provider outages from model formatting.

## Caveats
Ten turns cannot rank model quality. Checkpoint progress is from the independent referee (map reads), not model self-report.
Costs include OCR cleanup and failed attempts.
