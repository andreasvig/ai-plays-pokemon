# Append-and-compact agent: design draft

Status: implemented experimental mode, 2026-09-05. See [usage and validation](append-agent.md) for the actual config surface, defaults, live evidence, and remaining provider limitations. The sections below preserve the design decisions; illustrative option names are superseded by `configs/config-append.yaml`.

## Purpose

Add a selectable self-directed agent that accumulates conversation history and periodically produces a handover containing free-form text and structured memory. During gameplay it makes no memory updates. Keep the existing agent modes available.

This draft records Andreas's decisions from the design conversation and findings from the current repository. Open questions and proposed defaults are explicitly distinguished from accepted requirements.

## Accepted requirements

- Keep the current harness working and selectable.
- Append new observations and complete responses between compactions.
- Compact after a configurable number of completed game turns.
- Only the compaction step creates, edits, or deletes persistent memory.
- Compaction returns both free-form continuation text and a structured memory object.
- Return a complete replacement memory object; allow corrections and deletion.
- Distinguish observations from assumptions, and avoid repeating all memory in the prose summary.
- Use the same selected model for gameplay and compaction.
- Keep behavioral prompts and adjustable settings in YAML configs.
- Save the full conversation independently of the active compacted context.
- Validate and persist the summary and memory together before replacing active context.
- Keep `inputs`, visible `reasoning` ending in a concrete prediction, and `last_turn_succeeded` during gameplay; remove only `memory_updates` from the gameplay output.
- Keep memory as an unrestricted JSON object with suggested keys, as in the current system. Do not enforce fixed categories or typed domain records.
- During compaction, briefly review progress and revise a failing plan when supported by recent evidence. This is not a separate TaskMaster or a substantial strategy-review phase.
- Simplify the gameplay system prompt. Put memory-writing guidance, suggested keys, and compaction instructions in the configured message appended at the compaction boundary.
- Preserve the existing tracing capabilities and add per-request cache accounting and reasoning-continuity diagnostics. Expose these in the existing run/turn views, not only raw log files.

## Current implementation

The baseline for this design is `configs/config-4.0.yaml`, the self-directed single-agent mode. Official benchmark runs currently use the separate, frozen `configs/config-3.13.yaml` with TaskMaster, selected in `src/app/executor.py`. Introducing this agent does not promote it to the official leaderboard configuration.

The 4.0 user prompt supplies OCR, the full memory dictionary, a rendered log of the last 10 turns, one historic screenshot, the current screenshot, and the top goal. Its system prompt asks the player to observe, grade its last action, update memory, plan, and output an action with a concrete prediction.

The four gameplay fields are `inputs`, `reasoning`, `last_turn_succeeded`, and `memory_updates`. `src/agent/agent.py` selects the structured-output format using the model registry. The new agent needs a distinct gameplay contract without `memory_updates`.

`src/core/state.py` stores an unrestricted JSON dictionary. Categories such as `current_goal`, `current_location`, `party`, `map`, and `notes` are prompt suggestions, not enforced database fields. Memory changes currently use dot-notation updates; an empty string deletes a key.

`src/agent/turn.py` starts each call without earlier `message_history`. Its display trace serializer replaces images with placeholders and retains thinking text rather than a lossless representation of provider response blocks. This display log cannot serve as the new agent's replay store. `src/core/patches.py` also extracts plain reasoning text; transport support for complete reasoning details must be checked separately.

`src/config.py` hoists `player_agent` keys to the root at load time. Model selection comes from the CLI/model registry. Bare runs choose the highest numbered config, so creating a new numbered config can unintentionally change the default. The experiment needs explicit selection without silently changing the default.

## Proposed lifecycle

1. Start a segment with stable gameplay instructions, the top goal/mode, and the most recent handover. Start with empty memory and no handover on a fresh run.
2. Append each fresh screenshot and OCR observation once. Do not repeat a generated previous-turns block or update older messages.
3. Request a game action. Retain the complete accepted response and any required tool-result structure, including supported provider reasoning fields.
4. Execute the buttons once, wait for the screen to settle, and record the resulting observation.
5. After X completed game-action turns, compact before asking for the next action. The compactor must see the observation AFTER the last action, so it does not turn a prediction into a claimed fact.
6. Ask the same model for a handover using the previous handover, intervening conversation, and latest observation. No game buttons execute during this call.
7. Validate and durably save the new memory and prose together. Begin a new segment using that handover and the latest observation, then continue appending.

Compaction is a model call but not a game-action turn. All its costs, including failed attempts, count toward the run's spend. An early stop or achieved terminal condition should not trigger a needless final compaction.

Proposed starting cadence: 20 completed game turns, plus an earlier trigger when the next request would approach the context limit. Both are configurable. API retries do not advance the cadence.

