# Endpoint survey — every kept model, 2026-09-07

> Snapshot of `GET https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints` plus the
> model rows from `GET /api/v1/models`, taken 2026-09-07 for the 21 models in the pruned
> `configs/models.yaml`. Prices are **USD per million tokens**. `cache r` / `cache w` are
> `pricing.input_cache_read` / `input_cache_write`; a dash means the endpoint publishes no
> such price, which is not the same as "no cache" — it means a hit cannot be priced and
> `AppendAgent._warn_cache_economics` will say so at run start. `quant` is the endpoint's own
> `quantization` field (`unknown` where the host does not state it; the tag suffix usually
> carries it). Generated from the saved snapshots, not typed by hand.

**Endpoint rule applied here:** the model author's own endpoint tag where one exists,
otherwise the cheapest `input_cache_read`. Ties broken toward the larger context window.
The chosen row is marked **>** and is what `configs/provider-profiles.yaml` pins.

## Chosen endpoint per model

| Model | Registry alias | Released | Multimodal | Endpoint (chosen) | Context | Max completion | Prompt | Completion | Cache r | Cache w | Quant | Why this endpoint |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| `openai/gpt-6-astra` | `gpt-6-astra` | 2026-09-04 | yes | **>** `openai` | 1,050,000 | 128,000 | 10 | 50 | 1 | 12.5 | unknown | first-party OpenAI |
| `openai/gpt-5.6-sol` | `gpt-5.6-sol` | 2026-07-09 | yes | **>** `openai` | 1,050,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | unknown | first-party OpenAI |
| `openai/gpt-5.6-luna` | `gpt-5.6-luna` | 2026-07-09 | yes | **>** `openai` | 1,050,000 | 128,000 | 0.2 | 1.2 | 0.02 | 0.25 | unknown | first-party OpenAI |
| `openai/gpt-5.6-terra` | `gpt-5.6-terra` | 2026-07-09 | yes | **>** `openai` | 1,050,000 | 128,000 | 2 | 12 | 0.2 | 2.5 | unknown | first-party OpenAI |
| `anthropic/claude-opus-5` | `claude-opus-5` | 2026-07-24 | yes | **>** `anthropic` | 1,000,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | unknown | first-party Anthropic |
| `anthropic/claude-sonnet-5` | `claude-sonnet-5` | 2026-06-30 | yes | **>** `anthropic` | 1,000,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | unknown | first-party Anthropic |
| `anthropic/claude-fable-5.1` | `claude-fable-5.1` | 2026-09-01 | yes | **>** `anthropic` | 1,000,000 | 128,000 | 10 | 50 | 0.25 | 12.5 | unknown | first-party Anthropic |
| `anthropic/claude-haiku-4.5` | `claude-haiku-4.5` | 2025-10-15 | yes | **>** `anthropic` | 200,000 | 64,000 | 1 | 5 | 0.1 | 1.25 | unknown | first-party Anthropic |
| `google/gemini-3.8-flash` | `gemini-3.8-flash` | 2026-09-02 | yes | **>** `google-ai-studio` | 1,048,576 | 65,536 | 0.75 | 3.75 | 0.075 | 0.0416667 | unknown | first-party Google AI Studio |
| `google/gemini-3.5-flash-lite` | `gemini-3.5-flash-lite` | 2026-07-21 | yes | **>** `google-ai-studio` | 1,048,576 | 65,536 | 0.3 | 2.5 | 0.03 | 0.0833333 | unknown | first-party Google AI Studio |
| `google/gemma-4-31b-it` | `gemma-4-31b` | 2026-04-02 | yes | **>** `deepinfra/turbo` | 262,144 | 16,384 | 0.09 | 0.34 | 0.05 | — | fp4 | no first party (open weights); cheapest priced cache read among hosts that also serve 262k context |
| `qwen/qwen3.8-flash` | `qwen3.8-flash` | 2026-08-26 | yes | **>** `alibaba` | 1,000,000 | 131,072 | 0.15 | 0.47 | 0.016 | 0.2 | unknown | first-party Alibaba (sole endpoint) |
| `qwen/qwen3.7-plus` | `qwen3.7-plus` | 2026-06-03 | yes | **>** `alibaba` | 1,000,000 | 131,072 | 0.32 | 1.28 | 0.064 | 0.4 | unknown | first-party Alibaba (sole endpoint) |
| `moonshotai/kimi-k3` | `kimi-k3` | 2026-07-16 | yes | **>** `moonshotai/mxfp4` | 1,048,576 | 943,718 | 3 | 15 | 0.3 | — | mxfp4 | first-party Moonshot AI |
| `x-ai/grok-4.6` | `grok-4.6` | 2026-08-12 | yes | **>** `xai` | 500,000 | 450,000 | 2 | 6 | 0.5 | — | unknown | first-party xAI (ZDR tags excluded) |
| `z-ai/glm-5.3-flash` | `glm-5.3-flash` | 2026-08-26 | yes | **>** `z-ai/fp8` | 1,048,576 | 131,072 | 0.075 | 0.25 | 0.015 | — | fp8 | first-party Z.AI |
| `minimax/minimax-m3` | `minimax-m3` | 2026-05-31 | yes | **>** `minimax/fp8` | 524,288 | 512,000 | 0.3 | 1.2 | 0.06 | — | fp8 | first-party MiniMax |
| `deepseek/deepseek-v4-flash-vision-exp` | `deepseek-v4-flash-vision-exp` | 2026-08-21 | yes | **>** `deepseek` | 1,048,576 | 384,000 | 0.44 | 1.32 | 0.014 | — | unknown | first-party DeepSeek |
| `meta/muse-spark-1.3` | `muse-spark-1.3` | 2026-09-02 | yes | **>** `meta` | 1,048,576 | 943,718 | 1.25 | 4.25 | 0.15 | — | unknown | first-party Meta (sole endpoint) |
| `sakana/fugu-ultra` | `fugu-ultra` | 2026-06-24 | yes | **>** `sakana` | 1,000,000 | 128,000 | 5 | 30 | 0.5 | — | unknown | first-party Sakana AI (sole endpoint) — REGION BLOCKED, see below |
| `xiaomi/mimo-v2.5` | `mimo-v2.5` | 2026-04-22 | yes | **>** `gmicloud/fp8` | 1,050,000 | 945,000 | 0.119 | 0.238 | 0.00255 | — | fp8 | no first party; cheapest cache read ($0.0026 vs $0.026) and 4x the context |

