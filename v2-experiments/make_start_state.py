#!/usr/bin/env python3
"""Re-create the canonical FireRed start state on SkyEmu, from cold boot.

v1's start (`configs/saves/pokebench-v1/emulator.state`) is an mGBA savestate and
SkyEmu refuses it (`/load` -> `failed`). So the start is not ported, it is
**replayed**: this script boots the ROM headless, plays the intro with a fixed
input sequence, and saves the state the benchmark starts from — the player
standing on the rug in the upstairs bedroom of the house in Pallet Town
(`PalletTown_PlayersHouse_2F`, map 4:1), facing the PC.

Why it is reproducible
----------------------
* SkyEmu headless starts PAUSED and advances only via `/step`, so the sequence
  below is measured in FRAMES, not wall-clock. A slow machine and a fast one
  replay the identical game.
* Nothing is typed. Both naming screens are confirmed EMPTY with START, which
  makes the game substitute its own hardcoded defaults: **KAY** for the player
  and **GREEN** for the rival. Verified constant across three different frame
  offsets into the naming screen, so it is a constant and not an RNG draw.
* The gender prompt's cursor starts on BOY, so one A takes Red.
* The ROM is copied to a scratch directory first. A `.sav` (battery SRAM) next
  to the ROM makes the title screen offer CONTINUE and derails every press after
  it — and `roms/` in this repo does ship a FireRed `.sav`. The copy is what
  makes "cold boot" mean cold boot.

Usage
-----
    PYTHONPATH=. ./venv/bin/python v2-experiments/make_start_state.py \
        --out configs/saves/skyemu/firered-pokebench-v2 --port 8123
"""
from __future__ import annotations

import argparse
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "v2-experiments" / "harness"))

from skyemu import SkyEmu  # noqa: E402

DEFAULT_ROM = REPO / "roms" / "Pokemon - FireRed Version (USA, Europe) (Rev 1).gba"
DEFAULT_OUT = REPO / "configs" / "saves" / "skyemu" / "firered-pokebench-v2"

# --- the referee's own addresses (src/referee/referee.py) ---------------------
GSAVEBLOCK1_PTR = 0x03005008
SB1_PLAYER_X, SB1_PLAYER_Y = 0x0000, 0x0002   # s16
SB1_MAP_GROUP, SB1_MAP_NUM = 0x0004, 0x0005   # u8
PLAYER_PARTY_COUNT = 0x02024029               # u8, fixed EWRAM

# --- the sequence ------------------------------------------------------------
# ("a"|"b"|"start"|"up"|"down"|... , n) presses that button n times.
# ("wait", n) advances n frames with nothing held.
# One press is hold 12 + gap 24 = 36 frames (skyemu.SkyEmu.press defaults).
#
# Every wait here is generous on purpose: the cost of the whole run is ~25 s of
# wall clock, and a wait that is 100 frames too short turns into a press landing
# on the wrong screen fifty steps later.
SEQUENCE: list[tuple[str, int]] = [
    # 1. Cold boot -> Game Freak logo -> the Charizard intro -> title screen.
    ("wait", 1800),

    # 2. Title screen -> the blue CONTROLS pages. FireRed with no save data goes
    #    straight into its instruction pages; there is no NEW GAME / OPTION menu
    #    to pick from, so A is all that is needed here.
    ("a", 1), ("wait", 360),

    # 3. Six blue instruction pages ("The various buttons will be explained...",
    #    ... "Press the A Button, and let your adventure begin!").
    ("a", 1), ("wait", 60),
    ("a", 1), ("wait", 60),
    ("a", 1), ("wait", 120),

    # 4. Oak appears. His speech up to "But first, tell me a little about you":
    #    Welcome / My name is OAK / the POKéMON PROFESSOR / this world is
    #    inhabited by creatures / for some people POKéMON are pets / I study...
    ("a", 1), ("wait", 90), ("a", 1), ("wait", 90), ("a", 1), ("wait", 90),
    ("a", 1), ("wait", 90), ("a", 1), ("wait", 90), ("a", 1), ("wait", 90),
    ("a", 1), ("wait", 90), ("a", 1), ("wait", 90), ("a", 1), ("wait", 90),
    ("a", 1), ("wait", 90), ("a", 1), ("wait", 90), ("a", 1), ("wait", 90),

    # 5. "Are you a boy? Or are you a girl?" -> the cursor starts on BOY, so the
    #    first A of this block takes Red. The rest run out Oak's follow-up to
    #    "Let's begin with your name. What is it?".
    #    (Two presses per beat here: the gender menu and the boxes after it
    #    advance on a shorter cadence than Oak's opening monologue.)
    ("a", 2), ("wait", 60), ("a", 2), ("wait", 60), ("a", 2), ("wait", 60),
    ("a", 2), ("wait", 60), ("a", 2), ("wait", 60), ("a", 2), ("wait", 60),
    ("a", 2), ("wait", 60), ("a", 2), ("wait", 60), ("a", 2), ("wait", 60),

    # 6. Player naming screen (the keyboard, empty). The player gets NO preset
    #    list in FireRed — only the rival does — so the default is taken by
    #    pressing START (= OK) on an empty name: the game fills in KAY.
    ("a", 1), ("wait", 90),
    ("start", 1), ("wait", 150),

    # 7. "So your name is KAY." -> YES -> "This is my grandson... he's been your
    #    rival since you both were babies" -> the rival's preset menu, whose
    #    cursor starts on NEW NAME -> the keyboard, empty.
    ("a", 1), ("wait", 90), ("a", 1), ("wait", 90), ("a", 1), ("wait", 90),
    ("a", 1), ("wait", 90), ("a", 1), ("wait", 90), ("a", 1), ("wait", 90),
    ("a", 1), ("wait", 90), ("a", 1), ("wait", 90),

    # 8. Same trick for the rival: START on an empty name -> GREEN.
    ("start", 1), ("wait", 150),

    # 9. "...Er, was it GREEN?" -> YES -> "Your very own POKéMON legend is about
    #    to unfold!" -> the player shrinks into the overworld and the bedroom
    #    fades in.
    ("a", 1), ("wait", 120), ("a", 1), ("wait", 120), ("a", 1), ("wait", 120),
    ("a", 1), ("wait", 120), ("a", 1), ("wait", 120), ("a", 1), ("wait", 120),
    ("a", 1), ("wait", 120), ("a", 1), ("wait", 120),

    # 10. Let the fade-in and the map load finish. No A past this point: the
    #     player spawns facing the NES, and one more A plays it ("KAY played
    #     with the NES.") and leaves a text box on screen in the saved state.
    ("wait", 600),
]


