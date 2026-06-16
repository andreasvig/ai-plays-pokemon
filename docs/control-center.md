# The Control Center (`pokemon app`)

The control center is the primary way to run, watch, and review benchmark runs.
It's a single long-lived process that owns the emulator, a serial run queue, and
the full web UI. Start it once, leave it up, and drive everything from the
browser.

```bash
pokemon app
```

- **Web UI:** <http://localhost:3420/> (opens automatically)
- **Emulator bridge:** mGBA + Lua on TCP `127.0.0.1:8888`

---

## What it is

`pokemon run` launches a fresh mGBA for every run and exits when the run ends.
The control center instead keeps **one warm emulator** and **one web server** up
across many runs, draining a queue serially. That removes the per-run mGBA boot
+ Lua handshake (the one manual step), and gives you a persistent leaderboard,
run history, and a live spectate view.

```
pokemon app  (one process)
├── AppSupervisor      → owns the mGBA process + Lua TCP bridge (:8888)
├── RunExecutor        → serial drain loop: one run at a time
│   ├── QueueManager   → local/app/queue.json   (the pending queue)
│   └── RunIndex       → local/app/runs_index.json (leaderboard + history)
└── FastAPI server     → :3420, serves the Svelte SPA + JSON/WS API
```

Only **one run executes at a time**. While a run is in flight the supervisor is
"busy" and the queue holds; when it finishes the next item dispatches
automatically.

---

## Boot sequence

On `pokemon app` the process:

1. **Reclaims stale processes** — kills anything left over from a previous launch
   holding the web port (`:3420`), the emulator socket (`:8888`), a stray mGBA,
   or a `caffeinate` keep-awake. (Disable with `--no-reclaim`, which instead
   prints the manual fix and exits.)
2. **Launches mGBA** and positions its window.
3. **Waits for the manual Lua handshake** — the one step only you can do:
   > In the mGBA **Scripting** window: **File → Load recent script → `socketserver-1.lua`**

   The boot blocks here until the Lua client connects (timeout `--connect-timeout`,
   default 300s).
4. **Binds the web server** on `--port` (default `3420`) and opens a browser tab
   (unless `--no-browser`).
5. **Loads / backfills the run index** — if `runs_index.json` is empty but run
   folders exist under `local/runs/`, it rebuilds the index by scanning them.
6. **Starts the executor drain loop** in a background thread.
7. **Idles** — "emulator warm, server up, queue draining. Ctrl-C to shut down."

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--port` | `3420` | Web server port. |
| `--no-browser` | off | Don't open a browser tab on boot. |
| `--connect-timeout` | `300` | Seconds to wait for the Lua handshake. |
| `--no-reclaim` | off | Don't auto-kill stale processes; print manual fix and exit if a port is held. |
| `--fake-emulator` | off | Headless dev mode: boot with a fake supervisor + seeded index, **no mGBA**. For UI work. |
| `--seed-runs PATH` | — | With `--fake-emulator`, seed the index from a `runs_index.json` or a directory of run folders. |

---

## The web UI

The UI is a Svelte single-page app served at `/`. Client-side routes:

| Route | View | What it shows |
|---|---|---|
| `/` | **Home** | Per-benchmark leaderboard (tabs switch benchmark; best official run per model) + charts. |
| `/spectate` | **Spectate** | The live run as a fit-to-screen 16:9 kiosk. |
| `/history` | **History** | Filterable / sortable list of every run. |
| `/history/<run_id>` | **Report** | One run's full report (KPIs + gate scorecard + trace). |
| `/about` | About | Project blurb. |

### Spectate — the live view

A non-scrolling 16:9 kiosk (fits any TV, nothing below the fold):

- the live **emulator frame** (streamed over WebSocket),
- the **current task** the TaskMaster issued,
- the agent's **memory dictionary**,
- a **live trace feed** of each turn's reasoning, tool calls, and button inputs,
- the **gate ladder** with the current rung highlighted,
- **elapsed time**, always computed from the real run start (not reset when you
  re-open the page).

### History & Report

History lists every run with its model, kind (official/casual), status, furthest
gate, turns, and cost — filter by kind/status, search, and sort by recency,
completion, cost, or duration. Clicking a run opens its **Report**: meta KPIs,
the benchmark **gate scorecard** (which gates were reached, and when), and the
full **master → player trace** — system prompts, per-step thinking, tool calls +
responses, the TaskMaster's strategy + verdict per task, and the player's
hand-back. The report renders natively from the run's on-disk `events.jsonl` and
`run_summary.json`.

---

## Running things from the UI

### Enqueue a run

Two kinds (see [the benchmark doc](benchmark.md) for the full distinction):

- **Benchmark** (official) — you pick the **model** + **which benchmark**
  (`pokebench-easy` / `first-badge` / `full`). The benchmark selects the gate
  ladder and the goal; config (`config-3.13`) and the start save are locked.
  Counts on that benchmark's leaderboard.
- **Casual** — you pick **model + config + max-turns**. No gates, never on the
  leaderboard. For experiments.

### Continue a run

From a finished (or stopped) run you can **continue** it from its latest
savepoint. Continues are **always casual** — they reuse the source run's config
and model, resume from the highest `savepoints/turn_<N>/`, and start a **fresh
turn counter** (cost + turn counters reset; the only carry-over is whatever the
agent wrote into its `state.json` plus the restored game + TaskMaster state).

### Reorder / remove / stop

- The queue can be reordered and items removed before they run.
- The active run can be **stopped** ("kill run", with a confirm dialog). Stops
  are **cooperative**: the run halts at the **next turn boundary** (never
  mid-turn — that would corrupt the savepoint), so for a slow model it can take
  up to a turn's worth of time. The UI shows an amber "stopping…" state
  immediately so the latency doesn't read as a dead button. A stopped run saves
  a savepoint and is marked `cancelled`; a cancelled **official** run is voided
  (it never reaches the leaderboard).

---

## Run output

Each run writes a directory under `local/runs/<timestamp>_<config>__<model>/`
(gitignored):

| Path | Contents |
|---|---|
| `events.jsonl` | One JSON event per line — the full run log (turns, traces, tool calls, OCR, task events). The UI is built entirely from this. |
| `run_summary.json` | Nested summary: session, cost (incl. per-turn), turns, and `referee` (gates, furthest, termination reason). The control plane stamps `run_id`, `kind`, `status`, `benchmark` (which benchmark it played), `benchmark_version`, `continued_from` onto it when the run finalizes. |
| `config.json` | The exact config the run used (so a continue can reuse it). |
| `state.json` | The agent's persistent memory at end of run. |
| `screenshots/` | Per-turn frames the agent saw. |
| `ocr/` | OCR captures. |
| `savepoints/turn_<N>/` | Periodic checkpoints (emulator `.state` + agent/task state) — the resume points for `--continue`. |

The index and queue live separately under `local/app/`: `runs_index.json`
(rebuildable by scanning `local/runs/`) and `queue.json`.

---

## Headless / dev mode

For UI work without an emulator, boot the server with a fake supervisor:

```bash
pokemon app --fake-emulator --seed-runs local/runs   # serve seeded history, no mGBA
```

> ⚠️ Headless mode and fakes verify the SPA and the JSON/WS surface, **not** the
> real emulator path (the Lua handshake, live frame stream, real turn timing).
> Always smoke a real `pokemon app` boot before trusting a UI/backend change
> end-to-end.

See also: [CLI reference](cli.md) · [The PokeBench benchmark](benchmark.md).
