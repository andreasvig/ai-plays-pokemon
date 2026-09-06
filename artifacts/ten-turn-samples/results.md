# Ten-turn real Pokémon samples

Same canonical FireRed bedroom save, compaction after turn 5, real agent-process and emulator restart after turn 7. Provider defaults and production action execution; no mock screenshots or emulator actions.

| Model/profile | Settled turns | Restart + continuity | Furthest checkpoint | Cached input (reported) | Cached input (implied by billing) | Cache economics | Total cost | Trace |
|---|---:|---|---|---:|---:|---|---:|---|
| gemma-guidance | 10 | Pass | None | 16.7% | 16.7% | $0.0002 saved | $0.0058 | [Open](http://localhost:3420/history/2026-09-06_12-05-32_config-append-sample__google-gemma-4-31b-it__gemma-guidance_continued_from_turn_7) |
| gemma-replay | 10 | Pass | None | 69.0% | 69.0% | $0.0008 saved | $0.0044 | [Open](http://localhost:3420/history/2026-09-06_12-10-59_config-append-sample__google-gemma-4-31b-it__gemma-replay_continued_from_turn_7) |
| google/gemini-3.8-flash | 10 | Pass | Chose a starter | 0.0% | 0.0% | 90% discount unused | $0.1266 | [Open](http://localhost:3420/history/2026-09-06_11-06-45_config-append-sample__google-gemini-3-8-flash_continued_from_turn_7) |
| anthropic/claude-opus-5 | 10 | Pass | Stepped outside in Pallet Town | 75.1% | 75.1% | $0.4320 saved | $0.4992 | [Open](http://localhost:3420/history/2026-09-06_11-11-35_config-append-sample__anthropic-claude-opus-5_continued_from_turn_7) |
| anthropic/claude-fable-5.1 | 10 | Pass | Entered Oak's Lab | 71.8% | 71.8% | $0.7388 saved | $0.6219 | [Open](http://localhost:3420/history/2026-09-06_11-15-32_config-append-sample__anthropic-claude-fable-5-1_continued_from_turn_7) |
| qwen/qwen3.8-flash | 10 | Incomplete / inspect | Entered Oak's Lab | 10.6% | 10.6% | $0.0021 saved | $0.0466 | [Open](http://localhost:3420/history/2026-09-06_11-28-13_config-append-sample__qwen-qwen3-8-flash_continued_from_turn_7) |
| z-ai/glm-5.3-flash | 10 | Pass | Left the bedroom | 66.6% | 66.6% | $0.0040 saved | $0.0070 | [Open](http://localhost:3420/history/2026-09-06_11-36-56_config-append-sample__z-ai-glm-5-3-flash_continued_from_turn_7) |
| deepseek/deepseek-v4-flash-vision-exp | 10 | Pass | Left the bedroom | 88.5% | 88.5% | $0.0147 saved | $0.0151 | [Open](http://localhost:3420/history/2026-09-06_11-43-44_config-append-sample__deepseek-deepseek-v4-flash-vision-exp_continued_from_turn_7) |
| x-ai/grok-4.6 | 10 | Pass | Entered Oak's Lab | 45.5% | 45.5% | $0.1098 saved | $0.3996 | [Open](http://localhost:3420/history/2026-09-06_11-54-21_config-append-sample__x-ai-grok-4-6_continued_from_turn_7) |
| moonshotai/kimi-k3 | 10 | Pass | Stepped outside in Pallet Town | 72.7% | 72.7% | $0.1827 saved | $0.1644 | [Open](http://localhost:3420/history/2026-09-06_12-00-27_config-append-sample__moonshotai-kimi-k3_continued_from_turn_7) |

Costs include OCR and retries; continuation totals already include the first seven turns and are counted once. Superseded failed attempts are preserved in results.json and included in total reported campaign cost. Cache fractions describe observed requests, including compaction, not a controlled cache-performance benchmark. The implied column backs the cache discount out of the billed prompt cost against the endpoint's list prices, so it stays meaningful when a provider (Google AI Studio) reports no cache figures. These short runs do not establish a reliable ranking.

Local reasoning replay checks show what was sent. They do not establish internal use by the serving provider. Gemma guidance intentionally omits prior raw thoughts; replay preserves them until compaction.

Total reported spend across retained attempts: $1.897737.
