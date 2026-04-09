# Changelog

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
