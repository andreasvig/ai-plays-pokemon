# Old vs new agent system — 25-turn comparison plan

> Source: conversation 2026-09-06. Decisions by Andreas: old arm = `config-4.0` (option A); one run per arm for now, repeats only if inconclusive; endpoints pinned on both arms.

## Question

Does the append-and-compact agent (`config-append.yaml`) beat the self-directed sliding-window agent (`config-4.0.yaml`) on **speed**, **price**, and **game progress** over 25 turns from the canonical FireRed bedroom save?

This is a system-versus-system test. The arms differ in prompt, output schema, image history, context strategy and transport. It answers "which system should we run", not "which ingredient matters".

## Arms

| | Old (`config-4.0`) | New (`config-append`) |
|---|---|---|
| Context | sliding window, last 10 turns as text, 1 historic screenshot | full conversation appended, compaction (handover) after turn 15 |
| Transport | pydantic-ai → OpenRouter, registry alias `model(level)` | raw OpenRouter body, provider profile |
| Endpoint | pinned via registry `provider.only` + `allow_fallbacks: false` | pinned via profile `endpoint` |
| Output | prompted/tool per registry | per profile (tool / prompted JSON) |
| Budgets | registry defaults | none (endpoint ceilings) |

Same on both arms: save (`configs/saves/pokebench-v1`), 25 turns, OCR cleanup model, referee ladder `checkpoints-firered-firstbadge.yaml` (observe-only), savepoints, `max_spend_usd` safety net, one warm emulator, no mid-run restart.

## Models (one run per arm)

| Model | Effort | Endpoint tag | Why |
|---|---|---|---|
| `openai/gpt-6-astra` | medium | `openai` ($10/$50 per M, cache read $1) | frontier, OpenAI family not yet profiled — needs registry entry, profile and a live probe |
| `google/gemini-3.8-flash` | medium | `google-ai-studio` | cheap, system-marker caching known |
| `z-ai/glm-5.3-flash` | high | `z-ai/fp8` | cheapest, different family |

Effort is held equal across arms per model (registry level on the old arm, profile default on the new arm).

## Metrics (per arm, from `events.jsonl` + `run_summary.json`)

- **Speed**: per-turn wall clock (`turn_start` → next `turn_start`), LLM time per turn (`turn_user_message` → `turn_usage`), emulator/settle time (shared control). Compaction requests count as LLM time on the new arm.
- **Price**: OpenRouter billed cost per turn and total, cost curve over turns (the new arm grows within a segment), compaction cost, cached-input share and implied cache.
- **Performance**: referee checkpoints reached and the turn each was reached; furthest checkpoint; self-grade true-rate as secondary.
- Also recorded: retries, transport errors, output-validation retries, truncations.

## Procedure

1. Registry: add `gpt-6-astra`, `gemini-3.8-flash`, `glm-5.3-flash` to `configs/models.yaml` with pinned providers; sync release dates.
2. Profile: add `openai/gpt-6-astra` to `configs/provider-profiles.yaml` from the replay and cache probes (reasoning replay consumed? caching automatic without markers?).
3. Runner `test_scripts/run_system_comparison.py`: per model, old arm then new arm, 25 turns each, `compaction.every_n_turns: 15`, manifest + logs under `local/system-comparison/`.
4. Smoke: 2 turns per arm on GLM before the full campaign (the legacy path last ran 2026-08-05).
5. Campaign, then `test_scripts/summarize_system_comparison.py` → `artifacts/system-comparison/results.md|json`.

## Budget and time

Rough spend $8–15, nearly all Astra. About 3–5 hours serial on one emulator (10–20 min per 25-turn run).

## Caveats named up front

- n = 1 per arm: progress differences of one checkpoint are within noise (Gemma today: 0/2 → 2/8 on the same question). Speed and price have 25 samples per arm and will separate.
- The old arm's cost per turn is flat; the new arm's rises until compaction. Report the curve, not only the total.
- Astra's profile is new; a probe failure or an unconsumed replay is a finding, not a blocker.
