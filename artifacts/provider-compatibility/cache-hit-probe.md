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
