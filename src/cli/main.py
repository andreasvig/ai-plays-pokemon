"""Top-level `pokemon` CLI dispatcher.

Commands are grouped by what you are trying to do, not alphabetically — the
help text below is the map most people (and most agents) read first, so it
leads with orientation (`status`, `ls`) rather than with the biggest command.

Each subcommand delegates to a module-level `main()` and consumes its own
`--help`.
"""

import difflib
import sys
from importlib import import_module

SUBCOMMANDS = {
    "status":   "src.cli.status",
    "ls":       "src.cli.ls",
    "app":      "src.cli.app",
    "queue":    "src.cli.queue",
    "run":      "src.cli.runner",
    "launch":   "src.cli.launch",
    "runs":     "src.cli.runs",
    "snapshot": "src.cli.snapshot",
}

HELP = """\
pokemon — a vision-only LLM agent that plays Pokemon, and the control center around it.

USAGE
  pokemon <command> [options]
  pokemon <command> --help        options + examples for one command

START HERE
  status     What is running right now: app, emulator, ROM, active run, queue.
  ls         What you can pick: models, roms, configs, events, benchmarks.

RUN SOMETHING
  app        The control center — persistent emulator + queue + web UI on :3420.
  queue      Add / inspect / cancel runs on the running control center.
  run        One-shot: launch mGBA and run the agent directly. No app needed.
  launch     mGBA + Lua only, no agent (manual play / debug).

AFTERWARDS
  runs       History: list, continue, stop, delete, leaderboard.
  snapshot   Save / load / list game snapshots.

RECIPES
  # what is going on
  pokemon status

  # a casual run: 20 turns of FireRed, stopping early if the starter is picked
  pokemon queue add "gpt-5.6-sol(medium)" --kind casual --rom firered \\
      --max-turns 20 --stop-at starter_chosen --record simple

  # a scored benchmark run
  pokemon queue add "claude-opus-5(high)" --benchmark pokebench-easy --record simple

  # carry on / call it off / see the board
  pokemon runs continue <run_id>
  pokemon runs stop
  pokemon runs board

NOTES
  The control center is usually ALREADY RUNNING — `pokemon status` says so, and
  `queue`/`runs` talk to it over HTTP. `run` and `launch` drive mGBA themselves
  and will collide with a running app over the emulator port.
"""


def _print_help() -> None:
    print(HELP, end="")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        _print_help()
        return

    sub = sys.argv[1]
    if sub not in SUBCOMMANDS:
        print(f"Unknown command: {sub}", file=sys.stderr)
        # A typo is the common case, so name the likely intent instead of just
        # reprinting the whole map underneath the error.
        close = difflib.get_close_matches(sub, SUBCOMMANDS, n=1, cutoff=0.5)
        if close:
            print(f"Did you mean `pokemon {close[0]}`?\n", file=sys.stderr)
        else:
            print("", file=sys.stderr)
        _print_help()
        sys.exit(2)

    # Rewrite argv so the sub-command's argparse sees its own program name +
    # the remaining args (and not the top-level `pokemon` invocation).
    sys.argv = [f"pokemon {sub}"] + sys.argv[2:]
    module = import_module(SUBCOMMANDS[sub])
    module.main()


if __name__ == "__main__":
    main()
