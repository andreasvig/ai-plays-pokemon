"""`pokemon runs` — inspect + manage HISTORICAL runs from the shell.

A thin wrapper over the running app's run/leaderboard routes (the app must be up
— see `pokemon app`). Where `pokemon queue` manages PENDING items, this manages
finished runs: list/filter history, delete (→ Trash + de-index), continue from a
savepoint, stop the active run, and show the leaderboard.

  pokemon runs list --benchmark pokebench-easy --limit 20
  pokemon runs list --status terminated
  pokemon runs delete 2026-06-16_xyz_config-3.13__gemini --yes
  pokemon runs continue 2026-06-15_abc_config-3.13__claude
  pokemon runs stop                       # stops the active run
  pokemon runs board --benchmark pokebench-easy

``--json`` on any subcommand prints the raw API payload. ``delete`` and ``stop``
on an OFFICIAL run are destructive (delete trashes the folder; stop VOIDS the
run), so both require ``--yes``.
"""

from __future__ import annotations

import argparse
import sys

from src.cli.ctl_client import api, detail, emit_json


def _cmd_list(args) -> int:
    qs = []
    if args.status:
        qs.append(f"status={args.status}")
    if args.benchmark:
        qs.append(f"benchmark={args.benchmark}")
    if args.model:
        qs.append(f"q={args.model}")
    path = "/api/runs" + ("?" + "&".join(qs) if qs else "")
    status, data = api("GET", path, port=args.port)
    if status != 200:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    rows = data if isinstance(data, list) else []
    if args.limit:
        rows = rows[: args.limit]
    if args.json:
        emit_json(rows)
        return 0
    if not rows:
        print("no runs match")
        return 0
    print(f"{'run_id':<44} {'kind':<8} {'status':<11} {'gates':>6} {'turns':>6}  model")
    for s in rows:
        gates = f"{s.get('gates_reached', 0)}/{s.get('total_gates', 0)}"
        print(
            f"{s['run_id']:<44} {s.get('kind', ''):<8} {s.get('status', ''):<11} "
            f"{gates:>6} {s.get('turns', 0):>6}  {s.get('model', '')}"
        )
    return 0


def _cmd_delete(args) -> int:
    if not args.yes:
        print(f"would delete {len(args.run_ids)} run(s); re-run with --yes to confirm")
        return 0
    rc = 0
    for run_id in args.run_ids:
        status, data = api("DELETE", f"/api/runs/{run_id}", port=args.port)
        if status == 200:
            where = data.get("trashed_to")
            print(f"deleted {run_id}" + (f" → {where}" if where else " (index only)"))
        else:
            print(f"ERROR deleting {run_id}: {detail(data)}", file=sys.stderr)
            rc = 1
    return rc


def _cmd_continue(args) -> int:
    body = {}
    if args.max_turns is not None:
        body["max_turns"] = args.max_turns
    status, data = api(
        "POST", f"/api/runs/{args.run_id}/continue", port=args.port, body=body
    )
    if status != 201:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    if args.json:
        emit_json(data)
    else:
        print(f"enqueued continue {data['queue_id']}  {data['model']}  from {args.run_id}")
    return 0


def _cmd_stop(args) -> int:
    run_id = args.run_id
    if run_id is None:
        # The stop route matches on the RUN id (not the queue_id); the executor's
        # active run id is exposed on /api/emulator/status.
        status, data = api("GET", "/api/emulator/status", port=args.port)
        if status != 200:
            print(f"ERROR: {detail(data)}", file=sys.stderr)
            return 1
        run_id = (data or {}).get("active_run_id")
        if not run_id:
            print("no active run to stop")
            return 0
    if not args.yes:
        print(
            f"would stop {run_id} (an official run stopped this way is VOIDED); "
            f"re-run with --yes to confirm"
        )
        return 0
    status, data = api("POST", f"/api/runs/{run_id}/stop", port=args.port)
    if status != 200:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    if args.json:
        emit_json(data)
    else:
        print(f"stop requested for {data.get('stopping')} (matched={data.get('matched')})")
    return 0


