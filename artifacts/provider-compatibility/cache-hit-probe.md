# Can the pinned endpoint be made to cache our prefix?

Probe: `test_scripts/probe_cache_hits.py`. Identical request sent twice, 1.5 s apart, with the real system prompt plus filler
(~8k tokens), the real turn-1 screenshot, and a conversation tail; `cache_control` markers placed in different spots.
Reported on the second call. Probed 2026-09-06.

## google/gemini-3.8-flash

| Endpoint | Marker | Prompt tokens | Cached on 2nd call | Prompt cost 2nd call |
|---|---|---:|---:|---:|
| google-ai-studio | none | 13,459 | 0 | $0.01009 |
| google-ai-studio | system | 13,459 | 8,041 (the marked block) | $0.00467 |
| google-ai-studio | last message | 13,459 | 8,148 | $0.00459 |
| google-ai-studio | system + last | 13,459 | 8,041 | $0.00467 |
| google-vertex | none | 13,449 | 0 | $0.01009 |
| google-vertex | system | 13,449 | 8,041 | $0.00466 |
| google-vertex | last message | **26,899** | 13,450 | $0.01029 |
| google-vertex | system + last | **26,899** | 13,450 | $0.01029 |

- Implicit caching never hits through OpenRouter, even for a 13k-token identical prefix seconds apart. Matches the campaign
  (0 of 11 requests).
- An explicit marker caches exactly the marked block; the conversation tail after it is never cached on AI Studio.
- On Vertex a marker on the last message doubles the billed prompt tokens (cached content billed on top of the full prompt):
  no saving. Marking only the system block is the safe choice on both Google endpoints.
- **Profile change:** `cache_mode: system_breakpoint` for Gemini. Expected saving in the campaign shape: ~1.5k system tokens
  at 90% off per request, roughly 15% of Gemini's prompt spend; more if stable content moves into the system block.

## qwen/qwen3.8-flash on alibaba

| Marker | Prompt tokens | Cached on 2nd call | Prompt cost 2nd call |
|---|---:|---:|---:|
| none | 9,895 | **9,728 (98%)** | $0.00018 |
| system | 9,895 | 8,471 (system block only) | $0.00035 |
| system + last | 9,895 | 9,889 (99.9%) | $0.00016 |

- Alibaba caches the whole identical prefix implicitly. Our explicit system marker *restricted* caching to the system block,
  which is why the campaign's Qwen run cached a flat 1,060 tokens (11%) on every request.
- **Profile change:** `cache_mode: implicit` for Qwen (no markers). Expected saving: most of Qwen's prompt spend at 89% off.

## Endpoint cache economics from the campaigns

| Endpoint | Prompt $/M | Cache read $/M | Discount | Observed hits | Verdict |
|---|---:|---:|---:|---:|---|
| novita/bf16 (Gemma) | 0.14 | none listed | — | 0% | no cache offered |
| coreweave/fp4 (Gemma) | 0.10 | 0.10 | 0% | 44–70% | hits not discounted |
| google-ai-studio (Gemini) | 0.75 | 0.075 | 90% | 0% | priced, no hits → fixed by system marker |
| alibaba (Qwen) | 0.15 | 0.016 | 89% | 11% | system-only → fixed by implicit mode |
| deepinfra/turbo (Gemma) | 0.09 | 0.05 | 44% | 5–69% | saves money, erratic |
| xai (Grok) | 2.00 | 0.50 | 75% | 46% | saves money ($0.11 on one run) |
| anthropic (Opus / Fable) | 5 / 10 | 0.50 / 0.25 | 90 / 98% | 75 / 72% | saves money ($0.43 / $0.74 on one run) |
| moonshotai/mxfp4 (Kimi) | 3.00 | 0.30 | 90% | 73% | saves money ($0.18) |
| z-ai/fp8 (GLM) | 0.075 | 0.015 | 80% | 67% | saves money |
| deepseek (DeepSeek) | 0.22 | 0.007 | 97% | 89% | saves money |

## google/gemini-3.8-flash — replay of a real run, 2026-09-07

The 2026-09-06 verdict above was wrong for real prompt sizes. Replaying turns 10 and 11 of `2026-09-07_12-20-20_config-5.0__gemini-3-8-flash-medium` (27,256 and 31,449 prompt tokens, 10–11 screenshots) against `google-ai-studio`:

| Marker placement | turn 10 cached | turn 11 (extends) cached | turn 11 prompt cost |
|---|---|---|---|
| system (the run's setting) | 1,274 | 1,274 | $0.0227 |
| last text block of the final user message | 12,717 (text before the marker) | 14,002 | $0.0147 |
| last assistant/tool message | 12,685 | 13,941 | $0.0148 |
| **none, second send** | **24,365 (89%)** | **28,389 (90%)** | **$0.0044** |

No-marker sequence, after a 5.5-minute cool-down: first send 0 cached; second and third sends 24,365; the extension 28,389 — the whole prefix including images. An explicit marker anywhere caps the cache at the marked block, as on Alibaba. gemini-3.5-flash-lite, same prompts: 16,243 (60%) on the second send, 16,251 on the extension. Both profiles moved to `cache_mode: implicit`. The 13k and 9.9k prompts probed on 2026-09-06 never hit, so the implicit minimum lies between 13k and 27k tokens; a fresh run will pay full price for its first few turns and then cache from there. Scripts: `test_scripts/probe_replay_cache_placements.py`, `test_scripts/probe_replay_implicit_cache.py`. Spend: $0.31 (one accidental duplicate of the placement sweep included).
