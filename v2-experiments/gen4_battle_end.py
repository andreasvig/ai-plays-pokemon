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
from collections import deque
from pathlib import Path

import numpy as np

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
#: battleMons[0].curHP, as an offset from the BattleContext STRUCT (which is
#: the allocator header + 0x20, not the header + 8 the earlier note called
#: "data" — see gen4-outcome.md). battleMons is at +0x2d40 of the struct and
#: curHP at +0x4c of a BattleMon (pokeplatinum include/battle/battle_mon.h).
#: --nerf writes it to 1 so the NEXT enemy hit faints the player's only mon,
#: which is how the LOST cells of the outcome x kind table get filled: a model
#: that wins every fight cannot produce one, and a corpus missing a cell cannot
#: tell an outcome field from a kind field.
G4_BATTLE_MONS, G4_CUR_HP, G4_STRUCT = 0x2D40, 0x4C, 0x20
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
    ap.add_argument("--nerf", choices=["me", "foe"], default=None,
                    help="write a battler's curHP to 1 before the first press: "
                         "'me' drives this battle to a LOSS, 'foe' to a WIN. The "
                         "outcome x kind table needs both in BOTH kinds and a "
                         "model that always wins (or always flees) fills one cell.")
    ap.add_argument("--fine", type=int, default=0,
                    help="after each input, take this many samples of --fine-frames "
                         "instead of one. The close of a FLEE is short: at one "
                         "sample per input the battle is gone by the next one.")
    ap.add_argument("--fine-frames", type=int, default=10)
    ap.add_argument("--dump-close", action="store_true",
                    help="write 4 MB main-RAM dumps around the close: the last "
                         "TWO samples taken inside the battle and every sample "
                         "after it. Gen 4 frees the battle heap at the close, so "
                         "'is there anything left to read' is a question about "
                         "these frames and no others.")
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
    #: the two most recent in-battle main-RAM dumps, written out at the close.
    recent: deque = deque(maxlen=2)
    with SkyEmu(g["rom"], port=args.port) as emu:
        emu.load_state(st)
        emu.step(30)
        # A launch onto a port that is ALREADY serving a SkyEmu exits quietly
        # and leaves the client talking to somebody else's emulator, which here
        # showed up as a battle savepoint that was not in a battle (three runs
        # wasted, 2026-09-21). src/emulator/backends/skyemu.py refuses an
        # occupied port for the same reason; this harness does not, so the
        # state itself is the check.
        if not in_battle(args.game, flag_of(args.game, emu)):
            raise SystemExit(f"{args.state} did not load INSIDE a battle "
                             f"(flag={flag_of(args.game, emu)}) — is another "
                             f"SkyEmu already on port {args.port}?")
        if args.nerf:
            hp = (regions["bctx"][0] + G4_STRUCT + G4_BATTLE_MONS + G4_CUR_HP
                  + (0xC0 if args.nerf == "foe" else 0))
            before = struct.unpack("<i", emu.read_memory(hp, 4))[0]
            for i, b in enumerate((1, 0, 0, 0)):
                emu.write_byte(hp + i, b)
            print(f"nerf: battleMons[0].curHP {before} -> "
                  f"{struct.unpack('<i', emu.read_memory(hp, 4))[0]}")
        left_after = None
        for n in range(args.presses):
            raw = flag_of(args.game, emu)
            here = in_battle(args.game, raw)
            if left_after is None and not here and n:
                left_after = 0
            gap = 0 if args.fine else 100
            if args.policy == "run" and here and n % 3 == 0:
                emu.tap(*RUN_BUTTON, hold=20, gap=gap)
                name = "tap:run"
            elif args.policy == "fight" and here:
                # tap FIGHT, tap the first move, then advance the text that
                # follows. Eight presses is one full round of the menu.
                step = n % 8
                if step == 0:
                    emu.tap(*FIGHT_BUTTON, hold=20, gap=gap)
                    name = "tap:fight"
                elif step == 1:
                    emu.tap(*MOVE1_BUTTON, hold=20, gap=gap)
                    name = "tap:move1"
                else:
                    name = "a"
                    emu.press(name, hold=12, gap=gap)
            else:
                name = "a" if n % 2 == 0 else "b"
                emu.press(name, hold=12, gap=gap)
            emu.step(60 if not args.fine else 0)
            for sub in range(args.fine or 1):
                if args.fine:
                    emu.step(args.fine_frames)
                raw = flag_of(args.game, emu)
                r = {"n": n, "sub": sub, "input": name, "flag": raw,
                     "in_battle": in_battle(args.game, raw)}
                for k, (a, ln) in regions.items():
                    r[k] = emu.read_memory(a, ln).hex()
                (args.out / "png" / f"{n:04d}_{sub:02d}.png").write_bytes(emu.screen())
                if args.dump_close:
                    tag = f"{n:04d}_{sub:02d}"
                    # Only the ENDGAME is dumped: BattleContext.battleEndFlag
                    # (struct +0x311f, and the struct is the allocator header
                    # +0x20) goes TRUE on the same frame the outcome is written.
                    # A 4 MB read per sample is too slow to take on every press.
                    endgame = bytes.fromhex(r["bctx"])[0x20 + 0x311F] != 0
                    if r["in_battle"]:
                        if endgame:
                            recent.append((tag, emu.read_memory(0x02000000, 0x400000)))
                    else:
                        while recent:
                            t, blob = recent.popleft()
                            np.save(args.out / f"in_{t}.npy",
                                    np.frombuffer(blob, dtype=np.uint8))
                            r.setdefault("dumped", []).append(f"in_{t}")
                        np.save(args.out / f"out_{tag}.npy",
                                np.frombuffer(emu.read_memory(0x02000000, 0x400000),
                                              dtype=np.uint8))
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
