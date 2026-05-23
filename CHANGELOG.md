# Changelog

## 2026-05-23 — `pokemon` console CLI replaces `tests/test_phase5.py`

### What
- New top-level `pokemon` console script with subcommands `run`,
  `launch`, `report`, `snapshot`. Replaces the awkward
  `python tests/test_phase5.py …` invocation.
- `pokemon run` is the unified single+sequential entry point. Pairing
  rules between `--config` and `--model`: 1×1 single, 1×N / N×1 fan-out,
  N×N paired 1:1, N×M (N≠M, both >1) rejected.
- `tests/test_phase5.py` and `tests/test_sequential.py` deleted —
  helpers lifted into `src/cli/runner.py`. `tests/` is back to holding
  only harness validation scripts (`test_emulator.py`, `test_phase3.py`,
  `test_phase4.py`).

### Why
The agent entry point lived under `tests/test_phase5.py` as a relic of
the phase-by-phase build. After phase 5 shipped, it stopped being a
test and became the way to run the project — but the name, location,
and import path (`sys.path.insert(0, …)`) all still read like a scratch
test. New contributors and future-Andreas alike had to be told
"that's actually the main entrypoint, ignore the name." This refactor
makes the invocation match what it is: a CLI.

### Pieces
- **`pyproject.toml`** (new) — minimal setuptools project declaration.
  Dependencies mirror `requirements.txt`. `[project.scripts]` wires
  `pokemon = "src.cli.main:main"`. Setup adds one step: `pip install -e .`.
- **`src/cli/main.py`** (new) — top-level dispatcher. Reads
  `sys.argv[1]`, rewrites `sys.argv` so each subcommand's existing
  `main()` sees its own argparse program name + flags, then delegates
  via lazy `import_module`. Heavy pydantic-ai imports stay lazy.
- **`src/cli/runner.py`** (new) — the lifted launcher helpers from
  `tests/test_phase5.py` (`run_prepare_phase`, `run_connect_phase`,
  `run_single_loop`, `cleanup_handle`, `prepare_config`, AppleScript
  helpers) plus a unified `main()` with the pairing matrix above.
- **`src/cli/launch.py`, `src/cli/report.py`, `src/cli/snapshot.py`** —
  untouched at the function level; `main.py` just routes to them.
- **`README.md`, `docs/cli.md`** — rewritten "Running it" /
  "CLI Reference" sections against the new commands.
- **`src/config.py`, `src/cli/launch.py`** — comment + docstring
  references to the old test paths updated to `pokemon run`.

### Verification
`pokemon --help` and `pokemon run --help` print expected subcommands
and flags. `python -m src.cli.main run --help` works as a fallback for
anyone who hasn't run `pip install -e .` yet.

## 2026-05-22 — gemma-4-31b throughput-sort + provider diagnostic

### What
- `gemma-4-31b(thinking)` now sets `provider: {sort: "throughput"}` —
  verified by 24 direct-API probes (`scripts/probe_throughput.py`) and
  a 3T agent run that all completed cleanly.
- `src/agent/turn.py` Done + ERROR lines now print
  `provider=<name>` so future flaky-provider debugging has a name to
  blocklist without re-running probes.

### Why
The 2026-04-24 default routing on Gemma 4 31B averaged 89.9s/turn —
unusable. The earlier 2026-05-22 throughput attempt failed because one
provider was returning `finish_reason: null` and crashed pydantic-ai
validation. Today's re-probe shows that failure mode no longer
reproduces — throughput-sort now lands on {Ambient, Novita, Together,
Chutes} and every endpoint returns a valid response shape.

### Pieces
- **`configs/models.yaml`** — `gemma-4-31b(thinking)` gets
  `provider: {sort: "throughput"}` + a long comment block with the
  empirical per-provider latencies from the probe.
- **`scripts/probe_throughput.py`** (new) — 30-line script that calls
  OpenRouter directly with `provider.sort=throughput` and dumps
  `{provider, finish_reason, latency, tokens}` per call. Reusable for
  diagnosing any model with provider-routing issues. Skips providers
  via `PROBE_IGNORE=Together,Chutes` env var. Run as
  `OPENROUTER_API_KEY=... ./venv/bin/python scripts/probe_throughput.py 16`.
