#!/usr/bin/env python3
"""Load every savepoint state and screenshot it, so each can be LABELLED by eye.

A turn number is not a label: savepoints lag screenshots (Crystal's turn_120).
This loads the state itself and captures what the state shows.
"""
from __future__ import annotations
import sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "v2-experiments"))

from find_battle_flag import machine, outdir  # noqa: E402
from src.app.roms import load_roms  # noqa: E402


def main() -> int:
    port = int(sys.argv[1])
    states = sys.argv[2:]
    rom = next(r for r in load_roms() if r.id == "soulsilver")
    out = outdir("soulsilver") / "label"
    out.mkdir(parents=True, exist_ok=True)
    emu = machine(rom, port)
    emu.start_server()
    try:
        emu.wait_for_connection(timeout=300.0)
        print("pid", getattr(emu, "proc", None) and emu.proc.pid)
        for s in states:
            name = Path(s).parent.name if Path(s).name == "emulator.state" else Path(s).stem
            t0 = time.time()
            emu.load_state(s)
            emu.wait_for_stable_screen()
            emu.capture_screenshot().save(out / f"{name}.png")
            print(f"  {name:12s} {time.time()-t0:5.1f}s")
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
