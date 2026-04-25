# Changelog

## 2026-04-25 — Session 8: Model Registry, Output-Mode Fallbacks, OpenRouter Research

### Models Registry (`configs/models.yaml`)
- New alias layer: configs reference short names (`gemini-3-flash(low)`, `qwen3.6-plus`) instead of raw `provider/model` strings
- Each entry holds: `openrouter_id`, optional `reasoning`, optional `output_mode`, optional `fallbacks` (chained alias resolution)
- `src/config.py` resolves the alias at load: rewrites `llm_model` to the raw id, expands `thinking`, expands fallbacks; raw ids still pass through untouched
- Original alias preserved in `_llm_alias` and surfaced in run summaries

### Output-Mode Fallback (`tool` / `native_json` / `prompted`)
- Some OpenRouter providers don't expose `tool_choice="required"` — the previous default broke for those models
- Registry entry can now declare `output_mode: native_json` (uses Pydantic AI's `NativeOutput` → `response_format: json_schema`) or `output_mode: prompted` (text + parse) per model
- Default remains `tool` — strongest schema enforcement, broadest support
- Retries bumped 3 → 5 (prompted mode occasionally needs extra rounds to nail JSON shape)

### Robust Prompted-Mode Template
- Pydantic AI's default prompted template was too weak for Qwen3.6-Plus — the model kept emitting ` ```json{...}``` ` despite "no fences" instructions
- Pydantic AI's built-in fence stripper is asymmetric: eats the leading `{` with the opening fence, leaves the trailing fence in place — produces unparseable output
- Custom template: explicit `{{` / `}}` boundary chars, concrete example, escaped braces (Python `.format(schema=...)` substitution requires it)
- New `_robust_strip_markdown_fences` helper in `src/core/patches.py` for symmetric stripping when the model still wraps

### Prompted-Mode Display Parity
- In tool mode the GameAction arrives as a `final_result` tool call; in prompted mode it arrives as a `TextPart`
- New `_try_parse_game_action` in `src/agent/turn.py` recognizes prompted JSON TextParts and re-routes them to the same trace shape
- Dashboard, terminal display, `events.jsonl` (`llm_output` + `memory_update_output`) now render identically across both modes

### OpenRouter Image-Input Research
- `scripts/test_media_resolution.py` — 9-probe test confirming Gemini's `media_resolution` parameter is silently dropped by OpenRouter (every shape tested returned `prompt_tokens=1102`)
- `scripts/test_resize_spatial.py` — 4 image resolutions (240×160 → 1440×960) of the same Pokemon screenshot all return `prompt_tokens=1318` and byte-identical answers at temperature=0
- `scripts/test_resize_nocache.py` — temperature=0.7 control rules out caching as the explanation
- Findings captured at `agent_brain/references/openrouter.md` in the Marvin vault: passthrough whitelist, silent-drops list, image-input behavior, when to bypass for direct provider APIs

### config-2.2.yaml
- Built around the OCR cleanup pipeline from Session 7 (background Tesseract → Gemma 4 26B cleanup → injected as "Recent OCR Text")
- Latest snapshot of the prompt + memory schema before the v3 schema overhaul

---

## 2026-04-10 — Session 5b: Button Timing, Screen Stability, Prompt Refinements

### Button Timing: A/B Dialogue Gap
- A and B button presses now use a longer gap (45 frames / 750ms) vs directional buttons (24 frames / 400ms)
- Lua `socketserver.lua`: per-button gap — checks if key is A or B, uses `queue_ab_gap_frames`
- Python sleep calculation accounts for mixed timing per button in sequence
- New config option: `emulator.ab_gap_frames` (default 45)
- Fixes: `[A, A, A, A, A, A]` previously only advanced 2 dialogue boxes, now advances ~5-6

### Screen Stability Rewrite
- New approach: captures 3 images at poll_interval apart, then compares all 3 pairwise
- Higher resolution comparison: 120×80 grayscale (was 48×32)
- 3 pairwise comparisons (1↔2, 2↔3, 1↔3) instead of 2 consecutive
- Sliding window: on failure, captures new image, drops oldest, re-checks latest 3
- Config changes: `poll_interval: 0.2`, `max_wait: 15.0`, `threshold_end: 0.95`

### Prompt Refinements
- Memory: "Never update based on what you expect — only after confirmed on screen"
- Memory: always explain in `i_did` what was updated and why
- Dialogue guidance: use A to start conversation, B to advance (B won't restart dialogue if you overshoot), A for Yes/No confirmations
- Approach angles: doors/stairs may need specific direction, use sweeping techniques
- Trust screen over game knowledge: verify locations via signs/dialogue/landmarks
- Grid overlay: use red grid lines to count tile coordinates

### Test Results (20 turns, $0.20)
- Bedroom → 1F → Pallet Town → Oak encounter → Oak's Lab → chose Squirtle → rival battle incoming
- Dialogue chaining works reliably with A/B timing fix
- Memory updates correctly deferred until confirmation

---

## 2026-04-09 — Session 5: Config 2.0 Prompt Rewrite, Output Schema Overhaul

### Config 2.0 System Prompt
- New markdown-formatted system prompt with clear sections: Top Goal, Input Descriptions, High-Level Turn Strategy, Movement & Navigation Guidelines, Memory Guidelines, Miscellaneous Guidelines, Output Format
- Game-agnostic top goal (follows task description, not hardcoded to FireRed)
- Screenshot descriptions per screen type: overworld (with coordinate system inline), menu, battle
- 5-step turn strategy: observe inputs → evaluate last action → update memory → plan ahead → execute and document
- Movement guidelines compressed from config 1.2 with action chaining, wall hugging, corner sweep
- Added dialogue chaining (e.g. Pokemon Center healing with [A, A, A, A, A, A])
- Menu/battle chaining example (e.g. selecting 4th move with [A, down, right, A])
- Memory guidelines with suggested keys: current_location, party, map, notes, plus free-form keys (bag, badges, pc_pokemon)
- Stuck detection: change approach after 2+ turns without progress
- Ambitious turn guidance: aim for 6-12 inputs for predictable actions, fewer for uncertain outcomes

### Output Schema Changes
- Renamed `i_thought` → removed, `i_did` now includes reasoning and plan context
- Added `i_expect`: predicted next screen state, used by next turn to evaluate success
- All `Field(description=...)` rewritten with detailed guidance and examples
- `i_saw`: detailed observation including coordinates for objects, NPCs, doors, exits
- `i_did`: action + why + plan context + memory update notes
- `i_expect`: specific prediction with battle example (type effectiveness, HP estimates)
- `inputs`: guidance on 6-12 for predictable, 1-5 for uncertain outcomes
- Updated all references across turn.py, report.py, dashboard/index.html

### Other Changes
- Removed LB/RB from valid inputs (config + Button Literal type)
- Unified task format: `task: {goal, description}` across all configs
- Disabled OCR in config 2.0
- Removed hardcoded missing memory key warning from turn.py
- User input messages now use markdown formatting (## headings, ```json blocks, **bold** labels)

---

## 2026-04-09 — Session 4: Code Cleanup, Coordinate Fix, Grid Overlay

### Codebase Cleanup (~340 lines removed)
- Removed dead code from `emulator.py`: `_insert_turning_frames()`, `_DIRECTION_CODES`, `_CODE_TO_FACING`, `_FACING_TO_CODE`, unused `import hashlib`
- Simplified `state.py` from 274 → 68 lines: removed entire visibility system (`_hide`, `_seen` tracking, `start_turn`, `read_state`, `update_state`, `move_state`, `set_hide`). Made `set_by_path`, `delete_by_path`, `save`, `get_by_path` public API
- Cleaned `agent.py`: removed unused imports (`field`, `Dict`, `OpenAI`), removed unused `for_subtask()` method
- Consolidated `logger.py` from 216 → 140 lines: removed 6 unused methods (`log_button_press`, `log_llm_request`, `log_llm_response`, `log_task_event`, `log_ocr`, `log_snapshot`), removed `remove_listener()`, added generic `log_event()` method
- Extracted duplicated agent iteration block in `turn.py` into `_run_agent_iter()` helper
- Fixed `dashboard/server.py`: removed duplicate `import time`, removed `import time as _time` alias
- Deleted dead `tests/test_movement.py` (tested only removed methods)
- Rewrote `tests/test_phase3.py` for simplified state API
- Pinned `pydantic-ai>=0.8.0,<0.9.0` in requirements.txt (monkey-patches depend on 0.8.x internals)

### Coordinate System Fix
- Flipped y-axis to natural convention: positive = up, negative = down
- Previously: `y: negative = up, positive = down` (screen coordinates)
- Now: `y: negative = down, positive = up` (mathematical/intuitive)
- Updated all examples in config-1.2.yaml prompt (coordinate system, action chaining, i_saw, i_thought, memory_updates)

### Map Memory: Compass Directions
- Map entries in memory dictionary now use compass directions (north, south-east, etc.) instead of coordinates
- Coordinates `(x,y)` are reserved for real-time player-relative positions in `i_saw` only
- Prevents confusion between persistent map descriptions and per-turn relative positions

### Grid Overlay
- New `screenshot.grid_overlay` config option (default: false)
- Draws red semi-transparent tile grid on agent screenshots and report images
- NOT applied to live dashboard stream (comes from separate Lua capture)
- Grid aligns to GBA 16×16 tile boundaries with 8px vertical offset
- Line width scales with upscale factor (`scale * 2` pixels)
- Helps VLM count tiles for more accurate spatial reasoning

---

## 2026-04-09 — Session 3: Direct Multimodal, Memory System, Reliable Movement

### Config Versioning
- Configs now live in `configs/` as `config-X.Y.yaml` (e.g. `config-1.0.yaml`)
- `load_config()` auto-picks the latest version by parsing X.Y from filenames
- Override with `--config path` flag on test scripts and launch.py
- Each config has a description block at the top

### Direct Multimodal Vision (Config 1.1+)
- LLM receives raw screenshots directly instead of VLM text descriptions
- `vision_mode: "direct_multimodal"` — no separate VLM call
- `ask_vlm` tool disabled (LLM sees the screen itself)
- `ImageUrl` from pydantic-ai passed as multimodal user message content
- Trace serializer replaces base64 images with `[image]` placeholder

### Memory Dictionary (replaces State Tools)
- Removed all state tools: `update_state`, `read_state`, `move_state`, `set_hide`
- Removed per-tool budget system (no longer needed)
- Memory updates are now a field on `GameAction` output model
- `memory_updates: str` — JSON string parsed by harness after LLM responds
  - String type was critical: Gemini returned `{}` for Dict fields but writes content for str
  - `"none"` sentinel for no changes, parsed/filtered by turn manager
- State manager seen/hidden tracking bypassed — direct `_set_by_path`/`_delete_by_path`
- Missing required keys warning injected into user message: `⚠ MISSING MEMORY KEYS: goal`
- Agent has zero tools in config 1.2 (memory on output, no ask_vlm)

### Memory Dictionary Keys (Config 1.2 Prompt)
- `location`: current room/area/town
- `party`: Pokemon team with levels, HP, moves
- `goal`: current objective + next concrete step
- `story_progress`: milestones completed
- `map`: nested dict of visited locations with coordinates and connections
- `obstacles`: failed paths/actions to avoid repeating

### Coordinate System (Config 1.2)
- Player at (0,0), x=left(-)/right(+), y=up(-)/down(+)
- Ranges for uncertainty: `(-3..-4, 2..3)`
- Used in i_saw descriptions, map entries, navigation planning

### Action Chaining & Wall Hugging (Config 1.2)
- Wall hugging: overestimate inputs to guarantee hitting a wall (extra presses do nothing)
- Action chaining: interleave directions to sweep toward targets diagonally
- Corner sweeps: hit one wall then slide along it to find exits
- Prompt teaches these as named tactics with examples

### Reliable Movement (Fire-and-Forget)
- Removed PING/PONG pre-check before button sequences (was causing timeout errors)
- `press_button_list` is now fire-and-forget: send SEQ, sleep for calculated duration, drain buffer
- No more TCP recv waits during button execution — screen stability check confirms completion
- `_drain_buffer()` clears QUEUED/SEQUENCE_DONE responses after sleep
- Lua: handle `socket.ERRORS.AGAIN` in `poll_commands()` (non-blocking error)

### Tool Filtering
- Agent tools now filtered by config `tools:` section (was previously ignored)
- `ask_vlm: false` in config now actually removes the tool from the agent

### Dashboard & Report Improvements
- Memory updates shown in live dashboard as `🧠 Memory Update` box (separate `memory_update_output` event)
- Memory updates shown in HTML report (both explanation section and trace output)
- Cache-busting URL query string on dashboard open (prevents stale browser cache)
- `terminal.log` written to run folder (tee of all stdout during run)
- "Running Vision..." label instead of "Running VLM..." in direct_multimodal mode
- Report trace now properly renders `final_result` tool calls (was being skipped)
- All tool calls logged (including empty/budget-exceeded) for full visibility

### Validation & Retries
- Pydantic validation catches invalid outputs (misspelled buttons, missing fields)
- Pydantic-ai sends `RetryPromptPart` back to model with error description (up to 3 retries)
- Retries visible in terminal, live dashboard (`🔄 Output Retry` box), and HTML report

### Results
- 10-turn runs: bedroom → 1F → Pallet Town with memory tracking, zero timeout errors
- ~$0.03-0.07 per 10-turn run
- Agent builds spatial map with coordinates, tracks obstacles, updates location on room changes

---

## 2026-04-07 — Session 2: Live Dashboard, State Simplification, Movement Fixes

### State Management
- Merged `add_state` + `edit_state` + `delete_state` into single `update_state` tool
  - Set key to `""` or `null` to delete
  - Empty `{}` is a safe no-op (no state wipe)
- Deleted unused `src/agent/tools.py` (dead code, never imported)
- Per-tool budgets: `update_state` 3/turn, `ask_vlm` 2/turn, `read_state` 3/turn
  - Shows "(N/M used)" on every call
  - Over-budget returns error, forcing agent to act
  - Empty calls count against budget

### Known Issue: Empty update_state calls
- Model (Gemini 3 Flash) reflexively calls `update_state({})` with empty params
- Happens as parallel tool call alongside final_result, or in loops before acting
- Budget system caps it at 3/turn (prevents 50-call loops from earlier)
- Prompt says "NEVER call with empty dict" but model ignores it ~50% of turns
- Root cause: model wants to "do state management" but has nothing to write
- **Not yet solved** — needs either a model-level fix or architectural change (e.g. remove tool from schema when state hasn't changed, or auto-inject state updates)

### VLM & Prompts
- VLM coordinate system: player at (0,0), x=left/right, y=up/down
- Objects reported as `name: x=N, y=N | interaction notes`
- BLOCKED section: which directions are immediately passable
- LLM prompt teaches coordinate-to-button translation
- Stronger prompt against empty update_state calls

### Movement & Emulator
- Button hold increased: 6 → 12 frames (100ms → 200ms), ensures walk not just turn
- Button gap decreased: 30 → 24 frames (500ms → 400ms)
- Removed turning frame compensation (unnecessary with hold=12)
- VLM facing sync: parse "PLAYER: facing X" from VLM to track direction
- Facing resets to None on execution errors
- Timeout formula: `(N * frames_per_button / 60) * 3 + 30s` buffer
- PING + retry before each sequence (flushes socket, verifies connection)

### Live Dashboard
- Full web dashboard at localhost:3000 (FastAPI + vanilla JS, no build step)
- Live GBA screen stream via Lua auto-capture (15fps) + WebSocket PNG frames
- Streaming chat with boxed sections: Vision, Thinking (with markdown), Output, Action, Tools, Errors
- Collapsible JSON state viewer, live-updating
- Header: task, cost, turn count, tokens — all live
- Last 2 turns stay open, older auto-collapse
- Cursor-based event tracking (reconnect-safe)
- Auto-opens browser on run start

### Streaming Agent
- Switched from `agent.run()` to `agent.iter()` (pydantic-ai)
- Emits `llm_thinking`, `llm_output` events as nodes complete
- Thinking and output appear in dashboard during the turn, not just at end

### mGBA Stability
- `pauseOnFocusLost=0` in mGBA config (keeps running when browser takes focus)
- `caffeinate -i` wraps mGBA process (prevents macOS App Nap)
- Lua auto-capture: runs AFTER game logic, wrapped in `pcall` (never breaks callback)
- PNG completeness validation in ScreenStreamer (checks IEND marker, skips truncated files)
- RunLogger listener hook for live event broadcasting

### Infrastructure
- `src/dashboard/` package: server.py, screen_stream.py, event_bridge.py, static/index.html
- `CLAUDE.md` project documentation
- Dependencies: added `fastapi>=0.100.0`, `uvicorn>=0.23.0`

### Results
- 8-turn run: zero timeout errors, $0.044 total
- Agent navigates bedroom → downstairs → 1F → toward exit consistently
- Dashboard streams smoothly at 15fps

---

## 2026-04-06 — Session 1: Core Architecture (Phases 1-6)

- Phase 1: Emulator connection (Lua TCP socket, mGBA control)
- Phase 2: Snapshot system (save/restore game + agent state)
- Phase 3: Run logging (events.jsonl, screenshots, crash-safe)
- Phase 4: State system (JSON state with visibility, seen tracking)
- Phase 5: Agent turn loop (Pydantic AI, VLM → LLM → execute)
- Phase 6: Evaluation & iteration (prompts, OCR, report generation)
- Phase 6.5: Architecture improvements (cost tracking, model fallback, structured logging)
