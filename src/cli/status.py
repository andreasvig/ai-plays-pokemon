"""`pokemon status` — one screen answering "what is going on right now?".

Before this existed, orienting meant `ps aux` for the app, `lsof` for the port,
then three separate `/api/*` calls for the emulator, the queue and history —
five commands to find out whether you could start a run and on which game. This
is those five, and it is the intended first command of any session.

It is also the only CLI that treats "the control center is down" as an answer
rather than an error: it says so, and says what to do instead.

  pokemon status
  pokemon status --json      # the same three payloads, unformatted
  pokemon status --limit 10  # more history
"""

from __future__ import annotations

import argparse
import json
import sys

from src.cli.ctl_client import api


def _fmt_cost(v) -> str:
    """`$0.51`, or `—` when there is nothing to show."""
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_dur(seconds) -> str:
    """Compact wall clock: `48s`, `12m`, `1h 25m`."""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h {(s % 3600) // 60}m"


def _print_down(port: int) -> None:
    print(f"control center   DOWN — nothing answering on http://localhost:{port}")
    print()
    print("  start it:            pokemon app")
    print("  or skip it entirely: pokemon run --model \"<alias>\" --turns 20")
    print()
    print("`pokemon ls models` works either way — it reads the registry off disk.")


def _print_emulator(emu: dict) -> None:
    rom = emu.get("rom") or {}
    rom_str = f"{rom.get('id', '?')} ({rom.get('name', '?')})" if rom else "—"
    bits = []
    bits.append("connected" if emu.get("connected") else "NOT CONNECTED")
    if emu.get("switching_to"):
        bits.append(f"switching→{emu['switching_to']}")
    bits.append("busy" if emu.get("busy") else "idle")
    if emu.get("muted"):
        bits.append("muted")
    if emu.get("awaiting_lua"):
        bits.append("AWAITING LUA")
    print(f"emulator         {', '.join(bits)}")
    print(f"rom              {rom_str}")


def _print_active(emu: dict, runs: list) -> None:
    run_id = emu.get("active_run_id")
    if not run_id:
        print("active run       — (idle)")
        return
    print(f"active run       {run_id}")
    # The index row for a live run exists as soon as it starts, so this is the
    # cheapest place to get turns/cost without opening events.jsonl.
    row = next((r for r in runs if r.get("run_id") == run_id), None)
    if row:
        print(
            f"                 {row.get('kind', '?')} · {row.get('model', '?')} · "
            f"turn {row.get('turns', 0)} · {_fmt_cost(row.get('total_cost_usd'))} · "
            f"{_fmt_dur(row.get('duration_s'))}"
        )


def _print_queue(q: dict) -> None:
    items = q.get("items", [])
    active = q.get("active")
    pending = [it for it in items if it.get("queue_id") != active]
    print(f"queue            {len(pending)} pending")
    for it in pending[:5]:
        tail = it.get("benchmark") or it.get("config") or ""
        rom = f" rom={it['rom']}" if it.get("rom") else ""
        print(f"                 {it['queue_id']}  {it['kind']:<8} {it['model']}  {tail}{rom}")
    if len(pending) > 5:
        print(f"                 … {len(pending) - 5} more (`pokemon queue get`)")

    # The whole point of last_error: a dispatch failure used to leave the queue
    # looking exactly like an idle one. Loud, and above the fold.
    err = q.get("last_error")
    if err:
        print()
        print(f"!! last dispatch FAILED  {err.get('at', '')}")
        print(f"   {err.get('queue_id', '?')}  {err.get('model', '?')}")
        print(f"   {err.get('error', '')}")


def _print_runs(runs: list, limit: int) -> None:
    if not runs:
        print("\nno runs yet")
        return
    print(f"\nrecent runs ({min(limit, len(runs))} of {len(runs)} shown)")
    print(f"  {'status':<11} {'turns':>5} {'cost':>8}  {'model':<26} run_id")
    for r in runs[:limit]:
        print(
            f"  {str(r.get('status', '?')):<11} {r.get('turns', 0):>5} "
            f"{_fmt_cost(r.get('total_cost_usd')):>8}  "
            f"{str(r.get('model', '?')):<26} {r.get('run_id', '')}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pokemon status",
        description="What is running right now: control center, emulator, ROM, active run, queue, recent runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  pokemon status                 # the usual first command of a session
  pokemon status --limit 10      # more history
  pokemon status --json          # {emulator, queue, runs} for scripting

Exit code is 0 whether the app is up or down — "down" is an answer, not an
error. Use --json and read `.emulator` to branch on it in a script.
""",
    )
    parser.add_argument("--port", type=int, default=3420, help="Control center port (default 3420).")
    parser.add_argument("--limit", type=int, default=5, help="How many recent runs to show (default 5).")
    parser.add_argument("--json", action="store_true", help="Print the raw payloads instead.")
    args = parser.parse_args()

    # soft=True: a refused connection is the "app is down" answer, not an exit.
    st_emu, emu = api("GET", "/api/emulator/status", port=args.port, soft=True)
    if st_emu == 0:
        if args.json:
            print(json.dumps({"emulator": None, "queue": None, "runs": None}, indent=2))
        else:
            _print_down(args.port)
        sys.exit(0)

    _, queue = api("GET", "/api/queue", port=args.port, soft=True)
    _, runs = api("GET", f"/api/runs?limit={max(args.limit, 20)}", port=args.port, soft=True)
    emu = emu or {}
    queue = queue or {"items": [], "active": None}
    runs = runs if isinstance(runs, list) else []

    if args.json:
        print(json.dumps({"emulator": emu, "queue": queue, "runs": runs}, indent=2))
        sys.exit(0)

    print(f"control center   UP   http://localhost:{args.port}")
    _print_emulator(emu)
    _print_active(emu, runs)
    _print_queue(queue)
    _print_runs(runs, args.limit)
    sys.exit(0)


if __name__ == "__main__":
    main()
