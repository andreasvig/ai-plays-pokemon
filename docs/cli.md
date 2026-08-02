# CLI Reference

All commands are dispatched through the `pokemon` console script. Install it once with `pip install -e .` (from the project root, with the venv active) and the entry point is available for the rest of the venv's life.

```
cd /path/to/ai-plays-pokemon
source venv/bin/activate
pip install -e .       # one-time
pokemon --help
```

Top-level subcommands:

| Subcommand          | Purpose                                                          |
|---------------------|------------------------------------------------------------------|
| `pokemon status`    | What is running right now: app, emulator, ROM, active run, queue.|
| `pokemon ls`        | List models / roms / configs / stop events / benchmarks.         |
| `pokemon app`       | Long-lived control center: persistent emulator + queue + web UI. |
| `pokemon queue`     | Add / inspect / reorder / cancel runs on the running app.        |
| `pokemon run`       | Launch mGBA + Lua and run the agent for one or more pairs.       |
| `pokemon launch`    | Launch mGBA + Lua and idle (no agent — manual play / debug).     |
| `pokemon runs`      | History: list, continue, stop, delete, leaderboard.              |
| `pokemon snapshot`  | Save / load / list game snapshots.                               |

Every subcommand has its own `--help`, with examples.

---

## Recipes

The five things you actually do, in full.

```bash
# 0. Orient. The control center is usually already running.
pokemon status

# 1. A scored benchmark run, recorded.
pokemon queue add "claude-opus-5(high)" --benchmark pokebench-easy --record simple

# 2. A casual run — your own game, turn cap and finish line.
pokemon queue add "gpt-5.6-sol(medium)" --kind casual --rom firered \
    --max-turns 20 --stop-at starter_chosen \
    --record simple --record-speed cut-thinking

# 3. Resume one that ran out of turns (from its latest savepoint).
pokemon runs continue <run_id>

# 4. Call off whatever is running. An official run is voided by this.
pokemon runs stop

# 5. Look at the results.
pokemon runs board --benchmark pokebench-easy
pokemon runs list --status terminated
```

Don't know a name? `pokemon ls models sol`, `pokemon ls roms`,
`pokemon ls configs`, `pokemon ls events`, `pokemon ls benchmarks`. Every one of
those flags is validated at enqueue, and a wrong value is rejected with the
valid ones named — never accepted and dropped later.

Casual defaults: the latest config, the default ROM, no early stop, no
recording. Official runs ignore `--config` / `--max-turns` / `--stop-at` /
`--rom` — a benchmark is frozen, and takes its ROM from its own ladder.

---

## `pokemon status` — what is going on

The intended first command of a session. Replaces `ps` + `lsof` + three `/api`
calls: whether the app answers, which ROM is loaded, whether the emulator is
busy, the active run with its turn/cost/elapsed, the pending queue, and the last
few runs.

```bash
pokemon status               # the usual
pokemon status --limit 10    # more history
pokemon status --json        # {emulator, queue, runs}
```

It exits 0 whether the app is up or down — "down" is one of the answers it
exists to give, and it prints how to start it. Every other CLI that needs the
app exits 3 with the same hint.

It also surfaces `last_error`: the last queued item that was dequeued but never
became a run. Without it such a failure is invisible — the item is gone from the
queue either way, so the queue alone looks idle.

---

## `pokemon ls` — the vocabulary

```bash
pokemon ls                   # the categories
pokemon ls models sol        # aliases + thinking levels, substring-filtered
pokemon ls roms              # which games are registered AND on disk
pokemon ls configs           # config stems; the last is the casual default
pokemon ls events            # ids accepted by --stop-at (FireRed only)
pokemon ls benchmarks
```