- **`src/agent/turn.py`** —
  `_extract_provider_from_messages(messages)` returns the OpenRouter
  provider name (or pydantic-ai's `model_name` as a last-resort
  fallback). The Done and ERROR lines now print it, so multi-turn
  variance attributable to provider-shuffling shows up in the log
  directly.

### Verification + caveat
- 24/24 direct probes clean. Per-provider average latency on a 288-token
  request: **Novita 4.8s, Ambient 5.1s, Together 16.8s, Chutes 22.4s**.
- 3T agent run with full payload (3800-tok input, 400-1300 out): T1=26.7s
  (fast provider), T2=114.8s, T3=150.1s. Average 97.2s/turn — slightly
  worse than 2026-04-24's 89.9s default-routing because the slow tail
  (Chutes/Together) drags the average. Best case is 3× better than the
  baseline.
- The `provider=<name>` diagnostic currently falls back to pydantic-ai's
  `model_name` (e.g. `google/gemma-4-31b-it-20260402`) instead of the
  actual OpenRouter provider (Ambient/Novita/...) because
  `msg.provider_details` doesn't expose `provider` in this pydantic-ai
  version. To get the true name today, use the probe script.

### If avg latency matters more than crash-fix
Set `provider.order: ["Novita", "Ambient"]` in the registry entry
alongside `sort: "throughput"` — should pin to the fast pair and only
fall back to other throughput-sorted providers if both are down.

## 2026-05-22 — Model selection moved from config files to CLI flag

### What
`llm_model` (and `llm_fallback_models`) is no longer a config-file field.
Model choice is a required CLI flag on the agent entry points:

```bash
# Single run
./venv/bin/python tests/test_phase5.py --config configs/config-3.9.yaml \
    --model "gemini-3.5-flash(medium)" --turns 50

# Sequential, fan-out (1 config × N models, same baseline)
./venv/bin/python tests/test_sequential.py --config configs/config-3.9.yaml \
    --models "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" --turns 50

# Sequential, paired (N configs × N models, 1:1)
./venv/bin/python tests/test_sequential.py \
    --configs configs/config-3.5.yaml configs/config-3.6.yaml \
    --models "gpt-5.5(medium)" "claude-opus-4.7(medium)" --turns 50
```

The flag values are aliases from `configs/models.yaml` — the registry
stays the single source of truth for per-model settings (reasoning,
temperature, provider routing, `output_mode`). The CLI alias is injected
into the config dict in `load_config()`, then `_resolve_llm_alias()`
expands it into the full settings block exactly as before.

### Why
The 18 existing `config-3.X.yaml` files differed mostly by the
`llm_model:` line — true behavioral knobs (`upscale_factor`,
`historic_images_count`, `vision_mode`, prompt) varied across ~3 files.
The rest were model bookkeeping that duplicated `models.yaml`. Moving
model choice to the CLI makes configs answer one question — "what
experimental setup did this run use" — and lets the same config run
against any model without copy-paste.

### Pieces
- **`src/config.py`** — `load_config(path, *, llm_alias=None)` injects
  the CLI alias into `config["llm_model"]` before registry resolution.
  Rejects YAML files that still carry `llm_model` or
  `llm_fallback_models` with a clear "use --model on CLI" error. Alias
  is optional at the `load_config` level so non-agent callers
  (`snapshot.py`, `test_emulator.py`) keep working — the CLI layer
  enforces the requirement for agent runs.
- **`tests/test_phase5.py`** — `--model "<alias>"` required. Run dir
  slug becomes `phase5_test__<model-slug>` for distinguishability.
- **`tests/test_sequential.py`** — `--models A B C` required. Two
  semantics via a mutually-exclusive group: `--config X --models A B`
  fans out (1 baseline × N models = N runs), `--configs X Y --models
  A B` pairs 1:1 (mismatched lengths → error). Cartesian (M×N) not
  supported — use a shell `for` loop. Per-run `run_label = "<stem> ·
  <alias>"` so the dashboard `/current` page shows which model is
  driving the current run.
