#!/usr/bin/env python3
"""Walk a gen-4 cartridge somewhere its recorded runs never reached.

Why this exists: SoulSilver's whole battle corpus is Route 29, and Route 29 has
no trainers. A corpus with no trainer battle cannot settle a wild/trainer
discriminator — it is the mistake that produced a false refutation on Emerald —
so the corpus has to be extended by PLAYING, not by scoring harder.

It drives a program of moves, flees any wild encounter it walks into (the RUN
button, tapped: the D-pad does not move the HGSS battle cursor), and saves a
state at the end so the next leg starts where this one stopped.

    ./venv/bin/python v2-experiments/gen4_drive.py --game soulsilver \\
        --state local/runs/.../savepoints/turn_196/emulator.state \\
        --moves "left:40" --out /tmp/leg1 --port 8393
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
from gen4_battle_end import RUN_BUTTON, in_battle  # noqa: E402
from gen4_battle_probe import GAMES  # noqa: E402
from skyemu import SkyEmu  # noqa: E402


def read_state(game: str, emu: SkyEmu) -> dict:
    g = GAMES[game]
    loc = struct.unpack("<5i", emu.read_memory(g["location"], 20))
    raw = struct.unpack("<I", emu.read_memory(g["flag"], 4))[0]
    return {"map_id": loc[0], "x": loc[2], "y": loc[3], "flag": raw,
            "in_battle": in_battle(game, raw)}


def flee(game: str, emu: SkyEmu, shots: Path, tries: int = 14) -> int:
    """Tap RUN until the battle lets go. Returns the presses it took."""
    for n in range(tries):
        emu.tap(*RUN_BUTTON, hold=20, gap=100)
        emu.step(90)
        for _ in range(4):
            emu.press("b", hold=12, gap=60)
            emu.step(60)
        if not read_state(game, emu)["in_battle"]:
            return n + 1
    (shots / "flee_stuck.png").write_bytes(emu.screen())
    return -1


#: Which way each seek direction wants a coordinate to move, and the two
#: sideways buttons to try when it is blocked.
SEEK = {"left": ("x", -1, ("up", "down")), "right": ("x", +1, ("up", "down")),
        "up": ("y", -1, ("left", "right")), "down": ("y", +1, ("left", "right"))}


def seek(game: str, emu: SkyEmu, args, shots: Path, log: list) -> int:
    """Wall-follow toward one direction, fleeing anything that jumps out.

    A route is not a corridor, so a fixed program of presses stalls against the
    first tree: 70 LEFT presses on Route 29 bought seven tiles. This pushes the
    wanted direction, and when a press does not move the coordinate it steps
    SIDEWAYS — alternating which side, so a dead end on one is escaped by the
    other rather than retried forever.
    """
    axis, want, sides = SEEK[args.seek]
    side = 0
    prev = read_state(game, emu)
    for i in range(args.steps):
        emu.press(args.seek, hold=12, gap=24)
        emu.step(30)
        s = read_state(game, emu)
        if s["in_battle"]:
            (shots / f"{i:04d}_battle.png").write_bytes(emu.screen())
            if args.stop_on_battle:
                log.append({**s, "i": i, "input": args.seek})
                print("BATTLE at", i, s)
                break
            s["fled_in"] = flee(game, emu, shots)
            s = read_state(game, emu)
        moved = (s[axis] - prev[axis]) * want > 0
        if not moved:
            for _ in range(3):
                emu.press(sides[side], hold=12, gap=24)
                emu.step(30)
            side ^= 1
            s = read_state(game, emu)
        log.append({**s, "i": i, "moved": bool(moved)})
        prev = s
        if i % 20 == 0:
            (shots / f"{i:04d}.png").write_bytes(emu.screen())
            print(i, s)
    (shots / "final.png").write_bytes(emu.screen())
    emu.save_state(args.out / "final.state")
    print("end", read_state(game, emu))
    (args.out / "log.json").write_text(json.dumps(log, indent=1) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--moves", default="",
                    help='"left:40,up:6,a:2" — button:count, in order')
    ap.add_argument("--seek", choices=["left", "right", "up", "down"], default=None,
                    help="wall-follow toward this direction instead of a fixed program")
    ap.add_argument("--steps", type=int, default=120, help="presses, with --seek")
    ap.add_argument("--port", type=int, default=8393)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stop-on-battle", action="store_true",
                    help="keep the first battle instead of fleeing it")
    args = ap.parse_args()

    g = GAMES[args.game]
    args.out.mkdir(parents=True, exist_ok=True)
    shots = args.out / "png"
    shots.mkdir(exist_ok=True)
    program = []
    for part in filter(None, args.moves.split(",")):
        btn, _, n = part.partition(":")
        program += [btn.strip()] * int(n or 1)

    log = []
    with SkyEmu(g["rom"], port=args.port) as emu:
        emu.load_state(args.state)
        emu.step(30)
        print("start", read_state(args.game, emu))
        if args.seek:
            return seek(args.game, emu, args, shots, log)
        for i, btn in enumerate(program):
            emu.press(btn, hold=12, gap=24 if btn not in ("a", "b") else 100)
            emu.step(30)
            s = read_state(args.game, emu)
            s.update({"i": i, "input": btn})
            if s["in_battle"]:
                (shots / f"{i:04d}_battle.png").write_bytes(emu.screen())
                if args.stop_on_battle:
                    log.append(s)
                    print("BATTLE at", i, s)
                    break
                s["fled_in"] = flee(args.game, emu, shots)
                s.update({k: v for k, v in read_state(args.game, emu).items()})
            log.append(s)
            if i % 10 == 0:
                (shots / f"{i:04d}.png").write_bytes(emu.screen())
        (shots / "final.png").write_bytes(emu.screen())
        emu.save_state(args.out / "final.state")
        print("end", read_state(args.game, emu))
    (args.out / "log.json").write_text(json.dumps(log, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
