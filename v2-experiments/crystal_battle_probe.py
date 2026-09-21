#!/usr/bin/env python3
"""Crystal's battle reads, checked against the SCREEN of the same instant.

Task A of the Crystal battle work: the claim under test is that 0xd22d is
pokecrystal's ``wBattleMode`` (0 none, 1 wild, 2 trainer). The evidence it was
adopted on cannot tell that claim apart from "a byte that reads 2 in every
battle" plus two states inferred wild from the SPECIES alone -- and species is
not evidence of kind.

So the screen is captured from the emulator at the same instant the byte is
read: load the savepoint, step, read memory AND grab /screen. No pairing
question between a run's screenshot file and a run's savepoint, because both
come off the same loaded state.

    ./venv/bin/python v2-experiments/crystal_battle_probe.py --port 8300
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
from skyemu import SkyEmu  # noqa: E402

ROM = ROOT / "roms" / "Pokemon - Crystal Version (USA).gbc"
RUNS = {
    "r1": ROOT / "local/runs/2026-09-19_22-48-56_config-v2-crystal__gemini-3-8-flash-minimal",
    "r2": ROOT / "local/runs/2026-09-20_00-28-46_config-v2-crystal__gemini-3-8-flash-minimal",
}
OUT_PNG = ROOT / "v2-experiments/states/crystal"

# The whole neighbourhood, so the analysis is not limited to the addresses the
# hypothesis names. 0xd200..0xd25f covers wEnemyMon and everything after it.
DUMP = (0xD200, 0x60)
SVBK = 0xFF70


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--out", type=Path, default=ROOT / "v2-experiments/crystal-battle/savepoints.json")
    ap.add_argument("--png", action="store_true", help="also write a PNG per state")
    args = ap.parse_args()

    OUT_PNG.mkdir(parents=True, exist_ok=True)
    rows = {}
    with SkyEmu(ROM, port=args.port) as emu:
        for tag, run in RUNS.items():
            sp = sorted((run / "savepoints").glob("turn_*"),
                        key=lambda p: int(p.name.split("_")[1]))
            for d in sp:
                st = d / "emulator.state"
                if not st.is_file():
                    continue
                turn = int(d.name.split("_")[1])
                emu.load_state(st)
                emu.step(1)
                blob = emu.read_memory(*DUMP)
                rows[f"{tag}:{turn}"] = {
                    "run": tag, "turn": turn,
                    "svbk": emu.read_byte(SVBK),
                    "d22d": blob[0xD22D - DUMP[0]],
                    "dump": blob.hex(),
                }
                if args.png:
                    (OUT_PNG / f"{tag}_turn_{turn}.png").write_bytes(emu.screen())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=1) + "\n")
    for k, r in sorted(rows.items(), key=lambda kv: (kv[1]["run"], kv[1]["turn"])):
        print(f"{k:8s} svbk={r['svbk']} d22d={r['d22d']}")
    print(f"\n{len(rows)} states -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