- **`src/cli/report.py`** — report HTML LLM field prefers the alias
  over the raw OpenRouter id.
- **`configs/config-*.yaml`** — `llm_model:` and
  `llm_fallback_models:` stripped from all 18 files. "Model
  Configuration" section header renamed to "Vision Configuration"
  (vision_mode + vlm_model still live there). Rich prose comments
  about specific models intentionally left in place as historical
  notes — configs are now generic templates that can run against any
  model.

### Verification
3T sequential smoke: `--config configs/config-3.9.yaml --models
"gemini-3.5-flash(medium)" "claude-opus-4.7(medium)"`. Both runs
completed, dashboard `/current` page reloaded cleanly at the model
transition, run dirs distinguishable as
`<ts>_config-3.9__gemini-3-5-flash-medium/` and
`<ts>_config-3.9__claude-opus-4-7-medium/`.

## 2026-05-22 — Sequential orchestrator + dashboard `/current` URL

### What
Replaced the parallel-mGBA system (`scripts/run_multi.py`, slots 2/3/4,
`socketserver-{2,3,4}.lua`) with a sequential orchestrator that runs N
configs back-to-back against a single warm mGBA + Lua connection. The
user loads `socketserver-1.lua` once after the first config's Scripting
window appears; every subsequent config runs against that same
connection with a snapshot reload between runs.

Parallel was dropped because macOS AppleScript focus dedup defeated
AXRaise for the 2nd+ mGBA process (Scripting window never opened ~50%
of the time), and the dashboard's per-process port-3420 singleton
silently lost its bind on slot 2. Sequential sidesteps all of it: one
mGBA, one Lua connection, one dashboard.

### Pieces
- **`tests/test_sequential.py`** (new) — CLI orchestrator. Takes
  `--configs path1 path2 … --turns N`. Single prepare + connect, then
  loops `run_single_loop` per config. Each run gets its own RunLogger,
  EventBridge, ScreenStreamer, dashboard registration, report.html.
- **`tests/test_phase5.py`** (rewritten) — building blocks exposed as
  importable functions: `run_prepare_phase`, `run_connect_phase`,
  `run_single_loop`, `cleanup_handle`. CLI surface preserved for
  single-config runs (`--slot` dropped).
- **`src/cli/slots.py`** (collapsed) — single `_SLOT` for slot 1
  (port 8888, `lua/socketserver-1.lua`, `/tmp/mgba_stream_1.png`).
  `get_slot(2|3|4)` rejects with explanatory error.
- **Dashboard `/current`** — stable URL that follows the
  latest-registered run. `_render_run_html()` injects
  `RUN_PREFIX_OVERRIDE` (so inner WS/API calls hit the latest run's
  prefix) and `IS_CURRENT_VIEW` (so WebSocket close code 1008 triggers
  `location.reload()` instead of the default 2s reconnect).
- **Deleted**: `scripts/run_multi.py`, `lua/socketserver-{2,3,4}.lua`.

### Bug fixes during validation
1. **WS handlers ignored unregister.** `ws_events` and `ws_screen`
   only looked up the session at connect-time; once running, they
   never re-checked the registry. When run 1 unregistered, both WS
   loops kept streaming run 1's last state forever — the page never
   got the 1008 close that triggers `/current` reload. Fixed: each
   iteration polls `_REGISTRY.get(run_id)`; if `None`, close with 1008.
2. **`config["paths"]["stream"]` only stamped on configs[0].**
   `run_prepare_phase` set `paths["stream"] = "/tmp/mgba_stream_1.png"`
   on the first config dict. Subsequent runs got their own deep-copied
   config without that field, so `start_dashboard`'s fallback resolved
   `stream_path` to `<run_dir>/mgba_stream.png` — a path Lua never
   writes to. Result: run 2's screen pane showed a broken-image icon.
   Fixed: `run_single_loop` re-stamps `paths.{stream,screenshot,lua}`
   + `emulator.port` from `handle["slot_cfg"]` on every call.

