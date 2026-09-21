#!/usr/bin/env python3
"""Press a scripted list one button at a time, screenshotting and reading probes.

The negative-class generator for the Platinum battle flag. The two-class corpus
(battle vs plain overworld) cannot tell an IN-BATTLE byte from a
"the field map is not the thing on screen" byte, because it contains no
full-screen menu, no party screen and no bag. This drives the game through them
and reads the candidate addresses at every press, so the label comes from the
SHOT and the value comes from the same instant.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.app.roms import load_roms            # noqa: E402
from src.emulator import make_emulator        # noqa: E402


def machine(rom, port: int):
    stage = "local/platwalk/stage"
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
    ap.add_argument("--state", required=True)
    ap.add_argument("--presses", required=True, help="comma list")
    ap.add_argument("--out", required=True)
    ap.add_argument("--addr", action="append", required=True, help="NAME=0xADDR:LEN")
    ap.add_argument("--save-states", action="store_true")
    args = ap.parse_args()

    probes = []
    for spec in args.addr:
        name, rest = spec.split("=", 1)
        a, n = rest.split(":")
        probes.append((name, int(a, 0), int(n)))
    presses = [p.strip() for p in args.presses.split(",") if p.strip()]

    rom = next(r for r in load_roms() if r.id == args.rom)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    emu = machine(rom, args.port)
    emu.start_server()
    rows = []
    try:
        emu.wait_for_connection(timeout=300.0)
        emu.load_state(args.state)
        emu.wait_for_stable_screen()
        seq = [None] + presses
        for i, btn in enumerate(seq):
            if btn is not None:
                emu.press_button_list([btn])
                emu.wait_for_stable_screen()
            emu.capture_screenshot().save(out / f"step_{i:02d}.png")
            if args.save_states:
                emu.save_state(str(out / f"step_{i:02d}.state"))
            vals = {n: emu.read_memory(a, ln).hex() for n, a, ln in probes}
            rows.append({"step": i, "button": btn, "values": vals})
            print(f"    step {i:02d} {str(btn):6s} " +
                  "  ".join(f"{k}={v}" for k, v in vals.items()))
    finally:
        try: emu.disconnect()
        except Exception: pass
    (out / "walk.json").write_text(json.dumps(rows, indent=2))
    print(f"  wrote {out/'walk.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
