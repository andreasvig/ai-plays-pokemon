# AI Plays Pokemon

An AI agent that plays Pokemon FireRed using only screen vision (no RAM reading). The agent sees screenshots, reasons about them, and presses buttons via a socket connection to mGBA.

## Project Structure

```
src/
  config.py              # YAML config loader
  agent/
    agent.py             # Pydantic AI agent, tools, GameAction output model
    turn.py              # Turn manager: orchestrates VLM → LLM → execute loop
  core/
    logger.py            # RunLogger: event logging to events.jsonl
    state.py             # StateManager: agent's persistent JSON state
    snapshots.py         # Save/restore game + agent state
    prompts.py           # Prompt template substitution
    patches.py           # Monkey-patches for OpenRouter cost/reasoning extraction
  emulator/
    emulator.py          # EmulatorClient: TCP socket to mGBA, screenshots, buttons
    vision.py            # VisionPipeline: VLM calls for screen analysis
    ocr.py               # OCRRunner: background Tesseract OCR + LLM cleanup (Gemma)
  dashboard/
    server.py            # FastAPI server (localhost:3000) with WebSocket endpoints
    screen_stream.py     # Background thread: polls PNG → serves via WebSocket
    event_bridge.py      # Bridges RunLogger events → WebSocket broadcast
    static/index.html    # Live dashboard UI (vanilla JS, no build step)
  cli/
    launch.py            # Interactive mGBA launcher
    report.py            # Post-run HTML report generator
    snapshot.py          # Snapshot save/load/list CLI
configs/
  config-X.Y.yaml         # Versioned configs (latest auto-selected)
lua/
  socketserver.lua       # mGBA Lua script: TCP client, button control, auto-capture
tests/
  test_phase3.py         # State manager + logger unit tests
  test_phase5.py         # Full integration test (launches mGBA, runs N turns)
local/                   # Git-ignored runtime data
  runs/                  # Run logs, screenshots, reports
  snapshots/             # Saved game states
  state/                 # Agent state files
```

## Tech Stack

- **Python 3.9** (venv at `./venv`)
- **Pydantic AI 0.8.x** with OpenRouter provider — uses `agent.iter()` for streaming
- **OpenRouter** for all LLM/VLM calls (API key in `.env` as `OPENROUTER_API_KEY`)
- **Gemini 3 Flash** (LLM) + **Gemini 3.1 Flash Lite** (VLM) — configurable in `configs/`
- **FastAPI + uvicorn** for live dashboard
- **mGBA** emulator with Lua socket script
- **Pillow, numpy** for image processing
- **Tesseract** for OCR + **Gemma 4 26B** (via OpenRouter) for OCR cleanup

## Key Commands

```bash
# Activate venv
source venv/bin/activate

# Run agent (launches mGBA + dashboard, runs N turns, auto-picks latest config)
python tests/test_phase5.py --turns 10

# Run with a specific config
python tests/test_phase5.py --turns 10 --config configs/config-1.0.yaml

# Run state manager tests
python tests/test_phase3.py

# Generate report from a run
python -m src.cli.report local/runs/<run_folder>
```

## Architecture Notes

- **Vision pipeline**: Two modes configured via `vision_mode`:
  - `separate_vlm`: VLM analyzes screenshots into text, LLM receives text only
  - `direct_multimodal`: LLM receives raw screenshots as `ImageUrl` (used in config 1.1+)
