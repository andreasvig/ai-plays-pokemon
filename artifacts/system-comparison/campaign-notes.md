# Old vs new system comparison — running notes

> Source: conversation 2026-09-06. Plan: [plan.md](plan.md). Runner: `test_scripts/run_system_comparison.py`; summarizer: `test_scripts/summarize_system_comparison.py`. Campaign dir: `local/system-comparison/` (gitignored).

## Setup findings (before the campaign)

1. **GPT-6 Astra probes (openai tag, effort medium).** Replayed `reasoning_details` (encrypted + summary items) are tokenized by the endpoint: +57 prompt tokens per replayed turn, versus 0 for a bare `reasoning` string. Verdict "consumed", so the profile keeps `reasoning_replay: all`. Caching is automatic on the whole prefix: an 8.5k-token identical prefix came back 8489/8492 cached on both calls, with and without a system marker, at $1/M (90% off). Profile `cache_mode: implicit`, no markers. Files: `artifacts/provider-compatibility/reasoning-replay-probe__openai--gpt-6-astra.json`, `cache-hit-probe__openai--gpt-6-astra.json`. Probe spend ≈ $0.06.
2. **Registry entries** `gpt-6-astra`, `gemini-3.8-flash`, `glm-5.3-flash` added to `configs/models.yaml`, each pinned with `provider.only` + `allow_fallbacks: false` to the tag the append-agent profile uses. Release dates synced.
3. **Legacy transport vs Z.AI.** First smoke (old arm, GLM): HTTP 400 `Tool choice must be auto` on the forced tool call the pydantic-ai path uses. `output_mode: native_json` then failed client-side (pydantic-ai: "Native structured output is not supported by the model"), so the GLM registry entry uses `output_mode: prompted` (locally validated JSON, the Gemma/Qwen path). The new arm sends tools with `tool_choice: auto` per its profile, so the two arms use different output plumbing for GLM; both are schema-validated.
4. **mGBA Lua auto-load flake.** The second child in a campaign got "Load recent script" (AppleScript load missed) and timed out after 60 s. The child now re-issues the load and waits again, up to four times.
5. The per-run dashboard cannot bind :3420 while the review server holds it; the run continues without it (logged as ERROR, harmless).