The structured memory is a snapshot as of the last compaction. Gameplay may change goals and discover facts between compactions without writing the memory object. New observations can supersede the snapshot; the current 4.0 instruction to rewrite `current_goal` every time a goal changes must therefore be replaced.

## Compaction contract

Proposed outer shape:

```json
{
  "continuation_summary": "Recent context, unfinished work, and how to continue.",
  "memory": {}
}
```

Memory stores durable knowledge. Prose supplies recent context and continuity, including a brief revision of the plan when recent evidence shows it failed. Memory is an unrestricted JSON object. Suggested keys may include the existing `current_goal`, `current_location`, `party`, `map`, and `notes`, plus `bag`, `badges`, or other keys the agent finds useful. These are suggestions only: the agent may invent keys, change nesting, and remove outdated entries. Validate the outer handover fields and that `memory` is an object, without imposing fixed inner categories or domain field types.

Because compaction returns a full replacement object, the agent deletes an entry by omitting it from the new object. Do not carry over the old gameplay update protocol's dot-notation patch or empty-string deletion semantics.

The compactor only uses evidence already available to the player, including the same screenshots and OCR. It must not receive referee RAM, checkpoint detectors, hidden game state, walkthroughs, or another model's advice. Existing memory is model-authored knowledge, not guaranteed game truth.

Compaction replaces the full active segment. Do not transplant signed reasoning from the old segment into rewritten history. This first mode uses a text-and-memory handover, not a claim of preserving encrypted private reasoning across compaction. Preserve supported reasoning unchanged within each segment. Native compaction remains a possible separately labelled future mode.

## Prompt responsibilities

The gameplay system prompt remains unchanged throughout a segment, including the compaction request. It describes the goal, screenshot/OCR interpretation, navigation and button controls, observation-based planning, explicit predictions, and success grading. A short explanation says that a supplied handover contains older notes and that newer observations take precedence. It does not teach memory editing, list memory keys, prescribe compaction cadence, or require memory maintenance during gameplay.

Scope the gameplay output instructions to normal game-action requests. Avoid an unconditional instruction such as "always output a game action," which would conflict with the later compaction message. The system prompt can briefly acknowledge that an explicit handover request has its own output contract; all detailed handover behavior lives in that message.

The segment-start message introduces the last summary and memory snapshot once, along with the top goal and relevant last-action continuity metadata. Subsequent gameplay messages append the fresh observation, without duplicating the memory or generating a separate rolling history.

At the configured boundary, append the compaction message after the latest observed outcome. This message asks the same agent to switch from playing to preparing its handover, and contains the memory guidance and output instructions. The harness selects and validates the corresponding output contract and does not execute buttons for a compaction response.

Illustrative compaction-message text, to be authored in YAML rather than embedded in runtime code:

> Pause gameplay and prepare a handover for yourself. Your earlier conversation will be replaced by this handover before you continue. Use the previous handover, the subsequent conversation, and the latest observation.
>
> Return `continuation_summary` and `memory`. Write the complete updated memory as a flexible JSON object: preserve useful knowledge, correct mistakes, and omit outdated entries. Suggested keys are current_goal, current_location, party, map, notes, bag, and badges; use other keys or structures when helpful. Record confirmed observations as facts and label uncertainties. Do not record an intended action as a completed outcome.
>
> In the summary, explain the recent situation, unfinished work, failed attempts, and intended next steps without repeating the entire memory object. Briefly review whether your recent approach worked and revise it if the evidence calls for that. Goals in old memory are revisable; you may change goals while playing without waiting for another compaction.
>
> Preserve the last action's prediction and recent success/failure context for the next gameplay turn. Stay within the configured handover budget. Return only the handover object; do not output or execute game inputs.

This is a behavioral draft; exact wording, placeholders, and size targets will be finalized in the config. The fresh post-action screenshot/OCR and last-action metadata must still be supplied reliably by the harness rather than relying only on the compactor to remember them.

## Config surface

Names below are illustrative, not an implemented schema:

- `player_agent.agent_type`: selects existing behavior or append-and-compact behavior.
- `player_agent.system_prompt`: gameplay behavior, observation interpretation, action guidance.
- `player_agent.user_prompt`: only the new turn's observation and relevant appended metadata.
- `player_agent.segment_start_prompt`: renders the handover at a segment boundary.
- `player_agent.compaction.every_n_turns`: completed game turns between compactions.
- `player_agent.compaction.context_limit_fraction`: early compaction safety margin. The trigger estimate is in approximate tokens: the provider's last reported input count, plus ~4 bytes per token for new text, `image_token_reserve` per image, and raw reasoning counted once even when the gateway returns it twice. (An earlier byte count made verbose reasoners such as Qwen compact every turn.)
- `player_agent.compaction.prompt`: appended compaction instruction template, including flexible-memory guidance, suggested keys, brief plan review, and handover output instructions.
- `player_agent.compaction.summary_target_tokens`: bounded prose target.
- `player_agent.compaction.max_output_tokens`: response budget covering prose, memory, and model-specific reasoning needs.
- `player_agent.compaction.max_retries`: bounded compaction recovery attempts.
- `player_agent.observability.reasoning_replay_checks`: enable capture-to-request integrity checks.
- `player_agent.observability.on_reasoning_loss`: configured handling of unexpected loss, mutation, or provider-reported drops; proposed default is save and stop for a continuity experiment.
- `player_agent.caching`: provider-supported cache controls and stable session routing policy; resolve alongside model-registry settings and record the effective values.

