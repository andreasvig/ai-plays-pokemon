#!/usr/bin/env python3
"""Live, both-directions check of SoulSilver's battle/species word.

Reads FOUR bytes at 0x021D05C8 and the Location block at 0x0227D448 on the live
cartridge, once per probe, and saves the screenshot that goes with each reading.
Nothing here decides whether a probe is a battle — the PNG does, by eye. That is
deliberate: a verifier that labelled its own samples with the thing under test
would agree with itself everywhere.

    probe ::= NAME=state[:presses]        presses is a '+'-separated button list
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "v2-experiments"))
from find_battle_flag import machine  # noqa: E402
from src.app.roms import load_roms  # noqa: E402

SPECIES = 0x021D05C8
LOCATION = 0x0227D448


def main() -> int:
    port = int(sys.argv[1]); outdir = Path(sys.argv[2]); probes = sys.argv[3:]
    outdir.mkdir(parents=True, exist_ok=True)
    rom = next(r for r in load_roms() if r.id == "soulsilver")
    emu = machine(rom, port)
    emu.start_server()
    rows = []
    try:
        emu.wait_for_connection(timeout=300.0)
        for spec in probes:
            name, rest = spec.split("=", 1)
            state, _, presses = rest.partition(":")
            buttons = [b for b in presses.split("+") if b]
            t0 = time.time()
            emu.load_state(state)
            emu.wait_for_stable_screen()
            if buttons:
                emu.press_button_list(buttons)
                emu.wait_for_stable_screen()
            emu.capture_screenshot().save(outdir / f"{name}.png")
            w = emu.read_memory(SPECIES, 4)
            loc = emu.read_memory(LOCATION, 20)
            enemy = w[0] | (w[1] << 8)
            mine = w[2] | (w[3] << 8)
            mid = int.from_bytes(loc[0:4], "little")
            x = int.from_bytes(loc[8:12], "little")
            y = int.from_bytes(loc[12:16], "little")
            rows.append({"name": name, "state": state, "presses": buttons,
                         "enemy_species": enemy, "player_species": mine,
                         "map_id": mid, "x": x, "y": y})
            print(f"  {name:22s} enemy={enemy:5d} player={mine:5d} "
                  f"map={mid:4d} ({x},{y})  {time.time()-t0:4.1f}s")
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass
    (outdir / "readings.json").write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
