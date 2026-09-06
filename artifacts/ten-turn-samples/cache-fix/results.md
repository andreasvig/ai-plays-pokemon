# Ten-turn real Pokémon samples

Same canonical FireRed bedroom save, compaction after turn 5, real agent-process and emulator restart after turn 7. Provider defaults and production action execution; no mock screenshots or emulator actions.

| Model/profile | Settled turns | Restart + continuity | Furthest checkpoint | Replayed reasoning billed | Cached input (reported) | Cached input (implied by billing) | Cache economics | Total cost | Trace |
|---|---:|---|---|---|---:|---:|---|---:|---|
| google/gemini-3.8-flash | 10 | Pass | Chose a starter | Yes (corr 0.998) | 14.6% | 14.6% | $0.0095 saved | $0.1259 | [Open](http://localhost:3420/history/2026-09-06_14-03-17_config-append-sample__google-gemini-3-8-flash_continued_from_turn_7) |
| qwen/qwen3.8-flash | 10 | Pass | Stepped outside in Pallet Town | Yes (corr 1.0) | 70.5% | 70.5% | $0.0144 saved | $0.0261 | [Open](http://localhost:3420/history/2026-09-06_14-12-40_config-append-sample__qwen-qwen3-8-flash_continued_from_turn_7) |

Costs include OCR and retries; continuation totals already include the first seven turns and are counted once. Superseded failed attempts are preserved in results.json and included in total reported campaign cost. Cache fractions describe observed requests, including compaction, not a controlled cache-performance benchmark. The implied column backs the cache discount out of the billed prompt cost against the endpoint's list prices, so it stays meaningful when a provider (Google AI Studio) reports no cache figures. These short runs do not establish a reliable ranking.

Local reasoning replay checks show what was sent. They do not establish internal use by the serving provider. Gemma guidance intentionally omits prior raw thoughts; replay preserves them until compaction.

Total reported spend across retained attempts: $0.151910.
