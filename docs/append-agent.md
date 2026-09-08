# Append-and-compact agent

**This is the standard harness.** It lives in `configs/config-5.0.yaml` (renamed from the experimental `config-append.yaml` on 2026-09-07) and is now BOTH the casual default and the official benchmark config, so you get it without asking:

```bash
# no --config: `find_latest_config()` picks the highest config-X.Y, which is config-5.0
./venv/bin/pokemon run --model 'gpt-5.6-sol(medium)' --turns 30 --max-spend 1 \
  --snapshot configs/saves/pokebench-v1
```

Name it explicitly when you want to be unambiguous in a script:

```bash
./venv/bin/pokemon run --config configs/config-5.0.yaml \
  --model 'gpt-5.6-sol(medium)' --turns 30 --max-spend 1 \
  --snapshot configs/saves/pokebench-v1
```

If the control center already owns mGBA, use its queue rather than starting a second emulator runner (omit `--config` for the default):

```bash
./venv/bin/pokemon queue add 'gpt-5.6-sol(medium)' --kind casual \
  --max-turns 30 --max-spend 1
```

config-5.0 is **frozen**: it is what official runs are scored on, so a scored run has to stay reproducible. Changes go to a new `config-5.1+`, which becomes the casual default on its own (highest `config-X.Y` wins) and is promoted to official by moving `executor.OFFICIAL_CONFIG`. The leaderboard ranks config-5.x runs only, so a 5.1 joins the same board.

Legacy configs still run. `config-4.0` (self-directed, sliding window) and `config-3.13` (the old official config, TaskMaster-enabled) both load, list in `pokemon ls configs` and the dialog's config picker, queue as casual runs, and continue. What they lost is default status and leaderboard eligibility — `turns` is the ranking tiebreak and it counts game turns PLUS TaskMaster invocations on those harnesses versus game turns only here, so the board is partitioned on the config stem rather than mixing two units. Finished official config-3.13 runs keep their badge and their scorecard in History.

## Behavior and configuration

All behavioral prompts and adjustable limits are authored under `player_agent` in `configs/config-5.0.yaml`; model selection, reasoning effort, sampling, and provider preferences still use `configs/models.yaml` and `--model`.

Four prompts are required, and `_validate_append_config` refuses a config missing any of them:

| Key | When it is sent |
| --- | --- |
| `system_prompt` | Once per segment, at the segment's first turn. |
| `user_prompt` | Every gameplay turn — turn number, new OCR text, current screenshot. |
| `segment_start_prompt` | The first turn of a segment after a compaction: goal, continuation summary, memory, last action (and, in config-5.0 only, the last three grades). |
| `action_prompt` | Split-turn profiles only (`final_turn_text_only: true`, e.g. the `openai` tag): the screenshot turn is closed with a one-word assistant acknowledgement and the request ENDS with this text-only user message. It exists because OpenAI cannot extend the prompt cache past the first image when the final turn carries one. On every other profile `user_prompt` is the last message and `action_prompt` is never sent. |

`action_prompt` is required unconditionally rather than only for split-turn profiles: which profile a run gets is decided by the model, one layer below the config, so a config that validates for one model and silently falls back for another is the worse contract. `{{turn_number}}` is the one placeholder it takes.

Normal turns append a turn number, the new OCR text, the current screenshot, and a short action request. The agent returns `inputs`, visible `reasoning` ending in a prediction, and `last_turn_succeeded`. It does not write memory on gameplay turns. Earlier screenshots and complete responses remain in the conversation until compaction. **config-5.1 (2026-09-08):** the gameplay output is `inputs` and `reasoning` only (`player_agent.self_grade: false`); the comparison with the previous prediction and the next prediction live in the reasoning, and the trace carries no `last_turn_succeeded` key for such runs. config-5.0 keeps the graded output.

At the boundary, the same model sees the observed outcome of the last action and receives the configured compaction message. It returns `continuation_summary` and a complete replacement `memory` object. The memory dictionary has suggested keys only; the model may add keys, correct entries, and delete entries by omitting them. Both outputs are validated and committed together, and the next segment starts with the handover and current observation. Last-action reasoning and recent success grades are carried as ordinary continuity metadata.

Defaults:

