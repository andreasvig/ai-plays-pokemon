# Frontend review: making the append harness the standard

> Analysis only, 2026-09-07. No code changed. Four read-only audits (trace viewer, live view, run starter, cross-cutting seams) plus a browser walk of the add-run dialog on the :3420 review server. Every claim below was verified at file:line by the auditor; the ones marked ✔ were re-verified by hand.

## Where we are

- **Two harnesses share one run loop.** `src/agent/turn.py` owns screenshot, OCR, buttons, settle, referee, savepoints for both. `agent_type: current` (config-4.0, config-3.13) swaps in the legacy pydantic-ai agent; `agent_type: append_compact` (config-append) swaps in `AppendAgent`. Only the decision call differs.
- **Append is the better system on the evidence.** The 25-turn comparison (`artifacts/system-comparison/findings.md`) showed it faster on all three models and cheaper wherever whole-prefix caching exists (GLM, OpenAI with `final_turn_text_only`). Gemini is the exception (+23%, system-block-only caching).
- **"Standard" today means config-4.0 for casual runs and config-3.13 for official/leaderboard runs.** Neither is a constant. The casual default is "highest `config-N.M.yaml`", implemented by two independent regexes, and `config-append` is deliberately excluded from that rule in three places. The official config is one constant, `executor.OFFICIAL_CONFIG`.
- **The UI keys on capabilities, not harness.** It branches on `task_master`, `run.kind`, `referee.ladder` and on the presence of append-only events. No component reads a config name. That is the healthy part and the reason the append work displays as well as it does.
- **Harness identity is written but never read.** `run_summary.json["agent_type"]` has zero readers ✔. It is absent from `RunSummary`, the index, History, the leaderboard and every guard that would need it.

## Refuted hypotheses (so we don't chase them)

- The live view does **not** consume a legacy-only vocabulary. Spectate already handles `compaction_start/complete/thinking`, `llm_request_usage`, `llm_request_error`. The defects are labels and coverage holes, not missing events.
- Spend accounting **does** include compaction and retries (`on_usage` fires per accepted request; `cost_usd` populated on all 26 Astra requests ✔). The spend ceiling is live mid-turn.
- Nothing counts compaction requests as game turns.
- `final_turn_text_only` is **not** a bug farm: 12 of 13 message-list readers handle the "Observed." ack correctly, most by construction.
- `configs/benchmarks.yaml` does **not** encode a config. Swapping the official config is one constant.
- Run-folder readers do **not** assume the legacy shape; append overwrites `session.total_turns/player_turns` so projections read correctly. The problem is the reverse: append writes more and nothing reads it.
- Cross-harness `--continue` is impossible in both directions, but by structure (continue never re-reads a config file), not by check.

## Findings, ranked by user-visible impact

### Tier 1 — wrong today, on every append run

