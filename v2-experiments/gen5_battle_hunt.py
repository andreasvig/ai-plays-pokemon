#!/usr/bin/env python3
"""Farm WILD battles on Gen 5 by walking the grass from one savepoint.

The replay walker (``gen5_battle_walk.py``) re-plays a run's own inputs, which
is the right tool for labelling the battles a run actually had. It is the wrong
tool for MAKING more: a wild encounter is RNG keyed on frame count, so a replay
reproduces the run's route and not its luck.

This does the other half. From one savepoint it walks a variable number of
tiles, which lands on a different frame every time and therefore a different
encounter, and stops the moment the opponent's species goes non-zero. Each hit
gets the frame, a save state and the whole of main RAM, which is what
``gen5_heap_map.py`` needs to say which allocation a candidate lives in.

Why the corpus matters more than the search: Black's only wild battle in the
savepoint corpus is one Lillipup, and a single wild sample cannot refute a
wild/trainer discriminator any more than a wild-only corpus can (SoulSilver,
Emerald). Composition is the claim; the address is the detail.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \\
      v2-experiments/gen5_battle_hunt.py --game black --port 8446 \\
        --state local/runs/<run>/savepoints/turn_70/emulator.state \\
        --walk "up,up,up" --tries 12 --out /tmp/g5/hunt70
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
from gen5_battle_probe import GAMES, MAIN_BASE, MAIN_SIZE  # noqa: E402
from gen5_battle_walk import HOLD, GAP, AB_HOLD, AB_GAP, settle  # noqa: E402
from skyemu import SkyEmu  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--prefix", default="", help="tiles walked once, to reach the grass")
    ap.add_argument("--walk", default="up,down", help="the tile cycle to repeat inside it")
    ap.add_argument("--tries", type=int, default=12)
    ap.add_argument("--max-steps", type=int, default=24)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--intro-frames", type=int, default=150,
                    help="frames to run before the intro screenshot")
    ap.add_argument("--settle-presses", type=int, default=0,
                    help="A presses after the encounter, to reach the command menu")
    args = ap.parse_args()

    g = GAMES[args.game]
    cycle = [b.strip().lower() for b in args.walk.split(",") if b.strip()]
    prefix = [b.strip().lower() for b in args.prefix.split(",") if b.strip()]
    args.out.mkdir(parents=True, exist_ok=True)
    import numpy as np
    rows = []
    with SkyEmu(g["rom"], port=args.port) as emu:
        for t in range(1, args.tries + 1):
            emu.load_state(args.state)
            emu.step(4)
            steps = 0
            hit = None
            # try t extra tiles before giving up, so every attempt lands on a
            # different frame and therefore a different roll
            # Try t walks 2(t-1) extra tiles in and out of the grass before
            # settling into the cycle. That is what makes each try a different
            # ROLL rather than a re-run of the same one: gen 5 advances the
            # encounter RNG per STEP, so idle frames before the walk change
            # nothing (measured — tries 1 and 2 gave the same Lillipup after
            # the same 11 tiles with only a frame offset between them).
            lead = prefix + (["left", "right"] * (t - 1) if prefix else [])
            for s in range(len(lead) + args.max_steps):
                b = lead[s] if s < len(lead) else cycle[(s - len(lead)) % len(cycle)]
                emu.press(b, hold=HOLD, gap=GAP)
                steps += 1
                sp = struct.unpack("<H", emu.read_memory(g["species"], 2))[0]
                if sp:
                    hit = sp
                    break
            if hit is None:
                print(f"try {t}: no encounter in {steps} tiles", flush=True)
                continue
            # The INTRO frame, before anything is pressed: gen 5 announces the
            # kind in words there ("A wild Lillipup appeared!"), and
            # ``settle()`` below runs straight past it to the command menu.
            emu.step(args.intro_frames)
            (args.out / f"w{t:02d}_intro.png").write_bytes(emu.screen())
            for _ in range(args.settle_presses):
                emu.press("a", hold=AB_HOLD, gap=AB_GAP)
            settle(emu)
            sp = struct.unpack("<H", emu.read_memory(g["species"], 2))[0]
            tag = f"w{t:02d}"
            (args.out / f"{tag}.png").write_bytes(emu.screen())
            emu.save_state(args.out / f"{tag}.state")
            buf = emu.read_memory(MAIN_BASE, MAIN_SIZE)
            np.save(args.out / f"{tag}.npy", np.frombuffer(buf, dtype=np.uint8))
            rows.append({"tag": tag, "tiles": steps, "species": sp})
            print(f"try {t}: {tag} species={sp} after {steps} tiles", flush=True)
    (args.out / "hunt.json").write_text(json.dumps(rows, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
