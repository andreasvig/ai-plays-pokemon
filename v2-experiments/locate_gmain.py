#!/usr/bin/env python3
"""Locate Gen-3 ``gMain`` on the cartridge itself, without trusting a published symbol.

``find_battle_flag.py`` narrows the in-battle bit to a few dozen IWRAM bits by
behaviour alone. Choosing among them by quoting somebody's ``pokeemerald.sym``
would make the answer only as good as the quote. This script instead measures
the one thing about ``struct Main`` that leaves a fingerprint in a second,
independent piece of hardware state:

    gMain.oamBuffer is a 1 KB OAM shadow at gMain+0x38, DMA'd to OAM
    (0x07000000) every vblank.

So: read OAM, search IWRAM for those same 1024 bytes, and the hit minus 0x38 is
gMain's base. Two further checks come for free from the struct's own shape —
gMain+0x00..0x1B are seven callback pointers, which on a GBA must read as
0x08xxxxxx (ROM) or zero, and gMain+0x438 is ``state``.

The byte one past that, gMain+0x439, is the bitfield whose bit 1 is
``inBattle``.
"""
from __future__ import annotations

import argparse, os, sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.app.roms import load_roms            # noqa: E402
from src.emulator import make_emulator        # noqa: E402

OAM, OAM_LEN, OAM_OFF_IN_MAIN = 0x07000000, 0x400, 0x38
IWRAM, IWRAM_LEN = 0x03000000, 0x8000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True)
    ap.add_argument("--state", action="append", required=True)
    ap.add_argument("--port", type=int, default=8265)
    args = ap.parse_args()
    rom = next(r for r in load_roms() if r.id == args.rom)
    stage = f"local/battleflag/{rom.id}/stage"
    Path(stage).mkdir(parents=True, exist_ok=True)
    cfg = {"emulator": {"type": "skyemu", "host": "127.0.0.1", "port": args.port,
                        "binary_path": os.path.expanduser("~/Applications/SkyEmu.app/Contents/MacOS/SkyEmu"),
                        "rom_path": rom.path, "button_hold_frames": 12,
                        "frames_between_inputs": 24, "ab_hold_frames": 12,
                        "ab_gap_frames": 100, "wait_input_seconds": 5.0,
                        "boot_frames": 60, "step_rate": "fast", "rom_stage_dir": stage},
           "screenshot": {"grid_overlay": False},
           "valid_inputs": ["A", "B", "U", "D", "L", "R", "START", "SELECT"]}
    emu = make_emulator(cfg)
    emu.start_server()
    try:
        emu.wait_for_connection(timeout=300.0)
        hits_per_state = []
        for spec in args.state:
            name, path = spec.split("=", 1) if "=" in spec else (spec, spec)
            emu.load_state(path)
            emu.wait_for_stable_screen()
            oam = emu.read_memory(OAM, OAM_LEN)
            iw = emu.read_memory(IWRAM, IWRAM_LEN)
            hits = []
            start = 0
            while True:
                i = iw.find(oam, start)
                if i < 0:
                    break
                hits.append(IWRAM + i - OAM_OFF_IN_MAIN)
                start = i + 1
            print(f"  [{name}] OAM found at gMain candidate(s): "
                  f"{[hex(h) for h in hits] or 'none'}")
            # An exact match needs the read to land before any sprite moved
            # since the vblank DMA. When it does not, the shadow is still the
            # closest thing in IWRAM to OAM by a mile — so report the best
            # agreeing 4-byte-aligned window rather than nothing.
            a = np.frombuffer(iw, dtype=np.uint8)
            o = np.frombuffer(oam, dtype=np.uint8)
            best, bestn = None, -1
            for off in range(0, IWRAM_LEN - OAM_LEN + 1, 4):
                n = int((a[off:off + OAM_LEN] == o).sum())
                if n > bestn:
                    best, bestn = off, n
            print(f"      best-agreeing window: {IWRAM + best - OAM_OFF_IN_MAIN:#010x} "
                  f"({bestn}/{OAM_LEN} bytes equal to OAM)")
            if not hits and bestn >= OAM_LEN * 0.9:
                hits = [IWRAM + best - OAM_OFF_IN_MAIN]
            for base in hits:
                ptrs = np.frombuffer(iw[base - IWRAM: base - IWRAM + 28], dtype="<u4")
                ok = all(p == 0 or (p >> 24) == 0x08 for p in ptrs)
                st = iw[base - IWRAM + 0x438]
                flag = iw[base - IWRAM + 0x439]
                print(f"      {base:#010x}: callbacks={[hex(int(p)) for p in ptrs]} "
                      f"rom_or_zero={ok}  state(+0x438)={st:#04x}  "
                      f"bitfield(+0x439)={flag:#04x} at {base + 0x439:#010x}")
            hits_per_state.append(set(hits))
        common = set.intersection(*hits_per_state) if hits_per_state else set()
        print(f"\n  gMain base(s) agreeing across all {len(args.state)} states: "
              f"{[hex(h) for h in sorted(common)] or 'none'}")
        for b in sorted(common):
            print(f"    -> in-battle byte gMain+0x439 = {b + 0x439:#010x}")
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