| # | Finding | Where | Class |
|---|---|---|---|
| 1 | **Stale `trace.json` under an unbumped `TRACE_VERSION = 5`.** Introduced in f9a30a0, never bumped ✔, while the builder gained implied-cache economics (fe1e7a7) and the split-turn fold (5b8ab07). 13 runs on disk serve a pre-fe1e7a7 projection: Cache overview says "No pricing snapshot" / "Unknown" for runs whose `endpoint-pricing.json` is on disk. No user-reachable rebuild. `docs/append-agent.md:52` ("caches rebuild automatically") is false. | `trace_build.py:20`, `server.py:1460` | BUG |
| 2 | **Compaction and per-request diagnostics render under "Screen settling".** `llm_request_usage` and `compaction_start` are pushed as box kind `settle`, whose label is the literal `Screen settling` ✔. The defining behaviour of the harness is mislabelled as an emulator wait on every live turn. | `Spectate.svelte:376,379`, `TraceFeed.svelte:19` | BUG |
| 3 | **Every append run shows a benchmark Completion % and a full gate scorecard with deadlines** it was never scored against (referee is observe-only, `enforce: false`). History shows `—` for the same run. Astra 25-turn run reads "50%". | `Report.svelte:263, 279-296` vs `History.svelte:110-115` | BUG |
| 4 | **Gate HUD never renders for stop-at runs.** `showGates = run.kind !== 'casual'` ✔, but stop-at is casual-only and `executor._apply_stop_at`'s docstring promises "the live gate HUD" ✔. 100% incidence on append because append is structurally casual. | `Spectate.svelte:161`, `executor.py:501` | BUG |
| 5 | **Dialog can queue an append run that dies at dispatch with no error shown.** Thinking-level dropdown is the registry's full list, unfiltered by the profile's `reasoning_efforts`; `kimi-k3(xhigh)` + config-append raises `ValueError` inside `build_run_config` after the card went active. `last_error` is served by `/api/queue` but no component reads it ✔. Card flashes running, then vanishes. | `AddRunDialog.svelte:204,367-376`, `provider_profiles.py:84-85`, `api.js:181` | BUG ×2 |
| 6 | **Model-swap continue of an append run returns 201, then dies.** Casual continue accepts `player_model` with no append guard ✔; `_resolve_player_model` rewrites `llm_model` but `_provider_profile` stays the old model's, and `AppendAgent.restore` raises "checkpoint does not match model/game turn" (wrong cause) after the run dir, dashboard session and recorder were created. Same "201 then vanished" failure that `test_control_routes.py:310-316` was written to kill. | `server.py:1127-1131`, `executor.py:263-269`, `append_agent.py:437-438,458` | BUG |
| 7 | **Home active-run card shows `turn 0` / `turn undefined`.** `active.currentTurn` exists only in `mockData.js` ✔; no route emits it. | `QueueBar.svelte:44`, `QueuePanel.svelte:27` | BUG (mock rot) |
| 8 | **`ago()` hardcodes 2026-06-15 as "now"** ✔, so every recent run reads "(today)" in History. | `format.js:19` | BUG |

### Tier 2 — the default experience is unsafe or invisible

| # | Finding | Where | Class |
|---|---|---|---|
| 9 | **Profile `defaults:` block is unreachable for 37 of 43 registry models.** `resolve_provider_profile` returns at line 66 *before* line 68 builds from defaults ✔. Unprofiled append runs get no endpoint pin, **no `transforms: []` middle-out guard**, no `cache_mode`, no `final_turn_text_only`, context stuck at 131,072, output capped at 8,192, and a weaker provider-drift guard. Includes gpt-5.6-sol. Two config-append runs with radically different transport contracts look identical in the queue card, run name and History. | `provider_profiles.py:66-68`, `append_agent.py:574-601,736-738` | GAP, borderline BUG |
| 10 | **No profile axis on the queue path.** `QueuedRun`, `enqueue()`, both executor `prepare_config` calls and `pokemon queue add` have no `provider_profile`. All five named Gemma variants are control-center-unreachable; the documented A/B only runs via bare `pokemon run`, which fights the control center for mGBA. Unknown spec keys are silently dropped by `_enqueue_kwargs`. | `models.py:129-187`, `executor.py:284,320`, `server.py:922-1002` | GAP |
| 11 | **Registry pin and profile endpoint can disagree, profile silently wins.** gemma-4-31b: registry `provider: {sort: throughput}` ✔ vs profile `endpoint: deepinfra/turbo` ✔. Sampling hand-duplicated in both files. No validator compares them. The three new pins (Astra, Gemini 3.8, GLM 5.3) agree today by coincidence. | `models.yaml:172-173`, `provider-profiles.yaml:107,115`, `append_agent.py:574-576` | BUG (latent) |
| 12 | **Append runs can never reach the leaderboard, and split the same model into two rows.** Official is pinned to config-3.13; `leaderboard_eligible` requires official. Separately, `session.llm_alias` is `null` on every append run on disk ✔ (queue path unverified), so they group under raw OpenRouter ids while legacy groups under aliases. | `executor.py:42`, `models.py:116-126`, `projection.py:114`, `turn.py:2774` | LEGACY-ONLY + GAP |
| 13 | **`RunSummary.turns` mixes two units.** Legacy: game + TaskMaster turns. Append: game turns only. Drives the leaderboard tiebreak, cost/turn, s/turn, both scatter charts. Becomes a live bug the moment append and legacy runs are ranked together. | `turn.py:2785` vs `:2849`, `derivations.py:39,47`, `projection.py:121-122` | hazard |
| 14 | **Simple view and recorder are blind to compaction.** SimpleView handles only `turn_start`, `llm_output`, `screen_settled`; a compaction is a multi-minute "thinking" dot cycle with the screen paused. `realtime` recordings freeze; `cut-thinking` silently omits every compaction; neither is documented. | `SimpleView.svelte:236-257`, `docs/recording.md:78-93` | GAP |
| 15 | **Memory panel is a full-width `(empty)` for the first compaction interval** (20 turns by default). The code comment at `Spectate.svelte:43-48` says the goal panel was removed because memory shows `current_goal`; true for config-4.0's per-turn edits, false for append until the first compaction. | `Spectate.svelte:43-48,615,630-635` | GAP |
| 16 | **No spend-ceiling denominator live; `budget_exhausted` renders nothing.** `/runs/{id}/api/config` omits `max_spend_usd`; the cap shows only on the queued card, which disappears once active. | `server.py:336-352`, `Spectate.svelte:591` | GAP |

