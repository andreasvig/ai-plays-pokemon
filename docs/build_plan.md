# Build Plan

Each phase builds on the previous one and ends with a concrete evaluation to verify it works before moving on.

---

## Phase 1: Emulator Connection & Control -- COMPLETE

**Goal:** Establish a working connection to mGBA where we can capture screenshots and send button presses from Python.

**What was built:**
- `lua/socketserver.lua` - Lua client that connects to Python TCP server. Commands are queued and executed in frame callback (required by mGBA's API). Supports: screenshot capture, single button press, button sequences, save/load states, pause/unpause, configurable timing.
- `src/emulator.py` - `EmulatorClient` class. Python acts as TCP server, Lua connects as client (reversed from initial plan - mGBA's socket API doesn't support server mode reliably). Exposes: `capture_screenshot()`, `press_button()`, `press_sequence()`, `save_state()`, `load_state()`, `pause()`, `unpause()`, `ping()`. Screenshot preprocessing (upscale, contrast, saturation boost).
- `src/config.py` - Config loader with validation.
- `launch.py` - Launch script: starts Python TCP server, launches mGBA with ROM, opens Scripting window via AppleScript. User loads Lua script manually (one click from recent list). mGBA HEAD build supports `--script` flag but has a macOS display bug (black screen), so stable 0.10.5 is used.
- `test_emulator.py` - Phase 1 evaluation script.

**Evaluation results:**
- All tests passed: connection, ping, screenshots (240x160 raw, 720x480 preprocessed), button presses, sequences, save/load states, pause/unpause.
- Screenshots verified visually: player moves correctly, state restore returns to exact position.
- Launch script automates everything except one manual click to load the Lua script.

---

## Phase 2: Snapshot System -- COMPLETE

**Goal:** Be able to save and load full game snapshots so we can start runs from specific points.

**What was built:**
- `src/snapshots.py` - `SnapshotManager` class: `save_snapshot(name, description)`, `load_snapshot(path)`, `list_snapshots()`, `delete_snapshot()`. Each snapshot is a folder containing `emulator.state`, `state.json` (agent state copy), `metadata.json`, and `preview.png`.
- `snapshot_cli.py` - CLI tool for save/load/list operations.

**Evaluation results:**
- Saved snapshot "bedroom_start" (player in bedroom with menu open, character named "AI")
- Loaded snapshot in a fresh mGBA session - restored to exact same state (verified visually and via screenshot comparison)
- Snapshot folder structure: emulator.state (35KB), metadata.json, preview.png

---

## Phase 3: Core Infrastructure (Logging + State System) -- COMPLETE

**Goal:** Set up run logging and the state file system so the agent has memory and everything is recorded.

**What was built:**

*Run Logging (`src/logger.py`):*
- `RunLogger` class: creates run folder (`runs/{timestamp}_{run_name}/`), writes events as JSON lines to `events.jsonl`, saves screenshots as image files. All writes flush immediately (crash-safe). Config snapshot saved at run start.
- Log methods for: screenshots, button presses/sequences, tool calls/responses, LLM requests/responses, turn starts/explanations, task events, state changes, OCR, VLM requests/responses, snapshots, custom events.

*State File System (`src/state.py`):*
- `StateManager` class: single JSON file, all access via tool methods, `_hide` system, visibility-aware safety.
- All 6 state tools: `read_state`, `edit_state`, `add_state`, `delete_state`, `move_state`, `set_hide`.
- `get_truncated_view()` renders hidden values as `<hidden>`.
- Visibility tracking resets each turn. Visible keys auto-marked as seen. Hidden keys require explicit `read_state()`.
- Move only requires key existence, not content.

**Evaluation results:**
- 13 State Manager tests passed: add/edit/delete/move/hide all work correctly, safety rules enforced (hidden keys rejected, unseen parents rejected), seen tracking resets between turns.
- 5 Run Logger tests passed: folder creation, event logging, screenshot saving, JSON validity, crash safety (events survive without close()).

---

## Phase 4: Perception (OCR + Vision Pipeline) -- COMPLETE

**Goal:** Give the agent eyes - both continuous OCR and the configurable VLM pipeline with on-demand follow-up questions.

**What was built:**

*OCR System (`src/ocr.py`):*
- `OCRRunner` class: background thread, periodic capture, image hash deduplication, Tesseract preprocessing (4x upscale, binarize, high contrast), scrolling text merger, modular backend (tesseract or API).
- Tested: dialog text captured well, pixel font menu text garbled (expected), dedup and merging work correctly.

*Vision Pipeline (`src/vision.py`):*
- `VisionPipeline` class: separate_vlm mode (VLM → text description) and direct_multimodal mode (raw image to LLM).
- `ask_vlm` tool for follow-up questions. Uses OpenRouter via OpenAI SDK.
- Both modes produce correct output formats.

**Dependencies added:** pytesseract, openai, python-dotenv. Tesseract installed via brew.

---

## Phase 5: Single-Agent Turn Loop (First Gameplay) -- COMPLETE

**Goal:** A single agent that can play the game in a loop: see screen, think, act.

**What was built:**

*Agent (`src/agent.py`):*
- Pydantic AI agent with OpenRouter integration (provider="openrouter").
- `GameAction` output model with inputs + i_saw/i_thought/i_did fields.
- 7 tools registered: read_state, edit_state, add_state, delete_state, move_state, set_hide, ask_vlm.
- `AgentDeps` dataclass passes emulator, state, vision, logger to tools via RunContext.

*Turn Manager (`src/turn.py`):*
- `TurnManager` orchestrates the full loop: screenshot → VLM analysis → assemble context (VLM + OCR + state + history) → LLM with tools → extract GameAction → execute buttons → save explanation → cleanup.
- Turn explanations accumulate across turns as agent's local memory.

*Tool definitions (`src/tools.py`):*
- OpenAI-format tool schemas (created but not used - Pydantic AI generates schemas from function signatures).

**Evaluation results:**
- 3 turns completed successfully with Gemini 3 Flash as LLM, Gemini 3.1 Flash Lite as VLM.
- Turn 1: Agent saw menu open, used read_state (found empty state), asked VLM two follow-up questions, pressed B to close menu.
- Turn 2: Agent recognized it was in the bedroom (correcting its state), moved RRRUU toward stairs.
- Turn 3: Agent opened START menu to check party.
- State tools, VLM follow-ups, button execution, and logging all working.
- Note: Grok 4.20 was too slow for iteration; switched to Gemini 3 Flash.
- Note: Gemini has a schema warning about `additionalProperties` in edit_state (dict[str, Any]) but it works.

---

## Phase 6: Report Generator -- COMPLETE

**Goal:** Interactive HTML report for observability.

**What was built:**

*Report Generator (`report.py`):*
- Parses `events.jsonl` into turns with screenshots, explanations, tool calls, and traces.
- Generates interactive HTML with: collapsible turns, screenshots, "I saw / I thought / I did", VLM descriptions, tool calls with args/responses, full message trace with color-coded roles.
- Trace shows: SYSTEM prompt (collapsible) → USER input (collapsible) → THINKING (yellow, from OpenRouter reasoning) → TOOL CALL → TOOL RESULT → DECISION (green, final_result parsed into I saw/thought/did/action).
- Raw events available as collapsible JSON per turn.
- Auto-opens in browser on macOS.

*OpenRouter Reasoning Support (`src/patches.py`):*
- Monkey-patch for Pydantic AI 0.8.x to capture OpenRouter's `reasoning` field (returned as `message.reasoning`, which Pydantic AI only checks for DeepSeek's `reasoning_content`).
- Patches `OpenAIChatModel._process_response` to extract reasoning before model_validate strips it, injects as `ThinkingPart`.
- Config: `thinking.effort` in config.yaml (`"low"`, `"medium"`, `"high"`), passed via `extra_body.reasoning` to OpenRouter.