The selected gameplay model is inherited rather than introducing a separate default compactor model. Model-specific provider and reasoning settings continue to come from the registry. Prompt variants and settings must be captured in the run's resolved config.

The implementation must check how changing output formats for the compaction request affects provider reasoning validity and cache reuse. A phase instruction should be appended to an intact conversation where possible; changing the original system prompt/tool definitions can affect both. The final API representation is an engineering choice to verify, not a settled assumption.

## Persistence, observability, and compatibility

- Persist lossless replay data with immutable image references and the exact relevant provider fields. Keep human-readable display logs as a separate representation.
- Save segment ID, completed-turn count since compaction, current handover, active conversation, resolved config, model/provider identity, emulator state, and cumulative usage consistently.
- Resume must not repeat an already executed button sequence or forget a successfully committed compaction.
- A failed compaction keeps the old context available. If retries fail and no further request fits, save and stop with a clear reason rather than silently discard history.
- Log each compaction's input range, output, memory changes, duration, retries, cost, and cache usage where returned.
- Prefer a stable provider route within a segment. Do not silently transfer opaque reasoning to another model or incompatible endpoint; record actual continuity support and failures.
- Existing saves without replay data remain valid for their existing harness. Do not pretend they are exact continuations of the new mode.
- Reuse emulator, OCR, controls, scoring, and recording behavior. Archive screenshot history even after its removal from active context.

## Trace parity and new diagnostics

The new agent must retain the existing inspection capabilities: rendered prompts, screenshots, raw/cleaned OCR, visible model thinking when supplied, action explanations and predictions, success grades, actual inputs, retries/errors, provider/model identity, timing, token usage, spend, and savepoint/run links. Existing consumers should continue to understand ordinary game turns. Memory changes occur on compaction events and should remain inspectable as before/after diffs. Compaction gets a trace showing its own request, response, validation, and committed handover.

Record individual API attempts before aggregating into turns. A turn can contain multiple calls, and retries or compaction must not disappear inside a single success record. Each attempt links to run, segment, game turn/boundary, phase (`gameplay`, `compaction`, or existing auxiliary work such as OCR), attempt number, request/response ID where available, requested model, and actual serving provider. Do not count replayed historical usage again.

### Cache accounting

For each attempt, retain raw usage and normalized input/output/reasoning tokens, cache-read tokens, cache-write tokens, actual cost, latency, and time to first token when measurable. OpenRouter exposes cache reads and writes through `usage.prompt_tokens_details.cached_tokens` and `cache_write_tokens`. It also documents session IDs for sticky routing. Provider failover can still change the endpoint. [OpenRouter caching documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching)

Display two distinct measures: the fraction of reported requests with any cache reads, and the fraction of their input tokens read from cache. Compute the latter from summed token counts, not an average of per-request percentages. Normalize each provider's input accounting before calculating fractions; some native APIs separate ordinary input, cache reads, and writes. Missing counters mean unknown, not zero. Show measurement coverage and calculate ratios only over attempts with the necessary counters.

Break down totals by gameplay/compaction, provider, and segment. Label segment starts and post-compaction requests so expected cache rebuilding is visible. Record changes to system instructions, schemas/tools, images, reasoning settings, routing, and elapsed time as diagnostic evidence. A changed local prefix is observable; cache expiry or a provider-internal miss reason is only a hypothesis unless explicitly reported. An unchanged prefix does not guarantee a hit.

Use provider-reported cache discounts when available. Any calculated savings must be labelled estimates based on the recorded rate and include cache-write premiums; a cache-hit percentage is not a dollar-savings percentage.

### Reasoning continuity

A reasoning signature is not a cache entry with a universal hit counter. We can verify capture and replay locally; server acceptance and actual use are separate questions. OpenRouter represents reasoning using structured details that can include text, summaries, and encrypted blocks. Preserve the complete returned representation rather than just its display text. [OpenRouter reasoning documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)

For each response, save a manifest of reasoning/signature blocks: format, source response, block ID/index when supplied, associated message/content part, content length, and digest of the exact opaque value. Before the next request is sent, compare the expected retained blocks to the FINAL serialized outbound request after SDK conversion. Check values, ordering, attachments, and the preceding conversation structure. Instrument streaming assembly as well as non-streaming responses. Comparing only in-memory history would miss a serializer stripping fields.