### Tier 3 — gaps, drift and small fixes

| # | Finding | Where |
|---|---|---|
| 17 | `reachedN` counts only `done`; projection clears `done` and `auto` ✔. Header and Completion KPI disagree whenever a gate is `auto`. | `Report.svelte:41` vs `projection.py:33` |
| 18 | `action_prompt` has no declaration site: read at one line, absent from config-append.yaml, `_validate_append_config`, docs and tests. On split profiles it is the message that ends every request. `docs/append-agent.md:21` claims all prompts are authored in YAML. | `append_agent.py:498`, `config.py:493-495` |
| 19 | Data computed and never rendered: `compaction_count`, `max_turns` (written to summary, absent from `RunSummary`), `trace.user_messages`, `summary.agent_type`/`conversation`, an uncommitted compaction's proposed handover ("No committed compaction output" hides it). The Report never states which harness ran. | `trace_build.py:287`, `models.py:91-113`, `Report.svelte:496-508` |
| 20 | Duplicate Memory box at every compaction (one from `compaction_complete`, one from `memory_update_output`). | `Spectate.svelte:380,405-412` |
| 21 | `endpoint_warning` never reaches the browser; only `terminal.log`. | `append_agent.py:662`, `Spectate.svelte:449` |
| 22 | Thinking box silently absent when reasoning is opaque (`reasoning.encrypted` has no text); Astra medium reports 0 reasoning tokens on ~90% of turns. Collapsed-turn preview renders blank. | `append_agent.py:326-330,776-778`, `TraceFeed.svelte:96` |
| 23 | `turn_trace` re-broadcasts the whole segment every turn; `EventBridge._events` never pruned; Spectate ignores `turn_trace`. O(n²) on the wire, replayed in full on reconnect. | `append_agent.py:772`, `event_bridge.py:14`, `server.py:386` |
| 24 | Per-turn cost row is last-`turn_usage`-wins; a retried turn under-reports. Segment totals are computed independently and are right. | `event_parsing.py:101-102` |
| 25 | "n/a (first turn)" asserted for any null self-grade; null is legal on any turn. Latent, clean across 49 runs. | `Report.svelte:230-234` |
| 26 | "TaskMaster trace (0 tool calls)" on every legacy task node. | `Report.svelte:357` |
| 27 | "Casual run — no TaskMaster" heading on every append run. Will read as wrong once append is standard. | `Report.svelte:455` |
| 28 | Half the profile catalog (5 profiles) has no registry entry, so the dialog cannot reach it; profile `claude-fable-5.1` vs registry `claude-fable-5`. | `provider-profiles.yaml:75-105` |
| 29 | Compaction interval (20) invisible and unexposed; dialog default 100 turns, but a `max_turns < 20` append run never compacts. | `config-append.yaml:143`, `AddRunDialog.svelte:446` |
| 30 | Official label `config-3.13 (frozen…)` hard-coded as UI text, duplicating `OFFICIAL_CONFIG`; an official spec's `config` is dropped silently. | `AddRunDialog.svelte:401`, `server.py:949-954` |
| 31 | Queue-dispatched append runs never carry a profile marker in `run_name` (suffix requires a *named* variant). | `runner.py:518-521` |
| 32 | `[-2:]` growth window in the compaction trigger is position-based and ack-unaware. Numerically benign today (worked both ways), fragile under any future shape change, untested. | `append_agent.py:509-513` |
| 33 | Projection's 20-gate fallback ladder fires when a run's recorded absolute ladder path doesn't resolve on another checkout, changing Completion % silently. Pinned by `test_app_persistence.py:165`. | `projection.py:164-171` |
| 34 | Doc drift: `docs/append-agent.md:76` ("own mode") has no check behind it; `continue_from_run` docstrings omit `conversation/` (code copies it correctly); legacy `max_turns_before_trim`/`historic_images_count` still validated for append configs. | `runner.py:935,1012,1027`, `config.py:451-463` |