## Reasoning contract per model (from `/api/v1/models`)

`supported_efforts` is what the catalog enumerates for the MODEL; `reasoning_effort` is
whether the CHOSEN endpoint advertises the parameter. A model with no `supported_efforts`
is a binary thinking/non-thinking model and its profile lists `[thinking, non-thinking]`.

| Model | Reasoning mandatory | Catalog `supported_efforts` | Catalog default | Endpoint advertises `reasoning_effort` | Profile `reasoning_efforts` | Profile `reasoning_default` | Registry `thinking_levels` |
|---|---|---|---|---|---|---|---|
| `openai/gpt-6-astra` | True | max, xhigh, high, medium, low | medium | yes | max, xhigh, high, medium, low | `{'effort': 'medium'}` | max, xhigh, high, medium, low |
| `openai/gpt-5.6-sol` | False | max, xhigh, high, medium, low, none | medium | yes | max, xhigh, high, medium, low, none | `{'effort': 'medium'}` | max, xhigh, high, medium, low, none |
| `openai/gpt-5.6-luna` | False | max, xhigh, high, medium, low, none | medium | yes | max, xhigh, high, medium, low, none | `{'effort': 'medium'}` | max, xhigh, high, medium, low, none |
| `openai/gpt-5.6-terra` | False | max, xhigh, high, medium, low, none | medium | yes | max, xhigh, high, medium, low, none | `{'effort': 'medium'}` | max, xhigh, high, medium, low, none |
| `anthropic/claude-opus-5` | False | max, xhigh, high, medium, low | high | yes | max, xhigh, high, medium, low | `{'effort': 'high'}` | max, xhigh, high, medium, low |
| `anthropic/claude-sonnet-5` | False | max, xhigh, high, medium, low | high | yes | max, xhigh, high, medium, low | `{'effort': 'high'}` | max, xhigh, high, medium, low |
| `anthropic/claude-fable-5.1` | True | max, xhigh, high, medium, low | high | yes | max, xhigh, high, medium, low | `{'effort': 'high'}` | max, xhigh, high, medium, low |
| `anthropic/claude-haiku-4.5` | False | (none — binary) | — | NO | high, medium, low, minimal | `{'effort': 'high'}` | high, medium, low, minimal |
| `google/gemini-3.8-flash` | True | high, medium, low | medium | yes | high, medium, low | `{'effort': 'medium'}` | high, medium, low |
| `google/gemini-3.5-flash-lite` | True | high, medium, low, minimal | minimal | yes | high, medium, low, minimal | `{'effort': 'minimal'}` | high, medium, low, minimal |
| `google/gemma-4-31b-it` | False | (none — binary) | enabled False | NO | thinking, non-thinking | `{'enabled': True}` | thinking, non-thinking |
| `qwen/qwen3.8-flash` | False | (none — binary) | enabled True | NO | thinking, non-thinking | `{'enabled': True}` | thinking, non-thinking |
| `qwen/qwen3.7-plus` | False | (none — binary) | enabled True | NO | thinking, non-thinking | `{'enabled': True}` | thinking, non-thinking |
| `moonshotai/kimi-k3` | False | max, high, low | max | yes | max, high, low | `{'effort': 'high'}` | max, high, low |
| `x-ai/grok-4.6` | True | xhigh, high, medium, low | high | yes | xhigh, high, medium, low | `{'effort': 'high'}` | xhigh, high, medium, low |
| `z-ai/glm-5.3-flash` | True | max, high, low | max | yes | max, high, low | `{'effort': 'high'}` | max, high, low |
| `minimax/minimax-m3` | False | (none — binary) | — | NO | thinking, non-thinking | `{'enabled': True}` | thinking, non-thinking |
| `deepseek/deepseek-v4-flash-vision-exp` | False | max, high, low | high | yes | max, high, low | `{'effort': 'high'}` | max, high, low |
| `meta/muse-spark-1.3` | True | max, xhigh, high, medium, low, minimal | medium | yes | max, xhigh, high, medium, low, minimal | `{'effort': 'medium'}` | max, xhigh, high, medium, low, minimal |
| `sakana/fugu-ultra` | True | max, xhigh, high | xhigh | yes | max, xhigh, high | `{'effort': 'xhigh'}` | max, xhigh, high |
| `xiaomi/mimo-v2.5` | False | (none — binary) | — | NO | thinking, non-thinking | `{'enabled': True}` | thinking, non-thinking |

## Parameter pre-flight on the chosen endpoint

The append agent sends `max_tokens` on every request together with
`provider: {only: [tag], allow_fallbacks: false, require_parameters: true}`. Any parameter
in the body that the pinned endpoint does not advertise removes the only candidate and the
request fails with HTTP 404 `No endpoints found that can handle the requested parameters`.
So this table is a routability check, not trivia.

