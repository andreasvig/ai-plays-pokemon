# OpenAI via OpenRouter: the append agent's real request bodies do not extend the prompt cache past the first screenshot (2026-09-06)

> Source: old-vs-new system comparison, Astra new arm (`local/runs/2026-09-06_16-05-44_config-new__openai-gpt-6-astra`), then replays of its saved request bodies (`conversation/*-request.json`, screenshots rehydrated from `conversation/assets/`). Endpoint tag `openai`, effort medium. Probe spend for the investigation ≈ $2.

## What the live run showed

All 25 gameplay requests reported `cached_tokens = 1192` — the system prompt plus the first user text, i.e. everything before the first screenshot — with the rest of the prompt billed as `cache_write_tokens` at $12.50/M (1.25× input). Consecutive request bodies are byte-identical prefixes of each other (each is the previous one plus three messages), so this is not a prefix mutation.

## Replays of the real bodies (each pair uses a fresh nonce in the system prompt)

| Pair | A | A + 3 messages (next turn) | Extends? |
|---|---|---|---|
| control (real bodies) | 4,665 tok, cached 0, write 4,662 | 6,390 tok, cached **1,203**, write 5,184 | no |
| A+turn sent again, identical, +30 s / +130 s | | cached 6,387 of 6,390 | identical repeat hits |
| turn 4 body after that, extends the fully-cached turn-3 body | | cached 1,203 | no |
| stream false · no session_id · reasoning without exclude · no require_parameters | | cached ≈1,205 | no |
| assistant `reasoning`/`refusal` null keys removed (separately and together) | | cached ≈1,207 | no |
| first text-only user message dropped · first two user messages merged | | cached ≈1,206 | no |
| user texts / tool arguments / call ids replaced with probe text | | cached ≈1,207 | no |
| system prompt padded to ≈8k tokens | 11,473 | cached **8,011** (again: up to the image) | no |
| tools removed, assistant tool calls turned into plain text | | cached 0 | no |
| all images replaced by one screenshot file everywhere | | cached 1,203 | no |
| **images removed entirely** (text-only real bodies) | 1,412 | cached 1,409 of 1,516 | **yes** |

## The same pieces in a simplified body extend fine

`probe_cache_hits.py --grow` (request A, then A + one turn, 1.5 s apart) — system prompt (short real one or padded), the real screenshots (distinct per turn), the real `tools` list with assistant tool calls and tool results — **extends** in every variant tried (6 of 6: cached = all of A). Example: A 9,620 tok → A+turn cached 9,617.

So: image bytes, tool plumbing, system length, streaming, session id, reasoning fields and the user/tool texts are each innocent in isolation, yet the harness's exact bodies fail and a hand-built body with the same ingredients passes. The differing piece was not isolated in this session (open task in the Marvin brain). Identical repeats always hit, which is why every earlier probe reported 99.9%.

## Consequence

For the append-and-compact agent adding a screenshot per turn, OpenAI through OpenRouter cached only the system prompt and billed the growing conversation at 1.25× every turn. Astra's new arm cost $4.61 vs $1.72 on the sliding-window arm (2.7×); per-turn cost rose from $0.07 to $0.20 by turn 25. Z.AI cached 85% of the same growing image conversation; Anthropic ~75% in the ten-turn campaign; Gemini is limited to the system block for a different reason (marker placement).

Until the body difference is found, treat OpenAI endpoints as **no effective prefix caching** for this agent. Candidate mitigations, untested: keep only the latest screenshot as an image (earlier ones as text placeholders); `openai/flex` at half price.
