# CLI Reference

All commands are dispatched through the `pokemon` console script. Install it once with `pip install -e .` (from the project root, with the venv active) and the entry point is available for the rest of the venv's life.

```
cd /path/to/ai-plays-pokemon
source venv/bin/activate
pip install -e .       # one-time
pokemon --help
```

Top-level subcommands:

| Subcommand          | Purpose                                                         |
|---------------------|-----------------------------------------------------------------|
| `pokemon app`       | Long-lived control center: persistent emulator + queue + web UI.|
| `pokemon run`       | Launch mGBA + Lua and run the agent for one or more pairs.      |
| `pokemon launch`    | Launch mGBA + Lua and idle (no agent — manual play / debug).    |
| `pokemon snapshot`  | Save / load / list game snapshots.                              |

Every subcommand has its own `--help`.

---

## `pokemon app` — The control center

The primary entry point. One long-lived process that owns a warm emulator, a
serial run queue, a run index/leaderboard, and the web UI — start it once and
drive everything from the browser at <http://localhost:3420/>. Full guide:
[control-center.md](control-center.md).

```bash
pokemon app                          # boot the control center on :3420
pokemon app --port 8080              # different web port
pokemon app --no-browser             # don't auto-open a tab
pokemon app --fake-emulator --seed-runs local/runs   # headless UI dev, no mGBA
```

On boot it reclaims stale processes, launches mGBA, and **blocks for the one
manual step** — in the mGBA Scripting window: **File → Load recent script →
`socketserver-1.lua`** — then binds the server and starts draining the queue.

### Flags

| Flag                  | Default | Notes                                                       |
|-----------------------|---------|-------------------------------------------------------------|
| `--port N`            | `3420`  | Web server port.                                            |
| `--no-browser`        | off     | Don't open a browser tab on boot.                           |
| `--connect-timeout S` | `300`   | Seconds to wait for the Lua handshake.                      |
| `--no-reclaim`        | off     | Don't auto-kill stale processes; print the manual fix instead.|
| `--fake-emulator`     | off     | Headless dev mode — fake supervisor + seeded index, no mGBA.|
| `--seed-runs PATH`    | —       | With `--fake-emulator`, seed the index from a runs dir / index file. |

Runs are enqueued from the UI as **official** (frozen benchmark — pick a model)
or **casual** (pick model + config + max-turns), and finished runs can be
**continued** from their latest savepoint. See [benchmark.md](benchmark.md) for
the official/casual distinction and the gate ladder.

---

## `pokemon run` — Run the agent

> `pokemon run` is the standalone, single-shot path: it launches its own mGBA,
> runs one or more `(config, model)` pairs, and exits. For live spectating, a
> queue, history, and the leaderboard, use `pokemon app` instead.

Launches mGBA, opens its Scripting window (so you can `File > Load recent script` once), waits for the Lua client to connect, then runs the agent for `--turns` turns per `(config, model)` pair. Multiple pairs reuse the same mGBA + Lua connection.

```bash
# Single run — latest config, one model, default 10 turns
pokemon run --model "gemini-3.5-flash(medium)"

# Specific config + 50 turns
pokemon run --config configs/config-3.13.yaml --model "claude-opus-4.7(medium)" --turns 50

# Fan-out: one config across N models
pokemon run --config configs/config-3.13.yaml \
            --model "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" --turns 50

# Paired 1:1: N configs × N models
pokemon run --config configs/config-3.13.yaml configs/config-tm-smoke.yaml \
            --model "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" --turns 50

# Custom snapshot
pokemon run --model "gemini-3.5-flash(medium)" --snapshot local/snapshots/has_starter

# Kill any leftover mGBA before starting
pokemon run --model "gemini-3.5-flash(medium)" --kill-existing

# Continue a prior run from its latest savepoint (fresh turn counter)
pokemon run --continue local/runs/2026-05-26_..._config-3.13__claude-opus-4-7 --turns 30
```

### Pairing rules between `--config` and `--model`

| `--config` count | `--model` count | Behaviour                                |
|------------------|-----------------|------------------------------------------|
| 0 (omitted)      | N               | All N models use the latest config.      |
| 1                | 1               | Single run.                              |
| 1                | N               | Fan-out — one config across N models.    |
| N                | 1               | Fan-out — one model across N configs.    |
| N                | N (same N)      | Paired 1:1.                              |
| N                | M (≠N, both >1) | **Error** — Cartesian not supported.     |