- **Memory dictionary**: Agent's persistent memory across turns. Stored as JSON state file. Agent writes updates via `memory_updates` string field on `GameAction` output (not a tool call). Harness parses JSON and applies updates after each turn. Suggested keys: current_location, party, map, notes. Map entries use compass directions (north, south-east, etc.), not coordinates. Agent can create additional keys freely (badges, bag, pc_pokemon, etc.).
- **Coordinate system**: Player at (0,0). x: left=negative, right=positive. y: up=positive, down=negative. Ranges for uncertainty: `(-3..-4, -2..-3)`. Coordinates are only for real-time player-relative positions, not for map memory.
- **Grid overlay**: Optional red semi-transparent tile grid on agent screenshots (not live stream). Enabled via `screenshot.grid_overlay: true` in config. Helps VLM count tiles for spatial reasoning.
- **Socket protocol**: Newline-delimited commands over TCP port 8888. `CAP` for screenshots, `SEQ:btn1;btn2` for button sequences. Sequences are fire-and-forget (sleep for calculated duration, no TCP recv wait). A/B buttons use a longer gap (`ab_gap_frames`, default 60) than directional buttons (`frames_between_inputs`, default 24) to allow dialogue to fully render for OCR capture.
- **Screen stability**: After button execution, captures frames in a rolling window at `poll_interval` apart (120×80 grayscale), compares pairwise. Stable when similarity product ≥ threshold (starts strict, relaxes over `max_wait`).
- **OCR pipeline** (config 2.1+): Background thread captures screenshots from `/tmp/mgba_stream.png` (Lua-written at ~15fps, no TCP contention) during button execution + settle only (`set_active` gating). Pipeline: perceptual dHash dedup (256-bit, Hamming threshold) → Tesseract with confidence filter (conf≥40) → regex noise line stripping → text-level dedup → numbered buffer. At turn boundary, buffer is flushed to a cleanup LLM (Gemma 4 26B via OpenRouter with JSON-schema structured output) which merges/denoises the raw OCR. Cleaned text is injected as "Recent OCR Text" in the LLM's user message. Cost tracked per-flush and accumulated for the run summary. Per-window stats logged: attempts, hash dupes, tesseract runs, empty after filter, text dupes, buffer cap hits.
- **Dashboard**: Runs on port 3000 in daemon threads. WebSocket `/ws/screen` for live frames, `/ws/events` for streaming agent events. Cursor-based event tracking. Cache-busting URL on open. Shows OCR flushes per turn (amber box with cleaned text + collapsible raw buffer).
- **Report**: Generated by `src/cli/report.py` as standalone HTML. Shows memory updates, thinking, tool calls, retries, and OCR sections (cleaned text + raw buffer + cost/tokens) per turn. Top-level cost breakdown includes OCR.
- **Monkey-patches**: `src/core/patches.py` patches Pydantic AI's `_process_response` to extract OpenRouter cost and reasoning tokens.

## Config

Configs live in `configs/` as versioned files: `config-X.Y.yaml` (e.g. `config-1.0.yaml`).
By default the latest version (highest X.Y) is auto-selected. Override with `--config path`.
Each config has a description block at the top explaining what's special about it.

Key settings:
- `llm_model` / `vlm_model`: OpenRouter model IDs
- `thinking.effort`: "low" / "medium" / "high" for extended thinking
- `max_steps_per_turn`: Max tool calls per turn (default 8)
- `screenshot.upscale_factor`: Screenshot upscale multiplier (default 3)
- `screenshot.grid_overlay`: Red tile grid on agent screenshots (default false)
- `screen_stability`: Settings for post-action screen settling detection
- `ocr`: Background OCR pipeline settings (enabled, capture_interval, confidence_threshold, dedup params, cleanup model/prompts)
- System prompts for VLM, LLM, and OCR cleanup are inline in each config file

## Important Conventions

- Screenshots go to `/tmp/mgba_screenshot.png` (agent, via TCP CAP) and `/tmp/mgba_stream.png` (dashboard stream + OCR background thread, via Lua auto-write) — separate files to avoid contention.
- All events are logged to `events.jsonl` as JSON lines, flushed immediately for crash safety.
- The `local/` folder is for runtime data and should be git-ignored.
- Button inputs use `Literal["up","down","left","right","a","b","start","select"]` enum constraint.
- **GameAction output fields**: `inputs` (button list), `i_saw` (screen observation), `i_did` (action + reasoning + memory notes), `i_expect` (predicted next screen), `memory_updates` (JSON string with dot-notation keys).