## What "make append the standard" touches

**Executable sites encoding the casual default (7):** `config.py:22` regex, `config.py:207-222` `find_latest_config`, `config.py:275-276`, `runner.py:1094`, `catalog.py:109` regex, `catalog.py:131-133` (`insert(0, "config-append")` so that `[-1]` stays config-4.0), `server.py:879-886` (`known[-1]`), plus `AddRunDialog.svelte:31-55` `latestConfig()` which structurally cannot return a non-numeric stem. Sites 6 and 7 are coupled and must move in opposite directions.

**Tests that invert (3):** `test_self_directed_config.py:55-57`, `test_append_agent.py:211-214` (named `…does_not_replace_default`), `test_control_routes.py:318-326`. About 20 more files use config-3.13/4.0 as fixtures and survive.

**Doc/text sites (~8):** `CLAUDE.md:95-98`, `docs/append-agent.md:18`, `docs/cli.md:62,100,273`, `src/cli/ls.py:184`, `README.md:257,291`, `configs/config-4.0.yaml:29-33` "NOTE ON DEFAULTING", help strings in `cli/queue.py:293,328` and `cli/runner.py:1095`.

**Official config:** one constant (`executor.py:42`) plus the UI literal at `AddRunDialog.svelte:401`. Whether to move it is a separate decision (see below).

**What breaks for legacy if the append rendering path becomes unconditional:** the `if diagnostics:` gate at `trace_build.py:64` is the de-facto harness discriminator. Made unconditional, every legacy turn becomes "(fresh)" (legacy traces carry one user message) and the System Prompt panel can vanish on retried turns. Dropping `memory_updates` rows loses real per-turn edits (10 of 95 turns on one run). Dropping TaskMaster grouping removes the strategy layer of every current leaderboard run. So the gate stays, but it should key on a real harness field, not on event presence.

## Decisions for Andreas

