# Provider profile live probes — 2026-09-06

Nine accessible models passed. Muse Spark requires the account owner’s 18+ attestation. Total reported spend including unsuccessful probes: $0.683866.

Each passing probe used a recorded Pokémon screenshot: action, disk checkpoint reload, second action, compaction, fresh third action. No emulator inputs were executed. Default profile reasoning, 4,096 output tokens per request, zero retries. This is a protocol smoke test, not benchmark scoring or proof of private reasoning use.

| Model | Pinned route | Protocol | Cached input | Review |
|---|---|---|---:|---|
| meta/muse-spark-1.3 | meta | Account blocked | Unmeasured | [Trace](http://localhost:3420/history/2026-09-06_10-13-46_protocol-probe__meta--muse-spark-1.3) |
| google/gemini-3.8-flash | google-ai-studio | Pass (4 requests) | 0.0% | [Trace](http://localhost:3420/history/2026-09-06_10-13-47_protocol-probe__google--gemini-3.8-flash) |
| google/gemma-4-31b-it | deepinfra/turbo | Pass (4 requests) | 0.0% | [Trace](http://localhost:3420/history/2026-09-06_10-17-45_protocol-probe__google--gemma-4-31b-it) |
| anthropic/claude-opus-5 | anthropic | Pass (4 requests) | 41.7% | [Trace](http://localhost:3420/history/2026-09-06_10-14-47_protocol-probe__anthropic--claude-opus-5) |
| anthropic/claude-fable-5.1 | anthropic | Pass (4 requests) | 42.8% | [Trace](http://localhost:3420/history/2026-09-06_10-15-30_protocol-probe__anthropic--claude-fable-5.1) |
| qwen/qwen3.8-flash | alibaba | Pass (4 requests) | 15.5% | [Trace](http://localhost:3420/history/2026-09-06_10-22-30_protocol-probe__qwen--qwen3.8-flash) |
| z-ai/glm-5.3-flash | z-ai/fp8 | Pass (4 requests) | 43.0% | [Trace](http://localhost:3420/history/2026-09-06_10-16-30_protocol-probe__z-ai--glm-5.3-flash) |
| deepseek/deepseek-v4-flash-vision-exp | deepseek | Pass (4 requests) | 49.9% | [Trace](http://localhost:3420/history/2026-09-06_10-17-28_protocol-probe__deepseek--deepseek-v4-flash-vision-exp) |
| x-ai/grok-4.6 | xai | Pass (4 requests) | 36.2% | [Trace](http://localhost:3420/history/2026-09-06_10-21-21_protocol-probe__x-ai--grok-4.6) |
| moonshotai/kimi-k3 | moonshotai/mxfp4 | Pass (4 requests) | 31.5% | [Trace](http://localhost:3420/history/2026-09-06_10-19-37_protocol-probe__moonshotai--kimi-k3) |

Cache percentages are cached tokens divided by measured input tokens across the four calls, including compaction. They are not cost savings or provider rankings; response lengths and minimum cache sizes differ. Zero is observed zero, not missing telemetry.

Seven final profiles observed cache reads. Gemini and Gemma did not in this short sequence. Claude, Gemini, Grok, and Meta have signed/opaque reasoning contracts; returned blocks are archived without claiming hidden attention. GLM preserved-thinking passthrough remains unverified. Gemma intentionally omits prior raw thinking between user turns.

Live corrections: Gemma uses prompted JSON after the host rejected advertised json_schema support; one complete Markdown JSON fence is accepted and locally validated. Qwen uses prompted JSON after auto tools skipped calls, required tool choice was rejected by routing, and native JSON omitted the envelope at compaction. Grok encodes flexible memory as a JSON string on the wire, then decodes and validates it as a dictionary.

Streaming, long conversations, actual emulator performance, cross-provider migration, and separate-process resume are not certified by these probes. Disk serialization/reload and isolated-engine resume are covered; existing earlier Sol testing separately exercised process restart.

Full local request/response/config/endpoint snapshots are linked from the run directories in probe-results.json. Failed experiments are retained there for audit.