def _cmd_board(args) -> int:
    path = "/api/leaderboard" + (f"?benchmark={args.benchmark}" if args.benchmark else "")
    status, data = api("GET", path, port=args.port)
    if status != 200:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    rows = data if isinstance(data, list) else []
    if args.json:
        emit_json(rows)
        return 0
    if not rows:
        print("no leaderboard entries")
        return 0
    print(f"{'#':>2}  {'model':<28} {'gates':>6} {'turns':>6}  {'status':<11} run_id")
    for i, s in enumerate(rows, 1):
        gates = f"{s.get('gates_reached', 0)}/{s.get('total_gates', 0)}"
        print(
            f"{i:>2}  {s.get('model', ''):<28} {gates:>6} {s.get('turns', 0):>6}  "
            f"{s.get('status', ''):<11} {s.get('run_id', '')}"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pokemon runs",
        description="Inspect + manage historical runs (the app must be running).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  pokemon runs list                          # newest first
  pokemon runs list --status terminated      # the ones a gate killed
  pokemon runs list --model "gpt-5.6-sol(medium)"
  pokemon runs board --benchmark pokebench-easy
  pokemon runs continue <run_id>             # resume from its latest savepoint
  pokemon runs stop                          # stop whatever is running now
  pokemon runs delete <run_id> --yes         # folder → Trash, and de-index

`pokemon status` is the quicker look: what is running plus the last few runs.
Statuses: completed (ran to its end) · terminated (referee killed it on a
missed gate) · cancelled (you stopped it; an official run is then voided) ·
crashed (it died, or the model never produced a valid turn — neither reaches
the leaderboard).
""",
    )
    # A shared parent so --port / --json work BEFORE or AFTER the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=3420, help="Control center port (default 3420).")
    common.add_argument("--json", action="store_true", help="Print raw API JSON.")
    parser.add_argument("--port", type=int, default=3420, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="action", required=True)

    p_ls = sub.add_parser("list", parents=[common], help="List history (filter by status/benchmark/model).")
    p_ls.add_argument("--status", help="Filter by run status (completed/terminated/cancelled/…).")
    p_ls.add_argument("--benchmark", help="Filter by benchmark id.")
    p_ls.add_argument("--model", help="Substring match on model / run_id.")
    p_ls.add_argument("--limit", type=int, default=0, help="Show at most N rows (0 = all).")

    p_del = sub.add_parser("delete", parents=[common], help="Delete run(s): folder → Trash + de-index.")
    p_del.add_argument("run_ids", nargs="+", help="run_id(s) to delete.")
    p_del.add_argument("--yes", action="store_true", help="Confirm — actually delete.")

    p_cont = sub.add_parser("continue", parents=[common], help="Enqueue a casual continue from a run's savepoint.")
    p_cont.add_argument("run_id", help="Source run_id.")
    p_cont.add_argument("--max-turns", type=int, dest="max_turns", help="Max turns for the continue.")

    p_stop = sub.add_parser("stop", parents=[common], help="Stop the active run (or a named run_id).")
    p_stop.add_argument("run_id", nargs="?", default=None, help="run_id (default: the active run).")
    p_stop.add_argument("--yes", action="store_true", help="Confirm — official runs are VOIDED.")

    p_bd = sub.add_parser("board", parents=[common], help="Show the leaderboard (best per model).")
    p_bd.add_argument("--benchmark", help="Scope to one benchmark id.")

    args = parser.parse_args()
    dispatch = {
        "list": _cmd_list,
        "delete": _cmd_delete,
        "continue": _cmd_continue,
        "stop": _cmd_stop,
        "board": _cmd_board,
    }
    sys.exit(dispatch[args.action](args))


if __name__ == "__main__":
    main()