| Setting | Default |
| --- | --- |
| Compaction interval | 20 completed game turns (10 was tried on 2026-09-07; Gemini implicit caching needs ~16k-token prompts, which a 10-turn segment never reaches) |
| Context limit / early threshold | 131,072 tokens / 65%, with a conservative next-request reserve |
| Summary target | 1,500 tokens (prompt target) |
| Full handover size cap | 24,000 JSON characters |
| Prompt-token cap | 200,000 estimated tokens (safety valve above any normal segment; the context-fraction trigger is out of reach once a profile sets a 1M context) |
| Compaction output budget | 12,288 tokens |
| Gameplay output budget | Model-registry override, otherwise 8,192 tokens |
| Retry budget | Two retries per request phase |
| Request timeout | 360 seconds |
| Transport | OpenRouter Chat Completions, non-streaming by default |

The early context estimate uses returned input usage plus a conservative reserve for new content, the next image, and compaction output. It is not an exact provider tokenizer. Adjust the context limit and image reserve for the selected model. An unusually large response/observation may still exceed the provider's limit; the agent saves and stops rather than silently truncating history. No additional model fallback or reasoning-disable retry is performed by this mode.

Compaction requests count toward cost and latency, not game-action turns. A compaction after turn 20 runs only when another gameplay turn is about to start; a run ending at turn 20 does not pay for an unused handover. Normal spend checks apply before requests, including compaction retries. As with the existing harness, the final paid request may exceed a spend ceiling.

The default non-streaming transport avoids ambiguous endpoint-specific delta assembly while preserving full returned reasoning blocks. Optional `transport.stream: true` supports indexed reasoning/tool-call deltas and archives raw chunks; ambiguous reasoning chunks stop with an explicit error instead of guessing. Streaming has offline coverage but has not yet been live-verified across providers.

## Tracing and continuity

The existing turn report, screenshots, OCR, explanations, action display, success grades, and live events continue to work. Expand a turn in the report for **Conversation, cache & compaction**. Compaction events include the complete display trace, handover text, and memory before/after.

The gameplay trace shows additions to the conversation: the system prompt and initial context/handover appear only at each segment's first turn. Later turns show the new observation and response, without repeating earlier history in the Input panel. The transport still sends the retained conversation with every request; this display choice does not change model inputs or caching. A cached `trace.json` is only reused while it carries the current `TRACE_VERSION` (`src/app/trace_build.py`), so a report built under an older layout is rebuilt on first open — but only if that layout change came with a version bump, which is what retires the caches.

Segment-opening turns are labeled `(fresh)`. Compactions have their own numbered rows between gameplay turns, with their request usage and handover diagnostics. For an interval of five: Turn 1 (fresh), Turns 2–5, Compaction 1, Turn 6 (fresh). Compaction rows do not increase the gameplay turn count.

Compaction traces use the same Input → Thinking → Output cards as gameplay. Input contains the newly appended observation and compaction instruction; Output presents the validated `continuation_summary` and `memory` fields, with expandable structured JSON and previous memory. The trace API exposes this handover as a JSON object under `trace.output`.

Per-request diagnostics record actual provider, input/output/reasoning usage, cache reads/writes, cost, latency, retry identity, and reasoning replay checks. Cache totals show measurement coverage, token-weighted read fraction, and breakdowns by phase/provider/segment. Missing provider counters remain unknown.

Lossless requests/responses are stored under `conversation/` in the run folder. Image data URLs are replaced on disk by references to immutable, digest-checked assets in `conversation/assets/`; the full original image data is reconstructed for requests. Human-readable trace projections are separate from replay data.

Each final serialized request is checked against the retained conversation, including reasoning fields, order, tool associations, and preceding history. This establishes what our client sent, not whether OpenRouter or the underlying provider used the private reasoning. Provider transformation/drop metadata is recorded where exposed. Strict mode stops on detected replay corruption, reported drops, or a provider change while reasoning is being replayed; it does not fabricate signatures or strip reasoning to force acceptance.

Custom compaction intentionally resets the earlier private reasoning chain. The text summary and memory survive; old encrypted reasoning is archived, not transplanted into rewritten history. Native provider compaction is not implemented in this mode.

Anthropic-prefixed model IDs receive configurable ephemeral caching. Other providers use their implicit caching unless additional supported controls are configured. A stable OpenRouter session ID is reused, but neither sticky routing nor unchanged input guarantees a cache hit.

