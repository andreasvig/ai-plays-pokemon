#!/usr/bin/env python3
"""Widen the Crystal battle corpus by REPLAYING a run's own inputs from a savepoint.

Savepoints land every 10 turns, so the labelled corpus they give is 7 battle
states out of 47 -- too few, and too lopsided (5 of 7 trainer), to tell
"wBattleMode" apart from "a byte that reads 2 in every battle". Replaying the
recorded ``button_sequence`` list from a savepoint walks the game through the
same stretch at INPUT resolution, and every sample carries its own oracle: the
on-screen text, read out of wTilemap (v2-experiments/crystal_text.py), which is
where gen 2 writes "Wild ZUBAT appeared!" for a wild encounter and
"<name> wants to battle!" for a trainer's.

The replay is not expected to be frame-exact -- the run's screen-settling wait
is variable and wild encounters are RNG -- and it does not need to be. Every
state it reaches is a real state of the real cartridge, and it is LABELLED by
the words on its own screen rather than by the turn number it came from.

    ./venv/bin/python v2-experiments/crystal_battle_walk.py --run r1 --from 180 \
        --turns 36 --port 8302 --out v2-experiments/crystal-battle/walk2_r1_180.json
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments"))
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
from crystal_text import TILEMAP, screen_text  # noqa: E402
from skyemu import SkyEmu  # noqa: E402

ROM = ROOT / "roms" / "Pokemon - Crystal Version (USA).gbc"
RUNS = {
    "r1": ROOT / "local/runs/2026-09-19_22-48-56_config-v2-crystal__gemini-3-8-flash-minimal",
    "r2": ROOT / "local/runs/2026-09-20_00-28-46_config-v2-crystal__gemini-3-8-flash-minimal",
}
# config-v2-crystal: button_hold_frames 12 / frames_between_inputs 24,
# ab_hold_frames 12 / ab_gap_frames 100, wait_input_seconds 5.
HOLD, GAP, AB_HOLD, AB_GAP, WAIT = 12, 24, 12, 100, 300
#: The regions every sample carries. 0xd0c0 covers wBattleResult, 0xd200 the
#: enemy battle struct and the mode/trainer bytes, 0xdcb0 the position record
#: (so a phantom map read is visible beside the flag that shares its bank).
DUMPS = {"d0c0": (0xD0C0, 0x60), "d200": (0xD200, 0x60), "dcb0": (0xDCB0, 0x10)}
SVBK = 0xFF70


def sequences(run: Path) -> dict[int, list[str]]:
    """turn -> the button list the model actually played, from events.jsonl."""
    out: dict[int, list[str]] = {}
    turn = None
    for line in (run / "events.jsonl").open():
        e = json.loads(line)
        if e.get("type") == "turn_start":
            turn = e["turn"]
        elif e.get("type") == "button_sequence" and turn is not None:
            out.setdefault(turn, ast.literal_eval(e["sequence"]))
    return out


def settle(emu: SkyEmu, max_frames: int = 900, chunk: int = 12) -> None:
    """Step until the frame stops changing — the run's own wait_for_stable_screen."""
    prev = emu.screen()
    same = 0
    for _ in range(max_frames // chunk):
        emu.step(chunk)
        now = emu.screen()
        same = same + 1 if now == prev else 0
        prev = now
        if same >= 2:
            return


def sample(emu: SkyEmu) -> dict:
    out = {k: emu.read_memory(*a).hex() for k, a in DUMPS.items()}
    blob = bytes.fromhex(out["d200"])
    tiles = emu.read_memory(TILEMAP, 360)
    out.update({"d22d": blob[0xD22D - DUMPS["d200"][0]], "svbk": emu.read_byte(SVBK),
                "dump": out["d200"], "tiles": tiles.hex(), "text": screen_text(tiles)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="r1", choices=sorted(RUNS))
    ap.add_argument("--start", type=int, required=True, help="savepoint turn to load")
    ap.add_argument("--turns", type=int, default=20, help="how many turns to replay")
    ap.add_argument("--port", type=int, default=8302)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    run = RUNS[args.run]
    seqs = sequences(run)
    state = run / f"savepoints/turn_{args.start}/emulator.state"
    rows = []
    with SkyEmu(ROM, port=args.port) as emu:
        emu.load_state(state)
        emu.step(1)
        rows.append({"turn": args.start, "i": -1, "input": "<loaded>", **sample(emu)})
        for turn in range(args.start + 1, args.start + 1 + args.turns):
            for i, btn in enumerate(seqs.get(turn, [])):
                b = btn.strip().lower()
                if b == "wait":
                    emu.step(WAIT)
                else:
                    ab = b in ("a", "b")
                    emu.press(b, hold=AB_HOLD if ab else HOLD, gap=AB_GAP if ab else GAP)
                rows.append({"turn": turn, "i": i, "input": b, **sample(emu)})
            settle(emu)
            rows.append({"turn": turn, "i": 99, "input": "<settled>", **sample(emu)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=0) + "\n")
    print(f"{len(rows)} samples -> {args.out}")
    nz = [r for r in rows if r["d22d"]]
    print(f"  d22d non-zero on {len(nz)} of them; values {sorted({r['d22d'] for r in rows})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
