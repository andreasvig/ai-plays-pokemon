"""`pokemon queue` — drive the control center's serial run QUEUE from the shell.

A thin wrapper over the running app's ``/api/queue*`` routes (the app must be up
— see `pokemon app`, or `pokemon status` to check). Bulk-friendly by design:
``add`` takes a list of models, ``reorder`` takes the full id order, ``cancel``
takes a list of ids.

  pokemon queue get
  pokemon queue add --benchmark pokebench-easy gemini-3.1-flash-lite claude-haiku-4-5
  pokemon queue add --kind casual --max-turns 50 claude-haiku-4-5 --repeat 3
  pokemon queue add --kind casual --rom firered --stop-at starter_chosen "gpt-5.6-sol(medium)"
  pokemon queue events
  pokemon queue reorder q_ab12cd34 q_99ff00aa
  pokemon queue cancel q_ab12cd34 q_99ff00aa
  pokemon queue clear --yes

``--json`` on any subcommand prints the raw API payload (for scripting). NOTE:
``cancel`` removes a PENDING queue item; to delete a finished run from history
use ``pokemon runs delete``.

Every name a flag asks for is listable: ``pokemon ls models|roms|configs|events
|benchmarks``. Bad ones are rejected at enqueue with a 400 that lists the valid
values, never accepted and dropped later.
"""

from __future__ import annotations

import argparse
import sys

from src.cli.ctl_client import api, detail, emit_json


def _print_last_error(payload: dict) -> None:
    """Show the last dispatch failure, if the server reported one.

    An item that fails between being dequeued and starting its run is removed
    from the queue either way, so without this the queue reads as "idle" and the
    run is simply missing. See ``RunExecutor.last_error``.
    """
    err = payload.get("last_error")
    if not err:
        return
    print()
    print(f"!! last dispatch FAILED  {err.get('at', '')}")
    print(f"   {err.get('queue_id', '?')}  {err.get('model', '?')}")
    print(f"   {err.get('error', '')}")


def _print_queue(payload: dict) -> None:
    """Render ``{active, items, last_error}`` as a compact table."""
    active = payload.get("active")
    items = payload.get("items", [])
    if not items:
        print("queue empty")
        _print_last_error(payload)
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
        if it.get("rom"):
            flags.append(f"rom={it['rom']}")
        if it.get("stop_at"):
            flags.append(f"stop_at={it['stop_at']}")
        if it.get("max_spend_usd") is not None:
            flags.append(f"max_spend=${it['max_spend_usd']:g}")
        if it.get("gameplay"):
            flags.append(f"gameplay={it['gameplay']}")
        if it.get("record"):
            r = it["record"]
            flags.append(f"rec={r.get('view')}/{r.get('speed')}")
        print(
            f"{mark}{i:>1}  {it['queue_id']:<12} {it['kind']:<8} "
            f"{(it.get('benchmark') or '—'):<16} {it['model']:<28} {', '.join(flags)}"
        )
    _print_last_error(payload)


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
            # config omitted → the server fills in the latest config-X.Y (same
            # rule as a bare `pokemon run`). It used to be left unset, which the
            # server accepted and the executor then rejected at dispatch, after
            # the item was already off the queue.
            if args.config:
                spec["config"] = args.config
            if args.rom:
                # Casual-only, and validated server-side: an unknown id, or one
                # whose .gba isn't on disk, is a 400 here rather than an mGBA
                # that won't boot several minutes later.
                spec["rom"] = args.rom
            if args.max_turns is not None:
                spec["max_turns"] = args.max_turns
            if args.stop_at:
                # Validated server-side against the ladder (400 on an unknown
                # id), so a typo rejects the batch instead of enqueuing runs
                # that would quietly never stop early.
                spec["stop_at"] = args.stop_at
            if args.max_spend is not None:
                # Also validated server-side (400 on <= 0 or a non-number), so a
                # fat-fingered budget rejects the batch rather than capping a
                # run at a cent.
                spec["max_spend_usd"] = args.max_spend
            if args.gameplay:
                spec["gameplay"] = args.gameplay
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
        _hint_for(detail(data))
        return 1
    if args.json:
        emit_json(data)
    else:
        created = data.get("items", [])
        print(f"enqueued {len(created)} run(s):")
        for it in created:
            # Echo what the SERVER stored, not what was sent — that is how you
            # see a defaulted config or a dropped official-only field, rather
            # than assuming the request was taken verbatim.
            bits = [it.get("benchmark") or it.get("config") or ""]
            for key in ("rom", "max_turns", "stop_at", "max_spend_usd", "gameplay"):
                if it.get(key) is not None:
                    bits.append(f"{key}={it[key]}")
            if it.get("record"):
                bits.append(f"rec={it['record'].get('view')}/{it['record'].get('speed')}")
            print(f"  {it['queue_id']}  {it['kind']:<8} {it['model']}  {'  '.join(b for b in bits if b)}")
        print("\nwatch it: pokemon status   |   http://localhost:%d" % args.port)
    return 0