## Resume

```bash
./venv/bin/pokemon run --continue local/runs/<run-directory> --turns 20
```

Savepoints include `append_state.json` in the existing sealed emulator bundle. Continue copies the replay assets and restores the matching conversation, handover, segment count, last action, and session ID. A committed compaction can be resumed before the next game action without repeating the compaction. A pending model response is not committed as an executed action until button execution and screen settling finish.

If execution fails after some buttons may have been pressed, no uncertain emulator/conversation pair is saved; resume from the prior complete savepoint. Old harness savepoints continue to work for their own mode, but are not silently treated as exact append-agent continuations.

## Validation evidence

On 2026-09-05, GPT-5.6 Sol (medium) played three live FireRed turns with compaction every two turns, then resumed in a new process for turns four and five. Both compactions used the latest observed screen. Total cost including OCR was $0.065203. The seven gameplay/compaction requests reported cache usage; 52.6% of their input tokens were cache reads. This is a short integration check, not a gameplay-quality or cost benchmark.

Run folders:

- `local/runs/2026-09-05_23-44-21_append-smoke__gpt-5-6-sol-medium`
- `local/runs/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3`

Open the latter report and expand turn five to inspect compaction, memory replacement, cache accounting, and the expected private-reasoning reset. Browser inspection also caught and fixed stale action headers caused by replayed historical responses being counted as new output.

Live validation currently covers OpenAI via OpenRouter with non-streaming tool output. Other provider routes and streaming require their own capability checks before asserting private-reasoning continuity. Native JSON and prompted-output modes, streaming assembly, failed compaction, corrupted replay, state restoration, cache accounting, and the integrated emulator commit flow have offline tests in `tests/test_append_agent.py`.
# Provider profiles

Gemma has two named profiles for comparing reasoning replay:

| Profile | Previous raw thoughts | Other settings |
|---|---|---|
| `gemma-guidance` | Omitted between user turns | Same base Gemma settings |
| `gemma-replay` | Replayed unchanged until compaction | Same base Gemma settings |

Select one using `--provider-profile gemma-guidance` or `--provider-profile gemma-replay`
with `--config configs/config-5.0.yaml --model google/gemma-4-31b-it`.
Alternatively set `player_agent.provider_profiles.name` in your config. Omitting
the selection keeps the existing Gemma behavior (omit prior thoughts). The selected
name appears in run filenames, labels, saved checkpoints, and trace technical details.
Resume preserves it; comparing the other profile requires a new run.

Both profiles enable thinking and retain the visible action explanation, observations,
and the same compaction/memory prompts. Replay is experimental: request acceptance
does not establish that the serving provider used the returned raw thoughts.

The append config automatically selects a profile for each of the ten models in
`configs/provider-profiles.yaml`. Use the exact OpenRouter ID, for example:

```sh
./venv/bin/pokemon run --config configs/config-5.0.yaml --model google/gemini-3.8-flash --turns 20
```

All settings and transport prompts remain in YAML. `player_agent.provider_profiles`
selects the profile file and optional `overrides` for a new experiment. The resolved
profile is frozen in saved run config; changing it on resume is rejected. Raw IDs
use the profile's reasoning default, while existing aliases retain their selected
reasoning level if supported by the profile. Unlisted models use the old transport
settings; numbered configs are unaffected.

Profiles pin the serving route and disable fallback. A provider outage can therefore
stop a run instead of changing its model implementation. Cache hits are measured,
not guaranteed. See [provider compatibility](openrouter-provider-compatibility.md)
and the live protocol results in `artifacts/provider-compatibility/probe-results.md`.

To reproduce the small paid protocol probe without running an emulator:

```sh
./venv/bin/python -m test_scripts.probe_provider_profiles --image path/to/game.png --model google/gemini-3.8-flash
```

It uses three recorded-screen action requests, one compaction, and a disk checkpoint
reload. Requests use the profile's default reasoning, a 4,096-token output cap, and
no retries. Results, raw requests/responses, config and endpoint snapshots are saved
under `local/provider-profile-probes`. This checks protocol compatibility, not Pokémon
performance, long-context reliability, streaming, or internal reasoning use.
