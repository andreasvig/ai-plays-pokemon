#!/usr/bin/env python3
"""How long a button has to be held, and how long to wait after it, per game.

This is P-C of `artifacts/skyemu-backend/cross-game-plan.md`. Today one pair of
numbers serves every game: `button_hold_frames: 12` and `frames_between_inputs:
24`, whose provenance is a comment in `lua/socketserver-1.lua:32-33` reading
"200ms hold -- ensures walk, not just turn" and "400ms gap -- walk animation is
~16 frames (267ms)". Both are FireRed measurements wearing the clothes of
constants.

Why a wrong number here does not announce itself
------------------------------------------------
Hold too short on a game that needs more frames and every direction press turns
the character instead of moving it. The run burns twice the inputs -- and the
census files those presses as `turns_to_face`, which is a **free** bucket
(`src/referee/trace.py:34-36`), so the score drops while the instrument reports
nothing wrong. It looks like a bad model.

Gap too short and the per-input trace samples the tile mid-walk and reads the
OLD one (`socketserver-1.lua:253-256`). `overworld_steps` under-counts and the
press lands in `walls_hit` or `blocked_by_actor`.

So there are two thresholds, not one, and this measures both by experiment.

What it measures
----------------
    turn      the smallest hold, from a standing start NOT facing that way,
              that changes anything at all
    move      the smallest hold, from that same standing start, that moves a
              tile. `button_hold_frames` has to be at or above this
    repeat    the smallest hold that moves when the character is ALREADY facing
              the direction -- usually smaller, and the number that governs a
              run of presses in one direction
    gap       the smallest `frames_between_inputs` at which TWO consecutive
              presses both land. Below it the second is swallowed by the first
              one's animation

Every reading is taken from memory, not from the screen: the x or y the address
finder produced. `/load` before each probe, so nothing accumulates.

Usage
-----
    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
        v2-experiments/calibrate_timing.py --port 8190 \
        --x-ptr 0x03005008 --x-offset 0 --dir R
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "v2-experiments" / "harness"))

from skyemu import SkyEmu  # noqa: E402

DEFAULT_ROM = REPO / "roms" / "Pokemon - FireRed Version (USA, Europe) (Rev 1).gba"
DEFAULT_STATE = REPO / "configs" / "saves" / "skyemu" / "firered-pokebench-v2" / "emulator.state"
WORK = Path("/tmp/skyemu-timing")

DPAD = {"U": "up", "D": "down", "L": "left", "R": "right"}
SETTLE = 40


def cold_rom(rom: Path, tag: str) -> Path:
    d = WORK / tag
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    dst = d / ("rom" + rom.suffix)
    shutil.copy2(rom, dst)
    return dst


class Reader:
    """Whatever spelling of the coordinate the finder came back with.

    A raw address is right for a game that does not move its block; a
    pointer+offset is right for one that does, and FireRed does -- its
    SaveBlock1 sits at four different addresses across four probes
    (`p-a-results.md` section 3). Both are accepted so a caller never has to
    translate.
    """

    def __init__(self, emu: SkyEmu, addr=None, ptr=None, offset=0, width=2, signed=True):
        self.emu, self.addr, self.ptr = emu, addr, ptr
        self.offset, self.width, self.signed = offset, width, signed

    def __call__(self) -> int:
        base = self.addr
        if base is None:
            base = int.from_bytes(self.emu.read_memory(self.ptr, 4), "little") + self.offset
        return int.from_bytes(self.emu.read_memory(base, self.width), "little",
                              signed=self.signed)


def probe(emu, state: Path, read: Reader, prelude, presses) -> int:
    """Load, walk to the probe tile, run the presses, return the coordinate."""
    emu.load_state(state)
    emu.step(SETTLE)
    for b, hold, gap in prelude:
        emu.press(DPAD[b], hold=hold, gap=gap)
    emu.step(SETTLE)
    start = read()
    for b, hold, gap in presses:
        emu.press(DPAD[b], hold=hold, gap=gap)
    emu.step(SETTLE)
    return read() - start


def smallest(values, test, log=print, label=""):
    """The first value that passes, scanning upward, with the trace printed.

    Scanned in order rather than bisected on purpose: the response is not
    guaranteed monotone on a game nobody has measured, and a bisection would
    turn a non-monotone curve into a confident wrong number without saying so.
    The printed trace is what shows whether it was monotone after all.
    """
    trace, hit = [], None
    for v in values:
        got = test(v)
        trace.append((v, got))
        if hit is None and got:
            hit = v
    log(f"  {label:<8} {'  '.join(f'{v}:{int(bool(g))}' for v, g in trace)}")
    monotone = all(g for _, g in trace[[v for v, _ in trace].index(hit):]) if hit else True
    return hit, monotone, trace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--dir", default="R", choices=list(DPAD),
                    help="the direction to probe; it must be walkable for at least "
                         "three tiles from the probe tile")
    ap.add_argument("--prelude", default="", help="presses to reach a free tile")
    ap.add_argument("--x-addr", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--x-ptr", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--x-offset", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--width", type=int, default=2)
    ap.add_argument("--max-hold", type=int, default=24)
    ap.add_argument("--max-gap", type=int, default=40)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    if args.x_addr is None and args.x_ptr is None:
        print("need --x-addr or --x-ptr (run find_addresses.py first)", file=sys.stderr)
        return 2

    prelude = [(b.strip().upper(), 12, 24) for b in args.prelude.split(",") if b.strip()]
    d = args.dir
    rom = cold_rom(args.rom, f"timing-{args.port}")

    with SkyEmu(rom, port=args.port) as emu:
        read = Reader(emu, args.x_addr, args.x_ptr, args.x_offset, args.width, True)
        out = {}

        print(f"hold sweep, direction {d}, generous 60-frame gap:")
        move, mono_m, trace_m = smallest(
            range(1, args.max_hold + 1),
            lambda h: probe(emu, args.state, read, prelude, [(d, h, 60)]) != 0,
            label="move")
        out["move_hold"] = {"frames": move, "monotone": mono_m, "trace": trace_m}

        # Already facing: one long press first, then the one under test.
        print("hold sweep, already facing that way:")
        repeat, mono_r, trace_r = smallest(
            range(1, args.max_hold + 1),
            # ABSOLUTE value. x falls walking left and rises walking right, and
            # y falls walking up on both games measured -- a signed test reads
            # "nothing ever moved" for half the directions. Caught on Emerald,
            # where --dir L produced an all-zero row for both sweeps while the
            # move sweep above (which already tested != 0) was fine.
            lambda h: abs(probe(emu, args.state, read, prelude,
                                [(d, 20, 60), (d, h, 60)])) > 1,
            label="repeat")
        out["repeat_hold"] = {"frames": repeat, "monotone": mono_r, "trace": trace_r}

        if move:
            print(f"gap sweep at hold={move}, two presses must both land:")
            gap, mono_g, trace_g = smallest(
                range(1, args.max_gap + 1),
                lambda g: abs(probe(emu, args.state, read, prelude,
                                    [(d, move, g), (d, move, 60)])) == 2,
                label="gap")
            out["gap"] = {"frames": gap, "monotone": mono_g, "trace": trace_g}

        # An all-zero row is not a threshold, it is a broken probe: either the
        # direction is walled off from the probe tile or the reader is pointed
        # at the wrong address. Say so rather than printing None as if it were
        # a measurement.
        for key, val in out.items():
            if val["frames"] is None:
                print(f"\n  {key}: NO VALUE AT ANY SETTING. The probe never moved -- "
                      f"check the tile is free {args.dir}-wards and --x-ptr/--x-addr "
                      f"is the coordinate.")
        print("\n--- calibration ---")
        for k, v in out.items():
            note = "" if v["monotone"] else "   NON-MONOTONE, read the trace"
            print(f"  {k:<12} {v['frames']}{note}")
        if move and out.get("gap", {}).get("frames"):
            print(f"\n  emulator.button_hold_frames >= {move}"
                  f"   (today: 12)\n"
                  f"  emulator.frames_between_inputs >= {out['gap']['frames']}"
                  f"   (today: 24)")
        if args.json:
            args.json.write_text(json.dumps(out, indent=2))
            print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
