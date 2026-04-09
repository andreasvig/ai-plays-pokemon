# CLI Reference

All commands should be run from the project root with the venv activated:
```
cd "Desktop/Code/AI plays pokemon"
source venv/bin/activate
```

---

## src/cli/launch.py - Start the harness

Launches mGBA with the ROM, starts the Python TCP server, and opens the Scripting window. You load the Lua script manually (one click from recent scripts).

```bash
# Normal launch (starts from game boot)
python src/cli/launch.py

# Launch from a snapshot
python src/cli/launch.py --snapshot local/snapshots/bedroom_start

# Use a different config file
python src/cli/launch.py --config my_config.yaml
```

After mGBA opens:
1. The Scripting window opens automatically
2. Load `socketserver.lua` (should be in recent scripts)
3. The harness connects and you're ready

Press `Ctrl+C` to stop (closes mGBA automatically).

---

## tests/test_phase5.py - Run the AI agent

Launches mGBA, loads a snapshot, and runs the agent for N turns. The main way to test the AI playing.

```bash
# Run 5 turns from bedroom snapshot (default)
python tests/test_phase5.py

# Run 10 turns
python tests/test_phase5.py --turns 10

# Run from a different snapshot
python tests/test_phase5.py --turns 5 --snapshot local/snapshots/has_starter
```

State file is created inside the run folder (`state.json`) so each run is self-contained.

After mGBA opens, load the Lua script. The agent will start playing.
Run logs are saved to `local/runs/` with timestamps.
A `run_summary.json` is written at the end with cost, tokens, and per-turn summaries.

---

## src/cli/snapshot.py - Manage snapshots

Save, load, and list game snapshots. Each snapshot captures the full emulator state + agent memory so you can resume from any point.

### Save a snapshot

Launches mGBA so you can play to a desired point, then saves when you press Enter.

```bash
# Save with a name
python src/cli/snapshot.py save "pallet_town_outside"

# Save with a name and description
python src/cli/snapshot.py save "has_starter" -d "Just received Charmander from Oak"
```

### List all snapshots

```bash
python src/cli/snapshot.py list
```

### Load a snapshot

Launches mGBA and restores the snapshot so you can play from that point.

```bash
python src/cli/snapshot.py load local/snapshots/bedroom_start
python src/cli/snapshot.py load local/snapshots/has_starter
```

---

## src/cli/report.py - Generate HTML report

Creates an interactive HTML report from a run folder. Shows screenshots, agent thinking, tool calls, and decisions per turn.

```bash
# Generate report from latest run
python src/cli/report.py

# Generate report from a specific run
python src/cli/report.py local/runs/2026-04-06_22-25-08_phase5_test
```

The report auto-opens in your browser.

---

## tests/test_emulator.py - Test emulator connection

Runs through all emulator functions (screenshot, buttons, sequences, save/load state). Useful for verifying the setup works.

```bash
python tests/test_emulator.py
```

Then load the Lua script in mGBA when prompted.

---

## Available Snapshots

| Name | Description | Created |
|------|-------------|---------|
| `bedroom_start` | Standing in bedroom with menu open, character named "AI" | 2026-04-06 |

To use a snapshot with the harness:
```bash
python src/cli/launch.py --snapshot local/snapshots/bedroom_start
```

---

## Project Structure

```
.
├── config.yaml              # All configuration
├── .env                     # API keys (not in git)
├── requirements.txt         # Python dependencies
│
├── src/
│   ├── config.py            # Config loader
│   ├── cli/                 # CLI entry points
│   │   ├── launch.py        # Start mGBA + harness
│   │   ├── report.py        # Generate HTML report
│   │   └── snapshot.py      # Manage snapshots
│   ├── agent/               # Agent logic
│   │   ├── agent.py         # Pydantic AI agent + tools
│   │   ├── turn.py          # Turn loop orchestrator
│   │   └── tools.py         # Tool schema definitions
│   ├── emulator/            # Emulator + perception
│   │   ├── emulator.py      # mGBA TCP connection
│   │   ├── vision.py        # VLM pipeline
│   │   └── ocr.py           # Background OCR
│   └── core/                # Core infrastructure
│       ├── logger.py        # Run event logging
│       ├── state.py         # Agent state file system
│       ├── snapshots.py     # Snapshot save/load
│       ├── patches.py       # Pydantic AI monkey-patches
│       └── prompts.py       # Prompt template substitution
│
├── lua/                     # mGBA Lua scripts
│   └── socketserver.lua
│
├── local/                   # Runtime data (not in git)
│   ├── runs/                # Run logs + reports
│   └── snapshots/           # Game state snapshots
│
├── tests/                   # Phase evaluation scripts
│
├── docs/                    # Documentation
│   ├── cli.md               # This file
│   ├── build_plan.md        # Phase-by-phase build plan
│   ├── initial_ideas.md     # Original design document
│   └── analysis/            # Research on other projects
│
└── roms/                    # Game ROMs (not in git)
```

---

## Workflow Examples

### Create a series of checkpoint snapshots
```bash
# Start fresh, play to outside house, save
python src/cli/snapshot.py save "outside_house" -d "Just walked outside for the first time"

# Load that, play to getting starter, save
python src/cli/snapshot.py load local/snapshots/outside_house
# ... play in mGBA ...
python src/cli/snapshot.py save "has_starter" -d "Received starter Pokemon from Oak"
```

### Test the AI from a specific point
```bash
python tests/test_phase5.py --turns 10 --snapshot local/snapshots/has_starter
```

### View run results
```bash
# Generate report from latest run
python src/cli/report.py

# Each run folder contains:
# - state.json (agent's live state file during the run)
# - run_summary.json (structured: cost, tokens, per-turn summaries)
# - events.jsonl (raw event stream)
# - config.json (config snapshot)
# - screenshots/ (per-turn screenshots)
# - report.html (generated report)
```