| Model | Endpoint | `max_tokens` | `temperature` | `top_p` | `tools` | `tool_choice` | `response_format` | `structured_outputs` | Profile `output_mode` | Profile `sampling` | Routable |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-6-astra` | `openai` | yes | **NO** | **NO** | yes | yes | yes | yes | tool | — | yes |
| `openai/gpt-5.6-sol` | `openai` | yes | **NO** | **NO** | yes | yes | yes | yes | tool | — | yes |
| `openai/gpt-5.6-luna` | `openai` | yes | **NO** | **NO** | yes | yes | yes | yes | tool | — | yes |
| `openai/gpt-5.6-terra` | `openai` | yes | **NO** | **NO** | yes | yes | yes | yes | tool | — | yes |
| `anthropic/claude-opus-5` | `anthropic` | yes | **NO** | **NO** | yes | yes | yes | yes | prompted | — | yes |
| `anthropic/claude-sonnet-5` | `anthropic` | yes | **NO** | **NO** | yes | yes | yes | yes | prompted | — | yes |
| `anthropic/claude-fable-5.1` | `anthropic` | yes | **NO** | **NO** | yes | **NO** | yes | yes | prompted | — | yes |
| `anthropic/claude-haiku-4.5` | `anthropic` | yes | yes | yes | yes | yes | yes | yes | prompted | — | yes |
| `google/gemini-3.8-flash` | `google-ai-studio` | yes | yes | yes | yes | yes | yes | yes | tool | — | yes |
| `google/gemini-3.5-flash-lite` | `google-ai-studio` | yes | yes | yes | yes | yes | yes | yes | tool | — | yes |
| `google/gemma-4-31b-it` | `deepinfra/turbo` | yes | yes | yes | **NO** | **NO** | yes | yes | prompted | {'temperature': 0.3, 'top_p': 0.95} | yes |
| `qwen/qwen3.8-flash` | `alibaba` | yes | yes | yes | yes | yes | yes | yes | prompted | — | yes |
| `qwen/qwen3.7-plus` | `alibaba` | yes | yes | yes | yes | yes | yes | yes | prompted | {'temperature': 0.3, 'top_p': 0.95} | yes |
| `moonshotai/kimi-k3` | `moonshotai/mxfp4` | yes | **NO** | **NO** | yes | yes | yes | yes | tool | — | yes |
| `x-ai/grok-4.6` | `xai` | yes | yes | yes | yes | yes | yes | yes | tool | — | yes |
| `z-ai/glm-5.3-flash` | `z-ai/fp8` | yes | yes | yes | yes | yes | yes | **NO** | tool | — | yes |
| `minimax/minimax-m3` | `minimax/fp8` | yes | yes | yes | yes | yes | yes | **NO** | tool | {'temperature': 0.3, 'top_p': 0.95} | yes |
| `deepseek/deepseek-v4-flash-vision-exp` | `deepseek` | yes | yes | yes | yes | yes | yes | **NO** | tool | — | yes |
| `meta/muse-spark-1.3` | `meta` | yes | yes | yes | yes | yes | yes | yes | tool | — | yes |
| `sakana/fugu-ultra` | `sakana` | **NO** | **NO** | **NO** | yes | yes | **NO** | yes | tool | — | **NO** — max_tokens not advertised |
| `xiaomi/mimo-v2.5` | `gmicloud/fp8` | yes | yes | yes | yes | yes | **NO** | **NO** | tool | {'temperature': 0.3, 'top_p': 0.95} | yes |

## Every endpoint of every kept model

The full snapshot, so a later re-pin does not need another round of API calls. Rows are in
the order OpenRouter returned them (cheapest prompt price first, broadly).

### `openai/gpt-6-astra` — pinned: `openai`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `openai/flex` | OpenAI | 1,050,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | — | unknown |
|  | `azure` | Azure | 1,050,000 | 128,000 | 10 | 50 | 1 | 12.5 | — | unknown |
| **>** | `openai` | OpenAI | 1,050,000 | 128,000 | 10 | 50 | 1 | 12.5 | — | unknown |
|  | `azure/us` | Azure | 1,050,000 | 128,000 | 11 | 55 | 1.1 | 13.75 | — | unknown |
|  | `openai/fast` | OpenAI | 1,050,000 | 128,000 | 20 | 100 | 2 | 25 | — | unknown |

### `openai/gpt-5.6-sol` — pinned: `openai`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `openai/flex` | OpenAI | 1,050,000 | 128,000 | 1 | 5 | 0.1 | 1.25 | — | unknown |
| **>** | `openai` | OpenAI | 1,050,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | — | unknown |
|  | `openai/fast` | OpenAI | 1,050,000 | 128,000 | 4 | 20 | 0.4 | 5 | — | unknown |
|  | `amazon-bedrock/us-east-1` | Amazon Bedrock | 1,050,000 | 128,000 | 4.4 | 22 | 0.44 | 5.5 | — | unknown |
|  | `azure` | Azure | 1,050,000 | 128,000 | 5 | 30 | 0.5 | 6.25 | — | unknown |
|  | `azure/us` | Azure | 1,050,000 | 128,000 | 5.5 | 33 | 0.55 | 6.875 | — | unknown |
|  | `azure/eu` | Azure | 1,050,000 | 128,000 | 5.5 | 33 | 0.55 | 6.875 | — | unknown |

### `openai/gpt-5.6-luna` — pinned: `openai`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `openai/flex` | OpenAI | 1,050,000 | 128,000 | 0.1 | 0.6 | 0.01 | 0.125 | — | unknown |
|  | `azure` | Azure | 1,050,000 | 128,000 | 0.2 | 1.2 | 0.02 | 0.25 | — | unknown |
| **>** | `openai` | OpenAI | 1,050,000 | 128,000 | 0.2 | 1.2 | 0.02 | 0.25 | — | unknown |
|  | `azure/us` | Azure | 1,050,000 | 128,000 | 0.22 | 1.32 | 0.022 | 0.275 | — | unknown |
|  | `amazon-bedrock/us-east-1` | Amazon Bedrock | 1,050,000 | 128,000 | 0.22 | 1.32 | 0.022 | 0.275 | — | unknown |
|  | `azure/eu` | Azure | 1,050,000 | 128,000 | 0.22 | 1.32 | 0.022 | 0.275 | — | unknown |
|  | `openai/fast` | OpenAI | 1,050,000 | 128,000 | 0.4 | 2.4 | 0.04 | 0.5 | — | unknown |

### `openai/gpt-5.6-terra` — pinned: `openai`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `openai/flex` | OpenAI | 1,050,000 | 128,000 | 1 | 6 | 0.1 | 1.25 | — | unknown |
|  | `azure` | Azure | 1,050,000 | 128,000 | 2 | 12 | 0.2 | 2.5 | — | unknown |
| **>** | `openai` | OpenAI | 1,050,000 | 128,000 | 2 | 12 | 0.2 | 2.5 | — | unknown |
|  | `azure/us` | Azure | 1,050,000 | 128,000 | 2.2 | 13.2 | 0.22 | 2.75 | — | unknown |
|  | `amazon-bedrock/us-east-1` | Amazon Bedrock | 1,050,000 | 128,000 | 2.2 | 13.2 | 0.22 | 2.75 | — | unknown |
|  | `azure/eu` | Azure | 1,050,000 | 128,000 | 2.2 | 13.2 | 0.22 | 2.75 | — | unknown |
|  | `openai/fast` | OpenAI | 1,050,000 | 128,000 | 4 | 24 | 0.4 | 5 | — | unknown |

### `anthropic/claude-opus-5` — pinned: `anthropic`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `azure/us` | Azure | 1,000,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | — | unknown |
|  | `claude-on-aws` | Claude Platform on AWS | 1,000,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | — | unknown |
|  | `google-vertex/global` | Google | 1,000,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | — | unknown |
|  | `amazon-bedrock` | Amazon Bedrock | 1,000,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | — | unknown |
|  | `azure/global` | Azure | 1,000,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | — | unknown |
| **>** | `anthropic` | Anthropic | 1,000,000 | 128,000 | 5 | 25 | 0.5 | 6.25 | — | unknown |
|  | `google-vertex/us` | Google | 1,000,000 | 128,000 | 5.5 | 27.5 | 0.55 | 6.875 | — | unknown |
|  | `google-vertex/europe` | Google | 1,000,000 | 128,000 | 5.5 | 27.5 | 0.55 | 6.875 | — | unknown |
|  | `amazon-bedrock/us-east-1` | Amazon Bedrock | 1,000,000 | 128,000 | 5.5 | 27.5 | 0.55 | 6.875 | — | unknown |
|  | `anthropic/fast` | Anthropic | 1,000,000 | 128,000 | 10 | 50 | 1 | 12.5 | — | unknown |

### `anthropic/claude-sonnet-5` — pinned: `anthropic`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `claude-on-aws` | Claude Platform on AWS | 1,000,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | — | unknown |
|  | `azure/us` | Azure | 1,000,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | — | unknown |
|  | `azure/global` | Azure | 1,000,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | — | unknown |
|  | `google-vertex/global` | Google | 1,000,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | — | unknown |
|  | `amazon-bedrock/global` | Amazon Bedrock | 1,000,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | — | unknown |
| **>** | `anthropic` | Anthropic | 1,000,000 | 128,000 | 2 | 10 | 0.2 | 2.5 | — | unknown |
|  | `google-vertex/us` | Google | 1,000,000 | 128,000 | 2.2 | 11 | 0.22 | 2.75 | — | unknown |
|  | `google-vertex/europe` | Google | 1,000,000 | 128,000 | 2.2 | 11 | 0.22 | 2.75 | — | unknown |
|  | `amazon-bedrock/us-east-1` | Amazon Bedrock | 1,000,000 | 128,000 | 2.2 | 11 | 0.22 | 2.75 | — | unknown |

### `anthropic/claude-fable-5.1` — pinned: `anthropic`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `azure` | Azure | 1,000,000 | 128,000 | 10 | 50 | 0.25 | 12.5 | — | unknown |
| **>** | `anthropic` | Anthropic | 1,000,000 | 128,000 | 10 | 50 | 0.25 | 12.5 | — | unknown |
|  | `amazon-bedrock` | Amazon Bedrock | 1,000,000 | 128,000 | 10 | 50 | 0.25 | 12.5 | — | unknown |
|  | `google-vertex/global` | Google | 1,000,000 | 128,000 | 10 | 50 | 0.25 | 12.5 | — | unknown |

### `anthropic/claude-haiku-4.5` — pinned: `anthropic`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `azure/global` | Azure | 200,000 | 64,000 | 1 | 5 | 0.1 | 1.25 | — | unknown |
|  | `amazon-bedrock/global` | Amazon Bedrock | 200,000 | 64,000 | 1 | 5 | 0.1 | 1.25 | — | unknown |
|  | `google-vertex/global` | Google | 200,000 | 64,000 | 1 | 5 | 0.1 | 1.25 | — | unknown |
| **>** | `anthropic` | Anthropic | 200,000 | 64,000 | 1 | 5 | 0.1 | 1.25 | — | unknown |
|  | `amazon-bedrock/us` | Amazon Bedrock | 200,000 | 64,000 | 1.1 | 5.5 | 0.11 | 1.375 | — | unknown |
|  | `google-vertex/us-east5` | Google | 200,000 | 64,000 | 1.1 | 5.5 | 0.11 | 1.375 | — | unknown |
|  | `amazon-bedrock/eu-west-1` | Amazon Bedrock | 200,000 | 64,000 | 1.1 | 5.5 | 0.11 | 1.375 | — | unknown |
|  | `google-vertex/europe` | Google | 200,000 | 64,000 | 1.1 | 5.5 | 0.11 | 1.375 | — | unknown |

### `google/gemini-3.8-flash` — pinned: `google-ai-studio`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `google-ai-studio/flex` | Google AI Studio | 1,048,576 | 65,536 | 0.375 | 1.875 | 0.0375 | 0.0208333 | 1.875 | unknown |
|  | `google-vertex/global/flex` | Google | 1,048,576 | 65,536 | 0.375 | 1.875 | 0.0375 | 0.0208333 | 1.875 | unknown |
| **>** | `google-ai-studio` | Google AI Studio | 1,048,576 | 65,536 | 0.75 | 3.75 | 0.075 | 0.0416667 | 3.75 | unknown |
|  | `google-vertex/global` | Google | 1,048,576 | 65,536 | 0.75 | 3.75 | 0.075 | 0.0416667 | 3.75 | unknown |
|  | `google-ai-studio/priority` | Google AI Studio | 1,048,576 | 65,536 | 1.35 | 6.75 | 0.135 | 0.075 | 6.75 | unknown |
|  | `google-vertex/global/priority` | Google | 1,048,576 | 65,536 | 1.35 | 6.75 | 0.135 | 0.075 | 6.75 | unknown |

### `google/gemini-3.5-flash-lite` — pinned: `google-ai-studio`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `google-vertex/global/flex` | Google | 1,048,576 | 65,536 | 0.15 | 1.25 | 0.015 | 0.0416667 | 1.25 | unknown |
|  | `google-ai-studio/flex` | Google AI Studio | 1,048,576 | 65,536 | 0.15 | 1.25 | 0.015 | 0.0416667 | 1.25 | unknown |
| **>** | `google-ai-studio` | Google AI Studio | 1,048,576 | 65,536 | 0.3 | 2.5 | 0.03 | 0.0833333 | 2.5 | unknown |
|  | `google-vertex/global` | Google | 1,048,576 | 65,536 | 0.3 | 2.5 | 0.03 | 0.0833333 | 2.5 | unknown |
|  | `google-vertex/eu` | Google | 1,048,576 | 65,536 | 0.33 | 2.75 | 0.033 | 0.0833333 | 2.75 | unknown |
|  | `google-vertex/us` | Google | 1,048,576 | 65,536 | 0.33 | 2.75 | 0.033 | 0.0833333 | 2.75 | unknown |
|  | `google-vertex/global/priority` | Google | 1,048,576 | 65,536 | 0.54 | 4.5 | 0.054 | 0.15 | 4.5 | unknown |
|  | `google-ai-studio/priority` | Google AI Studio | 1,048,576 | 65,536 | 0.54 | 4.5 | 0.054 | 0.15 | 4.5 | unknown |

### `google/gemma-4-31b-it` — pinned: `deepinfra/turbo`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **>** | `deepinfra/turbo` | DeepInfra | 262,144 | 16,384 | 0.09 | 0.34 | 0.05 | — | — | fp4 |
|  | `coreweave/fp4` | CoreWeave | 262,144 | 235,929 | 0.1 | 0.34 | 0.1 | — | — | fp4 |
|  | `venice/bf16` | Venice | 256,000 | 8,192 | 0.12 | 0.36 | 0.09 | — | — | bf16 |
|  | `chutes/fp4` | Chutes | 131,072 | 65,536 | 0.12 | 0.37 | 0.012 | — | — | fp4 |
|  | `deepinfra/fp8` | DeepInfra | 262,144 | 16,384 | 0.13 | 0.38 | — | — | — | fp8 |
|  | `siliconflow/fp8` | SiliconFlow | 262,144 | 235,929 | 0.13 | 0.4 | — | — | — | fp8 |
|  | `crusoe` | Crusoe | 262,144 | 262,141 | 0.14 | 0.4 | 0.14 | — | — | unknown |
|  | `friendli` | Friendli | 262,144 | 8,192 | 0.14 | 0.4 | — | — | — | unknown |
|  | `novita/bf16` | Novita | 262,144 | 131,072 | 0.14 | 0.4 | — | — | — | bf16 |
|  | `parasail/fp8` | Parasail | 262,144 | 235,929 | 0.15 | 0.4 | 0.06 | — | — | fp8 |
|  | `deepinfra/ultra` | DeepInfra | 131,072 | 8,192 | 0.27 | 0.76 | — | — | — | fp8 |
|  | `sambanova` | SambaNova | 131,072 | 117,964 | 0.38 | 1.15 | — | — | — | unknown |
|  | `together` | Together | 262,144 | 235,929 | 0.39 | 0.97 | — | — | — | unknown |
|  | `modelrun/fp4` | ModelRun | 262,144 | 235,929 | 0.75 | 1 | 0.75 | — | — | fp4 |
|  | `cerebras/fp16` | Cerebras | 131,072 | 40,960 | 0.99 | 1.49 | 0.99 | — | — | fp16 |

### `qwen/qwen3.8-flash` — pinned: `alibaba`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **>** | `alibaba` | Alibaba | 1,000,000 | 131,072 | 0.15 | 0.47 | 0.016 | 0.2 | — | unknown |

### `qwen/qwen3.7-plus` — pinned: `alibaba`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **>** | `alibaba` | Alibaba | 1,000,000 | 131,072 | 0.32 | 1.28 | 0.064 | 0.4 | — | unknown |

### `moonshotai/kimi-k3` — pinned: `moonshotai/mxfp4`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `makora` | Makora | 1,048,576 | 943,718 | 2.55 | 12.75 | 0.256 | — | — | unknown |
|  | `sail-research/fp4` | Sail Research | 1,048,576 | 943,718 | 2.6 | 13 | 0.29 | — | — | fp4 |
|  | `morph/fp4` | Morph | 1,048,576 | 943,718 | 2.6 | 14 | 0.29 | — | — | fp4 |
|  | `deepinfra/bf16` | DeepInfra | 1,048,576 | 16,384 | 2.85 | 14.25 | 0.285 | — | — | bf16 |
|  | `digitalocean` | DigitalOcean | 1,048,576 | 943,718 | 2.85 | 14.25 | 0.285 | — | — | unknown |
|  | `wafer` | Wafer | 1,048,576 | 943,718 | 3 | 12.75 | 0.3 | — | — | unknown |
|  | `phala` | Phala | 1,048,576 | 943,718 | 3 | 15 | 0.3 | — | — | unknown |
|  | `chutes/mxfp4` | Chutes | 1,048,576 | 65,535 | 3 | 15 | 0.3 | — | — | mxfp4 |
|  | `parasail/fp4` | Parasail | 1,048,576 | 943,718 | 3 | 15 | 0.3 | — | — | fp4 |
|  | `modal/mxfp4` | Modal | 1,048,576 | 943,718 | 3 | 15 | 0.3 | — | — | mxfp4 |
|  | `together` | Together | 1,048,576 | 943,718 | 3 | 15 | 0.3 | — | — | unknown |
|  | `fireworks` | Fireworks | 1,048,576 | 943,718 | 3 | 15 | 0.3 | — | — | unknown |
|  | `baseten/fp8` | BaseTen | 1,048,576 | 262,144 | 3 | 15 | 0.3 | — | — | fp8 |
| **>** | `moonshotai/mxfp4` | Moonshot AI | 1,048,576 | 943,718 | 3 | 15 | 0.3 | — | — | mxfp4 |
|  | `fireworks/us` | Fireworks | 1,048,576 | 943,718 | 3.3 | 16.5 | 0.33 | — | — | unknown |
|  | `alibaba` | Alibaba | 1,048,576 | 943,718 | 3.45 | 17.25 | 0.345 | — | — | unknown |
|  | `fireworks/fast` | Fireworks | 1,048,576 | 943,718 | 4.5 | 22.5 | 0.45 | — | — | unknown |
|  | `morph/fast` | Morph | 1,048,576 | 943,718 | 6 | 22.5 | 0.6 | — | — | fp4 |

### `x-ai/grok-4.6` — pinned: `xai`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `xai/zdr` | xAI | 500,000 | 450,000 | 2 | 6 | 0.5 | — | — | unknown |
| **>** | `xai` | xAI | 500,000 | 450,000 | 2 | 6 | 0.5 | — | — | unknown |
|  | `amazon-bedrock/us-west-2` | Amazon Bedrock | 500,000 | 450,000 | 2.2 | 6.6 | 0.55 | 0 | — | unknown |
|  | `xai/zdr/priority` | xAI | 500,000 | 450,000 | 4 | 12 | 1 | — | — | unknown |
|  | `xai/priority` | xAI | 500,000 | 450,000 | 4 | 12 | 1 | — | — | unknown |

### `z-ai/glm-5.3-flash` — pinned: `z-ai/fp8`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `relace/fp4` | Relace | 1,048,576 | 131,072 | 0.07125 | 0.2375 | 0.01425 | — | — | fp4 |
|  | `gmicloud/fp8` | GMICloud | 1,048,576 | 943,718 | 0.075 | 0.25 | 0.015 | — | — | fp8 |
|  | `novita/fp8` | Novita | 1,048,576 | 131,072 | 0.075 | 0.25 | 0.015 | — | — | fp8 |
|  | `deepinfra/fp4` | DeepInfra | 1,048,576 | 131,072 | 0.075 | 0.25 | 0.015 | — | — | fp4 |
| **>** | `z-ai/fp8` | Z.AI | 1,048,576 | 131,072 | 0.075 | 0.25 | 0.015 | — | — | fp8 |
|  | `wafer` | Wafer | 1,048,576 | 943,718 | 0.1 | 0.35 | 0.02 | — | — | unknown |
|  | `makora` | Makora | 1,048,576 | 943,718 | 0.14 | 0.47 | 0.024 | — | — | unknown |
|  | `modal/fp8` | Modal | 1,048,576 | 943,718 | 0.149985 | 0.49995 | 0.029997 | — | — | fp8 |
|  | `coreweave/fp8` | CoreWeave | 1,048,576 | 943,718 | 0.15 | 0.5 | 0.05 | — | — | fp8 |
|  | `sail-research/fp8` | Sail Research | 1,048,576 | 131,072 | 0.15 | 0.5 | 0.03 | — | — | fp8 |
|  | `streamlake` | StreamLake | 1,024,000 | 128,000 | 0.15 | 0.5 | 0.03 | — | — | unknown |
|  | `nextbit/fp8` | NextBit | 1,048,576 | 128,000 | 0.15 | 0.5 | 0.03 | — | — | fp8 |
|  | `fireworks` | Fireworks | 1,048,576 | 943,718 | 0.15 | 0.5 | 0.03 | — | — | unknown |
|  | `friendli` | Friendli | 1,048,576 | 943,718 | 0.15 | 0.5 | 0.03 | — | — | unknown |
|  | `siliconflow/fp8` | SiliconFlow | 1,048,576 | 262,144 | 0.15 | 0.5 | 0.03 | — | — | fp8 |
|  | `digitalocean` | DigitalOcean | 1,048,576 | 943,718 | 0.15 | 0.5 | 0.03 | — | — | unknown |
|  | `together` | Together | 1,048,575 | 943,717 | 0.15 | 0.5 | 0.03 | — | — | unknown |
|  | `reka/fp8` | Reka | 262,144 | 235,929 | 0.15 | 0.5 | 0.03 | — | — | fp8 |
|  | `parasail/fp8` | Parasail | 1,048,576 | 943,718 | 0.15 | 0.5 | 0.03 | — | — | fp8 |
|  | `baseten/fp8` | BaseTen | 1,048,576 | 131,072 | 0.15 | 0.5 | 0.03 | — | — | fp8 |
|  | `venice` | Venice | 1,048,576 | 131,072 | 0.15 | 0.5 | 0.03 | — | — | unknown |
|  | `io-net/fp8` | Io Net | 262,144 | 131,072 | 0.15 | 0.5 | 0.03 | — | — | fp8 |
|  | `cloudflare` | Cloudflare | 1,310,720 | 1,179,648 | 0.15 | 0.5 | 0.03 | — | — | unknown |
|  | `morph/fp8` | Morph | 1,048,576 | 943,718 | 0.388 | 1.358 | 0.0776 | — | — | fp8 |

### `minimax/minimax-m3` — pinned: `minimax/fp8`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `coreweave/fp4` | CoreWeave | 262,144 | 235,929 | 0.23 | 0.96 | 0.05 | — | — | fp4 |
|  | `gmicloud/fp8` | GMICloud | 1,048,576 | 943,718 | 0.24 | 0.96 | 0.048 | — | — | fp8 |
|  | `deepinfra/fp8` | DeepInfra | 524,288 | 512,000 | 0.28 | 1.1 | 0.056 | — | — | fp8 |
|  | `streamlake/fp8` | StreamLake | 1,000,000 | 512,000 | 0.3 | 1.2 | 0.06 | — | — | fp8 |
|  | `venice/fp8` | Venice | 524,288 | 65,536 | 0.3 | 1.2 | 0.06 | — | — | fp8 |
|  | `together` | Together | 524,288 | 471,859 | 0.3 | 1.2 | 0.06 | — | — | unknown |
|  | `parasail/fp8` | Parasail | 1,048,576 | 524,288 | 0.3 | 1.2 | 0.06 | — | — | fp8 |
|  | `atlas-cloud/fp8` | AtlasCloud | 524,300 | 524,288 | 0.3 | 1.2 | 0.06 | — | — | fp8 |
|  | `novita/fp8` | Novita | 1,000,000 | 131,072 | 0.3 | 1.2 | 0.06 | — | — | fp8 |
| **>** | `minimax/fp8` | Minimax | 524,288 | 512,000 | 0.3 | 1.2 | 0.06 | — | — | fp8 |
|  | `sambanova` | SambaNova | 1,048,576 | 943,718 | 0.6 | 2.4 | — | — | — | unknown |
|  | `modelrun/fp4` | ModelRun | 1,048,576 | 943,718 | 0.75 | 3 | 0.15 | — | — | fp4 |

### `deepseek/deepseek-v4-flash-vision-exp` — pinned: `deepseek`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|  | `deepinfra/fp8` | DeepInfra | 1,048,576 | 262,144 | 0.2156 | 0.6468 | 0.0686 | — | — | fp8 |
|  | `fireworks` | Fireworks | 1,048,576 | 943,718 | 0.22 | 0.66 | 0.007 | — | — | unknown |
|  | `siliconflow/fp8` | SiliconFlow | 1,048,576 | 393,216 | 0.44 | 1.32 | 0.028 | — | — | fp8 |
|  | `atlas-cloud/fp8` | AtlasCloud | 1,048,576 | 65,536 | 0.44 | 1.32 | 0.028 | — | — | fp8 |
|  | `novita` | Novita | 1,048,576 | 393,216 | 0.44 | 1.32 | 0.028 | — | — | unknown |
| **>** | `deepseek` | DeepSeek | 1,048,576 | 384,000 | 0.44 | 1.32 | 0.014 | — | — | unknown |

### `meta/muse-spark-1.3` — pinned: `meta`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **>** | `meta` | Meta | 1,048,576 | 943,718 | 1.25 | 4.25 | 0.15 | — | — | unknown |

### `sakana/fugu-ultra` — pinned: `sakana`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **>** | `sakana` | Sakana AI | 1,000,000 | 128,000 | 5 | 30 | 0.5 | — | — | unknown |

### `xiaomi/mimo-v2.5` — pinned: `gmicloud/fp8`

| | Tag | Provider | Context | Max completion | Prompt | Completion | Cache r | Cache w | Internal reasoning | Quant |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **>** | `gmicloud/fp8` | GMICloud | 1,050,000 | 945,000 | 0.119 | 0.238 | 0.00255 | — | — | fp8 |
|  | `deepinfra/fp8` | DeepInfra | 262,144 | 131,072 | 0.13 | 0.65 | 0.026 | — | — | fp8 |
|  | `xiaomi/fp8` | Xiaomi | 1,048,576 | 131,072 | 0.14 | 0.28 | 0.0028 | — | — | fp8 |
|  | `streamlake` | StreamLake | 1,000,000 | 128,000 | 0.168 | 0.336 | 0.00336 | — | — | unknown |
|  | `novita/fp8` | Novita | 1,048,576 | 131,072 | 0.168 | 0.336 | 0.0034 | — | — | fp8 |

## Endpoints that publish no cache-read price

On these, a cache hit cannot be priced from the list tiers, so `implied_cache` reports
`no_cache_offered` / `unknown` rather than a discount. None of them is a pinned endpoint.

| Model | Tag | Provider | Is the pinned endpoint? |
|---|---|---|---|
| `google/gemma-4-31b-it` | `deepinfra/fp8` | DeepInfra | no |
| `google/gemma-4-31b-it` | `siliconflow/fp8` | SiliconFlow | no |
| `google/gemma-4-31b-it` | `friendli` | Friendli | no |
| `google/gemma-4-31b-it` | `novita/bf16` | Novita | no |
| `google/gemma-4-31b-it` | `deepinfra/ultra` | DeepInfra | no |
| `google/gemma-4-31b-it` | `sambanova` | SambaNova | no |
| `google/gemma-4-31b-it` | `together` | Together | no |
| `minimax/minimax-m3` | `sambanova` | SambaNova | no |

A pinned endpoint whose cache-read price EQUALS its prompt price is the other trap (hits
save nothing). Among the pinned rows above, none has that shape; `coreweave/fp4`, which
does, is only reachable through the `gemma-*-coreweave` named variants.

## Live probes run for this survey (2026-09-07)

Every request pinned with `provider: {only: [tag], allow_fallbacks: false}` and `transforms: []`.
Costs are the endpoint's own `usage.cost`, summed per probe. Budget was $10 with a $9 stop; total
spend was **$0.32**, so nothing was skipped for budget reasons — the only thing not measured was
blocked, not rationed.

| Probe | Model | Tag returned | Cost | Verdict | What it decided |
|---|---|---|---:|---|---|
| cache grow (live + split, with image) | `openai/gpt-5.6-sol` | `OpenAI` | $0.0399 | live shape **extends** (cached 8,155 / 8,158, shortfall 3) | `final_turn_text_only: false` — the astra rule does NOT generalise |
| cache grow, no-image control | `openai/gpt-5.6-sol` | `OpenAI` | (in the above) | A = 7,743 tok vs 8,158 with image | the screenshot is 415 tokens, so a shortfall can be read against it |
| cache grow (live + split, with image) | `openai/gpt-6-astra` | `OpenAI` | $0.1777 | live shape **does not extend** (cached 7,719 / 8,158, shortfall 439 = exactly the screenshot); split extends | replication of the 2026-09-06 arm, which is what licenses one run per arm on sol |
| cache grow (live + split, with image) | `openai/gpt-5.6-luna` | `OpenAI` | $0.0034 | live shape **extends** (shortfall 3) | `final_turn_text_only: false` |
| cache grow (live + split, with image) | `openai/gpt-5.6-terra` | `OpenAI` | $0.0341 | live shape **extends** (shortfall 3) | `final_turn_text_only: false` |
| cache placements (none / system / system+last) | `google/gemini-3.5-flash-lite` | `Google AI Studio` | $0.0106 | none → 0 cached; system → 8,041 (the marked block), $0.002964 → $0.000793; system+last → the same 8,041 | `cache_mode: system_breakpoint` |
| cache grow (live + split, with image) | `minimax/minimax-m3` | `MiniMax` | $0.0050 | both shapes **extend** (shortfall 15) | `cache_mode: implicit`, `final_turn_text_only: false` |
| reasoning replay (7 shapes) | `minimax/minimax-m3` | `MiniMax` | $0.0007 | **dropped** — all seven billed 255 prompt tokens, padded included | `reasoning_replay: omit_prior`, `reasoning_use: not_consumed_by_endpoint` |
| cache grow (live + split, with image) | `xiaomi/mimo-v2.5` | `GMICloud` | $0.0014 | both shapes **extend** (shortfall 63 / 21) | `cache_mode: implicit`, `final_turn_text_only: false` |
| reasoning replay (7 shapes) | `xiaomi/mimo-v2.5` | `GMICloud` | $0.0005 | **consumed** — harness +36, padded +1,116 | `reasoning_replay: all`, `reasoning_use: consumed_by_endpoint` |
| cache grow (live + split, with image) | `meta/muse-spark-1.3` | `Meta` | $0.0180 | both shapes **extend** (shortfall 6 / 27) | `cache_mode: unknown` → **implicit** |
| reasoning replay (7 shapes) | `meta/muse-spark-1.3` | `Meta` | $0.0225 | **consumed** — padded +~1,100 | `reasoning_replay: all`, `reasoning_use: consumed_by_endpoint` |
| routing shapes | `anthropic/claude-haiku-4.5` | `Anthropic` | $0.0007 | `{effort: minimal}`, `{effort: high}`, `{enabled: true}` all HTTP 200 | the undeclared effort ladder is legal on the wire; kept, see below |
| cache grow + bare request | `sakana/fugu-ultra` | — | $0.0000 | HTTP 403 region block, and HTTP 404 parameter filter | **unreachable**, see below |

`reported_spend_usd` was added to `test_scripts/probe_cache_hits.py` for this survey; it already
existed in `probe_reasoning_replay.py`. Both scripts also had their config path updated from
`configs/config-append.yaml` to `configs/config-5.0.yaml` (accepting either).

### The image-prefix caching rule is per MODEL, not per endpoint tag

`docs/openrouter-reasoning-and-caching-lessons.md` says endpoint tag, not model, is the unit of
provider behaviour, and that is right for replay, quantization and cache placement. It is **wrong**
for this one: `gpt-6-astra` and `gpt-5.6-sol/luna/terra` all serve on the `openai` tag, and on the
same day, with the same script, the same 415-token screenshot and the same effort, astra could not
extend its cache past the first image while all three gpt-5.6 models could. So the rule is a
property of the upstream model deployment, and `final_turn_text_only` must be decided per model.
The astra arm was re-run for exactly this reason: without replicating the known-poisoned arm, the
three "extends" results would have been indistinguishable from OpenAI having quietly fixed it
platform-wide overnight.

That re-run also exposed a defect in the probe itself. `--grow` judged the verdict as
`cached >= 0.9 * previous prompt_tokens`, and with an 8k system prompt the missing screenshot is
only 5.4% of the prompt — so astra's poisoned run was reported as `prefix_extends`. The 2026-09-06
run caught it only because its screenshot happened to be 1,640 of 9,364 tokens. The verdict now
measures the **shortfall in tokens** (`prefix_extends` iff `previous − cached <= max(64, 1%)`) and
records `cached_shortfall_tokens`, and the saved probe JSONs were re-derived under the new rule.
Under the old rule astra 2026-09-07 read `prefix_extends`; under the new one it reads
`prefix_does_not_extend` while sol, the astra split arm and the 2026-09-06 astra run keep their
verdicts — so the change bites exactly the case it was written for.

### `sakana/fugu-ultra` cannot run, for two independent reasons

1. **Region block.** Its only endpoint returns `HTTP 403 "sakana/fugu-ultra is not available in
   your region: Sakana AI blocks requests originating from your location"`. Reproduced through
   `probe_cache_hits.py` and through a bare unpinned `POST /chat/completions`, so it is not an
   artifact of pinning or of the probe. (The registry already records the same shape for
   `meta/muse-spark-1.1` in 2026-07-30, US-only; `muse-spark-1.3` answers fine.)
