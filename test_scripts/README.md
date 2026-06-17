# Test Scripts

Ad-hoc experiment and probe scripts for manual runs. These are **not** the pytest suite — see [`tests/`](../tests/) for automated tests.

Operational probes and one-off utilities live in [`scripts/`](../scripts/).

## Scripts

| Script | Purpose |
|--------|---------|
| `extract_ocr_buffer_dataset.py` | Build `tests/ocr_benchmark/buffer_dataset.json` from real run traces |
| `run_ocr_buffer_benchmark.py` | Compare cleanup models on the buffer dataset (LLM judge) |
| `ocr_buffer_configs.json` | Default model configs for the buffer benchmark |
| `test_media_resolution.py` | Check whether OpenRouter forwards Gemini's `media_resolution` parameter |
| `test_resize_nocache.py` | Control run to rule out caching when comparing image resize behavior |
| `test_resize_spatial.py` | Compare token cost and model output across image resolutions |

## OCR buffer benchmark

Real multi-frame OCR buffers are extracted from `local/runs/*/events.jsonl` (`ocr_flush` events).
Each test case has a noisy `raw` buffer and a `reference_text` gold answer. Models are scored
with an LLM judge (semantic), not string matching.

```bash
# 1. Build / refresh dataset (needs local runs with ocr_flush events)
./venv/bin/python test_scripts/extract_ocr_buffer_dataset.py --target 30

# 2. Compare cleanup models (needs OPENROUTER_API_KEY)
./venv/bin/python test_scripts/run_ocr_buffer_benchmark.py

# Preview without API calls
./venv/bin/python test_scripts/run_ocr_buffer_benchmark.py --dry-run
```

## Usage

Run from the repo root. Most scripts need `OPENROUTER_API_KEY` in `.env` or the environment.

```bash
OPENROUTER_API_KEY=... ./venv/bin/python test_scripts/test_media_resolution.py
```

Results JSON files are written alongside the scripts in this folder.