### Validation
- 2×10 turn run (gemini-3.5-flash medium + gemini-3.1-pro low):
  both completed cleanly; run 2 reached Pallet Town.
- 2×3 turn run after both fixes: dashboard `/current` page reloaded
  cleanly across the run-1→run-2 transition, run 2's GBA screen
  loaded frames immediately.

### OpenRouter provider-routing notes (`configs/models.yaml`)
Documented three routing quirks hit while debugging earlier provider
errors: (1) OpenRouter wraps upstream 5xx in HTTP 200 + `{"error": …}`
body, surfacing as pydantic-ai `'NoneType' object is not subscriptable`;
(2) multimodal capability filtering auto-narrows providers when image
input is present; (3) DeepInfra's `-turbo` variants of Gemma are
text-only and 405 on image input. `gemma-4-31b(thinking)` and
`gemma-4-26b-a4b(thinking)` entries left with NO `provider` block —
let OpenRouter default-route and rely on capability filtering.

### Files
- `tests/test_sequential.py` (new), `tests/test_phase5.py`
- `src/cli/slots.py` (new), `src/cli/launch.py`
- `src/dashboard/server.py`, `src/dashboard/static/index.html`,
  `src/dashboard/__init__.py`
- `src/emulator/emulator.py`
- `configs/models.yaml`
- `configs/config-3.11.yaml` (Gemma 4 31B thinking, new)
- `configs/config-3.12.yaml` (Gemma 4 26B-a4b thinking, new)
- `lua/socketserver-1.lua` (rename from `socketserver.lua`)

---

## 2026-05-21 — Claude Opus 4.7 (xhigh) 20T probe (config 3.10) — clamp confirmed

