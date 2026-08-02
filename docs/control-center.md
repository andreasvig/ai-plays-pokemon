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
+ Lua handshake, and gives you a persistent leaderboard, run history, and a live
spectate view.

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

> **Before first launch**, build the web UI: `cd src/dashboard/web && npm install
> && npm run build` (or run `scripts/setup.sh`). The built `web/dist/` is
> gitignored, so a fresh clone has none — without it the server runs but every
> page returns "SPA not built". See the [README setup](../README.md#setup).

On `pokemon app` the process:

1. **Reclaims stale processes** — kills anything left over from a previous launch
   holding the web port (`:3420`), the emulator socket (`:8888`), a stray mGBA,
   or a `caffeinate` keep-awake. (Disable with `--no-reclaim`, which instead
   prints the manual fix and exits.)
2. **Launches mGBA** and positions its window.
3. **Loads the Lua connector script** by driving the Scripting window's
   *File → Load recent script*, and waits for the handshake. If that automation
   is unavailable (no Accessibility permission, or the script isn't in mGBA's
   recent list yet) it prints the manual instruction instead:
   > In the mGBA **Scripting** window: **File → Load recent script → `socketserver-1.lua`**

   Either way the boot blocks here until the Lua client connects (timeout
   `--connect-timeout`, default 300s).
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
- **Casual** — you pick **game + model + config + max-turns**, and optionally a
  **Stop at** story event (`Entered Viridian Forest`, `Reached Pewter City`, …).
  The run then ends at whichever comes first, the event or the turn cap. Never
  on the leaderboard. For experiments. See
  [Stopping at a story event](benchmark.md#stopping-at-a-story-event).

### Choosing a game

The dialog's **Game** picker lists everything in
[`configs/roms.yaml`](../configs/roms.yaml) whose ROM file is present. FireRed is
the default; Emerald is there too.

A game can be benchmarked only if a **gate ladder is authored for it** — the
referee reads story flags out of that game's RAM map, so FireRed's gates address
nothing on another cartridge. Nothing declares this: it is derived from the
ladders' own `game:` field, so authoring `configs/checkpoints-emerald-*.yaml`
is all it takes to turn Emerald benchmarks on. Until then, picking Emerald
**greys out Benchmark** (and hides Stop at) and the run is casual.

Casual runs on the default ROM start from the canonical FireRed save; a run on
any other game starts from **that game's own `start_save`**, or from the title
screen when it hasn't got one yet.

### Switching the loaded game

The emulator holds one cartridge at a time. The **game selector in the top bar**
switches it, and the queue does it for you when the next run needs a different
one — so runs on different games can sit in the same queue and dispatch back to
back, with nothing to click between them.

The switch takes about two seconds and **keeps the Lua connection**. mGBA holds
one script context for the life of the *process* and merely re-attaches the core
around it, so the script only dies if the process does — and it doesn't:
the cartridge is changed by driving mGBA's own **File → Recent** menu. The result
is confirmed by reading the cartridge header back over the socket, which can
only answer correctly if the swap took, the script survived, and its frame
callback is still running.

It falls back to relaunching when the shortcut isn't available — a ROM that has
never been opened on this machine (so it isn't in Recent yet), no Accessibility
permission, or a swap that fails to verify. That path does cost a Lua re-load,
and the top bar says *"load the Lua script in mGBA"* while it waits. Opening a
game once puts it in Recent, so the fallback is normally a one-time cost per game.

Switching to the game already loaded does nothing at all, which is why the queue
can reconcile before every single run for the price of a string comparison.

You can also boot straight into a game: `pokemon app --rom emerald`.

Either kind can also be **recorded to MP4**. Tick "Record this run to MP4" in the
dialog and pick the interface (the 1:1 simple view, or the full wide panel) and
the speed (real time, or cut-thinking — which drops the model's response time).
The capture runs headlessly on the server, so it is unaffected by where you are
in the UI, whether the window has focus, or whether it is open at all. Full
guide: [recording.md](recording.md).

Recorded runs get a **▶** button on their **History** row that plays the MP4
in-page.

### Continue a run

From a finished (or stopped) run you can **continue** it from its latest
savepoint. A continue is **seamless** — indistinguishable from never stopping: it
reuses the source run's model, resumes from the highest `savepoints/turn_<N>/`,
and carries everything forward — the turn counter, the Player's turn history, the
TaskMaster task tree, the gate latch, and the cumulative cost / tokens / active
time (none of these reset). The continue **inherits the source run's kind**: an
**official** run continues official on the **same benchmark** (so a run stopped
overnight can be finished + scored), a casual run continues casual. See
[Pausing & resuming an official run](benchmark.md) for the validity details.

### Reorder / remove / stop

- The queue can be reordered and items removed before they run.
- The active run can be **stopped** ("kill run", with a confirm dialog). Stops
  are **near-instant**: the stop is detected within a fraction of a second and
  **the in-flight turn is cancelled immediately** rather than waiting for the
  (possibly slow) LLM call to finish. That's safe because a turn's buttons are
  only pressed *after* the model returns — so a cancelled turn never touched the
  emulator, and the kill savepoint lands on the **last settled turn** (a clean,
  byte-exact boundary). The UI still shows an amber "stopping…" state for the
  brief teardown. A stopped run saves a savepoint and is marked `cancelled`; a
  cancelled **official** run is voided (it never reaches the leaderboard) — but
  it stays **continuable as the same benchmark**, and **the continuation re-runs
  the exact turn the agent was killed on**, so finishing the ladder is what
  scores.

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
| `savepoints/turn_<N>/` | Atomic turn-N checkpoint bundles — the resume points for `--continue`. Each holds the emulator `.state`, agent `state.json`, `task_master_state.json`, the referee gate latch (`referee_state.json`), and a `checkpoint.sha256` tamper-seal. Official runs checkpoint every 10 turns + at every TaskMaster handoff (+ on stop/crash). |

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
