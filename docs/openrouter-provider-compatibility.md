# OpenRouter compatibility research — append/compaction agent

Researched 2026-09-06. The initial audit below used documentation and public metadata. Provider profiles have since been implemented and live protocol probes added; see the implementation section and `artifacts/provider-compatibility/probe-results.md` for measured results. Protocol acceptance and cache hits do not certify internal use of private reasoning.

All ten requested exact IDs are listed in OpenRouter's current catalogue with image input and reasoning support. I retrieved their individual endpoint metadata: 89 endpoint entries in total. A model-wide capability list is insufficient: providers differ in tools, structured output, context/output limits, cache pricing, and reasoning semantics.

Public sources: [model catalogue](https://openrouter.ai/api/v1/models), per-model `GET /api/v1/models/{author}/{slug}/endpoints`, and [OpenRouter OpenAPI](https://openrouter.ai/openapi.json). Timestamped extracted metadata and raw responses are in `artifacts/provider-compatibility/`, especially `snapshot-summary.json`.

## Recommended architecture

Keep the append/compaction engine and add configuration profiles for **model + serving endpoint + reasoning mode + output mode**. Compatibility has three separate outcomes:

1. Can read the game image and return validated actions and handovers.
2. Can reuse prompt cache, demonstrated by reported cache reads over several requests.
3. Can retain earlier reasoning according to that model's documented contract.

Do not collapse these into a single supported/unsupported flag. A response accepted with reasoning enabled does not demonstrate cross-turn reasoning retention. Gemma, in particular, has a different intended history format.

Keep full raw responses in the archive. Derive the next request according to a declared model policy, and validate that policy rather than requiring identical reasoning replay for every family. Compaction still starts a fresh segment containing summary and flexible memory; it intentionally ends the prior private reasoning chain.

## The ten models

Caching mechanisms below describe native/first-party routes where documented. Third-party hosts require independent verification; advertised cache pricing is not evidence of an observed hit.

| Exact OpenRouter model | Candidate route/profile | Caching | Reasoning and output implications |
|---|---|---|---|
| `meta/muse-spark-1.3` | `meta` | Cache-read rate advertised; exact activation/retention needs a probe. | OpenRouter documents `meta-responses-v1` reasoning objects. Preserve all returned blocks. Verify what 1.3 actually returns, account access, tools, and handover acceptance. |
| `google/gemini-3.8-flash` | `google-ai-studio` or one fixed Vertex endpoint | Implicit caching; begin without explicit cache resources. | Preserve thought signatures, their positions, IDs and tool associations. Thinking levels are high/medium/low in the current catalogue. |
| `google/gemma-4-31b-it` | A tested host; e.g. DeepInfra turbo for JSON, or another host for tools | Host-dependent, including different or absent cache-read discounts. | Google's normal-turn format removes prior raw thoughts. Preserve thoughts within tool-call continuations only. Our current prompted mode starts new user turns, so unconditional raw-thinking replay is inappropriate. |
| `anthropic/claude-opus-5` | `anthropic`, then separately certify other Claude hosts | Top-level `cache_control` for automatic advancing cache boundary. | Return intact signed thinking blocks across turns. Use automatic tool selection or a tested JSON/prompted mode; forced tools conflict with thinking. Existing registry already uses prompted mode. |
| `anthropic/claude-fable-5.1` | `anthropic`; Azure/Bedrock alternatives separately | Same cache control approach. | Preserve complete thinking blocks even if visible text is empty. All listed endpoints omit `tool_choice` support; Vertex endpoint also omits strict structured output. Do not clone Fable 5 settings blindly. |
| `qwen/qwen3.8-flash` | `alibaba` | OpenRouter documents explicit block cache markers for Alibaba; exact 3.8 activation needs confirmation. | Alibaba says 3.8 defaults `preserve_thinking` to true. Return historical `reasoning_content` in its own field, not ordinary content. OpenRouter mapping still needs a trace probe. |
| `z-ai/glm-5.3-flash` | `z-ai/fp8`, other hosts separately | Native Z.AI caching is automatic; session affinity helps. | Z.AI's preserved-thinking API uses `thinking.clear_thinking:false` plus unchanged reasoning. I did not find a documented OpenRouter equivalent/passthrough guarantee for that switch. This is unresolved for this exact route/model. |
| `deepseek/deepseek-v4-flash-vision-exp` | `deepseek` for native behavior; separately test Fireworks/DeepInfra | Native DeepSeek caching is automatic. | Current native thinking docs retain previous reasoning when `tools` is present; without tools it is ignored. Native endpoint lacks advertised `structured_outputs`, unlike some hosts. Prefer a tested tools path. |
| `x-ai/grok-4.6` | `xai` | Automatic on native xAI. | xAI exposes encrypted reasoning through Responses. OpenRouter documents `xai-responses-v1` normalized blocks. First probe Chat Completions for returned/replayed opaque data; compare OpenRouter Responses if needed. Do not infer no reasoning simply from no visible text. |
| `moonshotai/kimi-k3` | `moonshotai/mxfp4`, then other hosts | Automatic on native Moonshot. | Native K3 requires full assistant messages including reasoning. Use `required` or `auto`, not a named forced function with thinking. Official effort levels are low/high/max. |

Sources for the caching mechanisms: [OpenRouter prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching). Sources for normalized blocks and model reasoning metadata: [OpenRouter reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens). Model endpoint facts are in the saved API snapshots, not inferred from family names.

Specific native contracts:

- [Gemini thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures): return signatures in their original parts; function-call validation and current-turn boundaries matter. A successful new-user-turn call is not a strong negative-control test for missing older signatures.
- [Gemma prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4): distinguishes new user turns from calls within one tool-use turn; ordinary text summaries are an option for longer-term reasoning context.
- [Claude thinking](https://platform.claude.com/docs/en/about-claude/models/extended-thinking-models) and [context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows): later Opus and Fable models preserve prior thinking; keep signed blocks complete. [Tool definitions](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) document the thinking/forced-tool restriction.
- [Alibaba Chat API](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions): explicitly documents Qwen 3.8's default preserved thinking. [Qwen 3.8 Flash](https://help.aliyun.com/en/model-studio/qwen3-8-flash) advertises context cache. OpenRouter's cache guide's explicit model list lags this release, so native support should not be mistaken for verified gateway support.
- [Z.AI thinking](https://docs.z.ai/guides/capabilities/thinking-mode): documents the native preservation switch. The OpenRouter OpenAPI contains Anthropic's unrelated `clear_thinking_20251015` context-editing strategy, not a documented Z.AI `clear_thinking` request property. Do not confuse these.
- [DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/): full reasoning history is required with tools, and ignored without tools; several sampling settings have no effect in thinking mode. Verify this general native contract on the experimental vision model.
- [xAI reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning), [Responses versus Chat](https://docs.x.ai/developers/model-capabilities/text/comparison): native Responses supports encrypted reasoning. Native API differences do not establish which upstream API OpenRouter chooses.
- [Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart), [official model README](https://github.com/MoonshotAI/Kimi-K3/blob/main/README.md), [tool choice](https://platform.kimi.ai/docs/guide/use-tool-choice): named forced tools are incompatible with thinking; required tool selection is distinct and supported.

## Gaps identified before implementation

These are code findings and engineering recommendations, not claims of failed live runs.

1. **Tool choice is hardcoded.** `AppendAgent._body()` forces the phase's named function. Add a configured choice strategy: named, required, or auto. Keep the two schemas stable and use phase-specific instructions plus local validation when auto/required permits multiple choices. Claude's existing prompted registry avoids the forced path, but Kimi's default tool profile does not.
2. **Replay policy is universal.** The engine resends the complete assistant message and compares the retained prefix byte-for-byte. This is a good default for preserved-thinking models, but Gemma needs an explicit policy for stripping only prior-turn thought fields while preserving raw archives and current tool-continuation thinking. Never label that declared transformation as accidental loss.
3. **Provider routing is too loose for certification.** `session_id` exists, but provider sorting can select a different endpoint; display provider name alone does not identify region, quantization, or API implementation. Start with exact endpoint `provider.only`, `allow_fallbacks:false`, and `require_parameters:true` after removing unsupported request fields. Snapshot settings and endpoint metadata into each run. OpenRouter describes these controls in [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).
4. **Reasoning levels need current profiles.** The catalogue now includes `reasoning.supported_efforts`, `mandatory`, and optional token-budget support. Gemma/Qwen should not inherit a generic seven-level effort ladder. Our old Kimi entry offers medium/xhigh/none, while native docs say low/high/max and always-on thinking. OpenRouter currently marks Kimi optional: record this disagreement and certify the exact host before exposing an off mode. A reported zero count alone is insufficient to resolve it.
5. **Flexible memory needs schema compatibility checks.** Our `Handover.memory` is an arbitrary dictionary, and native JSON mode uses an `anyOf` envelope without `strict:true`. “Structured outputs supported” does not guarantee every schema feature or arbitrary dictionaries work. Keep local validation; test the exact two-phase schema. Where necessary, use auto tools or prompted JSON, or a JSON-string wire representation for flexible memory that is parsed/validated locally. Do not constrain the user's memory keys just to satisfy a host. See [OpenRouter structured output](https://openrouter.ai/docs/guides/features/structured-outputs).
6. **Cache settings are currently Claude-only.** Introduce configured cache strategy and marker placement, including Alibaba block markers where verified. Advance markers deterministically without rewriting semantic history; account for provider breakpoint limits. Keep model, system, tool declarations, image representation, effort, and schema stable within a segment. Our phase change currently changes named `tool_choice`; Claude documents that this can invalidate message caching. [Claude troubleshooting](https://platform.claude.com/docs/en/agents-and-tools/tool-use/troubleshooting-tool-use).
7. **Limits must follow the endpoint.** Gemma endpoints advertise output limits ranging from 8,192 upward; our compaction allowance is 12,288. GLM has an endpoint advertising only 2,048. A model-wide million-token context does not supersede smaller host limits. Make the configured safety threshold respect selected endpoint context and output limits, while allowing a smaller intentional benchmark cap.
8. **Transformation diagnostics need the documented route.** We currently look for a top-level `input_transformations` field. I did not find it in the reviewed public contract. Opt into `X-OpenRouter-Metadata: enabled` and store `openrouter_metadata.pipeline` and routing attempts. This documents gateway transformations; it still cannot attest to hidden model attention. [Router metadata](https://openrouter.ai/docs/guides/features/router-metadata).
9. **Streaming requires separate certification.** Non-streaming remains the first test path. Verify complete signatures and text/tool-call assembly before enabling streaming for a profile.

An additional finding for our existing Sol profile: OpenRouter now documents `reasoning.context: "all_turns"` for OpenAI GPT-5.6+; its default is model-dependent. This is not a universal GLM/Gemini/Claude switch. Our prior smoke run preserved opaque data but did not set that control, so it did not prove all-turn internal use. Source: [reasoning context mode](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#reasoning-context).

## What to verify next

Use a small certification suite before a long Pokémon benchmark. Keep prompts/settings in configuration and preserve the current agent/default benchmark.

For each chosen model/endpoint/profile:

1. Read a real game screenshot and produce a valid action using the actual schema.
2. Append the full response according to the model policy, send the next observation, and record what was replayed, transformed, accepted, and returned.
3. Observe multiple cache opportunities using a sufficiently large stable prefix. Report missing metrics as unknown; never use whole-response caching as evidence of prompt-cache reuse. Separate tests with warmed repeated prompts from gameplay benchmarking.
4. Run the actual custom compaction schema, commit the flexible memory, and start a fresh segment. Ensure zero old opaque blocks are transplanted into that new history.
5. Save/reload in a separate process and continue, verifying provider/profile identity, assets, signatures, and committed action boundaries.
6. Exercise invalid output, missing cost metadata, provider rejection, and budget limits. Treat protocol corruption as an error; don't silently turn off thinking or switch models.
7. For signatures with documented validation, a controlled missing-signature negative test within the *same tool turn* can test enforcement. Acceptance of a modified/new-user-turn request proves neither preservation nor use.

Publish independent capability statuses: vision/action/compaction/resume passed; cache observed/not observed/unreported; reasoning policy full-history/tool-turn-only/unsupported/unverified; and returned opaque state present/absent. Store resolved effort as well as the user's requested level.

Start with one first-party route per family where available, then expand hosts. Testing all 89 endpoint variants is a larger matrix than testing ten model names. Native documentation, endpoint metadata, and measured behavior must remain separate evidence levels.

## Implemented profiles and host switching

`configs/provider-profiles.yaml` contains ten model/endpoint profiles. `config-append.yaml` opts in; the numbered configs and existing saved runs keep their previous behavior. Resolved profiles are saved inside run config and conversation checkpoints. Resume rejects a changed profile before sending another request. Unknown models retain the existing unprofiled transport; they are not automatically certified.

Profiles control endpoint restrictions, reasoning settings, output mode, tool selection, reasoning projection, cache activation, and host limits. All profile requests send one `provider.only` entry, `allow_fallbacks:false`, `require_parameters:true`, an unchanged `session_id`, and `transforms:[]`. Tools and output schemas stay stable through gameplay and compaction. Unsupported effort settings and output budgets fail configuration validation. A reported provider/model change stops the profiled run, including across compaction boundaries. This does not detect invisible changes behind a provider's API.

**Changing providers matters for open-weight models.** Identical model IDs can route to different inference implementations, quantizations, regions, limits, and feature sets. The actual catalogue snapshot demonstrates this. Treat changing hosts as a different benchmark configuration. Ordinary conversation text is portable, but do not assume private reasoning signatures or prompt caches transfer. The cache-loss expectation is an inference from provider-local caching and OpenRouter's documented need to return to the same endpoint, not a measured cross-host cache migration experiment.

OpenRouter's [cache routing documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching) says `session_id` establishes affinity on the first successful request. Without it, affinity depends on identifying the opening conversation and a cache-eligible request. Sticky routing can fall back when a host is unavailable, expires after ten minutes of inactivity, and is overridden by `provider.order`. Our profiles use `only` rather than a rotating order. The same session ID survives our compaction and disk resume. Affinity is routing help, not a cache retention guarantee; each provider has its own minimum length, TTL and eviction behavior.

Use full endpoint suffixes where available. Base provider slugs can match several variants; service tiers have separate opt-in rules. The Grok profile explicitly excludes the ZDR variant. Future provider inventory changes still need review. See [endpoint matching](https://openrouter.ai/docs/guides/routing/provider-selection#targeting-specific-provider-endpoints).

Appending preserves the reusable prefix. Compaction replaces old observations with a summary and memory, so that discarded prefix cannot be reused on the next request. The unchanged system/tools may still hit cache. The custom handover intentionally starts fresh reasoning history; an old cache does not restore discarded thinking.

Claude profiles activate automatic top-level caching. Qwen marks the stable system block with explicit `cache_control`; this initial strategy does not explicitly cache the whole growing transcript. Other profiles use implicit caching where documented; Muse's activation remains unknown. Cache counters remain measured values, with unknown distinguished from zero. An initial four-call test without cache reads does not establish that a host cannot cache.

The HTTP transport requests [router metadata](https://openrouter.ai/docs/guides/features/router-metadata), stores the complete routing/pipeline report, and detects reported compression/drop/truncation. Missing metadata remains unknown. Edge region is not treated as serving endpoint identity, and an empty pipeline is not proof that private thoughts were used. Technical details show the configured route, cache strategy, replay policy, and unverified internal reasoning use. They also show what the prompt was billed at against the endpoint's list tiers (snapshotted once per run into `conversation/endpoint-pricing.json`) and the cache discount that billing implies; this is the only cache signal for providers whose usage block reports no cache figures.

Live probes exposed catalogue/runtime mismatches: DeepInfra's Gemma turbo endpoint rejected `json_schema`; Qwen rejected required tool choice and failed the native-JSON envelope during compaction. Their profiles use prompted JSON with strict local validation, optionally accepting a single complete JSON code fence. Raw responses are unchanged in the archive. Grok uses an explicit JSON-string representation for arbitrary memory on the wire, decoded into the same flexible dictionary before validation and handover commit. These are configured contracts, not hidden per-request fallback behavior.

For an intentional provider migration, finish a handover with the old provider, then start a new run/profile from that handover and game state. Do not alter the pinned host midway through a retained signed conversation. Automatic cross-provider migration is not implemented.
