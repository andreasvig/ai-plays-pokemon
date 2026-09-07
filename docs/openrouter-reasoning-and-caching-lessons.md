# What we learned about reasoning replay and caching through OpenRouter (2026-09-06)

Consolidated from the ten-turn provider samples and two probes. Raw tables: `artifacts/provider-compatibility/reasoning-replay-probe.md`,
`cache-hit-probe.md`; running log: `artifacts/ten-turn-samples/campaign-notes.md`. Probes: `test_scripts/probe_reasoning_replay.py`,
`test_scripts/probe_cache_hits.py`.

## The relay reports delivery, the bill reports consumption

OpenRouter echoes `reasoning_details` back and counts `cached_tokens`, but neither says whether the upstream model tokenized the
replayed reasoning or whether a cache hit cost less. Both are read from billing:

- **Replay consumed?** Send the same history with and without the prior reasoning; billed `prompt_tokens` grow by the reasoning
  length when consumed, by zero when the provider's chat template drops it. In-run: prompt growth between consecutive requests
  correlates ≥ 0.95 with prior reasoning tokens when consumed (the summarizer reports this per run).
- **Cache hit worth money?** `upstream_inference_prompt_cost / prompt_tokens` against the endpoint's list tiers gives
  `implied_cached = (n·P_full − billed) / (P_full − P_read)` after removing cache-write premiums. Shown per request in Technical
  details ("Billed at", "Implied discount") and per run ("Cache economics").

## Per-endpoint facts, not per-model

| Behaviour | What we saw |
|---|---|
| Replay tokenized | Gemini, Claude Opus/Fable, Qwen (Alibaba), GLM (Z.AI), DeepSeek, Grok: yes. Gemma 4 31B: 1 of 15 endpoints (coreweave/fp4). Kimi K3: 0 of 17. |
| Automatic caching | Anthropic: 63→85% within a segment; first request of a segment is mostly writes at 1.25×. |
| Implicit caching, markers hurt | Alibaba: no marker → 98%; system marker → system block only. Send none. |
| Implicit caching, markers hurt (2) | Google AI Studio: on real 27k+ prompts, no marker → 89% (gemini-3.8-flash) / 60% (3.5-flash-lite) cached from the second send on, extension included; any explicit marker suppresses it and caches only the marked block. 13k prompts never hit, so the implicit minimum sits between 13k and 27k. The 2026-09-06 verdict ("implicit never fires") came from prompts below that floor. |
| Cache priced at no discount | coreweave/fp4: read price = prompt price. Hits save $0. |
| No cache | novita/bf16: no cache-read price listed, `cached_tokens: 0` always. |
| Failure shape | Z.AI returned HTTP 200, empty choice, `native_finish_reason: network_error`, no usage, after 181 s. |
| Output ceilings | All 9 pinned endpoints accept `max_tokens` = advertised `max_completion_tokens`. Anthropic's thinking budget derives from it. |
| Quantization | Tag carries the quant. Gemma bf16/fp4/turbo played identically at n=2; differences were latency and cache. Artificial Analysis' Endpoint Accuracy Index tracks degradation (gpt-oss-120b only so far). |

## Harness rules that came out of it

1. Estimate compaction pressure in tokens, count reasoning once, reserve realistic output, not the ceiling (`approx_tokens`).
2. Accept unwrapped JSON everywhere; the schema is the guard.
3. Classify on `native_finish_reason`, not on empty content.
4. Snapshot endpoint prices per run; warn at start when the pinned endpoint cannot discount hits.
5. Pin the endpoint tag and record it in results; re-probe replay and cache placement when the tag changes.
6. Repeats before conclusions on sampled models: Gemma went from "0 of 2 leave the bedroom" to "2 of 8".

## Profile decisions (2026-09-06)

Gemma guidance only; no harness output budgets on profiled models; Kimi `omit_prior`; Gemini `system_breakpoint`; Qwen `implicit`.