2. **Parameter filter.** The `sakana` endpoint does not advertise `max_tokens`. `AppendAgent._body`
   sends `max_tokens` on every request and pins with `require_parameters: true`, so the request
   comes back `HTTP 404 No endpoints found that can handle the requested parameters` with
   `routing_funnel: [{step: "Initial Endpoints", endpoint_count: 1}]` and
   `failed_routing_step: "Filter by Parameters"`. This one survives the region block being lifted.

It stays in `configs/models.yaml` and has a profile because it is on the keep list, with
`cache_mode: unknown` — a listed cache-read price is not an observed hit.

### `anthropic/claude-haiku-4.5` is the one open level question

OpenRouter publishes **no** `reasoning.supported_efforts` for this model and none of its eight
endpoints advertises `reasoning_effort`, which reads as "binary thinking model, no ladder". The
obvious move was to convert the registry entry to `reasoning_type: binary`. It was measured
instead, and the measurement refused the move: `{effort: minimal}`, `{effort: high}` and
`{enabled: true}` all return HTTP 200 on the pinned `anthropic` tag with
`require_parameters: true`, so OpenRouter translates the effort into a thinking budget rather than
filtering the endpoint out. The ladder is therefore legal on the wire and the profile lists
`[high, medium, low, minimal]` to legalise what the registry offers.

