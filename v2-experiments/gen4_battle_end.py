#!/usr/bin/env python3
"""Play one loaded battle to its END, sampling memory after every press.

The outcome is the one battle field a replay of a run's own inputs rarely
delivers: a model that flees every wild encounter produces one ending over and
over, and a corpus of one ending cannot tell an OUTCOME field from a
"the battle is over" field. So this drives a chosen savepoint to a chosen
ending with a fixed policy — A-spam fights, the RUN button flees — and keeps
every sample from the last presses of the fight, which on gen 4 is where the
answer has to be read: the battle heap is FREED when the overlay unloads, so
unlike gen 2 and gen 3 there is nothing left to read after the flag clears.

    ./venv/bin/python v2-experiments/gen4_battle_end.py --game platinum \\
        --state p3:180 --policy run --port 8389 --out /tmp/end_p3_180
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
sys.path.insert(0, str(ROOT / "v2-experiments"))
from gen4_battle_probe import GAMES  # noqa: E402
from gen4_battle_walk import REGIONS  # noqa: E402
from skyemu import SkyEmu  # noqa: E402

#: The RUN button, in touch-screen coordinates. Bottom centre of the battle
#: menu on both cartridges — the D-pad does NOT move the HGSS cursor off FIGHT
#: (measured: one Down press left the red brackets where they were), so the
#: touch screen is the only way to choose it there.
RUN_BUTTON = (0.5, 0.92)
#: FIGHT, and the first move in the 2x2 list it opens. Also taps, and for the
#: same reason: a savepoint is loaded with the cursor wherever the model left
#: it, so "press A" is not "choose FIGHT" — an A/B alternation from p2:30 spent
#: 90 presses opening and closing the BAG.
FIGHT_BUTTON = (0.5, 0.42)
MOVE1_BUTTON = (0.25, 0.30)


def flag_of(game: str, emu: SkyEmu) -> int:
    g = GAMES[game]
    return struct.unpack("<I", emu.read_memory(g["flag"], 4))[0]


def in_battle(game: str, raw: int) -> bool:
    return raw == 16 if game == "platinum" else (raw & 0xFFFF) != 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--state", required=True, help="tag:turn of a savepoint")
    ap.add_argument("--policy", choices=["aspam", "run", "fight"], default="fight")
    ap.add_argument("--presses", type=int, default=90)
    ap.add_argument("--after", type=int, default=6, help="samples to keep past the close")
    ap.add_argument("--port", type=int, default=8389)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    g = GAMES[args.game]
    regions = REGIONS[args.game]
    tag, turn = args.state.split(":")
    st = ROOT / "local/runs" / g["runs"][tag] / f"savepoints/turn_{turn}/emulator.state"
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "png").mkdir(exist_ok=True)

    rows = []
    with SkyEmu(g["rom"], port=args.port) as emu:
        emu.load_state(st)
        emu.step(30)
        left_after = None
        for n in range(args.presses):
            raw = flag_of(args.game, emu)
            here = in_battle(args.game, raw)
            if left_after is None and not here and n:
                left_after = 0
            if args.policy == "run" and here and n % 3 == 0:
                emu.tap(*RUN_BUTTON, hold=20, gap=100)
                name = "tap:run"
            elif args.policy == "fight" and here:
                # tap FIGHT, tap the first move, then advance the text that
                # follows. Eight presses is one full round of the menu.
                step = n % 8
                if step == 0:
                    emu.tap(*FIGHT_BUTTON, hold=20, gap=100)
                    name = "tap:fight"
                elif step == 1:
                    emu.tap(*MOVE1_BUTTON, hold=20, gap=100)
                    name = "tap:move1"
                else:
                    name = "a"
                    emu.press(name, hold=12, gap=100)
            else:
                name = "a" if n % 2 == 0 else "b"
                emu.press(name, hold=12, gap=100)
            emu.step(60)
            raw = flag_of(args.game, emu)
            r = {"n": n, "input": name, "flag": raw,
                 "in_battle": in_battle(args.game, raw)}
            for k, (a, ln) in regions.items():
                r[k] = emu.read_memory(a, ln).hex()
            (args.out / "png" / f"{n:04d}.png").write_bytes(emu.screen())
            rows.append(r)
            if left_after is not None:
                left_after += 1
                if left_after > args.after:
                    break
            elif not r["in_battle"] and n > 2:
                left_after = 0
    (args.out / "samples.json").write_text(json.dumps(rows, indent=0) + "\n")
    print(f"{len(rows)} samples -> {args.out}; "
          f"in_battle {sum(r['in_battle'] for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