**Evaluation results:**
- Thinking tokens captured successfully from Gemini 3 Flash via OpenRouter.
- Report shows full traces: system prompt, user message, thinking blocks between tool calls, tool call args/responses, final decision.
- 8-turn run generated 12k+ events, report renders correctly with all data.

**Known issues:**
- Pydantic AI 0.8.x (Python 3.9 limit) lacks native `OpenRouterModelSettings` - monkey-patch required.
- Socket timing bug: occasionally `SCREENSHOT` response arrives during sequence wait (Turn 2 error in 8-turn run).

---

## Phase 6.5: Architecture Improvements & Hardening -- COMPLETE

**Goal:** Restructure the project, implement recommendations from translator/program-enrichment analysis, improve prompts, and fix reliability issues.

**What was built:**

*Project restructure:*
- `docs/` folder for all documentation (cli.md, build_plan.md, initial_ideas.md, analysis/)
- `local/` folder for runtime data (runs/, snapshots/, state/) — separated from source
- `tests/` folder for phase evaluation scripts
- `src/` reorganized into subpackages: `src/cli/`, `src/agent/`, `src/emulator/`, `src/core/`
- `src/config.py` at package root, re-exports via `__init__.py` for clean imports

*Architecture improvements (from translator/enrichment analysis):*
- Cost tracking: monkey-patch captures `usage.model_extra["cost"]` from OpenRouter responses. Both LLM (via provider_details) and VLM (via direct SDK) costs tracked per-turn and totaled.
- `capture_run_messages()` wraps all agent.run() calls — traces captured even on failure.
- Model fallback chain: `llm_fallback_models` config, tries each in order, with thinking-param fallback.
- `AgentDeps.for_subtask()` creates child deps sharing infra but fresh per-turn state.
- Prompt template substitution: `fill_prompt()` replaces `{{key}}` placeholders in YAML prompts.
- Structured `run_summary.json` at run end: session metadata, cost breakdown (LLM/VLM), per-turn stats.
- State file now lives inside run folder — each run is fully self-contained.
- Report auto-generates in `finally` block (even on crash/Ctrl+C).

