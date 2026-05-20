"""Compare input-token usage across two runs that differ only in upscale_factor.

Usage:
    python scripts/compare_upscale_runs.py <baseline_run_dir> <experiment_run_dir>

Example:
    python scripts/compare_upscale_runs.py \\
        local/runs/2026-05-20_..._phase5_test \\
        local/runs/2026-05-20_..._phase5_test

Reads `turn_usage` events from each run's events.jsonl and prints a turn-by-turn
side-by-side table plus a summary (totals, per-turn average, % delta, cost).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_turn_usage(run_dir: Path) -> list[dict]:
    events_file = run_dir / "events.jsonl"
    if not events_file.exists():
        raise FileNotFoundError(f"No events.jsonl in {run_dir}")
    rows = []
    for line in events_file.read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") == "turn_usage":
            rows.append(ev)
    rows.sort(key=lambda r: r.get("turn", 0))
    return rows


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    baseline_dir = Path(sys.argv[1])
    experiment_dir = Path(sys.argv[2])

    base = load_turn_usage(baseline_dir)
    expt = load_turn_usage(experiment_dir)

    base_by_turn = {r["turn"]: r for r in base}
    expt_by_turn = {r["turn"]: r for r in expt}
    all_turns = sorted(set(base_by_turn) | set(expt_by_turn))

    print(f"Baseline:   {baseline_dir}  ({len(base)} turns)")
    print(f"Experiment: {experiment_dir}  ({len(expt)} turns)")
    print()
    print(f"{'Turn':>4} | {'base_in':>8} {'expt_in':>8} {'Δ_in':>8} {'Δ%':>7} | "
          f"{'base_out':>8} {'expt_out':>8} | {'base_$':>9} {'expt_$':>9}")
    print("-" * 95)

    tot_base_in = tot_expt_in = 0
    tot_base_out = tot_expt_out = 0
    tot_base_cost = tot_expt_cost = 0.0

    for t in all_turns:
        b = base_by_turn.get(t, {})
        e = expt_by_turn.get(t, {})
        b_in = b.get("request_tokens") or 0
        e_in = e.get("request_tokens") or 0
        b_out = b.get("response_tokens") or 0
        e_out = e.get("response_tokens") or 0
        b_cost = b.get("cost_usd") or 0.0
        e_cost = e.get("cost_usd") or 0.0
        delta_in = e_in - b_in
        pct = (delta_in / b_in * 100) if b_in else 0.0
        print(f"{t:>4} | {b_in:>8} {e_in:>8} {delta_in:>+8} {pct:>+6.1f}% | "
              f"{b_out:>8} {e_out:>8} | ${b_cost:>8.4f} ${e_cost:>8.4f}")
        tot_base_in += b_in
        tot_expt_in += e_in
        tot_base_out += b_out
        tot_expt_out += e_out
        tot_base_cost += b_cost
        tot_expt_cost += e_cost

    print("-" * 95)
    delta_total_in = tot_expt_in - tot_base_in
    pct_total = (delta_total_in / tot_base_in * 100) if tot_base_in else 0.0
    n = max(len(base), len(expt)) or 1
    print(f"{'TOTAL':>4} | {tot_base_in:>8} {tot_expt_in:>8} {delta_total_in:>+8} "
          f"{pct_total:>+6.1f}% | {tot_base_out:>8} {tot_expt_out:>8} | "
          f"${tot_base_cost:>8.4f} ${tot_expt_cost:>8.4f}")
    print(f"{'AVG':>4} | {tot_base_in//n:>8} {tot_expt_in//n:>8} "
          f"{delta_total_in//n:>+8} {pct_total:>+6.1f}% | "
          f"{tot_base_out//n:>8} {tot_expt_out//n:>8} | "
          f"${tot_base_cost/n:>8.4f} ${tot_expt_cost/n:>8.4f}")
    print()
    print(f"Input-token cost multiplier (expt / base): {tot_expt_in / tot_base_in:.2f}×")
    print(f"USD cost multiplier (expt / base):         {tot_expt_cost / tot_base_cost:.2f}×")
    return 0


if __name__ == "__main__":
    sys.exit(main())