**D1. What does "standard" mean for the leaderboard?**
- (a) Casual default only. `config-append` becomes what bare `pokemon run` and the dialog pick; official stays config-3.13 and the leaderboard keeps its "same harness" tagline. Zero leaderboard risk, but append runs remain permanently ineligible and the two-turn-units hazard (#13) never surfaces.
- (b) Casual default now, official later. Same as (a) plus a harness column in the index and `RunSummary` so a future official flip is a one-constant change with a visible break in the table.
- (c) Flip official too. Requires resolving #13 (turn units), #12 (alias grouping), a new frozen `config-append-X.Y`, and a leaderboard reset or a harness partition. Big.
- (d) Keep config-4.0 default; only fix the bugs. Removes the "standard" question entirely.

**D2. Unprofiled models on append (#9).**
- (a) Apply the `defaults:` block to every model (move the early return). Every append run gets `transforms: []`, cache mode, drift guard. Endpoint stays unpinned unless the registry has a `provider:` block. Recommended.
- (b) Refuse unprofiled models on append (loud failure at enqueue). Safe but 37 models disappear from the dialog for append.
- (c) Leave as is and badge unprofiled models in the dialog.

**D3. Registry pin vs profile endpoint (#11).**
- (a) One source of truth: the profile owns `endpoint`, the registry `provider:` block is deleted for profiled models and a validator refuses both being set. Recommended.
- (b) Validator that refuses disagreement but keeps both.
- (c) Profile inherits sampling and provider from the registry when absent (removes the hand-duplication).

**D4. Profile axis in the dialog (#10).**
- (a) Auto-select the model's base profile, expose named variants in an "advanced" dropdown that appears only when config-append is chosen; filter thinking levels by `reasoning_efforts`; add `/api/profiles`. Recommended.
- (b) Profiles stay CLI-only; the dialog only filters levels and shows `last_error`.

## Proposed sequence (if authorised)

1. **Stop the lying, no behaviour change.** #1 bump `TRACE_VERSION` and add a test that pins it to the builder contract; #2 new box kinds `compaction`/`diag`; #3 gate the Completion tile and scorecard on `referee.enforce`; #4 `showGates` on ladder presence; #7 wire a real `current_turn` from the bridge; #8 `Date.now()`; #17 shared cleared-status set; #20, #26, #27 labels. One PR, all in the viewer, verifiable with the existing walk scripts.
2. **Make dispatch failures visible and prevented.** #5 render `last_error`; validate effort against the profile at enqueue (needs `/api/profiles`); #6 400 on model-swap continue when the source `agent_type` is `append_compact`. Needs `agent_type` in `RunSummary` first (#19).
3. **Safe defaults for append.** D2 + D3 + #18 declare and validate `action_prompt` + #29 expose compaction interval or warn when `max_turns < every_n_turns`.
4. **Flip the casual default (D1 a or b).** All 7 code sites, 3 tests, ~8 docs in one commit. Add a `DEFAULT_CASUAL_CONFIG` constant so it never fans out again.
5. **Live view coverage.** #14 SimpleView compaction phase + recorder note; #15 seed memory panel from the handover at segment start or show "first compaction at turn N"; #16 send `max_spend_usd` and render `budget_exhausted`; #21, #22, #23.
6. **Leaderboard readiness (only under D1 c).** #12 alias stamping, #13 turn-unit partition.

## Not verified

- Nothing was run live: no emulator, no dispatch, no test suite. All dispatch-failure paths (#5, #6) are traced statically; the "card flashes then vanishes" UX is inferred from `drain_once`'s `finally`.
- Whether queue-launched append runs also leave `llm_alias` null (all 49 on disk came from `test_scripts/` via `prepare_config`).
- Whether OpenRouter middle-out compression actually fires on any unprofiled model today (#9). The absence of the guard is certain; the consequence is inferred from the code comment.
- Whether `_compaction_trace` on a failed compaction really hides the proposed handover (#19); no such run exists on disk.
- Whether the image cache rule is OpenAI's or OpenRouter's (carried over from the comparison; needs a direct key).

## Decisions taken 2026-09-07

- **D1 → full flip.** The append harness becomes `configs/config-5.0.yaml` (config-append renamed, frozen) and is the default for casual AND official runs. The leaderboard becomes the **first-badge** leaderboard and shows only config-5.x runs (Q1 a). Legacy official runs stay in History with their badge and leave the leaderboard. Turn units therefore never mix.
- **D2 → prune, then profile by family.** Registry is cut to newest versions per family (gpt-5.6 explicitly kept), then every remaining model gets a profile; one representative per family is probed, siblings copy the profile with a note naming the representative.
- **D3 → profile owns the endpoint.** Registry `provider:` blocks are removed for profiled models; a validator refuses both being set.
- **D4 → dialog gets the profile axis.** Base profile auto-selected, named variants in an advanced control shown only for 5.x configs, thinking levels filtered by the profile, new `/api/profiles`.
- **Q3 → base profile only is official-eligible.** Variants (gemma-guidance, gemma-replay, …) remain casual. Note: the replay variant was NOT removed from `provider-profiles.yaml` (all five Gemma variants still exist); the base Gemma profile already defaults to `omit_prior`, i.e. guidance behaviour, and the ten-turn samples showed DeepInfra does not consume replayed reasoning.
- **Thinking levels stay a leaderboard dimension.** Rows remain model + level as today. Legality per endpoint lives in the profile's `reasoning_efforts`; the registry's `thinking_levels` (display list, carries observed stats) must be a subset, enforced by a test; the dialog offers the intersection.
- **Live compaction row.** The live feed gets an unnumbered "Compaction N · after turn M" row with its own elapsed timer and the handover summary on completion, mirroring the trace viewer.

## Delivered 2026-09-07 (branch harness/config-5.0, commits 735c127, f003168 + this doc)

Built in two waves by four then one parallel builders, each with file ownership and mutation-controlled tests. Full suite: 5 failed / 768 passed; the 5 are the pre-existing OCR and TaskMaster failures, unchanged since HEAD (which had 13 before the registry prune removed the three grok-4.3-shaped ones).

**Live verification (real emulator, real model).** Two 5–6 turn config-5.0 runs of glm-5.3-flash(high) with compaction every 2 turns, $0.004 each: 2 compactions each, 7 requests, alias stamped, agent_type append_compact, max_turns in the summary. Watched on `/spectate` mid-run: the compaction block is its own unnumbered row ("Compaction 1 · after turn 2 · 29.3s") with request diagnostics inside it under "Request", handover text and memory before/after; the Turn stat stayed at the gameplay turn; Cost read "$0.0016 of $0.300 cap"; the memory panel filled after the first compaction. Report on the review server: harness chip, "6 turns · 2 compactions", two compaction rows, Completion "—", 36.4% cache reuse. A 100-turn config-3.13 official report and the config-4.0 reports render as before, minus the "(0 tool calls)" label and the unenforced scorecard.

Two relaunches crashed mGBA (exit -11, then a `SEQUENCE_DONE` protocol desync) because I loaded the Lua connector a second time while the runner's own AppleScript load was in flight. Rule: after `pokemon run` prints "Waiting for mGBA to connect", give the runner's load ~15 s; load manually only if the log never says "mGBA connected", and only once. Run 1 worked because my loads came minutes later, after the runner's had failed for lack of a Scripting window.

**Open for Andreas**
- Legacy pins: the registry `provider:` blocks for gpt-6-astra, gemini-3.8-flash, glm-5.3-flash and gemma-4-31b were deleted (profile owns the endpoint), so config-4.0/3.13 runs of those four are now default-routed. Each removal is a comment at its site with the old block verbatim.
- claude-haiku-4.5 thinking ladder: OpenRouter advertises no efforts for it but accepts `effort` values live; the profile legalises [high, medium, low, minimal]. Confirm or trim.
- `QueuePanel.svelte` has no importer (App mounts QueueBar); its new profile chip and error strip are code-verified only.
- Cosmetic: Svelte trims the space before "·" at the start of `{#if}` blocks on queue cards and level options.
- Not produced live: a dispatch failure (last_error strip verified by pytest + patched response), a failed compaction, `budget_exhausted`, `endpoint_warning`, an official first-badge 5.0 run (verified through build_run_config only). The first real official 5.0 run will populate the empty leaderboard.