def _hint_for(message: str) -> None:
    """Point at the command that lists whatever the server said it didn't know.

    The API's 400s already name the valid values, but for the big registries
    (models especially) that list is long and the useful next step is a
    filterable command, not a wall of text.
    """
    hints = (
        ("unknown model", "pokemon ls models <substring>"),
        ("unknown config", "pokemon ls configs"),
        ("unknown benchmark", "pokemon ls benchmarks"),
        ("rom", "pokemon ls roms"),
        ("stop event", "pokemon ls events"),
        ("unknown stop", "pokemon ls events"),
    )
    low = message.lower()
    for needle, cmd in hints:
        if needle in low:
            print(f"  try: {cmd}", file=sys.stderr)
            return


def _cmd_events(args) -> int:
    """List the story events a casual run can be told to stop at."""
    status, data = api("GET", "/api/checkpoints", port=args.port)
    if status != 200:
        print(f"ERROR: {detail(data)}", file=sys.stderr)
        return 1
    if args.json:
        emit_json(data)
    else:
        print(f"{'id':<26} {'type':<7} name")
        for e in data:
            print(f"{e['id']:<26} {e['type']:<7} {e['name']}")
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
        prog="pokemon queue",
        description="Drive the control center's run queue (the app must be running).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # a scored benchmark run, recorded
  pokemon queue add "claude-opus-5(high)" --benchmark pokebench-easy --record simple

  # a casual run: 20 turns of FireRed, stopping early if the starter is picked
  pokemon queue add "gpt-5.6-sol(medium)" --kind casual --rom firered \\
      --max-turns 20 --stop-at starter_chosen --record simple --record-speed cut-thinking

  # the same model three times, to see the spread
  pokemon queue add "gemini-3.5-flash(high)" --kind casual --max-turns 50 --repeat 3

  # several models on one benchmark
  pokemon queue add --benchmark pokebench-easy "claude-haiku-4.5(high)" "grok-4.5(high)"

  pokemon queue get              # what is queued, and the last dispatch failure
  pokemon queue cancel q_ab12cd34
  pokemon queue clear --yes      # drop everything pending, keep the active run

Naming things: `pokemon ls models|roms|configs|events|benchmarks`.
Casual defaults: latest config, default ROM, no early stop, no recording.
Official runs ignore --config/--max-turns/--stop-at/--rom — a benchmark is
frozen by definition, and takes its ROM from its own ladder.
""",
    )
    # A shared parent so --port / --json work BEFORE or AFTER the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=3420, help="Control center port (default 3420).")
    common.add_argument("--json", action="store_true", help="Print raw API JSON.")
    parser.add_argument("--port", type=int, default=3420, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("get", parents=[common], help="Show the queue (active + ordered items).")

    p_add = sub.add_parser(
        "add", parents=[common],
        help="Enqueue one or more runs (list of models).",
        description="Enqueue one or more runs. Every value is validated now, not at dispatch.",
    )
    p_add.add_argument(
        "models", nargs="+",
        help='Model alias(es), e.g. "gpt-5.6-sol(medium)". `pokemon ls models` lists them.',
    )
    p_add.add_argument(
        "--kind", choices=["official", "casual"], default="official",
        help="official = the frozen scored benchmark (default). casual = your own "
             "config/turns/game, never on the leaderboard.",
    )
    p_add.add_argument(
        "--benchmark",
        help="Benchmark id (official only). `pokemon ls benchmarks`. Omit for the default.",
    )
    p_add.add_argument(
        "--config",
        help="Config stem (casual only), e.g. `config-4.0`. `pokemon ls configs`. "
             "Omit for the latest.",
    )
    p_add.add_argument(
        "--rom",
        help="Which game (casual only), e.g. `firered`. `pokemon ls roms`. Omit for "
             "the default ROM. The executor switches the emulator for you.",
    )
    p_add.add_argument(
        "--max-turns", type=int, dest="max_turns",
        help="Turn cap (casual only). Official runs end at their gate ladder.",
    )
    p_add.add_argument(
        "--stop-at", dest="stop_at", default=None,
        help="Stop when this story event is reached (casual only), e.g. "
             "`starter_chosen`. --max-turns still caps the run; whichever comes "
             "first ends it. `pokemon ls events` lists the ids. FireRed only.",
    )
    p_add.add_argument(
        "--max-spend", type=float, dest="max_spend", default=None,
        help="All-in USD ceiling (casual only), e.g. `--max-spend 2.50`. Counts "
             "Player + OCR + TaskMaster. Runs alongside --max-turns and "
             "--stop-at; whichever lands first ends the run. Omit for no cap.",
    )
    p_add.add_argument(
        "--gameplay", choices=["exploration", "speed"], default=None,
        help="How the agent is told to play (casual only). `exploration` "
             "(default) = wander, catch, roleplay. `speed` = shortest path to "
             "the top goal. Official runs always race.",
    )
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

    sub.add_parser(
        "events", parents=[common],
        help="List the story events a casual run can --stop-at (same as `pokemon ls events`).",
        description="List the story events a casual run can --stop-at. `pokemon ls events` "
                    "prints the same list without needing the app to be running.",
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
        "events": _cmd_events,
        "reorder": _cmd_reorder,
        "cancel": _cmd_cancel,
        "clear": _cmd_clear,
    }
    sys.exit(dispatch[args.action](args))


if __name__ == "__main__":
    main()
