#!/usr/bin/env python3
"""The contract, END TO END, on labelled states: real backend, real spec, real decoder.

Everything else in this family scores an ADDRESS against a screenshot. This
scores the shipped ``GameMemory`` — ``contracts.attach`` sets the spec, the
backend samples it, ``trace.decode_samples`` reads it back — because the three
can each be right while the join between them is wrong: a sample index is a
position in the spec list, and widening one range renumbers everything after it.

Each case names what its frame SHOWS, so a pass is agreement with the screen
and not with another byte.

It also exercises the POINTER form of the spec grammar on an NDS, which did not
work at all before 2026-09-20: the backend bounded every dereference to the
GBA's EWRAM window (top 0x02040000) and every DS heap pointer is above it.
``*<battle proc>+0x0c`` now lands on the BATTLE_SETUP_PARAM, whose first word is
the competitor — 1 trainer, 0 wild — so the chase and the shipped raw
``battle_kind`` are two independent routes to the same fact, and this asserts
they agree on every case.

    PYTHONPATH=.:v2-experiments ./venv/bin/python \\
      v2-experiments/gen5_contract_verify.py local/gen5-battle 8456 black
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v2-experiments"))
from find_map_id import machine
from src.app.roms import load_roms
from src.referee import contracts
from src.referee.trace import decode_samples

SD = Path(sys.argv[1]); port = int(sys.argv[2]); which = sys.argv[3]
R = ROOT / "local/runs/2026-09-20_01-40-28_config-v2-black__gemini-3-8-flash-minimal"
R2 = ROOT / "local/runs/2026-09-20_00-51-00_config-v2-black2__gemini-3-8-flash-minimal"
CASES = {
 "black": ("black", [
   (R/"savepoints/turn_10/emulator.state",  "Bianca's Snivy Lv5",    True, "trainer", 495, 5),
   (R/"savepoints/turn_130/emulator.state", "N's Purrloin Lv7",      True, "trainer", 509, 7),
   (R/"savepoints/turn_212/emulator.state", "Youngster's Patrat Lv7",True, "trainer", 504, 7),
   (R/"savepoints/turn_60/emulator.state",  "wild Lillipup Lv2",     True, "wild",    506, 2),
   (SD/"hunt70e/w01.state",                 "wild Lillipup Lv3",     True, "wild",    506, 3),
   (SD/"hunt210/w02.state",                 "wild Patrat, Route 2",  True, "wild",    504, None),
   (R/"savepoints/turn_50/emulator.state",  "overworld, Nuvema",     False, None, None, None),
   (R/"savepoints/turn_140/emulator.state", "overworld, Accumula",   False, None, None, None),
 ]),
 "black2": ("black2", [
   (R2/"savepoints/turn_170/emulator.state","Hugh, end of battle",   True, "trainer", 501, 5),
   (R2/"savepoints/turn_160/emulator.state","overworld, Aspertia",   False, None, None, None),
   (R2/"savepoints/turn_100/emulator.state","overworld, Aspertia",   False, None, None, None),
 ]),
}
romid, cases = CASES[which]
rom = next(r for r in load_roms() if r.id == romid)
emu = machine(rom, port); emu.start_server()
bad = 0
try:
    emu.wait_for_connection(timeout=300)
    contract = contracts.attach(emu, contracts.contract_for(f"{romid}-us"))
    print(f"  console measured as {emu.system}, pointer window "
          f"{tuple(hex(v) for v in emu.pointer_window)}")
    chase = {"black": "*0x0226976c+0:4", "black2": "*0x022572a0+0:4"}[romid]
    for state, label, in_battle, kind, species, level in cases:
        emu.load_state(str(state)); emu.wait_for_stable_screen()
        samples = [emu._read_spec_entry(e) for e in emu.trace_spec]
        d = decode_samples([("x", samples)], contract=contract)[0]
        want = {"in_battle": in_battle, "battle_kind": kind, "foe_species": species,
                "foe_level": level}
        got = {k: d[k] for k in want}
        if level is None:
            want.pop("foe_level"); got.pop("foe_level")
        ok = got == want
        # The pointer chase, on the same loaded state. In battle it must reach
        # the setup param and agree with the raw kind; out of battle the proc is
        # gone and the chase means nothing, so it is only checked in battle.
        raw = emu._read_spec_entry(chase)
        if in_battle:
            chased = "trainer" if int.from_bytes(raw, "little") else "wild"
            if len(raw) != 4 or chased != kind:
                ok = False
                print(f"      chase {chase} gave {raw.hex() or '(empty)'} -> {chased}")
        bad += not ok
        print(f"  {'OK ' if ok else 'BAD'} {label:26s} {got}"
              + (f"  chase={raw.hex()}" if in_battle else ""))
finally:
    try: emu.disconnect()
    except Exception: pass
print("FAILURES", bad)
