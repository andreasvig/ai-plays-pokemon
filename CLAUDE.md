# Working in this repo

A vision-only LLM agent that plays Pokemon (mGBA + Lua, no RAM reads), plus the
control center that queues, scores and records its runs. `README.md` is the
public front door; this file is the operational one.

## Orient before you act

```bash
pokemon status
```

One command: is the control center up, on which port, which ROM is loaded, is a
run active, what is queued, what ran recently. **It is usually already
running** — check before starting anything, because `pokemon run` and
`pokemon launch` drive mGBA themselves and will fight a running app over the
emulator port (8888).

To find the name of anything a command asks you for:

```bash
pokemon ls models sol      # model aliases (+ substring filter)
pokemon ls roms            # game ids
pokemon ls starts          # choosable openings per game (boy/girl on FireRed)
pokemon ls configs         # config stems
pokemon ls events          # ids for --stop-at
pokemon ls benchmarks
```

`ls` reads the registries off disk, so it works whether or not the app is up.

## Starting a run

With the control center running, everything goes through the queue:

```bash
# a scored benchmark run
pokemon queue add "claude-opus-5(high)" --benchmark pokebench-easy --record simple

# a casual run: 20 turns of FireRed, ending early if the starter gets picked
pokemon queue add "gpt-5.6-sol(medium)" --kind casual --rom firered \
    --max-turns 20 --stop-at starter_chosen --record simple --record-speed cut-thinking
```

Casual defaults: the latest config (`config-5.0`, the standard append-and-compact
harness), default ROM, that ROM's default opening, no early stop, no recording. Every value is validated at enqueue — an unknown
model/config/rom/start/event is a 400 that names the valid ones. `--rom` switches
the emulator for you.

`--start <label>` picks which opening a casual run plays from — FireRed has `boy`
and `girl`, registered in `configs/starts.yaml`. Labels are scoped to the ROM.
Official runs ignore it (a benchmark starts from the frozen canonical savepoint),
and a continue ignores it (it resumes its source run's savepoint).

Without the app, `pokemon run --model "<alias>" --turns N` does a single
one-shot run and owns mGBA itself.

## Environment

- **`python` is not on PATH.** Use `./venv/bin/python`. `python3` exists but has
  no pytest — running tests with it fails confusingly.
- **`timeout` does not exist** on this macOS box. Background the command and
  `sleep` + `pkill -f <pattern>` instead.
- **Deleting**: move to Trash. A global deny blocks `rm -rf`.
- **Frontend**: after editing anything under `src/dashboard/web/src/`, run
  `cd src/dashboard/web && npm run build`. The app serves `dist/` from disk, so
  a rebuild is live without restarting `pokemon app`.
- **Chrome/CDP**: one `--user-data-dir` per invocation, or the second instance
  hangs forever.

## Tests

```bash
./venv/bin/python -m pytest tests/ -q
```

**Baseline as of 2026-08-03: 566 pass, 8 fail.** Those 8 are pre-existing and
unrelated to any current work — do not spend time diagnosing them unless that
IS the work:

- `test_model_registry_collapse.py` — `test_always_on_type_none`,
  `test_catalog_picker_shape`, `test_default_level_helper`
- `test_phase4.py::test_ocr`
- `test_taskmaster_loop.py::test_official_config_savepoint_cadence_is_tight`
- `test_taskmaster_search_budget.py` — all three

If the count changes, something you did changed it. There is **no working
pre-commit hook** on this machine, so nothing runs the gates for you.

## Conventions

- **Never `git add -A` / `git add .`** — the working tree routinely carries
  unrelated work in progress. Stage explicit paths.
- **Never push or open a PR** without being asked.
- `configs/config-5.0.yaml` is the **frozen standard harness** — the
  append-and-compact agent. It is BOTH the official benchmark config (the
  control center hardcodes it: `executor.OFFICIAL_CONFIG`) and, being the
  highest `config-X.Y`, what a bare `pokemon run` loads. Changing it changes
  what every official run sees, so don't: put changes in a new `config-5.1+`,
  which becomes the casual default by itself and is promoted to official by
  moving that one constant. The leaderboard ranks `config-5.x` only
  (`RunSummary.leaderboard_eligible`).
- `config-4.0` (self-directed, sliding window) and `config-3.13` (the previous
  frozen official config, TaskMaster-enabled) are **legacy but runnable**: they
  load, list, queue as casual runs and continue. They are not the default and
  not leaderboard-eligible — `turns` counts game turns plus TaskMaster turns
  there and game turns only on 5.x, and turns is the ranking tiebreak.
- Never write the default config's name into a help string or an error. Ask
  `src.config.default_config_stem()` (or, in the browser, `/api/configs`'s last
  entry); the old literal fanned out to eight sites.
- Registries are data, not code: adding a model, ROM, start state, benchmark or
  gate is a YAML edit in `configs/`.

## Where things live

```
src/agent/      turn loop, output schema, TaskMaster
src/emulator/   mGBA TCP client, screenshot encoding, OCR
src/referee/    out-of-band memory reads + the gate ladder (never imported by the agent)
src/app/        control plane: queue, executor, run index, registries
src/dashboard/  FastAPI + WebSocket API, the Svelte SPA, the MP4 recorder
src/cli/        one module per `pokemon` subcommand
configs/        every registry + every agent config
local/runs/     run folders (events.jsonl, screenshots, savepoints, summary)
docs/           benchmark.md, cli.md, control-center.md, recording.md
```

The referee reads game memory; **the agent must never see it**. That module
boundary is the project's whole premise — nothing under `src/agent/` may import
`src/referee/`.
