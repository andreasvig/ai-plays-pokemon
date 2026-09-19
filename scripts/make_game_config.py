#!/usr/bin/env python3
"""Write a per-game copy of the SkyEmu run config, into local/.

`pokemon run` takes a config PATH, but the dashboard queue takes a config STEM
and only accepts numeric `config-X.Y` ones (`src/app/catalog.py:154`), which the
SkyEmu config is not. So the six non-FireRed games cannot be queued from the UI
yet, and the CLI is the way to run them.

Why generate instead of committing six files: they would be six near-identical
copies of a 300-line config differing in one line, and the FIRST thing to rot
would be the 299 lines nobody meant to fork. This derives them, so a change to
config-v2-firered.yaml reaches every game on the next run.

Why not just rename the SkyEmu config to `config-6.0.yaml` and let the queue
take it: the highest `config-X.Y` is what a bare `pokemon run` loads
(`catalog.list_configs`), so that one rename silently changes what the DEFAULT
run does. That is a decision about the project's default backend, not a step in
getting a walk graph, and it is not mine to make at 1am.

    scripts/make_game_config.py --rom emerald --port 8201
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app.roms import apply_rom, load_roms  # noqa: E402
from src.referee.contracts import contract_for  # noqa: E402

BASE = Path("configs/config-v2-firered.yaml")
OUT = Path("local/run-configs")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rom", required=True)
    ap.add_argument("--port", type=int, required=True,
                    help="the SkyEmu HTTP port — one per concurrent run, and "
                         "never 8161, which the committed config uses and a "
                         "stray process may still be holding")
    ap.add_argument("--pace", choices=["fast", "realtime"], default=None)
    args = ap.parse_args()

    rom = next((r for r in load_roms() if r.id == args.rom), None)
    if rom is None:
        raise SystemExit(f"Unknown rom {args.rom!r}")
    if not rom.exists():
        raise SystemExit(f"ROM missing on disk: {rom.path}")

    cfg = yaml.safe_load(BASE.read_text())
    # The ONE place rom wiring lives — rom_path, game_name, and every
    # {{game_name}} in the authored prompts. Telling a model playing Crystal
    # that it is playing FireRed is worse than telling it nothing.
    apply_rom(cfg, rom)
    cfg["emulator"]["port"] = args.port

    # `provider_profiles.path` is resolved against the CONFIG's own directory
    # (src/agent/provider_profiles.py:52), and this copy does not live in
    # configs/. Rewrite it absolute, so the derived config can sit anywhere —
    # which is the point of deriving it into local/ rather than forking a
    # 300-line file into configs/ once per game.
    #
    # Patch the block WHERE IT ALREADY IS. config-v2-firered keeps it under
    # `player_agent`, and adding a second copy at the top level is refused by
    # `_hoist_player_agent` ("define it in exactly one place") — correctly, and
    # it caught this on the first run.
    for holder in (cfg, cfg.get("player_agent") or {}):
        prof = holder.get("provider_profiles")
        if isinstance(prof, dict) and prof.get("enabled"):
            prof["path"] = str((Path("configs") / "provider-profiles.yaml").resolve())

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"config-v2-{rom.id}.yaml"
    out.write_text(yaml.safe_dump(cfg, sort_keys=False, width=100))

    contract = contract_for(rom.game)
    print(f"{out}")
    print(f"  rom      {rom.path}")
    print(f"  console  {rom.console}   port {args.port}")
    print(f"  start    {rom.start_save}")
    print(f"  contract {'yes — ' + str(list(contract.spec)) if contract else 'NONE (this run records no position)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
