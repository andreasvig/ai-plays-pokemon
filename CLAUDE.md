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

Casual defaults: latest config, default ROM, no early stop, no recording. Every
value is validated at enqueue — an unknown model/config/rom/event is a 400 that
names the valid ones. `--rom` switches the emulator for you.

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

**Baseline as of 2026-08-02: 527 pass, 8 fail.** Those 8 are pre-existing and
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
- `configs/config-3.13.yaml` is the **frozen official benchmark config**.
  Changing it changes what every official run sees; the control center
  hardcodes it (`src/app/executor.py`). `config-4.0` is the current
  self-directed line and what a bare `pokemon run` loads.
- Registries are data, not code: adding a model, ROM, benchmark or gate is a
  YAML edit in `configs/`.

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