def frame_cost(sequence: list[tuple[str, int]], hold: int = 12, gap: int = 24) -> int:
    return sum(n if verb == "wait" else n * (hold + gap) for verb, n in sequence)


def read_position(emu: SkyEmu) -> dict:
    """Player x/y + map, through the referee's own address map.

    `gSaveBlock1Ptr` is dereferenced fresh every time: FireRed DMA-shuffles the
    save blocks, so the pointer really does move between reads (observed here:
    0x0202552c at boot, 0x02025594 in the bedroom).
    """
    ptr = struct.unpack("<I", emu.read_memory(GSAVEBLOCK1_PTR, 4))[0]
    if not (0x02000000 <= ptr < 0x02040000):
        return {"sb1_ptr": ptr, "in_game": False}
    block = emu.read_memory(ptr, 8)
    x, y, group, num = struct.unpack_from("<hhBB", block, 0)
    return {
        "sb1_ptr": ptr, "in_game": True,
        "x": x, "y": y, "map_group": group, "map_num": num,
        "party_count": emu.read_memory(PLAYER_PARTY_COUNT, 1)[0],
    }


def play(emu: SkyEmu, sequence: list[tuple[str, int]], verbose: bool = True) -> int:
    frames = 0
    for i, (verb, n) in enumerate(sequence):
        if verb == "wait":
            emu.step(n)
            frames += n
        else:
            for _ in range(n):
                emu.press(verb)
                frames += 36
        if verbose and (i % 10 == 0 or i == len(sequence) - 1):
            print(f"  step {i + 1}/{len(sequence)}  {frames} frames", flush=True)
    return frames


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--log", type=Path, default=None)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    state_path = args.out / "emulator.state"
    preview_path = args.out / "preview.png"

    print(f"planned frames: {frame_cost(SEQUENCE)}")
    wall0 = time.time()
    with tempfile.TemporaryDirectory(prefix="firered-coldboot-") as tmp:
        # The copy is the cold boot: a `.sav` beside the ROM would put CONTINUE
        # on the title screen and desynchronise everything after step 2.
        rom = Path(tmp) / "firered.gba"
        shutil.copy2(args.rom, rom)
        with SkyEmu(rom, port=args.port, log=args.log) as emu:
            frames = play(emu, SEQUENCE)
            pos = read_position(emu)
            emu.save_state(state_path)
            preview_path.write_bytes(emu.screen())
    wall = time.time() - wall0

    print(f"\nframes: {frames}")
    print(f"wall clock: {wall:.1f}s  ({frames / wall:.0f} frames/s)")
    print(f"position: {pos}")
    print(f"wrote {state_path}")
    print(f"wrote {preview_path}")
    if not (pos.get("map_group"), pos.get("map_num")) == (4, 1):
        print("WARNING: not on PalletTown_PlayersHouse_2F (4:1) — the sequence drifted")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
