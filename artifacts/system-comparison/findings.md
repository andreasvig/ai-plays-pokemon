# Old vs new agent system — verdict from the 25-turn comparison (2026-09-06)

> Source: campaign run 2026-09-06 14:59–16:15; generated tables in [results.md](results.md); running notes in [campaign-notes.md](campaign-notes.md); design in [plan.md](plan.md). One run per arm — differences of one checkpoint are within noise; speed and price have 25 samples per arm.

## Headline

| Model | Speed (LLM s/turn, mean) | Price (25 turns) | Progress (furthest checkpoint, turn reached) |
|---|---|---|---|
| GLM 5.3 Flash | old 35.1 → **new 22.0** | old $0.026 → **new $0.0215** | Oak's Lab: old turn 9, new turn 16 |
| Gemini 3.8 Flash | old 10.8 → **new 7.9** | **old $0.39** → new $0.48 | old rival battle (25) → **new Route 1 (25)** |
| GPT-6 Astra | old 5.3 → **new 4.6** | **old $1.72** → new $4.61 | Route 1: old turn 25 → **new turn 18** |

Wall clock per turn was equal or slightly better on the new arm everywhere (GLM 46 → 36 s, Gemini 26.5 → 26.8 s, Astra 22.0 → 23.3 s); for the two fast models emulator settling dominates the turn, so LLM-time gains do not show up in wall clock.

## Speed — new wins on every model

LLM time per turn fell 13–37% on the new arm. Two reasons show in the traces: no per-turn re-reading of a 10-turn text history and one screenshot (the old prompt is rebuilt every turn, ~8–10k tokens; the new prompt is a cached prefix plus one new turn), and fewer output retries (GLM: 3 → 0). Compaction adds one request per 15 turns (5–29 s).

## Price — depends entirely on whether the endpoint caches a growing image prefix

- **GLM / Z.AI**: 85% of gameplay prompt tokens cached. New arm 17% cheaper despite the prompt growing 3.3k → 26.5k tokens.
- **Gemini / AI Studio**: only the system block is cacheable through OpenRouter (7%). Per-turn cost doubled over the run ($0.010 → $0.0215) as the prompt grew to 26.7k; new arm 23% dearer. Compaction cost $0.031.
- **Astra / OpenAI**: caching stopped at the first screenshot on every turn (1,192 tokens) and the rest was billed at the 1.25× cache-write price; per-turn cost tripled ($0.07 → $0.20), compaction cost $0.42, total 2.7× the old arm. Cause found (`artifacts/provider-compatibility/openai-image-prefix-caching-probe.md`): when the request's final turn contains an image, OpenAI's cache cannot be extended past the first image in the prompt, and the agent ends every request with the screenshot. A text-only final turn (screenshot, one-word assistant ack, text prompt) extends fully in probes at ≈$0.03/turn.

The old arm's cost is flat per turn (sliding window). The new arm's cost is a curve that rises until compaction; at 25 turns with one compaction it is already 2–3× the first-turn cost on the two endpoints without whole-prefix caching, and it would keep rising on longer segments.

## Progress — new arm ahead on 2 of 3, n = 1

Gemini and Astra reached every checkpoint earlier on the new arm (Astra: Route 1 at turn 18 vs 25; Gemini: one rung further). GLM reached the same rung later. Self-grade true-rate: Gemini 0.83 vs 0.50, Astra 0.79 vs 0.92, GLM 0.50 vs 0.54. With one run per arm this is a lean, not a result; repeats would settle it (Andreas: repeats only if inconclusive).

## What this means for choosing a system

1. **On endpoints with whole-prefix caching (Z.AI, and per the ten-turn campaign Anthropic and DeepSeek), the new system is faster, cheaper and at least as good.** Run it.
2. **On Gemini through OpenRouter the new system is faster and progressed further but ~25% dearer at 25 turns**, and the gap widens with segment length. Shorter compaction intervals or Vertex/AI-Studio-direct caching would change this; not tested.
3. **On OpenAI through OpenRouter the new system is not viable in its current request shape** — 2.7× the price for faster, better play — because a final turn with an image disables cache extension. The fix shape (text-only final turn) is probe-verified and needs a live rerun of the Astra arm to confirm the price.

## Side findings

- GPT-6 Astra at effort medium reports 0 reasoning tokens on ~90% of gameplay turns on both arms; it plays well anyway (Route 1 in 18 turns is the best run of the day).
- OpenRouter bills OpenAI cache writes at 1.25× ($12.50/M on `openai`); OpenAI direct does not charge cache writes. The first request of any conversation pays it.
- Legacy transport vs Z.AI: forced `tool_choice` is rejected ("Tool choice must be auto") and pydantic-ai refuses native JSON for the model; prompted JSON works.
- The identical-repeat cache probe cannot see a prefix-extension miss; `probe_cache_hits.py --grow` now tests the real shape.

## Caveats

System-versus-system: prompts, schemas, image history, output plumbing (GLM: prompted JSON vs tools) and transports all differ between arms. One emulator, one run per arm, same save and referee ladder. Gemini's new arm absorbed one transport error and one output retry; nothing else was retried.