Whether the ladder is *meaningful* is a separate question and stays open: the entry in
`configs/models.yaml` records n=3 per tier where only `high` separates (mean 571 reasoning tokens
against a 370-480 band for medium/low/minimal, ordered backwards against the "highest first"
contract), and the two calls above returned 33 reasoning tokens at both `minimal` and `high`. Those
four levels are the only dropped-level candidates in this whole prune that carry **real** run
telemetry, so collapsing them would rewrite four benchmark identities. Left for Andreas, as the
previous author also left it.

### Two registry entries are deliberately absent from this survey

`configs/models.yaml` carries 23 entries but only 21 are surveyed here. `gemini-3.5-flash` and
`claude-opus-4.7` are marked `retired: true`: they are off the keep list and have **no** provider
profile, so there is no pinned endpoint to survey. They are not *unrunnable* on config-5.0 —
`resolve_provider_profile` gives an unprofiled model the `defaults:` block with `endpoint: ""`, so
such a run loads, unpinned, with `unprofiled: true` on the resolved profile. It is simply
uncertified: the endpoint, quantization, cache regime and replay behaviour are whatever the router
picks. That is what the flag is for. They
exist only because two hard-coded aliases in `src/` resolve against the registry and
`resolve_model_selection` exits the process on an unknown one —
`src/cli/runner.py:414` (`DEFAULT_FREEPLAY_TASK_MASTER_MODEL = "gemini-3.5-flash(medium)"`, the
TaskMaster every freeplay run gets by default) and `src/cli/app.py:285`
(`prepare_config(None, "claude-opus-4.7(medium)")`, the control-center supervisor's placeholder).
Delete each entry when its caller stops naming it.
