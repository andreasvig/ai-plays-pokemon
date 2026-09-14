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
``referee.progress.legs`` entry for the leg with ``d_open`` None are touched,
unless ``--rescore-scored`` is given.

``score_to: reached`` (2026-09-14)
---------------------------------
A gate with several valid finishing tiles (``starter_chosen``: the three
Pokéballs in Oak's Lab) re-bases D when the leg CLOSES — see
``progress._rebase_to_reached``. ``--score-to`` picks the rule; the default
``auto`` reads it off the ladder, so the parcel leg keeps nearest-tile scoring
and the starter leg gets the new one:

    venv/bin/python scripts/backfill_parcel_leg.py --leg starter_chosen --rescore-scored [--oracle] [--apply]

Under the reached rule a CLOSED leg's ``d_open`` is the shortest walk from the
node the leg opened on to the node it was completed on (the last on-graph
position at or before ``closed_turn`` — the stamp turn's poll), with
``d_min = distance_now = 0``. An OPEN leg keeps nearest-tile scoring: the rule
only applies at close. ``--oracle`` re-runs the real
:class:`~src.referee.progress.ProgressTracker` over each run's stored
positions and stamps and compares, so two independent implementations have to
agree before anything is written.
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


def _gate(ladder_path: Path, leg_id: str) -> dict[str, Any]:
    import yaml

    lad = yaml.safe_load(ladder_path.read_text())
    nodes = lad.get("nodes") or lad.get("checkpoints") or []
    cp = next((c for c in nodes if isinstance(c, dict) and c.get("id") == leg_id), None)
    if cp is None:
        raise SystemExit(f"{ladder_path}: no gate {leg_id}")
    return cp


def leg_targets(graph: WalkGraph, ladder_path: Path, leg_id: str) -> frozenset[int]:
    cp = _gate(ladder_path, leg_id)
    return frozenset(graph.resolve_locus(type("Checkpoint", (), dict(cp))()))


def leg_score_to(ladder_path: Path, leg_id: str) -> str:
    """The gate's ``score_to`` rule from the ladder file ("nearest" when absent)."""
    return str(_gate(ladder_path, leg_id).get("score_to") or "nearest")


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


def window_nodes(leg: dict[str, Any], positions: list[list[int]], graph: WalkGraph, targets: frozenset[int],
                 last_turn: Optional[int] = None) -> tuple[Optional[int], Optional[int]]:
    """``(opening node, closing node)`` for the leg's turn window.

    Opening = the first on-graph position the targets are REACHABLE from — the
    node the tracker measured D from (``_seed_distance`` / the ``d_open is
    None`` branch of ``_fold``). Closing = the LAST on-graph position in the
    window, reachable or not: the tracker's ``prev_node`` at the moment the
    stamp closed the leg."""
    opened, closed = leg.get("opened_turn"), leg.get("closed_turn")
    if not isinstance(opened, int):
        return None, None
    end = closed if isinstance(closed, int) else (last_turn if isinstance(last_turn, int) else None)
    open_node = close_node = None
    for pos in positions:
        if len(pos) < 5 or pos[0] < opened or (end is not None and pos[0] > end):
            continue
        node = graph.node_id(pos[1], pos[2], pos[3], pos[4])
        if node is None:
            continue
        close_node = node
        if open_node is None and graph.distance_to(targets, node) is not None:
            open_node = node
    return open_node, close_node


def rescore_leg_reached(leg: dict[str, Any], positions: list[list[int]], graph: WalkGraph, targets: frozenset[int],
                        last_turn: Optional[int] = None) -> dict[str, Any]:
    """``score_to: reached`` — :func:`rescore_leg`, then re-based at close.

    For a CLOSED, scored leg ``d_open`` becomes the shortest walk from the
    opening node to the node the gate was completed on and ``d_min`` /
    ``distance_now`` become 0, so choosing among several valid finishing tiles
    is not charged as a detour. Mirrors ``progress._rebase_to_reached``,
    including its bail-outs: an open leg, an unknown end, or an unreachable one
    keeps the nearest-tile numbers. Steps and tiles stay as recorded."""
    out = rescore_leg(leg, positions, graph, targets, last_turn)
    if leg.get("status") != "closed" or not leg.get("scored", True):
        return out
    open_node, close_node = window_nodes(leg, positions, graph, targets, last_turn)
    if open_node is None or close_node is None:
        return out
    d = graph.steps_between(open_node, close_node)
    if d is None:
        return out
    out.update({"d_open": d, "d_min": 0, "distance_now": 0, "fraction": 0.0 if d <= 0 else 1.0})
    steps = leg.get("steps_walked")
    out["efficiency"] = (d / max(steps, d)) if d and isinstance(steps, int) else None
    return out


def rescore_summary(summary: dict[str, Any], positions: list[list[int]], graph: WalkGraph, targets: frozenset[int],
                    leg_id: str, score_to: str = "nearest", force: bool = False) -> bool:
    """Apply :func:`rescore_leg` (or :func:`rescore_leg_reached` when
    ``score_to == "reached"``) to ``summary["referee"]["progress"]`` in place.
    True when something changed. A leg that already has a ``d_open`` is skipped
    unless ``force`` — re-scoring under a NEW rule needs it."""
    prog = (summary.get("referee") or {}).get("progress")
    if not isinstance(prog, dict):
        return False
    legs = prog.get("legs") or []
    idx = next((i for i, l in enumerate(legs) if isinstance(l, dict) and l.get("node_id") == leg_id), None)
    if idx is None or (legs[idx].get("d_open") is not None and not force):
        return False
    last_turn = (summary.get("session") or {}).get("total_turns")
    fn = rescore_leg_reached if score_to == "reached" else rescore_leg
    new = fn(legs[idx], positions, graph, targets, last_turn)
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


ORACLE_FIELDS = ("d_open", "d_min", "distance_now", "fraction", "efficiency")


def tracker_leg(state: dict[str, Any], graph: WalkGraph, ladder_path: Path, leg_id: str) -> Optional[dict[str, Any]]:
    """The leg the real :class:`ProgressTracker` builds from this run's stored
    positions + stamps — the independent second opinion on the backfill."""
    from src.referee.checkpoints import load_ladder
    from src.referee.progress import ProgressTracker

    tr = ProgressTracker(graph, load_ladder(ladder_path).nodes)
    tr.load_state(state)
    tr.observe_stamps({k: int(v) for k, v in (state.get("stamps") or {}).items() if isinstance(v, int)})
    return next((l for l in tr.summary()["legs"] if l.get("node_id") == leg_id), None)


def oracle_disagreement(leg: dict[str, Any], ref: Optional[dict[str, Any]]) -> list[str]:
    """Fields where the backfilled leg differs from the tracker's (only fields
    the tracker actually gives a value for). ``steps_walked`` is reported
    separately by the caller: old runs recorded it from another source."""
    if ref is None:
        return []
    out = []
    for f in ORACLE_FIELDS:
        a, b = leg.get(f), ref.get(f)
        if b is None:
            continue
        if a is None or (abs(a - b) > 1e-9 if isinstance(b, float) else a != b):
            out.append(f"{f}: backfill={a} tracker={b}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", default="local/runs")
    ap.add_argument("--leg", default="parcel_delivered")
    ap.add_argument("--ladder", default=str(LADDER))
    ap.add_argument("--score-to", default="auto", choices=("auto", "nearest", "reached"),
                    help="scoring rule; auto (default) reads the gate's score_to off the ladder")
    ap.add_argument("--rescore-scored", action="store_true",
                    help="also re-score legs that already have a d_open (needed when the RULE changed)")
    ap.add_argument("--oracle", action="store_true",
                    help="rebuild each run's legs with ProgressTracker and compare before writing")
    ap.add_argument("--only", help="limit to run directories whose name contains this")
    ap.add_argument("--apply", action="store_true", help="write run_summary.json; default prints the changes")
    args = ap.parse_args()
    graph = WalkGraph.load(DEFAULT_GRAPH_PATH)
    targets = leg_targets(graph, Path(args.ladder), args.leg)
    score_to = leg_score_to(Path(args.ladder), args.leg) if args.score_to == "auto" else args.score_to
    print(f"{args.leg}: score_to={score_to}, {len(targets)} target tile(s) {sorted(graph.nodes[i] for i in targets)}")
    changed = disagreed = steps_differ = checked = 0
    for run in sorted(Path(args.runs_root).iterdir()):
        if args.only and args.only not in run.name:
            continue
        sp, st = run / "run_summary.json", run / "referee_state.json"
        if not sp.is_file() or not st.is_file():
            continue
        try:
            summary = json.loads(sp.read_text())
            state = json.loads(st.read_text())
        except (OSError, ValueError):
            continue
        positions = state.get("positions") or []
        old = next((l for l in ((summary.get("referee") or {}).get("progress") or {}).get("legs") or []
                    if isinstance(l, dict) and l.get("node_id") == args.leg), None)
        before = json.dumps(summary, sort_keys=True)
        if not rescore_summary(summary, positions, graph, targets, args.leg, score_to, args.rescore_scored):
            continue
        leg = next(l for l in summary["referee"]["progress"]["legs"] if l.get("node_id") == args.leg)
        changed += 1
        note, oracle_bad = "", False
        if args.oracle:
            ref = tracker_leg(state, graph, Path(args.ladder), args.leg)
            checked += 1 if ref is not None and ref.get("d_open") is not None else 0
            bad = oracle_disagreement(leg, ref)
            if bad:
                disagreed += 1
                oracle_bad = True
                note = "  ORACLE-DISAGREES " + "; ".join(bad)
            elif ref is not None and ref.get("steps_walked") != leg.get("steps_walked"):
                steps_differ += 1
                note = f"  (steps: recorded={leg.get('steps_walked')} tracker={ref.get('steps_walked')})"
        eff_o = (old or {}).get("efficiency")
        if eff_o is not None and leg["efficiency"] is not None and leg["efficiency"] < eff_o - 1e-9:
            note = f"  EFFICIENCY DROPPED {round(eff_o, 3)} -> {round(leg['efficiency'], 3)}" + note
        print(f"{'APPLY' if args.apply else 'would'} {run.name}: d_open={(old or {}).get('d_open')}->{leg['d_open']} "
              f"d_min={(old or {}).get('d_min')}->{leg['d_min']} now={leg['distance_now']} "
              f"frac={None if leg['fraction'] is None else round(leg['fraction'], 3)} "
              f"eff={None if eff_o is None else round(eff_o, 3)}->{None if leg['efficiency'] is None else round(leg['efficiency'], 3)} "
              f"steps={leg.get('steps_walked')} status={leg['status']} progress={summary['referee']['progress'].get('progress')}{note}")
        if args.apply and not oracle_bad and json.dumps(summary, sort_keys=True) != before:
            sp.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"{changed} run(s) {'rewritten' if args.apply else 'would change'}"
          + (f"; oracle checked {checked} (tracker gave a d_open), {disagreed} disagreement(s), "
             f"{steps_differ} with a different rebuilt steps_walked" if args.oracle else ""))


if __name__ == "__main__":
    main()
