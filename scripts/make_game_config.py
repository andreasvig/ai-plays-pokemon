#!/usr/bin/env python3
"""Write a per-game copy of the SkyEmu run config, into local/.

This is now the CLI convenience, not the only way in. As of 2026-09-20 the
SkyEmu config IS `configs/config-6.0.yaml` (Andreas approved the rename), so the
dashboard queue lists it — and the queue already carries a `rom` field that
`RunExecutor` feeds to `apply_rom`, so ALL SEVEN games can be queued from the UI
by picking the config and the ROM separately. Nothing here is needed for that.

What it is still for: running several games AT ONCE from the CLI, which needs a
distinct HTTP port per concurrent run, and a config file is where the port
lives. The queue runs one at a time, so it never has the problem.

Why generate instead of committing six files: they would be six near-identical
copies of a 300-line config differing in one line, and the FIRST thing to rot
would be the 299 lines nobody meant to fork. This derives them, so a change to
config-6.0.yaml reaches every game on the next run.

Note that the rename also changed what a bare `pokemon run` loads: the highest
`config-X.Y` is the default (`catalog.list_configs`), so the default backend is
now SkyEmu. That was the point of the decision, not a side effect of it.

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

BASE = Path("configs/config-6.0.yaml")
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
    # Patch the block WHERE IT ALREADY IS. config-6.0 keeps it under
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
