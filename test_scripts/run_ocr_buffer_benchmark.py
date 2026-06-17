"""Run OCR buffer cleanup models against the real-trace dataset with LLM judging.

Usage:
    ./venv/bin/python test_scripts/run_ocr_buffer_benchmark.py
    ./venv/bin/python test_scripts/run_ocr_buffer_benchmark.py --configs test_scripts/ocr_buffer_configs.json
    ./venv/bin/python test_scripts/run_ocr_buffer_benchmark.py --dry-run

Requires OPENROUTER_API_KEY in .env or environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.ocr_benchmark.buffer_benchmark import (  # noqa: E402
    CleanupConfig,
    DEFAULT_DATASET_PATH,
    DEFAULT_OPENROUTER_EXTRA,
    load_dataset,
    run_benchmark,
)
from src.emulator.ocr import DEFAULT_CLEANUP_SYSTEM_PROMPT, DEFAULT_CLEANUP_USER_PROMPT

DEFAULT_CONFIGS_PATH = Path(__file__).parent / "ocr_buffer_configs.json"
DEFAULT_OUTPUT = Path(__file__).parent / "ocr_buffer_benchmark_results.json"


def load_configs(path: Path) -> list[CleanupConfig]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    configs: list[CleanupConfig] = []
    for entry in data:
        configs.append(
            CleanupConfig(
                name=entry["name"],
                model=entry["model"],
                system_prompt=entry.get("system_prompt", DEFAULT_CLEANUP_SYSTEM_PROMPT),
                user_prompt=entry.get("user_prompt", DEFAULT_CLEANUP_USER_PROMPT),
                timeout_s=float(entry.get("timeout_s", 60.0)),
                extra=entry.get("extra", {}),
            )
        )
    return configs


def print_summary(results: dict) -> None:
    print("\n=== OCR Buffer Benchmark Summary ===")
    print(f"Judge model: {results['judge_model']}")
    print(f"Pass threshold: {results['pass_threshold']}/10\n")

    rows = []
    for name, config_result in results["configs"].items():
        summary = config_result["summary"]
        rows.append(
            (
                name,
                summary["avg_score"],
                summary["pass_rate"],
                summary["passed"],
                summary["total"],
                summary.get("avg_cleanup_latency_ms", 0.0),
                summary.get("p95_cleanup_latency_ms", 0.0),
                summary.get("avg_cleanup_cost_usd", 0.0),
                summary.get("cleanup_cost_usd", 0.0),
            )
        )

    rows.sort(key=lambda r: (-r[1], -r[2], r[5]))
    print(
        f"{'Config':<22} {'Score':>5} {'Pass%':>6} {'Pass':>7} "
        f"{'Avg ms':>7} {'P95 ms':>7} {'$/call':>8} {'Cleanup$':>9}"
    )
    print("-" * 82)
    for name, avg, pass_rate, passed, total, avg_ms, p95_ms, per_call, cleanup_cost in rows:
        print(
            f"{name:<22} {avg:>5.2f} {pass_rate * 100:>5.1f}% "
            f"{passed:>3}/{total:<3} {avg_ms:>7.0f} {p95_ms:>7.0f} "
            f"${per_call:>7.5f} ${cleanup_cost:>8.4f}"
        )

    print("\nNote: latency and $/call are cleanup-only (production path).")
    print("Judge cost is benchmark overhead, not paid per game turn.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Path to buffer_dataset.json",
    )
    parser.add_argument(
        "--configs",
        type=Path,
        default=DEFAULT_CONFIGS_PATH,
        help="JSON list of cleanup model configs to compare",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Where to write full results JSON",
    )
    parser.add_argument(
        "--judge-model",
        default="google/gemma-4-26b-a4b-it",
        help="OpenRouter model for LLM judging",
    )
    parser.add_argument(
        "--pass-threshold",
        type=int,
        default=7,
        help="Score threshold if judge does not set pass explicitly",
    )
    parser.add_argument(
        "--cases",
        nargs="*",
        help="Optional case IDs to run (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print dataset/config info without calling APIs",
    )
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    configs = load_configs(args.configs)

    print(f"Dataset: {args.dataset} ({dataset['case_count']} cases)")
    print(f"Configs: {[c.name for c in configs]}")
    print(f"OpenRouter profile: {DEFAULT_OPENROUTER_EXTRA}")

    if args.dry_run:
        print("\nCategory breakdown:", dataset.get("category_counts"))
        return

    results = run_benchmark(
        configs=configs,
        dataset=dataset,
        judge_model=args.judge_model,
        case_ids=args.cases,
        pass_threshold=args.pass_threshold,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print_summary(results)
    print(f"\nFull results: {args.output}")


if __name__ == "__main__":
    main()
