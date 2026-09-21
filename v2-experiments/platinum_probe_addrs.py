#!/usr/bin/env python3
"""Read a handful of named addresses across many labelled states.

Phase two of the Platinum battle-flag hunt: the 4 MB scan produces a shortlist,
this reads only the shortlist, which costs one HTTP round trip per 128 bytes
instead of 32,768 of them. Labels come from `--battle` / `--over`, which is
what the SCREENSHOTS said, and nothing here re-derives them.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.app.roms import load_roms            # noqa: E402
from src.emulator import make_emulator        # noqa: E402

WALKS = {"": [], "r": ["right"] * 3, "l": ["left"] * 3,
         "u": ["up"] * 3, "d": ["down"] * 3}


def machine(rom, port: int):
    stage = "local/platprobe/stage"
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
    ap.add_argument("--addr", action="append", required=True,
                    help="NAME=0xADDR:LEN")
    ap.add_argument("--battle", action="append", default=[], help="NAME=path")
    ap.add_argument("--over", action="append", default=[], help="NAME=path")
    ap.add_argument("--walks", action="store_true",
                    help="also sample each overworld state after 3 steps each way")
    ap.add_argument("--shots", action="store_true", help="save a screenshot per sample")
    args = ap.parse_args()

    probes = []
    for spec in args.addr:
        name, rest = spec.split("=", 1)
        a, n = rest.split(":")
        probes.append((name, int(a, 0), int(n)))

    rom = next(r for r in load_roms() if r.id == args.rom)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    emu = machine(rom, args.port)
    emu.start_server()
    rows = []
    try:
        emu.wait_for_connection(timeout=300.0)
        for kind, specs in (("battle", args.battle), ("over", args.over)):
            for spec in specs:
                name, path = spec.split("=", 1)
                walk = WALKS if (args.walks and kind == "over") else {"": []}
                for suffix, presses in walk.items():
                    tag = name + (f"_{suffix}" if suffix else "")
                    emu.load_state(path)
                    emu.wait_for_stable_screen()
                    if presses:
                        emu.press_button_list(presses)
                        emu.wait_for_stable_screen()
                    if args.shots:
                        emu.capture_screenshot().save(out / f"{tag}.png")
                    vals = {}
                    for pname, a, n in probes:
                        raw = emu.read_memory(a, n)
                        vals[pname] = raw.hex()
                    rows.append({"tag": tag, "kind": kind, "values": vals})
                    print(f"    {tag:16s} {kind:7s} " +
                          "  ".join(f"{k}={v}" for k, v in vals.items()))
    finally:
        try: emu.disconnect()
        except Exception: pass
    (out / "probe.json").write_text(json.dumps(rows, indent=2))
    print(f"  wrote {out/'probe.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