*Task file system:*
- `tasks.json` alongside `state.json` in snapshots and run folders.
- Simple format: `{"goal": "...", "description": "..."}`.
- Loaded from snapshot into run folder at start, presented to agent each turn.
- Snapshots now save/load: emulator.state, state.json, tasks.json, metadata.json.

*Prompt engineering (Phase 8 started):*
- VLM system prompt: enforces structured sections (SCENE TYPE, LOCATION, PLAYER, SURROUNDINGS, TEXT ON SCREEN, CURSOR). Prevents location guessing. Requires spatial info relative to player.
- VLM ask prompt: requires cardinal directions + tile distances.
- LLM system prompt: first-person framing, navigation rules (walls, doors, NPC interaction), menu/battle strategy, state management guidance, loop detection, Pokemon FireRed walkthrough knowledge.
- Button format changed to `list[Literal["up","down","left","right","a","b","start","select","lb","rb"]]` — eliminates hallucinated button names via schema-level enum constraint.

*Movement & emulator reliability:*
- Turning frame compensation: tracks player facing direction, auto-inserts extra press when direction changes (model sends intended movement, harness handles turning).
- Screen stability detection: after each sequence, polls screenshots every 0.3s comparing last 3 frames. Proceeds when similarity > threshold (starts at 0.99, relaxes to 0.90 over max_wait). Handles battle transitions, door fades, cutscenes.
- Dynamic sequence timeout: scales with button count (1s per button + 5s buffer).
- Socket timing bug fixed: `_recv_expected()` skips unexpected SCREENSHOT responses when waiting for QUEUED/SEQUENCE_DONE.
- Removed screenshot contrast/saturation boost — raw pixel art only, upscaled with nearest-neighbor.

*Terminal observability:*
- Structured `[Turn N, Step M, Type]` format for all terminal output.
- Shows: VLM preview, thinking previews, tool calls with args, responses, final output with saw/thought/did.
- Per-turn timing, cost, token counts.
- Pydantic-ai warnings silenced, stdout line-buffered for real-time background output.

*Config additions:*
- `llm_fallback_models`, `max_steps_per_turn` (enforced via UsageLimits), `screen_stability.*`, `post_sequence_delay` replaced by stability system.

**Evaluation results:**
- 5-turn runs complete with valid button presses (no hallucinated names).
- Cost tracking works: ~$0.03-0.07 per 5-turn run (LLM + VLM breakdown).
- Turning compensation verified with before/after screenshots across 8 test cases.
- Screen stability waits correctly for animations to settle.
- Socket timing bug no longer causes sequence failures.
- Agent successfully closes menus, navigates toward stairs (still working on consistent stair entry).

---

## Phase 6.75: Live Dashboard, Agent Streaming & Movement Hardening -- COMPLETE

**Goal:** Real-time web dashboard, streaming agent events, state/tool cleanup, and reliable movement execution.

**What was built:**

*State management simplification:*
- Merged `add_state` + `edit_state` + `delete_state` into single `update_state` tool
- Set key to "" or null to delete; empty {} is safe no-op
- Per-tool budgets: update_state 3/turn, ask_vlm 2/turn, read_state 3/turn — shows "(N/M used)", over-budget forces action
- Deleted dead code `src/agent/tools.py`

*VLM coordinate system:*
- Player at (0,0), x=left/right, y=up/down
- Objects as `name: x=N, y=N | interaction notes`
- BLOCKED section for immediate directions
- LLM prompt teaches coordinate-to-button translation

