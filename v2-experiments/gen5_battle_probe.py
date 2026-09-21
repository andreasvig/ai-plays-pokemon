#!/usr/bin/env python3
"""Load every savepoint of a Gen 5 run and dump the whole of NDS main RAM plus the frame.

Gen 5's sibling of ``gen4_battle_probe.py``, and it dumps WIDE on purpose. The
gen-4 work could sample two named allocations because pret/pokeplatinum names
them; there is no comparable decomp for Black/Black 2, so the structure has to
come out of the dump itself — Gen 5's debug allocator writes the SOURCE FILE
NAME of whoever asked for a block just before the block, which turns a 4 MB
dump into a labelled map of the battle heap (``gen5_heap_map.py``).

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \\
      v2-experiments/gen5_battle_probe.py --game black --port 8440 \\
        --out /tmp/gen5/black
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
from skyemu import SkyEmu  # noqa: E402

MAIN_BASE, MAIN_SIZE = 0x02000000, 0x400000

GAMES = {
    "black": {
        "rom": ROOT / "roms/Pokemon - Black Version (USA, Europe) (NDSi Enhanced).nds",
        "runs": {"b1": "2026-09-20_01-40-28_config-v2-black__gemini-3-8-flash-minimal"},
        "actor": 0x0224F90C,
        "species": 0x0226D8D4,
    },
    "black2": {
        "rom": ROOT / "roms/Pokemon - Black Version 2 (USA, Europe) (NDSi Enhanced).nds",
        "runs": {"c1": "2026-09-20_00-51-00_config-v2-black2__gemini-3-8-flash-minimal"},
        "actor": 0x0223B444,
        "species": 0x0225B414,
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--run", default=None)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--turns", default="", help="comma-separated savepoint turns; default all")
    ap.add_argument("--no-dump", action="store_true")
    args = ap.parse_args()

    g = GAMES[args.game]
    run_key = args.run or sorted(g["runs"])[0]
    run = ROOT / "local/runs" / g["runs"][run_key]
    sp = sorted((run / "savepoints").glob("turn_*"), key=lambda p: int(p.name.split("_")[1]))
    if args.turns:
        want = {int(t) for t in args.turns.split(",")}
        sp = [p for p in sp if int(p.name.split("_")[1]) in want]
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    import numpy as np
    with SkyEmu(g["rom"], port=args.port) as emu:
        for d in sp:
            turn = int(d.name.split("_")[1])
            emu.load_state(d / "emulator.state")
            emu.step(4)
            mid, x, h, y = struct.unpack("<Iiii", emu.read_memory(g["actor"], 16))
            species = struct.unpack("<H", emu.read_memory(g["species"], 2))[0]
            (args.out / f"t{turn:04d}.png").write_bytes(emu.screen())
            if not args.no_dump:
                buf = emu.read_memory(MAIN_BASE, MAIN_SIZE)
                np.save(args.out / f"t{turn:04d}.npy", np.frombuffer(buf, dtype=np.uint8))
            rows.append({"turn": turn, "map": mid, "x": x >> 16, "y": y >> 16,
                         "species": species})
            print(rows[-1], flush=True)
    (args.out / "savepoints.json").write_text(json.dumps(rows, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
