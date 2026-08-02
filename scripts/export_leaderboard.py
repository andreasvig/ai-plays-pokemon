#!/usr/bin/env python3
"""Export every run to a single flat table (JSON + CSV) for analysis/plots.

Reuses the app's OWN projection + derivations (``src/app/projection.py`` and
``src/app/derivations.py``) so the columns match the dashboard exactly —
``leaderboard_eligible``, ``gates_reached``, cost, etc. are the same definitions
the control center uses, not a re-implementation that could drift.

For each run folder under the runs dir it emits one row = the flat ``RunSummary``
fields + a few raw extras useful for analysis (thinking effort, token counts,
Player-vs-TaskMaster cost split, and per-gate reached-turn columns).

Outputs (default ``local/analysis/``):
  - ``runs.json``        — every run, one object per run (full detail)
  - ``runs.csv``         — same, flat CSV for spreadsheets/pandas
  - ``leaderboard.json`` — best official run per model, ranked farthest-then-fastest

Usage:
  ./venv/bin/python scripts/export_leaderboard.py
  ./venv/bin/python scripts/export_leaderboard.py --runs-dir local/runs --out local/analysis
  ./venv/bin/python scripts/export_leaderboard.py --stdout        # print JSON to stdout, no files
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.app.derivations import leaderboard as derive_leaderboard  # noqa: E402
from src.app.projection import project_run_dir  # noqa: E402

# Canonical gate ladder (pokebench-easy). Every run's referee carries the same
# ladder; we read each run's own gates so a schema change stays honest, and use
# this only as the stable COLUMN ORDER for the flattened per-gate fields.
_GATE_ORDER = [
    "left_bedroom", "left_house", "oaks_lab_entered", "starter_chosen",
    "rival1_done", "route1_reached", "viridian_reached",
]


def _raw_extras(run_dir: Path) -> dict:
    """Pull analysis-useful fields the flat RunSummary doesn't carry."""
    try:
        raw = json.loads((run_dir / "run_summary.json").read_text())
    except (OSError, ValueError):
        return {}
    session = raw.get("session") or {}
    cost = raw.get("cost") or {}
    referee = raw.get("referee") or {}
    thinking = session.get("thinking") or {}

    extras: dict = {
        "effort": thinking.get("effort"),
        "player_turns": session.get("player_turns"),
        "task_master_turns": session.get("task_master_turns"),
        "llm_usd": cost.get("llm_usd"),
        "task_master_usd": cost.get("task_master_usd"),
        "ocr_usd": cost.get("ocr_usd"),
        "total_input_tokens": cost.get("total_input_tokens"),
        "total_output_tokens": cost.get("total_output_tokens"),
    }
    # Per-gate reached turn (None = never reached) → one column per gate.
    gate_turn = {g["id"]: g.get("turn") for g in referee.get("gates", []) if g.get("id")}
    for gid in _GATE_ORDER:
        extras[f"gate_{gid}"] = gate_turn.get(gid)
    return extras


def build_rows(runs_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for run_dir in sorted(p.parent for p in runs_dir.glob("*/run_summary.json")):
        summary = project_run_dir(run_dir)
        if summary is None:
            continue
        row = summary.model_dump()
        # leaderboard_eligible is a computed property, not a dumped field — add it.
        row["leaderboard_eligible"] = summary.leaderboard_eligible
        row.update(_raw_extras(run_dir))
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default=str(REPO / "local" / "runs"))
    ap.add_argument("--out", default=str(REPO / "local" / "analysis"))
    ap.add_argument("--stdout", action="store_true",
                    help="print the runs JSON to stdout and write no files")
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir)
    rows = build_rows(runs_dir)
    if not rows:
        print(f"No runs with a run_summary.json under {runs_dir}", file=sys.stderr)
        return 1

    if args.stdout:
        json.dump(rows, sys.stdout, indent=2, default=str)
        print()
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    (out / "runs.json").write_text(json.dumps(rows, indent=2, default=str))

    # Stable, union-of-all-keys column order for the CSV.
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with (out / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})

    # Ranked leaderboard (best official run per model) via the app's derivation.
    summaries = [project_run_dir(p.parent) for p in runs_dir.glob("*/run_summary.json")]
    summaries = [s for s in summaries if s is not None]
    board = derive_leaderboard(summaries)
    board_rows = []
    for rank, s in enumerate(board, 1):
        d = s.model_dump()
        d["leaderboard_eligible"] = s.leaderboard_eligible
        board_rows.append({"rank": rank, **d})
    (out / "leaderboard.json").write_text(json.dumps(board_rows, indent=2, default=str))

    n_elig = sum(1 for r in rows if r["leaderboard_eligible"])
    print(f"Wrote {len(rows)} runs ({n_elig} leaderboard-eligible) to {out}/")
    print(f"  - runs.json / runs.csv  ({len(cols)} columns)")
    print(f"  - leaderboard.json      ({len(board_rows)} ranked models)")
    if board_rows:
        print("\nLeaderboard (farthest-then-fastest):")
        for r in board_rows:
            print(f"  {r['rank']:>2}. {r['model']:<28} "
                  f"gates={r['gates_reached']}/{r['total_gates']}  "
                  f"turns={r['turns']:<4} ${r['total_cost_usd']:.3f}  "
                  f"[{r['furthest_gate']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
