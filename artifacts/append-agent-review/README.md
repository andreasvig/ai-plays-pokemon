# Append-agent implementation review

This folder contains browser inspection scripts and screenshots for a real five-turn FireRed smoke test with GPT-5.6 Sol (medium), including process restart and two custom compactions. It is not a gameplay benchmark result.

Open http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3 and expand turn five. The review server uses isolated state under `local/append-review` and the project's headless/fake-emulator mode; it serves the real saved runs without dispatching gameplay.

Evidence:

- Real model/game requests: `local/runs/2026-09-05_23-44-21_append-smoke__gpt-5-6-sol-medium` and its `2026-09-05_23-47-09_..._continued_from_turn_3` continuation.
- Total observed spend including OCR: $0.065203.
- Seven model requests; 18,301 / 34,825 input tokens read from cache (52.6%).
- Original compaction interval reduced to two turns for this smoke test; shipped config defaults to twenty.
- Browser checks: five gameplay rows, correct turn-two action (no stale replayed action), compaction cache counters, memory before/after, and no false compaction on turn one. No browser console, page, or network errors.
- Full suite after the main integration fixes: 623 passed, eight documented pre-existing failures (model registry x3, OCR x1, TaskMaster cadence x1, TaskMaster search x3).
- The added provider-signature rejection test was subsequently run with the complete new-agent test file.

`verify.mjs` is the repeatable browser check through Marvin's browser wrapper. `03-handover.png` shows the expanded memory handover; `04-first-turn.png` is the negative control.

Live continuity evidence is limited to OpenAI via OpenRouter non-streaming tool output. Client replay integrity and provider acceptance do not establish internal reasoning use. Other routes, native compaction, and streaming need separate live verification.
