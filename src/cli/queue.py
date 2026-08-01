"""`pokemon queue` — drive the control center's serial run QUEUE from the shell.

A thin wrapper over the running app's ``/api/queue*`` routes (the app must be up
— see `pokemon app`). Bulk-friendly by design: ``add`` takes a list of models,
``reorder`` takes the full id order, ``cancel`` takes a list of ids.

  pokemon queue get
  pokemon queue add --benchmark pokebench-easy gemini-3.1-flash-lite claude-haiku-4-5
  pokemon queue add --kind casual --max-turns 50 claude-haiku-4-5 --repeat 3
  pokemon queue reorder q_ab12cd34 q_99ff00aa
  pokemon queue cancel q_ab12cd34 q_99ff00aa
  pokemon queue clear --yes

``--json`` on any subcommand prints the raw API payload (for scripting). NOTE:
``cancel`` removes a PENDING queue item; to delete a finished run from history
use ``pokemon runs delete``.
"""

from __future__ import annotations

import argparse
import sys

from src.cli.ctl_client import api, detail, emit_json


def _print_queue(payload: dict) -> None:
    """Render ``{active, items}`` as a compact table."""
    active = payload.get("active")
    items = payload.get("items", [])
    if not items:
        print("queue empty")
        return
    print(f"{'#':>2}  {'queue_id':<12} {'kind':<8} {'benchmark':<16} {'model':<28} flags")
    for i, it in enumerate(items):
        mark = "▶" if it["queue_id"] == active else " "
        flags = []
        if it.get("max_turns") is not None:
            flags.append(f"max_turns={it['max_turns']}")
        if it.get("continue_from"):
            flags.append(f"continue_from={it['continue_from']}")
        if it.get("config"):
            flags.append(f"config={it['config']}")
        if it.get("record"):
            r = it["record"]
            flags.append(f"rec={r.get('view')}/{r.get('speed')}")
        print(
            f"{mark}{i:>1}  {it['queue_id']:<12} {it['kind']:<8} "
            f"{(it.get('benchmark') or '—'):<16} {it['model']:<28} {', '.join(flags)}"
        )


def _cmd_get(args) -> int:
    status, data = api("GET", "/api/queue", port=args.port)
    if status != 200:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    if args.json:
        emit_json(data)
    else:
        _print_queue(data)
    return 0


def _cmd_add(args) -> int:
    specs = []
    for model in args.models:
        spec = {"kind": args.kind, "model": model}
        if args.kind == "official":
            if args.benchmark:
                spec["benchmark"] = args.benchmark
        else:
            if args.config:
                spec["config"] = args.config
            if args.max_turns is not None:
                spec["max_turns"] = args.max_turns
        if args.record:
            # Validated server-side (400 on a bad view/speed or a missing
            # ffmpeg/Chrome), so a batch that can't actually be recorded is
            # rejected whole rather than half-enqueued.
            spec["record"] = {
                "view": args.record,
                "speed": args.record_speed,
                "fps": args.record_fps,
            }
        for _ in range(args.repeat):
            specs.append(dict(spec))

    status, data = api("POST", "/api/queue/batch", port=args.port, body={"items": specs})
    if status != 201:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    if args.json:
        emit_json(data)
    else:
        created = data.get("items", [])
        print(f"enqueued {len(created)} run(s):")
        for it in created:
            tail = it.get("benchmark") or it.get("config") or ""
            print(f"  {it['queue_id']}  {it['kind']:<8} {it['model']}  {tail}")
    return 0


def _cmd_reorder(args) -> int:
    status, data = api(
        "POST", "/api/queue/reorder", port=args.port, body={"order": args.ids}
    )
    if status != 200:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    if args.json:
        emit_json(data)
    else:
        _print_queue(data)
    return 0


def _cmd_cancel(args) -> int:
    rc = 0
    for qid in args.ids:
        status, data = api("DELETE", f"/api/queue/{qid}", port=args.port)
        if status == 200:
            print(f"cancelled {qid}")
        else:
            print(f"ERROR cancelling {qid}: {detail(data)}", file=sys.stderr)
            rc = 1
    return rc


def _cmd_clear(args) -> int:
    status, data = api("GET", "/api/queue", port=args.port)
    if status != 200:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    active = data.get("active")
    pending = [it["queue_id"] for it in data.get("items", []) if it["queue_id"] != active]
    if not pending:
        print("nothing to clear (no pending items)")
        return 0
    if not args.yes:
        print(f"would cancel {len(pending)} pending item(s); re-run with --yes to confirm")
        return 0
    rc = 0
    for qid in pending:
        st, d = api("DELETE", f"/api/queue/{qid}", port=args.port)
        if st == 200:
            print(f"cancelled {qid}")
        else:
            print(f"ERROR cancelling {qid}: {detail(d)}", file=sys.stderr)
            rc = 1
    return rc


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pokemon queue", description="Drive the control center's run queue."
    )
    # A shared parent so --port / --json work BEFORE or AFTER the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=3420, help="Control center port (default 3420).")
    common.add_argument("--json", action="store_true", help="Print raw API JSON.")
    parser.add_argument("--port", type=int, default=3420, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("get", parents=[common], help="Show the queue (active + ordered items).")

    p_add = sub.add_parser("add", parents=[common], help="Enqueue one or more runs (list of models).")
    p_add.add_argument("models", nargs="+", help="Model alias(es) to enqueue.")
    p_add.add_argument("--kind", choices=["official", "casual"], default="official")
    p_add.add_argument("--benchmark", help="Benchmark id (official only).")
    p_add.add_argument("--config", help="Config path (casual only).")
    p_add.add_argument("--max-turns", type=int, dest="max_turns", help="Max turns (casual only).")
    p_add.add_argument("--repeat", type=int, default=1, help="Enqueue each model N times (default 1).")
    p_add.add_argument(
        "--record", choices=["simple", "detailed"], default=None,
        help="Record the run to <run_dir>/recording.mp4. `simple` = the 1:1 "
             "recording view (game screen + turn box) at 1080x1080; `detailed` = "
             "the whole wide spectate panel at 1920x1080. Rendered headlessly "
             "server-side, so it does not depend on any open browser.",
    )
    p_add.add_argument(
        "--record-speed", dest="record_speed",
        choices=["realtime", "cut-thinking"], default="realtime",
        help="`realtime` keeps every pause. `cut-thinking` records only each "
             "turn's execution window (llm_output → screen settled), cutting the "
             "model's response time. Default: realtime.",
    )
    p_add.add_argument(
        "--record-fps", dest="record_fps", type=int, default=30,
        help="Recording frame rate, 1-60 (default 30).",
    )

    p_re = sub.add_parser("reorder", parents=[common], help="Set the full queue order by ids.")
    p_re.add_argument("ids", nargs="+", help="queue_ids in the desired order (must be all current ids).")

    p_ca = sub.add_parser("cancel", parents=[common], help="Cancel one or more pending queue items.")
    p_ca.add_argument("ids", nargs="+", help="queue_id(s) to cancel.")

    p_cl = sub.add_parser("clear", parents=[common], help="Cancel ALL pending items (keeps the active run).")
    p_cl.add_argument("--yes", action="store_true", help="Confirm — actually cancel.")

    args = parser.parse_args()
    dispatch = {
        "get": _cmd_get,
        "add": _cmd_add,
        "reorder": _cmd_reorder,
        "cancel": _cmd_cancel,
        "clear": _cmd_clear,
    }
    sys.exit(dispatch[args.action](args))


if __name__ == "__main__":
    main()
