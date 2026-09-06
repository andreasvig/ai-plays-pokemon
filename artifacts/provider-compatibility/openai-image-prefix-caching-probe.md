# OpenAI via OpenRouter: a final turn that contains an image disables prompt caching beyond the first image (2026-09-06)

> Source: old-vs-new system comparison, Astra new arm (`local/runs/2026-09-06_16-05-44_config-new__openai-gpt-6-astra`), then ~30 replay requests built from its saved request bodies (`conversation/*-request.json`, screenshots rehydrated from `conversation/assets/`). Endpoint tag `openai`, effort medium. Investigation spend ≈ $4.

## The rule

**If the last (merged) turn of the request contains an image, OpenAI's prompt cache cannot be extended past the first image anywhere in the prompt.** Identical repeats still hit in full. If the final turn is text-only — even with screenshots in earlier turns — the next request hits the whole previous prompt.

The append agent ends every request with `user: [OCR text, screenshot]`, so all 25 live turns cached exactly 1,192 tokens (system + first user text) and paid the 1.25× cache-write price ($12.50/M on `openai`) on the rest: $4.61 for 25 turns vs $1.72 on the sliding-window agent.

## Evidence (fresh nonce in the system prompt per chain; "cached" is `prompt_tokens_details.cached_tokens` on the request)

| Chain | Shape of each request | Next request cached | Verdict |
|---|---|---|---|
| live bodies turn1→2→3 | ends `user[text, img]` | 1,203 / 1,203 | poisoned |
| hand-built, same shape (X) | ends `user[text, img]` | 1,226 / 1,226 | poisoned — so not JSON key order, not harness plumbing |
| parts swapped (Y) | ends `user[img, text]` | 1,223 | poisoned — not "last part is an image" |
| trailing text user message (Z1) | `user[text, img]`, `user "choose"` | 1,204 | poisoned — adjacent user messages are merged into one turn |
| screenshot inside the tool result (Q4) | ends `tool[text, img]` | 1,204 | poisoned — role does not matter, the image in the final turn does |
| ends with tool text (Z3, P11) | `user[img]`, `assistant call`, `tool "Accepted."` | 2,946 = all of A; 4,670 = all of B | extends through the images |
| final user turn text-only, image earlier (Q2) | `user[img]`, call, tool, `user "text"` | 2,980 = all | extends |
| **fix shape (Q5)**: `user[text, img]` → `assistant "Observed."` → `user "choose…"` | final turn text-only, chained 3 turns | 2,883 → 4,650 (all of the previous request each time) | **extends; model still calls the tool; $0.028/turn vs $0.070** |

Ruled out one at a time on the real bodies (no effect): `stream`, `session_id`, `reasoning.exclude`, `require_parameters`, `max_tokens`, assistant `refusal`/`reasoning` null keys, first text-only user message, merged user messages, user texts, tool arguments, call ids, system-prompt length (padded to 8k), image bytes, tool plumbing.

## Why the probes missed it

`probe_cache_hits.py` sent identical pairs (99.9% hits, with or without an image), and my first `--grow` mode ended request A with an assistant message, which is the shape that works. `--grow` now uses the live shape (A ends with `user[text, img]`), and `--grow-split` the fix shape, so both regimes are visible.

## What to do about it

The agent needs the current screenshot in the request. The fix shape keeps it in a user turn, follows it with a synthetic one-word assistant acknowledgement, and asks for the action in a text-only user message — the final turn is then text-only. Proposed as an opt-in profile flag for OpenAI endpoints; untested in a live run. Expected effect on Astra: per-turn cost ≈ $0.03 flat-ish instead of $0.07 → $0.20, i.e. the new arm would land near or below the old arm's $1.72.

Unknown: whether this is OpenAI's own behaviour or OpenRouter's Responses-API conversion (no direct OpenAI key to test). Z.AI and Anthropic cache the same growing image conversation without this restriction.
