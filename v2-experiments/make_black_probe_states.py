#!/usr/bin/env python3
"""Drive Pokemon Black from a save state, screenshot, and optionally save a state.

Sibling of make_emerald_state.py, but parameterised rather than scripted: the
routes out of Black's opening bedroom were not known when this was written, so
the workflow is "press a few buttons, LOOK at the shot, press a few more".
Every invocation starts from --load, so a press list is a route from a fixed
origin and nothing accumulates across calls.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
      v2-experiments/make_black_probe_states.py --port 8264 \
      --load configs/saves/skyemu/black/emulator.state \
      --press D,D,D --shot local/addr-hunt/shots/a.png [--save out.state]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app.roms import load_roms  # noqa: E402
from find_map_id import machine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default="black")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--load", default=None)
    ap.add_argument("--states", default=None,
                    help="LABEL=path;LABEL=path... — load each in turn and print --read "
                         "for all of them in one emulator session. The direct check that "
                         "a candidate address really holds what the search says it does.")
    ap.add_argument("--press", default="", help="comma-separated U/D/L/R/A/B/START")
    ap.add_argument("--shot", default=None)
    ap.add_argument("--dump", default=None,
                    help="write the whole 4 MB of NDS main RAM to this .npy after the "
                         "presses, so a candidate list can be re-cut offline against the "
                         "black_mapid_pairwise cache without another emulator run")
    ap.add_argument("--save", default=None)
    ap.add_argument("--sequence", default=None,
                    help="semicolon-separated PRESSES=SHOT entries, each run from a fresh "
                         "/load of --load with --press prepended. One emulator boot for "
                         "the whole set, which is what makes look-then-press affordable.")
    ap.add_argument("--read", default=None,
                    help="comma-separated addr:fmt, e.g. 0x0208d094:u32 — read after the presses")
    args = ap.parse_args()

    import struct
    WIDTHS = {"u8": (1, "<B"), "u16": (2, "<H"), "u32": (4, "<I"), "s16": (2, "<h")}

    def do_reads(emu, spec):
        out = []
        for one in spec.split(","):
            a, _, f = one.strip().partition(":")
            f = f or "u8"
            n, fmt = WIDTHS[f]
            out.append(f"{int(a, 0):#010x}:{f}={struct.unpack(fmt, emu.read_memory(int(a, 0), n))[0]}")
        return "  ".join(out)

    rom = next((r for r in load_roms() if r.id == args.rom), None)
    if rom is None or not rom.exists():
        raise SystemExit(f"Unknown or missing rom {args.rom!r}")

    presses = [b.strip().upper() for b in args.press.split(",") if b.strip()]
    emu = machine(rom, args.port)
    emu.start_server()
    try:
        emu.wait_for_connection(timeout=300.0)
        if args.states:
            for entry in args.states.split(";"):
                label, _, path = entry.partition("=")
                emu.load_state(path)
                emu.wait_for_stable_screen()
                print(f"  {label:<12} {do_reads(emu, args.read)}")
            return 0
        if args.sequence:
            for entry in args.sequence.split(";"):
                more, _, shot = entry.partition("=")
                route = presses + [b.strip().upper() for b in more.split(",") if b.strip()]
                emu.load_state(args.load)
                emu.wait_for_stable_screen()
                if route:
                    emu.press_button_list(route)
                    emu.wait_for_stable_screen()
                reads = f"  {do_reads(emu, args.read)}" if args.read else ""
                if shot:
                    Path(shot).parent.mkdir(parents=True, exist_ok=True)
                    emu.capture_screenshot(preprocess=False).save(shot)
                print(f"  [{','.join(route) or 'origin'}] {shot or ''}{reads}")
            return 0
        emu.load_state(args.load)
        emu.wait_for_stable_screen()
        if presses:
            emu.press_button_list(presses)
            emu.wait_for_stable_screen()
        if args.read:
            print(f"  READ {do_reads(emu, args.read)}")
        if args.shot:
            Path(args.shot).parent.mkdir(parents=True, exist_ok=True)
            emu.capture_screenshot(preprocess=False).save(args.shot)
            print(f"  wrote {args.shot}")
        if args.dump:
            import numpy as np
            Path(args.dump).parent.mkdir(parents=True, exist_ok=True)
            np.save(args.dump, np.frombuffer(emu.read_memory(0x02000000, 0x400000),
                                             dtype=np.uint8))
            print(f"  wrote {args.dump}")
        if args.save:
            Path(args.save).parent.mkdir(parents=True, exist_ok=True)
            emu.save_state(args.save)
            print(f"  wrote {args.save}")
        return 0
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
