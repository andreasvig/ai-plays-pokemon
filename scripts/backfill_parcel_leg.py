"""Re-score one leg of every stored run from its recorded positions.

Written 2026-09-14 for the Oak's Parcel leg: the ladder's locus for
``parcel_delivered`` pointed at tiles behind the Mart counter, unreachable on
the walk graph, so the tracker recorded no distance for that leg on any run
(``d_open`` None → no partial credit on the Performance card, no walk
efficiency for the leg). The positions the referee polled every turn are in
``referee_state.json`` (``positions``: ``[turn, map_group, map_num, x, y]``),
so the leg can be re-scored the way the tracker would have scored it live,
with the corrected ladder: ``d_open`` = distance from the first on-graph
position at or after the leg opened, ``d_min`` = the closest the run came,
``distance_now`` = the last on-graph position's distance, ``fraction`` and
``efficiency`` per ``progress._Leg``; ``progress.progress`` and
``current_leg`` follow when the leg is the one the run ended on. Steps and
tiles are left as recorded.

    venv/bin/python scripts/backfill_parcel_leg.py [--runs-root local/runs] [--leg parcel_delivered] [--apply]

Without ``--apply`` it prints what would change. Only runs whose summary has a
``referee.progress.legs`` entry for the leg with ``d_open`` None are touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.referee.progress import OPEN_LEG_FRACTION_CAP  # noqa: E402
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph  # noqa: E402

LADDER = Path("configs/checkpoints-firered-firstbadge.yaml")


def leg_targets(graph: WalkGraph, ladder_path: Path, leg_id: str) -> frozenset[int]:
    import yaml

    lad = yaml.safe_load(ladder_path.read_text())
    nodes = lad.get("nodes") or lad.get("checkpoints") or []
    cp = next((c for c in nodes if isinstance(c, dict) and c.get("id") == leg_id), None)
    if cp is None:
        raise SystemExit(f"{ladder_path}: no gate {leg_id}")
    return frozenset(graph.resolve_locus(type("Checkpoint", (), dict(cp))()))


def rescore_leg(leg: dict[str, Any], positions: list[list[int]], graph: WalkGraph, targets: frozenset[int],
                last_turn: Optional[int] = None) -> dict[str, Any]:
    """The leg dict with d_open / d_min / distance_now / fraction / efficiency
    recomputed from the positions inside the leg's turn window. Mirrors
    ``ProgressTracker._seed_distance`` / ``_fold``: the opening distance is the
    first on-graph position's, later positions update d_min and distance_now."""
    opened, closed = leg.get("opened_turn"), leg.get("closed_turn")
    if not isinstance(opened, int):
        return dict(leg)
    end = closed if isinstance(closed, int) else (last_turn if isinstance(last_turn, int) else None)
    d_open = d_min = d_now = None
    for pos in positions:
        if len(pos) < 5 or pos[0] < opened or (end is not None and pos[0] > end):
            continue
        node = graph.node_id(pos[1], pos[2], pos[3], pos[4])
        if node is None:
            continue
        d = graph.distance_to(targets, node)
        if d is None:
            continue
        if d_open is None:
            d_open = d
        d_now = d
        d_min = d if d_min is None else min(d_min, d)
    out = dict(leg)
    out.update({"d_open": d_open, "d_min": d_min, "distance_now": d_now})
    frac = None
    if leg.get("scored", True) and d_open is not None and d_min is not None:
        frac = 0.0 if d_open <= 0 else max(0.0, min(1.0, 1.0 - d_min / d_open))
        if leg.get("status") != "closed":
            frac = min(frac, OPEN_LEG_FRACTION_CAP)
    out["fraction"] = frac
    steps = leg.get("steps_walked")
    out["efficiency"] = (d_open / max(steps, d_open)) if leg.get("status") == "closed" and d_open and isinstance(steps, int) else None
    return out


def rescore_summary(summary: dict[str, Any], positions: list[list[int]], graph: WalkGraph, targets: frozenset[int], leg_id: str) -> bool:
    """Apply :func:`rescore_leg` to ``summary["referee"]["progress"]`` in place. True when something changed."""
    prog = (summary.get("referee") or {}).get("progress")
    if not isinstance(prog, dict):
        return False
    legs = prog.get("legs") or []
    idx = next((i for i, l in enumerate(legs) if isinstance(l, dict) and l.get("node_id") == leg_id), None)
    if idx is None or legs[idx].get("d_open") is not None:
        return False
    last_turn = (summary.get("session") or {}).get("total_turns")
    new = rescore_leg(legs[idx], positions, graph, targets, last_turn)
    if new == legs[idx]:
        return False
    legs[idx] = new
    cur = prog.get("current_leg")
    if isinstance(cur, dict) and cur.get("node_id") == leg_id:
        prog["current_leg"] = dict(new)
        gates = prog.get("gates_reached")
        if isinstance(gates, int) and prog.get("progress") is not None:
            prog["progress"] = float(gates) + (new["fraction"] or 0.0)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", default="local/runs")
    ap.add_argument("--leg", default="parcel_delivered")
    ap.add_argument("--ladder", default=str(LADDER))
    ap.add_argument("--apply", action="store_true", help="write run_summary.json; default prints the changes")
    args = ap.parse_args()
    graph = WalkGraph.load(DEFAULT_GRAPH_PATH)
    targets = leg_targets(graph, Path(args.ladder), args.leg)
    print(f"{args.leg}: {len(targets)} target tile(s) {[graph.nodes[i] for i in targets]}")
    changed = 0
    for run in sorted(Path(args.runs_root).iterdir()):
        sp, st = run / "run_summary.json", run / "referee_state.json"
        if not sp.is_file() or not st.is_file():
            continue
        try:
            summary = json.loads(sp.read_text())
            positions = json.loads(st.read_text()).get("positions") or []
        except (OSError, ValueError):
            continue
        before = json.dumps(summary, sort_keys=True)
        if not rescore_summary(summary, positions, graph, targets, args.leg):
            continue
        leg = next(l for l in summary["referee"]["progress"]["legs"] if l.get("node_id") == args.leg)
        changed += 1
        print(f"{'APPLY' if args.apply else 'would'} {run.name}: d_open={leg['d_open']} d_min={leg['d_min']} now={leg['distance_now']} "
              f"frac={None if leg['fraction'] is None else round(leg['fraction'], 3)} eff={None if leg['efficiency'] is None else round(leg['efficiency'], 3)} "
              f"status={leg['status']} progress={summary['referee']['progress'].get('progress')}")
        if args.apply and json.dumps(summary, sort_keys=True) != before:
            sp.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"{changed} run(s) {'rewritten' if args.apply else 'would change'}")


if __name__ == "__main__":
    main()
