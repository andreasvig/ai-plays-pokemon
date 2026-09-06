# Ten-turn real Pokémon samples

Same canonical FireRed bedroom save, compaction after turn 5, real agent-process and emulator restart after turn 7. Provider defaults and production action execution; no mock screenshots or emulator actions.

| Model/profile | Settled turns | Restart + continuity | Furthest checkpoint | Replayed reasoning billed | Cached input (reported) | Cached input (implied by billing) | Cache economics | Total cost | Trace |
|---|---:|---|---|---|---:|---:|---|---:|---|
| gemma-guidance #r1 | 10 | Pass | Stepped outside in Pallet Town | Not replayed (policy) | 54.9% | 54.9% | $0.0006 saved | $0.0036 | [Open](http://localhost:3420/history/2026-09-06_13-05-26_config-append-sample__google-gemma-4-31b-it__gemma-guidance_continued_from_turn_7) |
| gemma-guidance #r2 | 10 | Pass | None | Not replayed (policy) | 4.6% | 4.6% | $0.0001 saved | $0.0058 | [Open](http://localhost:3420/history/2026-09-06_13-13-05_config-append-sample__google-gemma-4-31b-it__gemma-guidance_continued_from_turn_7) |
| gemma-guidance-coreweave #r1 | 10 | Pass | None | Not replayed (policy) | 50.0% | 0.0% | Hits not discounted | $0.0048 | [Open](http://localhost:3420/history/2026-09-06_13-17-21_config-append-sample__google-gemma-4-31b-it__gemma-guidance-coreweave_continued_from_turn_7) |
| gemma-guidance-coreweave #r2 | 10 | Pass | Left the bedroom | Not replayed (policy) | 43.6% | 0.0% | Hits not discounted | $0.0046 | [Open](http://localhost:3420/history/2026-09-06_13-20-58_config-append-sample__google-gemma-4-31b-it__gemma-guidance-coreweave_continued_from_turn_7) |
| gemma-replay-coreweave #r1 | 10 | Pass | None | Yes (corr 0.992) | 55.1% | 0.0% | Hits not discounted | $0.0059 | [Open](http://localhost:3420/history/2026-09-06_13-23-14_config-append-sample__google-gemma-4-31b-it__gemma-replay-coreweave_continued_from_turn_7) |
| gemma-replay-coreweave #r2 | 10 | Pass | None | Yes (corr 0.984) | 69.8% | 0.0% | Hits not discounted | $0.0061 | [Open](http://localhost:3420/history/2026-09-06_13-26-23_config-append-sample__google-gemma-4-31b-it__gemma-replay-coreweave_continued_from_turn_7) |
| gemma-guidance-bf16 #r1 | 10 | Pass | None | Not replayed (policy) | 0.0% | 0.0% | No cache offered | $0.0057 | [Open](http://localhost:3420/history/2026-09-06_13-28-45_config-append-sample__google-gemma-4-31b-it__gemma-guidance-bf16_continued_from_turn_7) |
| gemma-guidance-bf16 #r2 | 10 | Pass | None | Not replayed (policy) | 0.0% | 0.0% | No cache offered | $0.0061 | [Open](http://localhost:3420/history/2026-09-06_13-31-58_config-append-sample__google-gemma-4-31b-it__gemma-guidance-bf16_continued_from_turn_7) |

Costs include OCR and retries; continuation totals already include the first seven turns and are counted once. Superseded failed attempts are preserved in results.json and included in total reported campaign cost. Cache fractions describe observed requests, including compaction, not a controlled cache-performance benchmark. The implied column backs the cache discount out of the billed prompt cost against the endpoint's list prices, so it stays meaningful when a provider (Google AI Studio) reports no cache figures. These short runs do not establish a reliable ranking.

Local reasoning replay checks show what was sent. They do not establish internal use by the serving provider. Gemma guidance intentionally omits prior raw thoughts; replay preserves them until compaction.

Total reported spend across retained attempts: $0.042520.
