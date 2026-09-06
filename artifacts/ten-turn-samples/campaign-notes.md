# Ten-turn real-gameplay samples — live campaign notes

Started 2026-09-06 10:50 by Codex; continued by Claude Code from ~11:15 after Codex ran out of usage.
Campaign runner: `test_scripts/run_ten_turn_samples.py` (parent PID 92456, one real mGBA emulator, models run sequentially).
Design: same canonical FireRed bedroom save (`configs/saves/pokebench-v1`, sha256 7512323c…), compaction after turn 5,
agent-process + emulator restart after turn 7 (resume from savepoint), 10 turns per profile, referee observing (not enforcing),
max spend $5 per run. Raw manifest: `local/ten-turn-samples/manifest.json`. Run dirs under `local/runs/`.

Rollup script (run after the campaign): `test_scripts/summarize_ten_turn_samples.py local/ten-turn-samples/manifest.json`
→ `artifacts/ten-turn-samples/results.{md,json}`.

## Results so far (updated as runs finish)

| Profile | Turns | Furthest checkpoint (turn) | Cost | Notes |
|---|---:|---|---:|---|
| gemma-guidance (attempt 1) | 5/10 | none | $0.0042 | Crashed at turn 6: post-handover output was valid action JSON without the `result` wrapper; harness rejected it 3×. Pre-fix. **Rerun pending.** |
| gemma-replay (attempt 1) | 5/10 | none | $0.0029 | Handover request took ~4+ min, then same missing-wrapper error. Process had loaded the old parser; Codex sent SIGINT to let the queue move on. **Rerun pending.** |
| google/gemini-3.8-flash | 10/10 | Chose a starter (t9) | $0.1266 | Left bedroom t1, outside t3, Oak's Lab t6, starter t9. Zero request errors. Compaction t5 and restart t7 both clean. |
| anthropic/claude-opus-5 | 10/10 | Stepped outside in Pallet Town | $0.4992 | 5 turns in the bedroom; left bedroom only at t6. Compaction needed 3 attempts: attempt 1 returned truncated JSON (`Expecting ',' delimiter` at char 4086, cost $0.08 wasted), attempt 2 hit an SSL `bad record mac` transport error, attempt 3 succeeded. Restart at t7 clean. |
| anthropic/claude-fable-5.1 | 7/10 so far (resume running) | Stepped outside in Pallet Town (t5) | $0.48 (first 7) | Left bedroom t3, outside t5. Zero request errors in first 7; compaction t5 clean. |
| qwen/qwen3.8-flash | queued | | | |
| z-ai/glm-5.3-flash | queued | | | |
| deepseek/deepseek-v4-flash-vision-exp | queued | | | |
| x-ai/grok-4.6 | queued | | | |
| moonshotai/kimi-k3 | queued | | | |
| meta/muse-spark-1.3 | excluded | | | OpenRouter account requires age attestation. |

## Harness findings (kept separate from model quality)

1. **Gemma unwrapped JSON after handover.** Fixed mid-campaign by Codex: `allow_unwrapped_json` profile flag
   (`configs/provider-profiles.yaml`, Gemma variants only) and parser fallback in `src/agent/append_agent.py`. Test added in
   `tests/test_provider_profiles.py` (invalid button names still rejected). 36 tests pass. Both Gemma rows must be rerun in a
   separate campaign dir with `--only gemma-guidance --only gemma-replay` once the main campaign releases the emulator.
2. **Opus compaction malformed JSON (not truncation).** Attempt 1 finished with `finish_reason=stop` at 2021 completion tokens
   (cap 12288), but the JSON body was one closing brace short (`…]}}` vs the valid attempt's `…]}}}`). A model formatting slip,
   retried successfully by the existing retry loop. Cost of the wasted attempt: $0.081.
3. **Transient SSL error** on Opus attempt 2 — transport-level, retried successfully.

## Caveats
Ten turns cannot rank model quality. Checkpoint progress is from the independent referee (map reads), not model self-report.
Costs include OCR cleanup and failed attempts.