*Movement fixes:*
- Button hold: 6→12 frames (100ms→200ms) — ensures walk, not just turn
- Button gap: 30→24 frames
- Removed turning frame compensation (unnecessary with hold=12, was causing double-moves)
- VLM facing sync: parse "PLAYER: facing X" from VLM each turn
- Facing resets to None on execution errors

*Live Dashboard (`src/dashboard/`):*
- FastAPI + vanilla JS at localhost:3000, no build step
- **Live GBA screen:** Lua auto-capture at 15fps + ScreenStreamer + WebSocket PNG frames
- **Streaming chat:** boxed sections (Vision, Thinking w/ markdown, Output, Action, Tools, Errors)
- **State viewer:** collapsible JSON tree, live-updating
- **Header:** task, cost, turns, tokens — all live
- Last 2 turns open, older auto-collapse
- Cursor-based event tracking (reconnect-safe, multi-tab)

*Streaming agent events:*
- `agent.run()` → `agent.iter()` — emits `llm_thinking`, `llm_output` as nodes complete
- Thinking appears in dashboard during the turn

*mGBA stability:*
- `pauseOnFocusLost=0` in mGBA config — keeps running when browser takes focus
- `caffeinate -i` wraps mGBA — prevents macOS App Nap
- Lua auto-capture runs AFTER game logic, wrapped in pcall
- PING + retry before each sequence (flushes socket, verifies connection)
- PNG completeness validation (IEND marker check, skips truncated files)
- Timeout formula: `(N * frames_per_button / 60) * 3 + 30s`

**Evaluation results:**
- 8-turn run: zero timeout errors, $0.044 total
- Agent consistently navigates bedroom → downstairs → 1F → toward exit
- Dashboard streams at 15fps, smooth quality
- Per-tool budgets prevent update_state loops (capped at 3/turn)

---

## Phase 7: Task System, Sub-Agents & Auto-Snapshots

**Goal:** Implement recursive task decomposition with sub-agent spawning, return statuses, stuck detection, and automatic snapshots on task completion.

**Build:**

*Task System:*
- `TaskManager` class: tracks the task tree (current depth, parent chain)
- `create_subtask` tool: validates depth limit, spawns a new agent loop, blocks parent
- `return_to_parent` tool: returns status + summary to parent, resumes parent with fresh screenshot + state
- Max depth enforcement: return error if at limit
- Stuck detection: force `failed` return when max turns per task exceeded
- Task tree logged to run folder

*Auto-Snapshots:*
- Hook into task completion events
- If completed task's depth <= `auto_snapshot_depth`, save snapshot

**Evaluation:**
- Load a snapshot, set task: "Go outside and walk to Route 1"
- Verify the agent can decompose or handle directly
- Trigger a wild battle: verify sub-agent spawns, completes, returns summary, parent resumes
- Test max depth: set `max_task_depth: 1`, verify error at depth limit
- Test stuck detection: set `max_turns_per_task: 3`, verify forced failure
- Test all three return statuses (success, failed, other)
- Set `auto_snapshot_depth: 1`, verify snapshots created at right moments
- Load an auto-snapshot, verify it works

---

## Phase 8: Prompt Engineering & First Real Runs (Next Up)

**Goal:** Write serious Pokemon-specific prompts and attempt real gameplay sessions.

**Build:**
- Detailed `system_prompt`: game mechanics, what to track in state, goal structuring, navigation tips, battle strategy, menu patterns
- Detailed `vlm_system_prompt`: what to describe, output format, what details matter
- Detailed `vlm_ask_prompt`: spatial questions, coordinate conventions
- Create good starting snapshots (after character select, in Pallet Town with starter, etc.)

**Evaluation:**
- Run: "Get your starter Pokemon from Professor Oak" from fresh game start
- Run: "Navigate from Pallet Town to Viridian City" from post-starter snapshot
- Run: "Defeat a wild Pokemon on Route 1" from Route 1 snapshot
- Analyse reports: Where does the agent struggle? Missing information? VLM failures?
- Iterate on prompts based on findings
- This is where the real tuning begins

---

## Phase 9: Web Search Tool (Optional)

**Goal:** Add web search capability as an agent tool.

**Build:**
- `web_search` tool implementation (search API integration)
- Result formatting for LLM consumption

**Evaluation:**
- Enable web search, run in a situation where game knowledge helps (e.g., "which starter is strong against Brock?")
- Verify it searches, gets useful results, and incorporates them into reasoning
- Compare runs with and without web search

---

*Phases 1-6.75 complete. Phase 8 (prompts) partially done. Phase 7 (task system) is next core feature. Phase 9 is optional.*
