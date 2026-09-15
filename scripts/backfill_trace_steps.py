"""SUPERSEDED 2026-09-15 — do not use on a traced run.

``src/app/replay.py`` rebuilds the legs and battle summary from
``events.jsonl`` every time a row is projected, against the run's OWN ladder
(``config.json["referee"]["checkpoints"]``). A rule change is therefore a
``PROJECTION_VERSION`` bump plus ``pokemon publish --site-only
--refresh-rows`` — nothing edits a run folder any more. This script stays only
for runs recorded BEFORE the per-input trace (2026-09-14), which have nothing
to replay, and it carries a defect worth remembering: it re-scored every run
against the DEFAULT ladder, so a casual run played on
``checkpoints-firered-v1.yaml`` had its starter leg rewritten with the
first-badge locus (D 1 -> 4). Pass --i-know-this-is-superseded to run it.

Re-derive the per-turn traced steps of stored traced runs under the
2026-09-15 rules and rewrite their legs through the real tracker.

    venv/bin/python scripts/backfill_trace_steps.py [--only <substr>] [--apply]

Rules (artifacts/route-fidelity/plan.md): R2 — a multi-tile displacement in
one input counts its walk-graph distance (a scripted walk was one step per
sample); R3 — movement the poll saw after the trace's last sample (a warp
still fading, the auto-step out of a door) belongs to the turn. Both are what
the live referee does from today; runs traced before it (2026-09-14 and the
runs in flight while this shipped) get the same figures here.

Per run: ``turn_input_trace`` samples (events.jsonl) → ``trace.derive`` with
the graph's distance and the run's own poll positions / battle records as the
turn-start state → new ``traced_steps`` + ``traced_end`` → ``ProgressTracker``
rebuilt from ``referee_state.json`` → ``run_summary.json["referee"]["progress"]``.
Default prints the per-leg step changes and the movement-efficiency delta;
``--apply`` writes both files. Runs without a trace are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.app import battle_stats  # noqa: E402
from src.referee import trace  # noqa: E402
from src.referee.checkpoints import load_ladder  # noqa: E402
from src.referee.progress import ProgressTracker  # noqa: E402
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph  # noqa: E402

LADDER = Path("configs/checkpoints-firered-firstbadge.yaml")


def trace_samples(run_dir: Path) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return out
    with path.open() as fh:
        for line in fh:
            if '"turn_input_trace"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "turn_input_trace" and isinstance(e.get("turn"), int):
                out[e["turn"]] = e.get("samples") or []
    return out


def rederive(samples_by_turn: dict[int, list[dict]], state: dict[str, Any], graph: WalkGraph
             ) -> tuple[dict[str, int], dict[str, list[int]], dict[str, int]]:
    """New (traced_steps, traced_end) from the samples, using the poll before
    each turn as its start state exactly like ``Referee.record_trace``."""
    positions = sorted((p for p in state.get("positions") or [] if len(p) == 5), key=lambda p: p[0])
    records = sorted((r for r in state.get("battle_records") or [] if len(r) >= 2), key=lambda r: r[0])

    def distance(a: tuple, b: tuple) -> Optional[int]:
        na, nb = graph.node_id(*a), graph.node_id(*b)
        return graph.steps_between(na, nb) if na is not None and nb is not None else None

    steps: dict[str, int] = {}
    ends: dict[str, list[int]] = {}
    stats = {"turns": 0, "scripted_tiles": 0, "blind": 0}
    for turn in sorted(samples_by_turn):
        before = [p for p in positions if p[0] < turn]
        start_tile = tuple(int(v) for v in before[-1][1:]) if before else None
        rec = [r for r in records if r[0] < turn]
        start_in_battle = bool(rec[-1][1]) if rec else (False if turn <= 1 else None)
        d = trace.derive(samples_by_turn[turn], start_tile, start_in_battle, distance=distance)
        stats["turns"] += 1
        if d["overworld_steps"] is None:
            stats["blind"] += 1
            continue
        steps[str(turn)] = d["overworld_steps"]
        stats["scripted_tiles"] += d["scripted_tiles"]
        if d["end_tile"] is not None:
            ends[str(turn)] = [int(v) for v in d["end_tile"]]
    return steps, ends, stats


def rebuild_progress(state: dict[str, Any], graph: WalkGraph, ladder: Path) -> dict[str, Any]:
    tr = ProgressTracker(graph, load_ladder(ladder).nodes)
    tr.load_state(state)
    tr.observe_stamps({k: int(v) for k, v in (state.get("stamps") or {}).items() if isinstance(v, int)})
    return tr.summary()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", default="local/runs")
    ap.add_argument("--ladder", default=str(LADDER))
    ap.add_argument("--only", help="limit to run directories whose name contains this")
    ap.add_argument("--apply", action="store_true", help="write referee_state.json and run_summary.json; default prints")
    ap.add_argument("--i-know-this-is-superseded", action="store_true",
                    help="required: src/app/replay.py re-derives this at projection time (2026-09-15)")
    args = ap.parse_args()
    if not getattr(args, "i_know_this_is_superseded", False):
        raise SystemExit(__doc__.strip().splitlines()[0] + "\n"
                         "Re-derive instead: pokemon publish --site-only --refresh-rows")
    graph = WalkGraph.load(DEFAULT_GRAPH_PATH)
    ladder = Path(args.ladder)
    touched = 0
    for run in sorted(Path(args.runs_root).iterdir()):
        if args.only and args.only not in run.name:
            continue
        state_path, summary_path = run / "referee_state.json", run / "run_summary.json"
        if not state_path.is_file() or not summary_path.is_file():
            continue
        samples = trace_samples(run)
        if not samples:
            continue
        state = json.loads(state_path.read_text())
        summary = json.loads(summary_path.read_text())
        referee = summary.get("referee") or {}
        old_progress = referee.get("progress") or {}
        steps, ends, stats = rederive(samples, state, graph)
        new_state = {**state, "traced_steps": steps, "traced_end": ends}
        new_progress = rebuild_progress(new_state, graph, ladder)
        # sanity: the tracker over the OLD state must reproduce the stored legs' steps
        check = rebuild_progress(state, graph, ladder)
        old_by_id = {l["node_id"]: l for l in old_progress.get("legs") or []}
        mismatch = [l["node_id"] for l in check["legs"] if l["node_id"] in old_by_id
                    and l.get("steps_walked") != old_by_id[l["node_id"]].get("steps_walked")]
        last_turn = (summary.get("session") or {}).get("total_turns")
        mv_old = battle_stats.movement(referee, None, last_turn)
        mv_new = battle_stats.movement({**referee, "progress": new_progress}, None, last_turn)
        print(f"\n{run.name}")
        print(f"  traced turns {stats['turns']}  blind {stats['blind']}  scripted tiles {stats['scripted_tiles']}  "
              f"old-state oracle {'OK' if not mismatch else 'MISMATCH ' + ','.join(mismatch)}")
        new_by_id = {l["node_id"]: l for l in new_progress.get("legs") or []}
        for node_id, old in old_by_id.items():
            new = new_by_id.get(node_id)
            if new is None or new.get("steps_walked") == old.get("steps_walked"):
                continue
            print(f"  {node_id:26s} steps {old.get('steps_walked')} → {new.get('steps_walked')}  "
                  f"eff {old.get('efficiency')} → {new.get('efficiency')}")
        eo, en = (mv_old or {}).get("efficiency"), (mv_new or {}).get("efficiency")
        print(f"  movement efficiency {eo if eo is None else round(eo, 4)} → {en if en is None else round(en, 4)}")
        if args.apply:
            if mismatch:
                print("  NOT written: the tracker does not reproduce the stored legs from the old state")
                continue
            state_path.write_text(json.dumps(new_state, indent=2))
            summary["referee"] = {**referee, "progress": new_progress}
            summary_path.write_text(json.dumps(summary, indent=2))
            touched += 1
            print("  written")
    if args.apply:
        print(f"\n{touched} run(s) written")


if __name__ == "__main__":
    main()
