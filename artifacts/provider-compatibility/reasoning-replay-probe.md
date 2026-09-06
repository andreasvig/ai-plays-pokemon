# Does the endpoint consume replayed reasoning?

Probe: `test_scripts/probe_reasoning_replay.py`. One live turn per endpoint to obtain real reasoning, then the identical
two-turn history sent seven ways: without reasoning, as the harness sends it (`reasoning` + `reasoning_details`), each field
alone, `reasoning_content` (native DeepSeek/Moonshot naming), and two padded variants (~1,100 extra tokens of filler in the
reasoning). Billed prompt tokens vs the no-reasoning baseline show whether the replayed thoughts were tokenized by the model.
Text only; no emulator. Provider pinned with `allow_fallbacks: false`. Probed 2026-09-06.

## google/gemma-4-31b-it  (reasoning param `{"enabled": true}`)

| Endpoint tag | Quant (from tag) | Prompt $/M | Thinking returned on turn 1 | Δ tokens: harness | Δ: padded | Verdict |
|---|---|---:|---|---:|---:|---|
| deepinfra/turbo | turbo | 0.09 | 101 tok / 391 chars | 0 | 0 | **dropped** |
| coreweave/fp4 | fp4 | 0.10 | 167 tok / 651 chars | 176 | 1256 | **consumed** |
| venice/bf16 | bf16 | 0.12 | 137 tok / 533 chars | 0 | 0 | **dropped** |
| chutes/fp4 | fp4 | 0.12 | 1 tok / 539 chars | 0 | 0 | **dropped** |
| deepinfra/fp8 | fp8 | 0.13 | 164 tok / 639 chars | 0 | 0 | **dropped** |
| siliconflow/fp8 | fp8 | 0.13 | 125 tok / 484 chars | 0 | 0 | **dropped** |
| crusoe | unstated | 0.14 | 108 tok / 415 chars | 0 | 0 | **dropped** |
| friendli | unstated | 0.14 | 117 tok / 451 chars | 0 | 0 | **dropped** |
| novita/bf16 | bf16 | 0.14 | 153 tok / 541 chars | 0 | 0 | **dropped** |
| parasail/fp8 | fp8 | 0.15 | 123 tok / 478 chars | 0 | 0 | **dropped** |
| deepinfra/ultra | ultra | 0.27 | 123 tok / 471 chars | 0 | 0 | **dropped** |
| sambanova | unstated | 0.38 | 120 tok / 459 chars | 0 | 0 | **dropped** |
| together | unstated | 0.39 | 136 tok / 532 chars | 0 | 0 | **dropped** |
| modelrun/fp4 | fp4 | 0.75 | 166 tok / 664 chars | 0 | 0 | **dropped** |
| cerebras/fp16 | fp16 | 0.99 | 0 tok / 0 chars | 0 | 0 | **dropped** |

## moonshotai/kimi-k3  (reasoning param `{"effort": "high"}`)

| Endpoint tag | Quant (from tag) | Prompt $/M | Thinking returned on turn 1 | Δ tokens: harness | Δ: padded | Verdict |
|---|---|---:|---|---:|---:|---|
| makora | unstated | 2.55 | — | — | — | **error: HTTP 429: {"error": {"message": "Provider returned error", "** |
| sail-research/fp4 | fp4 | 2.60 | 197 tok / 869 chars | 0 | 0 | **dropped** |
| morph/fp4 | fp4 | 2.60 | 123 tok / 540 chars | 0 | 0 | **dropped** |
| deepinfra/bf16 | bf16 | 2.85 | 0 tok / 764 chars | 0 | 0 | **dropped** |
| digitalocean | unstated | 2.85 | 150 tok / 663 chars | 0 | 0 | **dropped** |
| wafer | unstated | 3.00 | 145 tok / 637 chars | 0 | 0 | **dropped** |
| phala | unstated | 3.00 | 223 tok / 891 chars | 0 | 0 | **dropped** |
| chutes/mxfp4 | mxfp4 | 3.00 | 117 tok / 549 chars | 0 | 0 | **dropped** |
| parasail/fp4 | fp4 | 3.00 | 186 tok / 842 chars | 0 | 0 | **dropped** |
| modal/mxfp4 | mxfp4 | 3.00 | 265 tok / 1060 chars | 0 | 0 | **dropped** |
| together | unstated | 3.00 | 70 tok / 309 chars | 0 | 0 | **dropped** |
| fireworks | unstated | 3.00 | 230 tok / 993 chars | 0 | None | **dropped** |
| baseten/fp8 | fp8 | 3.00 | 94 tok / 442 chars | 0 | 0 | **dropped** |
| moonshotai/mxfp4 | mxfp4 | 3.00 | 123 tok / 554 chars | 0 | 0 | **dropped** |
| fireworks/us | us | 3.30 | 121 tok / 538 chars | 0 | 0 | **dropped** |
| alibaba | unstated | 3.45 | 244 tok / 1107 chars | 0 | 0 | **dropped** |
| fireworks/fast | fast | 4.50 | 146 tok / 654 chars | 0 | 0 | **dropped** |
| morph/fast | fast | 6.00 | 130 tok / 541 chars | 0 | 0 | **dropped** |

Effort sweep on `moonshotai/mxfp4`:

| effort | reasoning tokens | completion tokens |
|---|---:|---:|
| off | 0 | 24 |
| low | 39 | 74 |
| high | 121 | 156 |
| max | 118 | 155 |

## Reading the table

- **Gemma 4 31B**: 14 of 15 endpoints drop replayed reasoning regardless of which field carries it, including `reasoning_content`.
  Only `coreweave/fp4` tokenizes it (Δ ≈ its own reasoning length; padded Δ ≈ +1,250). Since OpenRouter forwards the fields to
  CoreWeave, the drop happens in the other providers' chat templates, not at the gateway. `cerebras/fp16` returns no thinking at
  all; `chutes/fp4` reports 1 reasoning token for 539 characters of thinking (usage misreport).
- **Kimi K3**: all 17 reachable endpoints drop replayed reasoning, Moonshot's own included (`makora` rate-limited). Thinking is
  on but brief: 0 / 39 / 121 / 118 reasoning tokens at effort off / low / high / max on Moonshot. `max` buys nothing over `high`.
- Consequence for the ten-turn campaign: gemma-replay on DeepInfra and kimi-k3 on Moonshot were both effectively no-replay runs.
- Quantization caveat (Andreas, 2026-09-06): providers can serve degraded quants or sampling defaults; see Artificial Analysis'
  Endpoint Accuracy Index (https://artificialanalysis.ai/#endpoint-accuracy-index-gpt-oss-120b-high), which currently covers
  gpt-oss-120b only. The one Gemma endpoint that consumes replay is an fp4 quant; any replay experiment on it must pair both arms
  on the same endpoint and, separately, compare gameplay against a bf16 endpoint before attributing anything to the model.
