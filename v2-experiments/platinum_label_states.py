#!/usr/bin/env python3
"""Load a list of savepoint states, screenshot each, and read the Location struct.

The labelling step for the Platinum battle-flag hunt. A savepoint's turn number
says NOTHING about what is on screen when that state is LOADED — savepoints lag
screenshots — so every label here comes from a shot taken after the load, read
by eye. Nothing in this file decides whether a state is a battle.
"""
from __future__ import annotations
import argparse, json, os, struct, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.app.roms import load_roms            # noqa: E402
from src.emulator import make_emulator        # noqa: E402

LOCATION = 0x0227F408


def machine(rom, port: int):
    stage = f"local/platlabel/stage"
    Path(stage).mkdir(parents=True, exist_ok=True)
    cfg = {"emulator": {"type": "skyemu", "host": "127.0.0.1", "port": port,
                        "binary_path": os.path.expanduser("~/Applications/SkyEmu.app/Contents/MacOS/SkyEmu"),
                        "rom_path": rom.path, "button_hold_frames": 12,
                        "frames_between_inputs": 24, "ab_hold_frames": 12,
                        "ab_gap_frames": 100, "wait_input_seconds": 5.0,
                        "boot_frames": 60, "step_rate": "fast", "rom_stage_dir": stage},
           "screenshot": {"grid_overlay": False},
           "valid_inputs": ["A", "B", "U", "D", "L", "R", "START", "SELECT"]}
    return make_emulator(cfg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default="platinum")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--state", action="append", required=True, help="NAME=path")
    ap.add_argument("--timing", action="store_true", help="time a 64KB read")
    args = ap.parse_args()

    rom = next(r for r in load_roms() if r.id == args.rom)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    emu = machine(rom, args.port)
    emu.start_server()
    rows = []
    try:
        emu.wait_for_connection(timeout=300.0)
        print(f"  system={emu.system}")
        if args.timing:
            for n in (1024, 16384, 131072):
                t = time.time(); emu.read_memory(0x02000000, n)
                dt = time.time() - t
                print(f"    read {n:>7} B in {dt:6.2f}s  -> {n/dt/1024:.0f} KB/s "
                      f"(4MB would be {4*1024*1024/(n/dt):.0f}s)")
        for spec in args.state:
            name, path = spec.split("=", 1)
            t = time.time()
            emu.load_state(path)
            emu.wait_for_stable_screen()
            emu.capture_screenshot().save(out / f"{name}.png")
            raw = emu.read_memory(LOCATION, 20)
            loc = struct.unpack("<5i", raw)
            rows.append({"name": name, "state": path, "location": loc})
            print(f"    {name:20s} map={loc[0]:5d} warp={loc[1]:3d} x={loc[2]:5d} "
                  f"z={loc[3]:5d} face={loc[4]}  ({time.time()-t:.1f}s)")
    finally:
        try: emu.disconnect()
        except Exception: pass
    (out / "labels.json").write_text(json.dumps(rows, indent=2))
    print(f"  wrote {out/'labels.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