### What
Follow-up to the Opus 4.7 (medium) 10T standout. Probed whether the
`xhigh` effort tier behaves differently on Opus 4.7. Pre-experiment
hypothesis (from docs research, same day): xhigh likely clamps to high
because Anthropic's native API for Opus 4.7 uses `thinking: {type:
"adaptive", effort: "low"|"medium"|"high"}` only — no native xhigh.
OpenRouter's docs explicitly state effort tiers map "to the nearest
supported level" on backends that don't support all five.

Config-3.10 = clone of 3.9 with model swap only (medium → xhigh).
20 turns from `bedroom_start`.

### Result: clamp confirmed (or adaptive thinking ignores the hint)

| metric              | (medium) 10T | (xhigh) 20T | Δ        |
|---------------------|-------------:|------------:|---------:|
| Cost / turn         | $0.0523      | $0.0551     | +5%      |
| Latency / turn      | 8.3s         | 8.7s        | +5%      |
| Output tokens / turn| ~300         | ~319        | +6%      |
| Grade rate (true)   | 78%          | 84%         | +6pp     |
| End state           | Pallet Town  | Oak's Lab   | +1 scene |

All numeric deltas are within run-to-run noise. The deeper end state is
better attributed to 2× turn budget than to the effort tier. Two
consistent explanations, indistinguishable from this single A/B:

1. **OpenRouter clamps xhigh→high** before forwarding to Anthropic (per
   their "nearest supported level" doc).
2. **Adaptive thinking picks its own budget** regardless of the hint —
   Opus 4.7's `thinking.type: "adaptive"` is dynamic by design.

Either way, **don't expect xhigh to materially outperform medium on
Opus 4.7.** Prefer `(medium)` for cost; the 5% xhigh premium buys ~noise.

### Latency
Median ~7s, p99 = T17 outlier at 30.9s / 1415 output tokens / $0.085 —
one extended-thinking spike, otherwise the band stayed at 5-10s/turn.
Total run: $1.10 / 20T / ~4 min wall clock.

### Files
- `configs/models.yaml` — `claude-opus-4.7(xhigh)` entry added with full
  observed numbers + clamp-vs-adaptive discussion
- `configs/config-3.10.yaml` — new (clone of 3.9, model swap only)
- `local/runs/2026-05-21_09-45-11_phase5_test` — full 20T run dir

### Failure mode encountered (worth recording)
First launch attempt failed at the config loader (`ValueError: Unknown
llm_model alias`) because the registry only had Opus 4.7 entries for
high/medium/low/minimal — xhigh was the documented-but-not-registered
gap. Lesson: when adding a new model family, include all five effort
tiers if the family supports them, even if the immediate run only
uses one. Two-minute fix; retry succeeded.

---

## 2026-05-21 — Claude Opus 4.7 (medium) 10T probe (config 3.9) — registry standout

### What
First Opus entry in the registry. `anthropic/claude-opus-4.7` on config-3.9
(clone of 3.8 — upscale=6, K=1, `bedroom_start`, model swap only). Same
Anthropic-family constraint as Haiku 4.5 / Sonnet 4.6 (`output_mode:
"prompted"` required; tool_choice="required" silently strips extended
thinking on Anthropic routes via OpenRouter).

### Result: first non-Gemini model to clear the spatial-grounding wall

End state: **Pallet Town** (out of the house!) with map memory written:
"Small town. Player's house at north-west. Professor Oak's lab to the
south-east. Exit to Route 1 at north."

This is the only Anthropic model — and the only non-Gemini model — to
have ever exited the player's house in this project. Same config and
snapshot as Sonnet 4.6 (medium), which was stuck in 2F bedroom for all
10T two hours earlier.

### Numbers vs Sonnet 4.6 (medium) baseline

| metric | Opus 4.7 (medium) | Sonnet 4.6 (medium) |
|---|---:|---:|
| End state | Pallet Town | 2F bedroom |
| Grade rate | 14/18 true (78%) | 0/18 (0%) |
| Latency / turn | 8.3s | 47.7s |
| Cost / turn | $0.0523 | $0.0557 |
| Output tokens / turn | ~300 | ~2338 |
| Total / 10T | $0.523 | $0.557 |

The output-tokens delta is the headline finding: **Opus reasoned ~8× more
compactly than Sonnet on the same task with the same prompt**. That's
what kept per-turn cost slightly *below* Sonnet despite Opus's higher
rate card ($5/$25 per M vs Sonnet's $3/$15). The "expensive frontier
model" framing didn't materialise — Opus is the best capability-per-
dollar Anthropic entry on this task by every metric.

### Where it sits in the registry

| model (medium effort) | end state at 10T | $/turn |
|---|---|---:|
| **claude-opus-4.7** | **Pallet Town + map memory** | **$0.052** |
| gemini-3.5-flash | (not measured at 10T; 50T → Route 1 + Bulbasaur lvl 6) | $0.013 |
| gpt-5.5 | 2F bedroom (config-3.0 baseline) / Oak's lab (upscale=6 A/B) | $0.030 |
| claude-sonnet-4.6 | 2F bedroom | $0.056 |
| claude-haiku-4.5 | 2F bedroom (T10 probe) | $0.030 |
| grok-4.3 | 1F house (T36 abort) | $0.016 |
| grok-build-0.1 | 2F bedroom (T4 abort) | $0.018 |
| perceptron-mk1 | 1F house (T19 abort) | $0.002 |

Recommended next: 50T run on config-3.9 to see how far Opus goes past
Pallet Town. Gemini 3.5 Flash (medium) reached Route 1 + Bulbasaur lvl 6
by T50 (current registry leader on raw progress) — Opus at 10T already
matches the early arc; the 50T question is whether it scales further or
plateaus around the starter pickup.

### Files
- `configs/models.yaml` — Opus 4.7 entries added (high/medium/low/minimal),
  medium populated with full observation block
- `configs/config-3.9.yaml` — new (clone of 3.8, model swap only)
- `local/runs/2026-05-21_09-25-29_phase5_test` — full 10T run dir

---

## 2026-05-21 — Claude Sonnet 4.6 (medium) 10T probe (config 3.8)

### What
First Anthropic Sonnet entry in the registry. `anthropic/claude-sonnet-4.6`
on config-3.8 (clone of 3.6 — upscale=6, K=1, `bedroom_start`, model swap
only). Same Anthropic-family constraint as Haiku 4.5 (`output_mode:
"prompted"` required; tool_choice=required silently strips extended
thinking).

### Result
**Same spatial-grounding failure as the bedroom-stuck cluster, but
qualitatively the highest-trust entry in that cluster.**

End state: 2F bedroom for all 10 turns — never went down the stairs.
Failure family includes Grok 4.3, Flash Lite (high), Qwen3.6-Plus,
Perceptron Mk1, Haiku 4.5 (medium), Grok Build 0.1 (medium).

What set Sonnet apart inside that family:
1. **Honest self-grading**: 0/18 true (0%). Never gamed `last_turn_succeeded`.
   Grok Build 0.1 falsely claimed true at T4; GPT-5.5 (medium) on
   config-3.0 had 70% grade rate while stuck in the bedroom. Sonnet
   correctly graded every stuck turn as false.
2. **Explicit stuck-state diagnosis at T10**: "Screenshots have been
   identical for 9+ turns despite many varied button presses including
   all directions. This almost certainly means something is blocking
   input — possibly a hidden dialogue/event." Tried `[b, b, b, a, up,
   up, up, right, right]` to recover. No prior model has surfaced the
   stuck state explicitly in prose.
3. **5× faster per turn than Grok Build 0.1 (medium)**: 47.7s/turn vs
   118.6s/turn on the same task.

### Cost
Total $0.5565 / 10T = **$0.0557/turn** — most expensive registry entry
by ~2× (Haiku 4.5 medium $0.0303, GPT-5.5 medium $0.0298). T9 alone
burned $0.15 with 8362 output tokens (extended-thinking spiral, 160s
latency). 50T extrapolation: ~$2.80.

### Verdict
Same bedroom-stuck capability tier as peers, but Sonnet's reasoning
quality + grading honesty make it the most trustworthy debug subject
if/when we break the spatial-grounding bottleneck (higher upscale,
better tile-coordinate prompting, vision_mode tweak). Not recommended
at current cost without that fix.

### Files
- `configs/models.yaml` — Sonnet 4.6 entries added (high/medium/low/minimal),
  medium populated with observed numbers + notes
- `configs/config-3.8.yaml` — new (clone of 3.6, model swap only)
- `local/runs/2026-05-21_09-11-23_phase5_test` — full 10T run dir

---

## 2026-05-21 — Grok Build 0.1 (medium) probe (config 3.7) — aborted at T4

### What
First probe of `x-ai/grok-build-0.1`, xAI's "fast agentic-coding" Grok variant
(256K ctx, $1/$2 per M, multimodal, single provider xAI). Reasoning shape
confirmed effort-tiered via direct OpenRouter probe (all four tiers
high/medium/low/minimal accepted, summary text returned natively without
needing `summary: "auto"`). Added `grok-build-0.1(medium)` to `models.yaml`
and `config-3.7.yaml` (clone of 3.6 with model swap only — upscale=6, K=1,
`bedroom_start` snapshot).

### Why aborted
Two failure modes in 4 turns:

1. **Latency monotonic degradation**: 78s → 83s → 147s → 165s. Reasoning
   length scaled with message history; extrapolating, 50T would have taken
   >2 hours wall clock. Same family as Grok 4.3 (71s/turn) and Kimi K2.6
   (110-225s/turn).
2. **Spatial grounding fail + gamed self-grade**: stuck in 2F bedroom for
   all 4 turns. T4 self-reported `last_turn_succeeded: true` while still
   in the bedroom — the same "conservative prediction trivially matched"
   failure mode GPT-5.5(medium) showed on config-3.0. Same root cause as
   Grok 4.3 / Flash Lite (high) / Qwen3.6-Plus / Perceptron Mk1 / Haiku
   4.5 (medium).

Cost was in line with expectations ($0.0184/turn avg, ~$0.92/50T extrapolated)
but irrelevant given the capability + latency fail.

### Files
- `configs/models.yaml` — `grok-build-0.1(medium)` entry added with full
  context (reasoning shape, registry placement) + 4T observed numbers and
  abort notes
- `configs/config-3.7.yaml` — new (clone of 3.6, model swap only)
- `local/runs/2026-05-21_08-48-00_phase5_test` — 4-turn run kept for reference

### Open
- Whether `(low)` or `(minimal)` effort tiers fare better — Grok 4.3's
  always-on reasoning was the bottleneck and a lower effort tier on
  Build 0.1 may cut latency. Not pursued: capability fail at medium
  already puts this in the bottom tier alongside Grok 4.3 / Haiku 4.5.

---

## 2026-05-20 — Upscale-factor A/B on GPT-5.5(medium) (configs 3.5 / 3.6)

### Experiment
Probe of whether GPT-class models benefit from feeding screenshots at higher
upscale. Two configs, identical except `screenshot.upscale_factor`:

- `config-3.5.yaml` — upscale=3 (720×480, current production default)
- `config-3.6.yaml` — upscale=6 (1440×960, 4× more pixels)

Same model (`gpt-5.5(medium)`), same prompt, same `historic_images_count=1`,
same snapshot (`bedroom_start`), 20 turns each. Run dirs:
`local/runs/2026-05-20_16-35-00_phase5_test` (3.5) and
`local/runs/2026-05-20_16-43-03_phase5_test` (3.6).

### In-game progress (n=1 each, treat directionally)
- **upscale=3** ended in Professor Oak's lab, **no starter received**
- **upscale=6** ended in Professor Oak's lab, **Squirtle received** + rival Green noted

Both runs are a large jump from the prior gpt-5.5(medium) baseline (config-3.0,
stuck in 2F bedroom) — but that lift is attributable to other unrelated changes
since (`historic_images_count=1`, `summary: auto`, prompt updates), not upscale.
The clean A/B signal is the starter-received delta.

### Input-token and cost impact

| metric | upscale=3 | upscale=6 | Δ |
|---|---:|---:|---:|
| total input tokens (20T) | 101,546 | 148,950 | **+46.7%** |
| avg input tokens / turn | 5,077 | 7,447 | +2,370 |
| avg output tokens / turn | 539 | 481 | -11% |
| LLM total cost | $0.630 | $0.815 | **+29%** |
| avg LLM latency / turn | 15.5s | 14.4s | -7% |

The image alone goes from 720×480 to 1440×960 (4× pixels) but GPT-5.5's token
accounting on `detail: high` images only roughly doubles per image — combined
with the two images per turn (`historic_images_count=1`), this works out to
+2.4k input tokens/turn. Output tokens actually *dropped* at upscale=6, so the
USD delta (+29%) is smaller than the input-token delta (+47%).

### Side finding: OpenAI `reasoning.summary: "auto"`
Without it, only 3/10 turns had visible reasoning summary text in
`llm_thinking` events even though every turn was billed for ~700-2600 hidden
reasoning tokens. With it, visible-summary turns jumped to 11/20. Added to all
three OpenAI entries in `configs/models.yaml`. Underlying thinking was on
either way — just the summary text from OpenAI is intermittent unless asked.

### Caveats
- n=1 per side. Run-to-run variance for this agent is high (emulator timing,
  model nondeterminism). The starter-received win could flip on a re-run.
- Comparison script: `scripts/compare_upscale_runs.py <run_dir_3> <run_dir_6>`
- Worth replicating 3×20T per upscale before treating as ground truth.

### Files
- `configs/config-3.5.yaml` — new
- `configs/config-3.6.yaml` — new
- `configs/models.yaml` — `summary: "auto"` added to all openai entries; `gpt-5.5(medium)` observed numbers refreshed
- `scripts/compare_upscale_runs.py` — new

---

## 2026-04-25 — Session 9: GameAction Schema Overhaul (config 3.0)

### New Output Schema: `reasoning` + `last_turn_succeeded`
- Replaces the old three-field `i_saw / i_did / i_expect` shape with one free-form `reasoning` prose field plus a `last_turn_succeeded: bool | None` grade
- `reasoning` ends with a one-sentence prediction the next turn grades against; no enforced internal structure (observe / plan / predict in any order)
- `last_turn_succeeded`: strict definition — `true` only if the current screen matches what last turn predicted; `false` otherwise (including vague predictions); `null` only on the first turn of a task
- Forces the "evaluate last action" step that the old prompt asked for but had no output slot for
- `i_did` past-tense fiction problem solved — the schema no longer asks the agent to describe an action that hasn't fired yet
- Pydantic `Field` descriptions kept sparse (one-line "what is this field"); behavioral rules live in the system prompt where iteration is fast

### Harness updates (`src/agent/turn.py`)
- `_GAME_ACTION_KEYS` rewritten for the new field set
- Previous-turns block formatter rewritten: each prior turn renders as `actions: ... / reasoning: ... / did this turn succeed?: ...`. Grade for turn k comes from turn (k+1)'s `last_turn_succeeded`. The most-recent prior turn shows `<for you to decide this turn>` — that's the value the agent fills into `last_turn_succeeded` this turn
- Terminal trace summary, `turn_explanations` shape, and `_write_run_summary` all updated to the new fields

### Context Trimming (`max_turns_before_trim` wired up)
- The config knob existed in 2.x configs but was dead code — never read by `src/`
- Now plumbed through `TurnManager` and applied in `_build_turn_message`: when set, only the most recent N turns render in the Previous Turns block; older turns are dropped with an italic "(Earlier turns have been truncated. Showing the last N of M turns.)" notice
- Turn numbering still reflects actual turn numbers; grade-threading still works since each visible turn's follower (except the most recent) is also visible
- `null` disables trimming (matches the convention from configs 1.0/1.1)
- config-3.0 uses `max_turns_before_trim: 10`

### Dashboard + Report Parity (`src/dashboard/static/index.html`, `src/cli/report.py`)
- Live and replay rendering updated for the new schema (3 spots in dashboard, 3 spots in report)
- Grade label inline: ✅ succeeded / ❌ failed / ➖ n/a (first turn)
- `reasoning` rendered as a single block instead of the three sub-blocks

### Encrypted Reasoning at Low Effort (Gemini 3 family)
- During smoke testing, discovered Gemini 3 Flash at `effort: low` + `tool_choice: required` returns reasoning content as an encrypted `thought signature` blob (`reasoning.encrypted`, `format: google-gemini-v1`), with `reasoning = None` plaintext
- At `effort: high`, plaintext markdown reasoning headers come through normally (10/10 turns in verification run)
- The encryption is Google's mechanism for preserving reasoning state across multi-turn tool calls — useful to the model, opaque to us
- Captured in detail in private notes

### config-3.0.yaml
- New file. System prompt rewritten around the new schema: Reasoning Guidance + Last Turn Succeeded sections, "Calibrate ambition to recent success" replacing the old static "be ambitious" rule, OCR Text and Previous Turns descriptions tightened
- Currently uses `gemini-3-flash(high)` alias

### Model Registry Updates (`configs/models.yaml`)
- `gemini-3-flash(high)`: refreshed observed numbers post-schema-overhaul. New sample: 20T across two runs (10T no-trim + 10T trim=5). Cost dropped $0.0090 → $0.0053/turn (~41% cheaper) and latency dropped 27.0s → 9.9s/turn (~63% faster) — the leaner output schema cut output tokens substantially
- `gemini-3.1-pro(low)`: first sample, 50T full run. $0.0158/turn, 11.4s/turn, 25/50 self-graded successes. Trajectory: bedroom → 1F → outside → Oak's Lab → got Squirtle → won rival battle (Squirtle Lv6) → back in Pallet Town overworld by T46. Slow start (7 consecutive bedroom failures T2-T10) — same encrypted-reasoning failure mode seen with Flash Lite high and Qwen3.6-Plus

---

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
- Findings captured in private notes: passthrough whitelist, silent-drops list, image-input behavior, when to bypass for direct provider APIs

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
