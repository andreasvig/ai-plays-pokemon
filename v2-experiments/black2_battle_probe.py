#!/usr/bin/env python3
"""Drive Black 2 one button-group at a time, reading the battle flag and both battlers.

    PYTHONPATH=.:v2-experiments ./venv/bin/python \\
      v2-experiments/black2_battle_probe.py PORT IN.state OUTDIR "DOWN" "RIGHT" "A" "A"

PORT is explicit and mandatory: other agents hold neighbouring ports and SkyEmu's
http_server exits quietly when it cannot bind, so the incumbent answers /ping and
the client silently drives the WRONG game.

Each argument after OUTDIR is a comma-separated press list; after each one the
tool saves a screenshot and a save state and prints position, the in-battle flag
and the two battle-mon blocks. It exists because ``find_battle_flag.py``'s
two-class collect cannot answer "on WHICH press did this flip?", and on Gen 5
that is the whole question: the battle OVERLAY id at 0x0209da80 both leads the
battle (set while the trainer is still talking) and lags it (still set six
presses into the overworld), so a candidate that looks perfect over two labelled
classes is off by several presses at both edges.

Addresses are the ones measured in local/battleflag/black2: flag 0x0213b2e0,
player's active battler 0x0225b1f0, opponent's 0x0225b414 (species u16 +0,
max HP +2, current HP +4, level +0xc — the
HP order was settled by landing a hit and watching +4 fall while +2 held). Species is only meaningful while the flag is set.
"""
import sys, struct
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO)); sys.path.insert(0,str(REPO/"v2-experiments"))
from find_battle_flag import machine
from src.app.roms import load_roms
SPEC=[0x0225b1f0,0x0225b414,0x0225b638,0x0225b85c]
port,src,outdir,segs=int(sys.argv[1]),sys.argv[2],Path(sys.argv[3]),sys.argv[4:]
outdir.mkdir(parents=True,exist_ok=True)
rom=next(r for r in load_roms() if r.id=="black2")
emu=machine(rom,port); emu.start_server()
def rd():
    mid,x,h,y=struct.unpack("<IiiI", emu.read_memory(0x0223B444,16))
    flag=emu.read_memory(0x0209da74,1)[0]
    sp=[struct.unpack("<HHHH", emu.read_memory(a,8)) for a in SPEC]
    return f"m{mid} {x>>16},{y>>16} flag={flag} | " + " | ".join(f"{a&0xffff:04x}:{s[0]}/{s[1]}/{s[2]}" for a,s in zip(SPEC,sp))
try:
    emu.wait_for_connection(timeout=300)
    emu.load_state(src); emu.wait_for_stable_screen()
    emu.capture_screenshot().save(outdir/"p00.png"); emu.save_state(str(outdir/"p00.state"))
    print("p00", rd(), flush=True)
    for i,seg in enumerate(segs,1):
        emu.press_button_list([b.strip() for b in seg.split(",") if b.strip()])
        emu.wait_for_stable_screen()
        emu.capture_screenshot().save(outdir/f"p{i:02d}.png"); emu.save_state(str(outdir/f"p{i:02d}.state"))
        print(f"p{i:02d} {seg:18s}", rd(), flush=True)
finally:
    try: emu.disconnect()
    except Exception: pass