Reads `configs/` directly, so unlike `queue` and `runs` it does **not** need the
app running. `--json` on any of them.

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
pokemon app --rom emerald            # boot a different game (configs/roms.yaml)
pokemon app --fake-emulator --seed-runs local/runs   # headless UI dev, no mGBA
```

On boot it reclaims stale processes, launches mGBA, **loads the Lua connector
script for you** (driving the Scripting window's *File → Load recent script*),
waits for the handshake, then binds the server and starts draining the queue.
If that automation is unavailable — no Accessibility permission, or the script
isn't in mGBA's recent list yet — it prints the manual instruction and waits for
you to do it, exactly as it always did.

### Flags

| Flag                  | Default | Notes                                                       |
|-----------------------|---------|-------------------------------------------------------------|
| `--port N`            | `3420`  | Web server port.                                            |
| `--no-browser`        | off     | Don't open a browser tab on boot.                           |
| `--connect-timeout S` | `300`   | Seconds to wait for the Lua handshake.                      |
| `--no-reclaim`        | off     | Don't auto-kill stale processes; print the manual fix instead.|
| `--rom ID`            | registry default | Which game to boot — a ROM id from `configs/roms.yaml` (`firered`, `emerald`). Switchable later from the UI. |
| `--fake-emulator`     | off     | Headless dev mode — fake supervisor + seeded index, no mGBA.|
| `--seed-runs PATH`    | —       | With `--fake-emulator`, seed the index from a runs dir / index file. |

Runs are enqueued from the UI as **official** (frozen benchmark — pick a model)
or **casual** (pick game + model + config + max-turns), and finished runs can be
**continued** from their latest savepoint. A casual run may pick any game in the
registry; benchmarks are limited to games a gate ladder is authored for (see
[control-center.md](control-center.md#choosing-a-game)). See [benchmark.md](benchmark.md) for
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

# Continue a prior run from its latest savepoint (resumes where it left off)
pokemon run --continue local/runs/2026-05-26_..._config-3.13__claude-opus-4-7 --turns 30

# Record the run to MP4 while it plays (headless — unaffected by your browser)
pokemon run --model "claude-opus-4.7(medium)" --turns 50 \
            --record simple --record-speed cut-thinking

# Play until a story event instead of a fixed length (--turns is still the cap)
pokemon run --model "claude-opus-5(medium)" --turns 400 \
            --stop-at viridian_forest_reached
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
| `--stop-at EVENT`     | off                              | End the run when this story event is detected. An id from the gate ladder (`pokemon queue events`, or pass a wrong one to see the list). `--turns` still caps it — whichever comes first wins. See [benchmark.md](benchmark.md#stopping-at-a-story-event). |
| `--snapshot PATH`     | `local/snapshots/bedroom_start`  | Reloaded before each run's turn loop.         |
| `--connect-timeout S` | `300.0`                          | Seconds to wait for the initial Lua connect.  |
| `--kill-existing`     | off                              | `pkill -f mgba` before launching.             |
| `--continue PATH`     | off                              | Resume from the source run's latest savepoint. Single-run only. Mutex with `--config` / `--model`. |
| `--record VIEW`       | off                              | Record to `<run_dir>/recording.mp4`. `simple` = the 1:1 view (1080×1080); `detailed` = the full wide panel (1920×1080). See [recording.md](recording.md). |
| `--record-speed S`    | `realtime`                       | `realtime` keeps every pause; `cut-thinking` records only each turn's execution window, cutting the model's response time. |
| `--record-fps N`      | `30`                             | Recording frame rate, 1–60.                   |

Run output lands in `local/runs/<timestamp>_<config-stem>__<model-slug>/` (gitignored): `events.jsonl`, `state.json`, `tasks.json`, `run_summary.json`, and screenshots. Reports are rendered natively in the `pokemon app` SPA (History view) from these files.

### Savepoints

When the config carries a `savepoints:` block, a run periodically writes mid-flight checkpoints into `<run_dir>/savepoints/turn_<N>/`. Each savepoint folder holds `emulator.state` + `state.json` + `metadata.json`, plus `task_master_state.json` (the task tree, on TaskMaster runs) or the legacy `tasks.json`. With `on_crash: true`, stopping or crashing a run also writes a savepoint at the exact current turn, so a continue resumes seamlessly. Loaded by `pokemon run --continue` to resume.

```yaml
# In configs/config-X.Y.yaml
savepoints:
  every_n_turns: 5   # 0 = disabled
  at_end: true       # save once the run finishes cleanly
  on_crash: true     # best-effort save on KeyboardInterrupt or exception