Store complete blocks in the replay artifact; show counts, identifiers, and integrity results in the ordinary trace. A matching digest proves local preservation, not cryptographic validity or upstream consumption. OpenRouter may transform the request after our client sends it, which remains outside local verification.

Keep separate evidence fields rather than one misleading "thinking hit" flag:

| Evidence | Possible states |
| --- | --- |
| Returned reasoning state | Present, absent, unsupported, unknown |
| Local replay | Intact, missing, modified/reordered, not applicable |
| Preceding history | Unchanged locally, changed, unknown |
| API request | Accepted, rejected, failed before acceptance, unknown |
| Provider reasoning feedback | Explicit drop/rejection, explicit validation result if supplied, not reported |
| Context transition | Same segment, intentional compaction reset, unexpected loss |

Capture provider transformation/drop metadata and signature errors where the endpoint exposes them. For example, supported Claude models can report dropped thinking blocks in `input_transformations` with the relevant beta controls; OpenRouter passthrough of that metadata needs verification. Gemini's legacy Generate Content API does not validate all historical signatures, so a successful response alone cannot establish preservation. [Claude preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking), [Gemini signature validation](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)

Neither generated reasoning-token counts nor a prompt-cache hit prove that prior private reasoning was used. Do not ask the model to certify its own hidden-state continuity. An absent signature on a model that returns no such field is not automatically a client failure; distinguish text-only reasoning, unsupported capabilities, first requests, and unexpected loss of previously captured state.

Custom compaction intentionally resets the old private reasoning chain. Mark that boundary explicitly; the next segment starts from the text summary and memory. Keep the compaction response's own reasoning in the archive, without assuming it is transferable into the rewritten segment.

Proposed strict experiment behavior: save and stop on unexpected local replay loss or provider-reported invalidation instead of silently stripping reasoning, disabling thinking, or changing models. Unknown upstream usage remains unknown, not a failure by itself. A configurable permissive mode may continue with a prominently recorded discontinuity. Where supported and verified, use native validation controls to reject invalid blocks; never use fabricated signatures to bypass validation.

Example trace labels (illustrative only): `Cache: 82% of reported input tokens read from cache`; `Reasoning: 19/19 expected blocks replayed intact; request accepted; provider usage not reported`; `Boundary: custom compaction, private reasoning reset expected`.

## Design decisions confirmed by Andreas

1. Keep visible reasoning, next-screen prediction, and previous-turn success grading during gameplay.
2. Preserve the current flexible memory dictionary with suggested keys only; do not introduce fixed categories.
3. Let compaction briefly review progress and revise a failing plan.
4. Move memory-writing and compaction behavior out of the gameplay system prompt and into the message sent at the configured compaction turn.
5. Preserve current tracing capabilities and add cache accounting plus evidence of reasoning/signature capture, replay, and provider feedback where available.

## Remaining proposed defaults

- Preserve current navigation, OCR, and benchmark/freeplay guidance while rewriting context and memory instructions.
- Retain all segment screenshots as originally sent until compaction; attach each new screenshot only once. Compaction starts a fresh segment with the latest observation.
- Explicitly carry the last action's prediction and recent grades across the boundary, so turn 21 can grade turn 20 and detect repeated failures. This is continuity metadata, not a transplant of earlier signed reasoning blocks.
- Begin with 20 turns per segment and a configurable summary/memory budget; settle concrete size defaults before implementation.
- First version targets OpenRouter custom compaction. Native compaction is a later, separately measured option.

## Verification requirements for a later implementation

Verify that old configs keep their old behavior; new gameplay outputs never contain memory writes; history and reasoning fields survive replay within a segment; compaction happens after the configured number of completed actions; summaries include the final action's observed outcome; invalid summaries leave prior state intact; and resume restores both pre-compaction and post-compaction saves without duplicate actions. Verify provider support with small explicitly selected runs before claiming reasoning continuity or cache savings.

For tracing, check existing run/turn views against a representative recorded run, including screenshots, reasoning, retries, and memory changes at compaction. Test cache counters with actual zeros, missing fields, writes, reads, and multiple attempts; verify aggregate coverage and no historical double-counting. Deliberately strip, modify, reorder, or detach a reasoning block in an offline transport fixture and ensure final-request diagnostics detect it. Verify provider-reported drops and planned compaction resets have different statuses. Test both streaming and non-streaming capture. A later live capability check should exercise normal append, compaction, and resume on the exact selected model/provider route; record whether upstream validation metadata is available rather than claiming generic support.

Implementation and live smoke-test evidence are recorded in [append-agent.md](append-agent.md). The experiment remains explicitly selected; existing defaults and the official benchmark configuration are unchanged.
