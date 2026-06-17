"""Extract real OCR buffer test cases from run traces (events.jsonl).

Builds tests/ocr_benchmark/buffer_dataset.json — each case has:
  - raw: multi-frame OCR buffer from an ocr_flush event
  - reference_text: production cleaned output (gold standard for LLM judge)

Usage:
    ./venv/bin/python test_scripts/extract_ocr_buffer_dataset.py
    ./venv/bin/python test_scripts/extract_ocr_buffer_dataset.py --target 30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_OUTPUT = PROJECT_ROOT / "tests/ocr_benchmark/buffer_dataset.json"
DEFAULT_RUNS_DIR = PROJECT_ROOT / "local/runs"

CATEGORY_QUOTAS = {
    "dialogue": 10,
    "battle": 8,
    "other": 7,
    "sparse": 5,
}


def categorize(cleaned: str) -> str:
    upper = cleaned.upper()
    if "WHAT WILL" in upper or " USED " in upper or (
        "FIGHT" in upper and "BAG" in upper and "RUN" in upper
    ):
        return "battle"
    if "POKéMON LIST" in upper or "POKéMON INFO" in upper or "POKéMON SKILLS" in upper:
        return "menu"
    if "POKé BALL" in upper and "CANCEL" in upper:
        return "bag"
    if cleaned.startswith("OAK:") or "PROF." in upper or "GREEN" in upper:
        return "dialogue"
    if len(cleaned.strip()) < 40:
        return "sparse"
    return "other"


def normalize_key(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text[:120]


def collect_candidates(runs_dir: Path) -> list[dict]:
    candidates: list[dict] = []
    seen_raw: set[str] = set()
    seen_ref: set[str] = set()

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        events_file = run_dir / "events.jsonl"
        if not events_file.exists():
            continue

        with open(events_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get("type") != "ocr_flush":
                    continue

                raw = event.get("raw") or {}
                reference_text = (event.get("cleaned") or "").strip()
                if len(raw) < 2 or len(reference_text) < 15:
                    continue

                raw_hash = hashlib.sha256(
                    json.dumps(raw, sort_keys=True).encode()
                ).hexdigest()
                if raw_hash in seen_raw:
                    continue
                seen_raw.add(raw_hash)

                ref_key = normalize_key(reference_text)
                if ref_key in seen_ref:
                    continue

                category = categorize(reference_text)
                candidates.append(
                    {
                        "id": "",  # filled after selection
                        "source_run": run_dir.name,
                        "turn": event.get("turn"),
                        "category": category,
                        "raw": raw,
                        "reference_text": reference_text,
                        "n_captures": event.get("n_captures", len(raw)),
                        "source_model": event.get("model"),
                        "ref_key": ref_key,
                        "raw_frames": len(raw),
                    }
                )

    return candidates


def select_diverse(candidates: list[dict], target: int) -> list[dict]:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        by_category[candidate["category"]].append(candidate)

    for cat in by_category:
        by_category[cat].sort(
            key=lambda c: (c["raw_frames"], len(c["reference_text"])),
            reverse=True,
        )

    selected: list[dict] = []
    seen_ref: set[str] = set()

    def try_add(candidate: dict) -> bool:
        if candidate["ref_key"] in seen_ref:
            return False
        seen_ref.add(candidate["ref_key"])
        selected.append(candidate)
        return True

    for category, quota in CATEGORY_QUOTAS.items():
        added = 0
        for candidate in by_category.get(category, []):
            if added >= quota or len(selected) >= target:
                break
            if try_add(candidate):
                added += 1

    if len(selected) < target:
        remaining = [
            c
            for c in candidates
            if c["ref_key"] not in seen_ref
        ]
        remaining.sort(key=lambda c: c["raw_frames"], reverse=True)
        for candidate in remaining:
            if len(selected) >= target:
                break
            try_add(candidate)

    for idx, case in enumerate(selected, start=1):
        slug = re.sub(r"[^a-z0-9]+", "_", case["category"])[:12]
        run_slug = re.sub(r"[^a-z0-9]+", "_", case["source_run"])[:24]
        case["id"] = f"{slug}_t{case['turn']}_{run_slug}_{idx:02d}"
        case.pop("ref_key", None)

    return selected


def build_dataset(runs_dir: Path, target: int) -> dict:
    candidates = collect_candidates(runs_dir)
    selected = select_diverse(candidates, target)

    by_cat = defaultdict(int)
    for case in selected:
        by_cat[case["category"]] += 1

    return {
        "version": 1,
        "description": (
            "Real multi-frame OCR buffers from run traces. reference_text is the "
            "production ocr_flush.cleaned output; an LLM judge scores cleanup models "
            "semantically against it."
        ),
        "source_runs_dir": str(runs_dir.relative_to(PROJECT_ROOT)),
        "case_count": len(selected),
        "category_counts": dict(by_cat),
        "cases": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Directory containing run folders with events.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON path",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=30,
        help="Number of test cases to include (minimum 25 recommended)",
    )
    args = parser.parse_args()

    if not args.runs_dir.exists():
        print(f"Runs directory not found: {args.runs_dir}", file=sys.stderr)
        sys.exit(1)

    dataset = build_dataset(args.runs_dir, args.target)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    print(f"Wrote {dataset['case_count']} cases to {args.output}")
    print("Categories:", dataset["category_counts"])


if __name__ == "__main__":
    main()