Aliases come from `configs/models.yaml`. Raw `"provider/model"` OpenRouter ids also work and bypass the registry.

### Flags

| Flag                  | Default                          | Notes                                         |
|-----------------------|----------------------------------|-----------------------------------------------|
| `--config PATH ...`   | latest in `configs/`             | One or more config files.                     |
| `--model ALIAS ...`   | (required)                       | One or more model aliases or raw ids.         |
| `--turns N`           | `10`                             | Turns per run, applied to every pair.         |
| `--snapshot PATH`     | `local/snapshots/bedroom_start`  | Reloaded before each run's turn loop.         |
| `--connect-timeout S` | `300.0`                          | Seconds to wait for the initial Lua connect.  |
| `--kill-existing`     | off                              | `pkill -f mgba` before launching.             |
| `--continue PATH`     | off                              | Resume from the source run's latest savepoint. Single-run only. Mutex with `--config` / `--model`. |

Run output lands in `local/runs/<timestamp>_<config-stem>__<model-slug>/` (gitignored): `events.jsonl`, `state.json`, `tasks.json`, `run_summary.json`, and screenshots. Reports are rendered natively in the `pokemon app` SPA (History view) from these files.

### Savepoints

When the config carries a `savepoints:` block, a run periodically writes mid-flight checkpoints into `<run_dir>/savepoints/turn_<N>/`. Each savepoint is a standard snapshot folder (emulator.state + state.json + tasks.json + metadata.json) and can be loaded by `pokemon run --continue` to resume.

```yaml
# In configs/config-X.Y.yaml
savepoints:
  every_n_turns: 5   # 0 = disabled
  at_end: true       # save once the run finishes cleanly
  on_crash: true     # best-effort save on KeyboardInterrupt or exception
```

`pokemon run --continue <run_dir>` finds the highest `turn_<N>/` in that run's `savepoints/`, copies events.jsonl + screenshots + ocr + terminal.log into a new `<ts>_<run_name>_continued_from_turn_<N>/` run dir, then resumes the agent from there. **The agent's turn counter and text history start fresh** — the only narrative continuity is whatever the agent wrote into `state.json` during the original run. Cost counters also reset; the new run reports only its own spend. For a full cumulative view, read both run dirs' `run_summary.json`.

The control center serves the UI at <http://localhost:3420/>: live runs at `/spectate`, finished runs under `/history/<run_id>`. (`pokemon app` opens it at boot.)

---

## `pokemon launch` — Manual mGBA + Lua session

Launches mGBA, starts the TCP server, opens the Scripting window, and idles after the Lua client connects. No agent — useful for manual play, debugging the connection, or building snapshots interactively.

```bash
pokemon launch
pokemon launch --snapshot local/snapshots/bedroom_start
pokemon launch --config configs/config-3.13.yaml
```

Press `Ctrl+C` to shut mGBA down cleanly.

---

## `pokemon snapshot` — Manage snapshots

Each snapshot captures the full emulator state + agent memory so a run can resume from any point.

```bash
# Save: launches mGBA, you play to the point you want, press Enter to capture
pokemon snapshot save pallet_town_outside
pokemon snapshot save has_starter -d "Just received Charmander from Oak"

# List
pokemon snapshot list

# Load: launches mGBA and restores the snapshot for manual play
pokemon snapshot load local/snapshots/bedroom_start
```

---


## Test scripts (not part of the CLI)

The `tests/` directory still holds connection-test scripts that don't go through the `pokemon` command:

```bash
python tests/test_emulator.py     # exercise screenshot, buttons, save/load state
```

These are kept as bare scripts because they exist to validate the harness itself, not to run the agent.

---

## Workflow examples

### Build a chain of checkpoint snapshots
```bash
pokemon snapshot save outside_house -d "Just walked outside for the first time"
pokemon snapshot load local/snapshots/outside_house
# ...play in mGBA to the next checkpoint...
pokemon snapshot save has_starter -d "Received starter Pokemon from Oak"
```

### Compare two models on the same snapshot
```bash
pokemon run --config configs/config-3.13.yaml \
            --model "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" \
            --snapshot local/snapshots/has_starter --turns 50
```
