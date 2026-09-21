#!/usr/bin/env python3
"""Full NDS main-RAM dumps of named savepoints, for the wild/trainer diff.

Deliberately NOT a blind sweep — ``local/battleflag/black/NOTES.md`` is what a
blind sweep of DS RAM costs (18 hours, 14,670 indistinguishable candidates).
This dumps a handful of states whose kind the SCREEN already settled, so the
question asked of the dump is "which word separates these two labelled sets",
and the answer is then required to sit at a fixed offset from a struct that can
be named — not merely to correlate.

    ./venv/bin/python v2-experiments/gen4_dump_states.py --game platinum \
        --states p1:60 p3:100 p2:70 --out /tmp/dumps --port 8381
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
from skyemu import SkyEmu  # noqa: E402
sys.path.insert(0, str(ROOT / "v2-experiments"))
from gen4_battle_probe import GAMES  # noqa: E402

MAIN = (0x02000000, 0x400000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--states", nargs="+", required=True, help="tag:turn, e.g. p3:100")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8381)
    args = ap.parse_args()

    g = GAMES[args.game]
    args.out.mkdir(parents=True, exist_ok=True)
    with SkyEmu(g["rom"], port=args.port) as emu:
        for s in args.states:
            tag, turn = s.split(":")
            st = ROOT / "local/runs" / g["runs"][tag] / f"savepoints/turn_{turn}/emulator.state"
            emu.load_state(st)
            emu.step(1)
            blob = emu.read_memory(*MAIN)
            (args.out / f"{tag}_{turn}.bin").write_bytes(blob)
            print(f"{s} -> {len(blob)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
