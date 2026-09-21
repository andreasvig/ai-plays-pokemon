#!/usr/bin/env python3
"""Widen the gen-4 battle corpus by REPLAYING a run's own inputs from a savepoint.

The method is ``crystal_battle_walk.py``'s, which turned a 47-savepoint corpus
into 1,318 labelled samples: savepoints land every 10 turns, so the states they
give are a handful of arbitrary moments inside a fight, while the run's own
recorded ``button_sequence`` lists walk the same stretch again at INPUT
resolution — every press its own sample.

The oracle is different, and it has to be. Gen 2 writes its dialogue into a
tilemap as character codes, so Crystal could read the screen out of memory; the
DS draws glyphs into VRAM and there is no such shortcut. So every sample also
captures the FRAME, and the kind of a battle is settled by reading the words on
the intro frame — "A wild STARLY appeared!" against "Youngster Tristan would
like to battle!" — rather than by any byte.

    ./venv/bin/python v2-experiments/gen4_battle_walk.py --game platinum \\
        --run p3 --start 90 --turns 14 --port 8382 --out /tmp/walk_p3_90
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
sys.path.insert(0, str(ROOT / "v2-experiments"))
from gen4_battle_probe import GAMES  # noqa: E402
from skyemu import SkyEmu  # noqa: E402

# configs/config-v2-platinum + config-v2-soulsilver: button_hold_frames 12,
# frames_between_inputs 24, ab_hold_frames 12, ab_gap_frames 100,
# wait_input_seconds 5.0 at 60 fps.
HOLD, GAP, AB_HOLD, AB_GAP, WAIT = 12, 24, 12, 100, 300

#: The regions every sample carries, per game. Deliberately WIDER than the
#: fields under test: the whole BattleSystem allocation rather than the one
#: word that separated a trainer from a wild in the savepoint diff, so the
#: analysis can ask about the outcome without a second run of the emulator.
REGIONS = {
    "platinum": {
        "loc": (0x0227F408, 20),
        "overlay": (0x022A647C, 4),
        # The BattleContext heap block (hdr 0x022c29cc, size 0x3168): battler 0
        # sits at +0x2d58 of its data, which is what makes the already-shipped
        # foe species (+0x2e18) an offset into a struct rather than an address.
        "bctx": (0x022C29CC, 0x3170),
        # The BattleSystem heap block (hdr 0x022bf950, size 0x2494).
        "bsys": (0x022BF950, 0x24A0),
    },
    "soulsilver": {
        "loc": (0x0227D448, 20),
        "species": (0x021D05C8, 4),
        # The same two allocations as Platinum, at HGSS's bases: BattleSystem
        # hdr 0x022c01ec size 0x24a0, BattleContext hdr 0x022c32b8 size 0x3168
        # with battleMons[0] at its data + 0x2d58 -- byte-identical offsets, on
        # the engine Platinum's were measured on.
        "bctx": (0x022C32B8, 0x3170),
        "bsys": (0x022C01EC, 0x24B0),
    },
}


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--turns", type=int, default=14)
    ap.add_argument("--port", type=int, default=8382)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()

    g = GAMES[args.game]
    regions = REGIONS[args.game]
    run = ROOT / "local/runs" / g["runs"][args.run]
    seqs = sequences(run)
    state = run / f"savepoints/turn_{args.start}/emulator.state"
    args.out.mkdir(parents=True, exist_ok=True)
    pngs = args.out / "png"
    if not args.no_png:
        pngs.mkdir(exist_ok=True)

    rows = []

    def take(emu, turn, i, name):
        r = {"turn": turn, "i": i, "input": name}
        for k, (a, n) in regions.items():
            r[k] = emu.read_memory(a, n).hex()
        if not args.no_png:
            p = pngs / f"{len(rows):05d}.png"
            p.write_bytes(emu.screen())
            r["png"] = p.name
        rows.append(r)

    with SkyEmu(g["rom"], port=args.port) as emu:
        emu.load_state(state)
        emu.step(1)
        take(emu, args.start, -1, "<loaded>")
        for turn in range(args.start + 1, args.start + 1 + args.turns):
            for i, btn in enumerate(seqs.get(turn, [])):
                b = btn.strip().lower()
                if b == "wait":
                    emu.step(WAIT)
                else:
                    ab = b in ("a", "b")
                    emu.press(b, hold=AB_HOLD if ab else HOLD, gap=AB_GAP if ab else GAP)
                take(emu, turn, i, b)
            settle(emu)
            take(emu, turn, 99, "<settled>")
    (args.out / "samples.json").write_text(json.dumps(rows, indent=0) + "\n")
    print(f"{len(rows)} samples -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
