"""Shared helpers for real OCR buffer cleanup benchmarking.

Production captures multi-frame Tesseract buffers during a turn and sends them
to an LLM for cleanup. This module runs that same cleanup path against a fixed
dataset and scores outputs with an LLM judge (semantic, not string match).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from src.emulator.ocr import DEFAULT_CLEANUP_SYSTEM_PROMPT, DEFAULT_CLEANUP_USER_PROMPT

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = Path(__file__).parent / "buffer_dataset.json"

JUDGE_SYSTEM_PROMPT = """You judge OCR cleanup quality for game screenshot text.

You receive:
1. A noisy multi-frame OCR buffer (raw Tesseract captures from one game turn)
2. A model's cleaned output
3. A reference answer (gold standard for what information should be present)

Score whether the cleaned output captures the same meaningful in-game information
as the reference. Minor wording differences, punctuation, and line breaks are fine.
Penalize missing key facts, invented text not supported by the raw buffer, and
leaving obvious gibberish/noise in the output.

Return strict JSON only."""

JUDGE_USER_PROMPT = """## Raw OCR buffer
{raw_preview}

## Model cleaned output
{predicted}

## Reference (gold standard)
{reference}

Rate the model output."""

# Low-temp + throughput routing. Do NOT set reasoning.enabled=false globally —
# gpt-oss-20b rejects it ("Reasoning is mandatory... cannot be disabled").
# Omit reasoning for that model; add {"reasoning": {"enabled": false}} per-config
# for models that support explicit non-thinking (gemma, qwen, etc.).
DEFAULT_OPENROUTER_EXTRA: dict[str, Any] = {
    "temperature": 0.1,
    "top_p": 0.95,
    "provider": {"sort": "throughput"},
}

NON_THINKING_EXTRA: dict[str, Any] = {
    "reasoning": {"enabled": False},
}


def merge_openrouter_extra(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_OPENROUTER_EXTRA)
    if overrides:
        merged.update(overrides)
    return merged


@dataclass
class CleanupConfig:
    name: str
    model: str
    system_prompt: str = DEFAULT_CLEANUP_SYSTEM_PROMPT
    user_prompt: str = DEFAULT_CLEANUP_USER_PROMPT
    timeout_s: float = 60.0
    extra: dict[str, Any] = field(default_factory=dict)

    def resolved_extra(self) -> dict[str, Any]:
        return merge_openrouter_extra(self.extra)


def load_api_key() -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        return api_key
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    return api_key


def load_dataset(path: Path | None = None) -> dict:
    dataset_path = path or DEFAULT_DATASET_PATH
    with open(dataset_path, encoding="utf-8") as f:
        return json.load(f)


def cleanup_buffer(
    raw: dict[str, str],
    config: CleanupConfig,
) -> tuple[str, dict[str, Any]]:
    """Run production-style buffer cleanup via OpenRouter."""
    api_key = load_api_key()
    raw_json = json.dumps(raw, indent=2, ensure_ascii=False)
    user_prompt = config.user_prompt.format(raw_json=raw_json)

    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "usage": {"include": True},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "cleaned_ocr",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "cleaned_text": {
                            "type": "string",
                            "description": "Cleaned text with line breaks preserved.",
                        },
                    },
                    "required": ["cleaned_text"],
                    "additionalProperties": False,
                },
            },
        },
        **config.resolved_extra(),
    }

    t0 = time.perf_counter()
    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=config.timeout_s,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    data = response.json()
    if "error" in data:
        err = data["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Cleanup LLM error: {msg}")

    usage_block = data.get("usage") or {}
    usage = {
        "cost_usd": float(usage_block.get("cost", 0.0) or 0.0),
        "input_tokens": int(usage_block.get("prompt_tokens", 0) or 0),
        "output_tokens": int(usage_block.get("completion_tokens", 0) or 0),
        "latency_ms": latency_ms,
    }

    content = data["choices"][0]["message"].get("content", "")
    if not content:
        return "", usage
    try:
        parsed = json.loads(content)
        return parsed.get("cleaned_text", "").strip(), usage
    except json.JSONDecodeError:
        return content.strip(), usage


def judge_cleanup(
    raw: dict[str, str],
    predicted: str,
    reference_text: str,
    judge_model: str = "google/gemma-4-26b-a4b-it",
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """LLM judge: semantic comparison of predicted vs reference cleanup."""
    api_key = load_api_key()

    raw_preview = json.dumps(raw, indent=2, ensure_ascii=False)
    if len(raw_preview) > 4000:
        raw_preview = raw_preview[:4000] + "\n... [truncated]"

    user_prompt = JUDGE_USER_PROMPT.format(
        raw_preview=raw_preview,
        predicted=predicted or "[empty]",
        reference=reference_text,
    )

    t0 = time.perf_counter()
    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": judge_model,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "usage": {"include": True},
            **merge_openrouter_extra(),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ocr_judge_verdict",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "score": {
                                "type": "integer",
                                "description": "0-10 quality score",
                            },
                            "pass": {
                                "type": "boolean",
                                "description": "True if output is good enough for agent use",
                            },
                            "reasoning": {"type": "string"},
                            "missing_info": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "hallucinated_info": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "score",
                            "pass",
                            "reasoning",
                            "missing_info",
                            "hallucinated_info",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
        },
        timeout=timeout_s,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    data = response.json()
    if "error" in data:
        err = data["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Judge LLM error: {msg}")

    usage_block = data.get("usage") or {}
    usage = {
        "cost_usd": float(usage_block.get("cost", 0.0) or 0.0),
        "input_tokens": int(usage_block.get("prompt_tokens", 0) or 0),
        "output_tokens": int(usage_block.get("completion_tokens", 0) or 0),
        "latency_ms": latency_ms,
    }

    content = data["choices"][0]["message"].get("content", "")
    verdict = json.loads(content)
    verdict["usage"] = usage
    return verdict


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def run_benchmark(
    configs: list[CleanupConfig],
    dataset: dict | None = None,
    dataset_path: Path | None = None,
    judge_model: str = "google/gemma-4-26b-a4b-it",
    case_ids: list[str] | None = None,
    pass_threshold: int = 7,
) -> dict[str, Any]:
    """Run all cleanup configs against the buffer dataset with LLM judging."""
    if dataset is None:
        dataset = load_dataset(dataset_path)

    cases = dataset["cases"]
    if case_ids:
        case_id_set = set(case_ids)
        cases = [c for c in cases if c["id"] in case_id_set]

    results: dict[str, Any] = {
        "dataset_version": dataset.get("version"),
        "judge_model": judge_model,
        "pass_threshold": pass_threshold,
        "configs": {},
    }

    for config in configs:
        print(f"\n--- Running config: {config.name} ({config.model}) ---", flush=True)
        config_results = {
            "model": config.model,
            "cases": [],
            "summary": {
                "total": 0,
                "passed": 0,
                "avg_score": 0.0,
                "total_cost_usd": 0.0,
                "cleanup_cost_usd": 0.0,
                "judge_cost_usd": 0.0,
                "avg_cleanup_latency_ms": 0.0,
                "p95_cleanup_latency_ms": 0.0,
            },
        }

        scores: list[int] = []
        cleanup_latencies: list[float] = []
        cleanup_costs: list[float] = []
        for idx, case in enumerate(cases, start=1):
            print(f"  [{idx}/{len(cases)}] {case['id']}", flush=True)
            case_result: dict[str, Any] = {
                "id": case["id"],
                "category": case.get("category"),
            }
            try:
                cleaned, cleanup_usage = cleanup_buffer(case["raw"], config)
                case_result["predicted"] = cleaned
                case_result["cleanup_usage"] = cleanup_usage

                verdict = judge_cleanup(
                    case["raw"],
                    cleaned,
                    case["reference_text"],
                    judge_model=judge_model,
                )
                case_result["judge"] = verdict
                score = int(verdict.get("score", 0))
                passed = bool(verdict.get("pass", score >= pass_threshold))
                scores.append(score)
                config_results["summary"]["total"] += 1
                if passed:
                    config_results["summary"]["passed"] += 1
                cleanup_cost = cleanup_usage.get("cost_usd", 0.0)
                judge_cost = verdict.get("usage", {}).get("cost_usd", 0.0)
                config_results["summary"]["cleanup_cost_usd"] += cleanup_cost
                config_results["summary"]["judge_cost_usd"] += judge_cost
                config_results["summary"]["total_cost_usd"] += cleanup_cost + judge_cost
                cleanup_latencies.append(cleanup_usage.get("latency_ms", 0.0))
                cleanup_costs.append(cleanup_cost)
            except Exception as exc:
                case_result["error"] = str(exc)

            config_results["cases"].append(case_result)

        if scores:
            config_results["summary"]["avg_score"] = round(
                sum(scores) / len(scores), 2
            )
        config_results["summary"]["pass_rate"] = round(
            config_results["summary"]["passed"]
            / max(config_results["summary"]["total"], 1),
            4,
        )
        if cleanup_latencies:
            config_results["summary"]["avg_cleanup_latency_ms"] = round(
                sum(cleanup_latencies) / len(cleanup_latencies), 1
            )
            config_results["summary"]["p95_cleanup_latency_ms"] = round(
                _percentile(cleanup_latencies, 95), 1
            )
        if cleanup_costs:
            config_results["summary"]["avg_cleanup_cost_usd"] = round(
                sum(cleanup_costs) / len(cleanup_costs), 6
            )
        config_results["summary"]["total_cost_usd"] = round(
            config_results["summary"]["total_cost_usd"], 6
        )
        config_results["summary"]["cleanup_cost_usd"] = round(
            config_results["summary"]["cleanup_cost_usd"], 6
        )
        config_results["summary"]["judge_cost_usd"] = round(
            config_results["summary"]["judge_cost_usd"], 6
        )
        results["configs"][config.name] = config_results

    return results
