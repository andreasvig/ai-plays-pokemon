"""`pokemon ls` — list the vocabulary you need to compose a run.

Everything a queue/run command asks you to name lives in a different file:
model aliases in ``configs/models.yaml`` (47KB of them), ROM ids in
``configs/roms.yaml``, config stems in ``configs/config-*.yaml``, stop events in
the gate ladder, benchmarks in ``configs/benchmarks.yaml``. This is the one
place that reads all five.

Deliberately **on-disk, not over HTTP**: `pokemon run` needs a model alias and
does not need the control center, so neither should looking one up. Nothing
here talks to a running app.

  pokemon ls                    # the categories
  pokemon ls models             # every model + its thinking levels
  pokemon ls models sol         # ...filtered by substring
  pokemon ls roms
  pokemon ls configs
  pokemon ls events             # ids for --stop-at
  pokemon ls benchmarks

``--json`` prints the raw registry rows for scripting.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

CATEGORIES = ("models", "roms", "configs", "events", "benchmarks")


def _emit(rows: Any) -> None:
    print(json.dumps(rows, indent=2))


def _match(needle: str | None, *fields: Any) -> bool:
    """True when ``needle`` is absent or appears in any field (case-insensitive)."""
    if not needle:
        return True
    n = needle.lower()
    return any(n in str(f).lower() for f in fields if f is not None)


def _ls_models(args) -> int:
    from src.app.catalog import list_models

    rows = [r for r in list_models() if _match(args.filter, r["model"], r["openrouter_id"])]
    if args.json:
        _emit(rows)
        return 0
    if not rows:
        print(f"no model matches {args.filter!r}", file=sys.stderr)
        return 1
    print(f"{'model':<28} {'reasoning':<9} levels")
    for r in rows:
        levels = ", ".join(lv["level"] for lv in r["levels"]) or "—"
        print(f"{r['model']:<28} {r['reasoning_type']:<9} {levels}")
    print()
    print("Pass a model as `model(level)` — e.g. \"gpt-5.6-sol(medium)\".")
    print("A `reasoning` of `none` takes the bare name, with no parentheses.")
    return 0


def _ls_roms(args) -> int:
    from src.app.roms import get_rom, list_roms

    rows = list_roms()
    for row in rows:
        # `roms/` is gitignored, so a registry entry with no dump behind it is a
        # normal state — and the only thing that decides whether it can boot.
        row["on_disk"] = get_rom(row["id"]).exists()
    rows = [r for r in rows if _match(args.filter, r["id"], r["name"], r["game"])]
    if args.json:
        _emit(rows)
        return 0
    print(f"{'id':<12} {'name':<24} {'game':<14} {'on disk':<8} {'benchmarks':<11} default")
    for r in rows:
        print(
            f"{r['id']:<12} {r['name']:<24} {r['game']:<14} "
            f"{('yes' if r['on_disk'] else 'NO'):<8} "
            f"{('yes' if r.get('benchmark_ok') else 'casual only'):<11} "
            f"{'*' if r.get('default') else ''}"
        )
    print()
    print("Use with `pokemon queue add --rom <id>` (casual runs only — an official")
    print("run takes its ROM from the benchmark's ladder).")
    return 0


def _ls_configs(args) -> int:
    from src.app.catalog import list_configs

    rows = [c for c in list_configs() if _match(args.filter, c)]
    if args.json:
        _emit(rows)
        return 0
    for i, stem in enumerate(rows):
        tail = "  ← default (latest)" if i == len(rows) - 1 else ""
        print(f"{stem}{tail}")
    print()
    print("A casual run with no --config gets the latest.")
    return 0


def _ls_events(args) -> int:
    from src.app.catalog import list_stop_events

    rows = [e for e in list_stop_events() if _match(args.filter, e["id"], e["name"])]
    if args.json:
        _emit(rows)
        return 0
    print(f"{'id':<26} {'type':<7} name")
    for e in rows:
        print(f"{e['id']:<26} {e['type']:<7} {e['name']}")
    print()
    print("Use with `--stop-at <id>` on a casual run. FireRed only — the gates are")
    print("addressed at FireRed's RAM map.")
    return 0


def _ls_benchmarks(args) -> int:
    from src.app.benchmarks import load_benchmarks

    rows = [b.to_dict() for b in load_benchmarks()]
    rows = [b for b in rows if _match(args.filter, b["id"], b["name"])]
    if args.json:
        _emit(rows)
        return 0
    print(f"{'id':<20} {'game':<14} {'default':<8} name")
    for b in rows:
        print(f"{b['id']:<20} {b['game']:<14} {('*' if b['default'] else ''):<8} {b['name']}")
    print()
    print("Use with `pokemon queue add --benchmark <id>` (official runs).")
    return 0


DISPATCH = {
    "models": _ls_models,
    "roms": _ls_roms,
    "configs": _ls_configs,
    "events": _ls_events,
    "benchmarks": _ls_benchmarks,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pokemon ls",
        description="List the models / roms / configs / stop events / benchmarks you can name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  pokemon ls models sol            # find the alias for "that GPT Sol model"
  pokemon ls models --json         # every level of every model, for scripting
  pokemon ls roms                  # which games are registered AND on disk
  pokemon ls events                # the ids --stop-at accepts
  pokemon ls configs               # config stems; the last one is the default

Reads the registries on disk — works whether or not `pokemon app` is running.
""",
    )
    parser.add_argument(
        "what", nargs="?", choices=CATEGORIES,
        help="What to list. Omit to see the categories.",
    )
    parser.add_argument(
        "filter", nargs="?", default=None,
        help="Optional case-insensitive substring filter.",
    )
    parser.add_argument("--json", action="store_true", help="Print raw registry rows.")
    args = parser.parse_args()

    if args.what is None:
        print("pokemon ls <what> [filter]\n")
        print("  models       model aliases + thinking levels   (configs/models.yaml)")
        print("  roms         registered games                  (configs/roms.yaml)")
        print("  configs      config stems                      (configs/config-*.yaml)")
        print("  events       story events for --stop-at        (the gate ladder)")
        print("  benchmarks   scored benchmarks                 (configs/benchmarks.yaml)")
        print("\ne.g. `pokemon ls models sol`")
        sys.exit(0)

    try:
        sys.exit(DISPATCH[args.what](args))
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR reading the {args.what} registry: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
