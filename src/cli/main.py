"""Top-level `pokemon` CLI dispatcher.

Subcommands:
  run        Launch mGBA and run the agent for one or more (config, model) pairs.
  launch     Launch mGBA + connect to Lua and idle (no agent — manual play / debug).
  report     Generate the standalone HTML report for a run directory.
  snapshot   Save / load / list game snapshots.

Each subcommand delegates to a module-level `main()` and consumes its own --help.
"""

import sys
from importlib import import_module

SUBCOMMANDS = {
    "run":      "src.cli.runner",
    "launch":   "src.cli.launch",
    "report":   "src.cli.report",
    "snapshot": "src.cli.snapshot",
}


def _print_help() -> None:
    print("Usage: pokemon <subcommand> [args...]")
    print()
    print("Subcommands:")
    print("  run        Launch mGBA and run the agent (single or sequential).")
    print("  launch     Launch mGBA + Lua connection, no agent (manual play / debug).")
    print("  report     Generate HTML report for a run directory.")
    print("  snapshot   Save / load / list game snapshots.")
    print()
    print("Run `pokemon <subcommand> --help` for subcommand-specific options.")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_help()
        return

    sub = sys.argv[1]
    if sub not in SUBCOMMANDS:
        print(f"Unknown subcommand: {sub}\n", file=sys.stderr)
        _print_help()
        sys.exit(2)

    # Rewrite argv so the sub-command's argparse sees its own program name +
    # the remaining args (and not the top-level `pokemon` invocation).
    sys.argv = [f"pokemon {sub}"] + sys.argv[2:]
    module = import_module(SUBCOMMANDS[sub])
    module.main()


if __name__ == "__main__":
    main()