```

`pokemon run --continue <run_dir>` finds the highest `turn_<N>/` in that run's `savepoints/`, copies events.jsonl + screenshots + ocr + terminal.log into a new `<ts>_<run_name>_continued_from_turn_<N>/` run dir, then **resumes exactly where it left off**: the emulator state and TaskMaster task tree restore from the savepoint, and the Player's turn history, turn counter, historic-image buffer, and in-progress-task evidence are rebuilt from the copied events.jsonl (so the agent's "## Previous Turns" context and turn numbering carry over, not just `state.json`). Only the cost counters reset — the new run reports only its own spend; for a cumulative view, read both run dirs' `run_summary.json`.

The control center serves the UI at <http://localhost:3420/>: live runs at `/spectate`, finished runs under `/history/<run_id>`. (`pokemon app` opens it at boot.)

---

## `pokemon queue` — Drive the running app's queue

The control center runs one run at a time off a serial queue. This is the shell
face of it (the new-run dialog in the UI is the other). Requires `pokemon app`
to be up.

```bash
pokemon queue get                       # active + pending, and the last dispatch failure
pokemon queue add <model> [...]         # enqueue one or more
pokemon queue events                    # same list as `pokemon ls events`
pokemon queue reorder q_ab12 q_99ff     # the full order, as a permutation
pokemon queue cancel q_ab12             # drop a PENDING item
pokemon queue clear --yes               # drop all pending, keep the active run
```

`add` takes a **list** of models and `--repeat N`, so a sweep is one command:

```bash
# four models, one benchmark
pokemon queue add --benchmark pokebench-easy \
    "claude-opus-5(high)" "gpt-5.6-sol(medium)" "gemini-3.6-flash(high)" "grok-4.5(high)"

# one model three times, to see the spread
pokemon queue add "gemini-3.5-flash(high)" --kind casual --max-turns 50 --repeat 3
```

| Flag | Kind | Meaning |
|---|---|---|
| `--kind official\|casual` | — | `official` (default) is the frozen scored benchmark. `casual` is everything else. |
| `--benchmark ID` | official | Which ladder + goal. Omit for the registry default. |
| `--config STEM` | casual | e.g. `config-4.0`. Omit for the latest. |
| `--rom ID` | casual | e.g. `firered`. Omit for the default ROM. The executor switches the emulator for you. |
| `--max-turns N` | casual | Turn cap. Official runs end at their ladder. |
| `--stop-at EVENT` | casual | End early on a story event. `--max-turns` still caps it. FireRed only. |
| `--repeat N` | both | Enqueue each model N times. |
| `--record simple\|detailed` | both | MP4 to `<run_dir>/recording.mp4`, rendered server-side. |
| `--record-speed realtime\|cut-thinking` | both | `cut-thinking` drops the model's response time from the video. |

Enqueue is where every value is checked. An unknown model, config, ROM, stop
event or benchmark is a 400 naming the valid ones — including the values the
server **defaulted** for you, which the confirmation line echoes back:

```
$ pokemon queue add "gpt-5.6-sol(medium)" --kind casual --rom firered --max-turns 20
enqueued 1 run(s):
  q_6b65abdb  casual   gpt-5.6-sol(medium)  config-4.0  rom=firered  max_turns=20
```

Cancel removes a *pending* item. To remove a finished run from history, use
`pokemon runs delete`.

---

## `pokemon runs` — History, continue, stop, leaderboard

```bash
pokemon runs list                        # newest first
pokemon runs list --status terminated    # the ones a gate killed
pokemon runs list --model "gpt-5.6-sol(medium)" --limit 20
pokemon runs board --benchmark pokebench-easy
pokemon runs continue <run_id>           # enqueue a casual continue from its savepoint
pokemon runs stop                        # stop the active run
pokemon runs delete <run_id> --yes       # folder → Trash, and de-index
```

Statuses:

| Status | Means | On the leaderboard? |
|---|---|---|
| `completed` | Ran to its natural end (official: final gate; casual: turn cap) | yes, if official |
| `terminated` | The referee killed it on a missed gate deadline | yes, if official |
| `cancelled` | You stopped it. An official run is voided | no |
| `crashed` | It died — or the model never produced a valid turn at all | no |

That last clause matters: a run whose model 400s or times out on every attempt
is `crashed`, not a zero. It played nothing, so it scores nothing.

`delete` and `stop` on an official run are destructive and require `--yes`.

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

# Another game (configs/roms.yaml) — how a non-FireRed start state gets made
pokemon snapshot save emerald_truck --rom emerald -d "Inside the truck, before Littleroot"
```

To make a captured state the **start state** for that game's casual runs, copy it
to `configs/saves/<name>/` (committed, like the canonical FireRed save) and point
the registry entry's `start_save:` at it. Until a game has one, its runs boot from
the title screen.

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
