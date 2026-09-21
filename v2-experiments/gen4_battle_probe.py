#!/usr/bin/env python3
"""Load every Platinum / SoulSilver savepoint and read the battle neighbourhood.

Task A of the gen-4 battle work, and the same shape as
``crystal_battle_probe.py``: the byte and the SCREEN come off the SAME loaded
state, so there is no pairing question between a run's screenshot file and the
memory that was live when it was taken.

What it records per savepoint: the location block, the battle signal each
cartridge already has (Platinum's overlay id, SoulSilver's opponent species),
and a dump of the whole battle NEIGHBOURHOOD — not just the two addresses the
contract knows — so the analysis can ask questions the probe was not told to
ask. Plus a PNG, which is the only thing here that can settle wild vs trainer:
gen 4 announces "A wild X appeared!" for one and "<name> would like to
battle!" for the other.

    ./venv/bin/python v2-experiments/gen4_battle_probe.py --game platinum --port 8380
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
from skyemu import SkyEmu  # noqa: E402

GAMES = {
    "platinum": {
        "rom": ROOT / "roms/Pokemon - Platinum Version (USA).nds",
        "runs": {
            "p1": "2026-09-19_22-50-18_config-v2-platinum__gemini-3-8-flash-minimal",
            "p2": "2026-09-19_23-34-33_config-v2-platinum__gemini-3-8-flash-minimal",
            "p3": "2026-09-20_00-17-45_config-v2-platinum__gemini-3-8-flash-minimal",
            "p4": "2026-09-20_09-53-29_config-v2-platinum__gemini-3-8-flash-minimal",
        },
        "location": 0x0227F408,
        "flag": 0x022A647C,
        "species": 0x022C57EC,
        # battleMons[0] through the end of battleMons[1], plus slack: the two
        # battler structs are 0xc0 apart and the whole point is to read the
        # fields the contract does NOT yet name.
        # The two battle heap ALLOCATIONS whole, not just the fields under
        # test: BattleSystem (hdr 0x022bf950, size 0x2494) and BattleContext
        # (hdr 0x022c29cc, size 0x3168, battleMons[0] at its data + 0x2d58).
        "dumps": {"mons": (0x022C5700, 0x400), "bsys": (0x022BF950, 0x200)},
    },
    "soulsilver": {
        "rom": ROOT / "roms/Pokemon - SoulSilver Version (Europe).nds",
        "runs": {"s1": "2026-09-20_00-25-45_config-v2-soulsilver__gemini-3-8-flash-minimal"},
        "location": 0x0227D448,
        "flag": 0x021D05C8,
        "species": 0x021D05C8,
        # Same two allocations at SoulSilver's bases: BattleSystem hdr
        # 0x022c01ec size 0x24a0, BattleContext hdr 0x022c32b8 size 0x3168 —
        # the SAME size and the SAME battleMons offset as Platinum, which is
        # what makes "HGSS is the Platinum engine" a measurement here.
        "dumps": {"near": (0x021D0400, 0x400), "mons": (0x022C5FF0, 0x200),
                  "bsys": (0x022C01EC, 0x200)},
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--port", type=int, default=8380)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    g = GAMES[args.game]
    out = args.out or ROOT / f"v2-experiments/gen4-battle/{args.game}-savepoints.json"
    shots = ROOT / f"v2-experiments/gen4-battle/{args.game}-shots"
    shots.mkdir(parents=True, exist_ok=True)

    rows = {}
    with SkyEmu(g["rom"], port=args.port) as emu:
        for tag, run in g["runs"].items():
            sp = sorted((ROOT / "local/runs" / run / "savepoints").glob("turn_*"),
                        key=lambda p: int(p.name.split("_")[1]))
            for d in sp:
                st = d / "emulator.state"
                if not st.is_file():
                    continue
                turn = int(d.name.split("_")[1])
                emu.load_state(st)
                emu.step(1)
                loc = struct.unpack("<5i", emu.read_memory(g["location"], 20))
                flag = struct.unpack("<I", emu.read_memory(g["flag"], 4))[0]
                row = {
                    "run": tag, "turn": turn, "map_id": loc[0],
                    "x": loc[2], "y": loc[3], "flag": flag,
                    "species": struct.unpack("<H", emu.read_memory(g["species"], 2))[0],
                }
                for name, (a, n) in g["dumps"].items():
                    row[name] = emu.read_memory(a, n).hex()
                rows[f"{tag}:{turn}"] = row
                (shots / f"{tag}_turn_{turn}.png").write_bytes(emu.screen())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1) + "\n")
    for k, r in sorted(rows.items(), key=lambda kv: (kv[1]["run"], kv[1]["turn"])):
        print(f"{k:8s} map={r['map_id']:4d} ({r['x']},{r['y']})  "
              f"flag={r['flag']:#x} species={r['species']}")
    print(f"\n{len(rows)} states -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
