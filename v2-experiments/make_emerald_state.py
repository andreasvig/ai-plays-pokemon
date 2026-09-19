#!/usr/bin/env python3
"""Replay Pokemon Emerald's opening on SkyEmu, from cold boot to two start states.

P-B of `artifacts/skyemu-backend/cross-game-plan.md`, for the second game.
`configs/saves/emerald-truck/emulator.state` exists but is an **mGBA** savestate
and SkyEmu answers `/load -> failed`, exactly as it does for v1's FireRed start.
So the opening is not ported, it is replayed.

Two states come out, because they answer different questions:

  truck   Inside the moving van, before the game has asked anything of the
          player. The Emerald equivalent of FireRed's bedroom and what
          `configs/roms.yaml:42-46` already describes as the casual start.
  probe   Downstairs in the player's house in Littleroot, after Mom's greeting.
          The truck interior is five tiles wide with boxes on three sides, and
          `find_addresses.py` needs a tile that is free in all four directions
          for its null probes -- the truck cannot provide one.

What is reproducible about it
-----------------------------
* SkyEmu headless starts PAUSED and advances only via `/step`, so the sequence
  is measured in FRAMES and a slow machine replays the identical game.
* Nothing is typed. The name field is confirmed EMPTY with START and Emerald
  substitutes its own default, **TERRY** -- read back off the screen in Mom's
  first line, not assumed.
* The gender prompt's cursor starts on BOY, so the A-mash takes Brendan.
* The ROM is copied to a scratch directory. A `.sav` beside it would put
  CONTINUE on the title screen and desynchronise every press after it.

Two things that had to be found by experiment, and neither is guessable
----------------------------------------------------------------------
* **The truck's exit is three tiles to the RIGHT.** Down, left and up are all
  boxes. Found by walking every offset from -3 to +3 and watching the mean frame
  brightness, which goes 19.8 -> 159.5 the moment the scene becomes outdoors.
  That brightness test is kept below as the verification, because at the time it
  ran no address on this cartridge was known -- there was nothing else to read.
* **The intro is about 45 A presses long**, not the dozen FireRed needs. Birch's
  speech runs to "All right. What's your name?" and each press advances one line.

Usage
-----
    PYTHONPATH=. ./venv/bin/python v2-experiments/make_emerald_state.py \
        --out v2-experiments/states/emerald --port 8198
"""
from __future__ import annotations

import argparse
import io
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "v2-experiments" / "harness"))

from skyemu import SkyEmu  # noqa: E402

DEFAULT_ROM = REPO / "roms" / "Pokemon - Emerald Version (USA, Europe).gba"
DEFAULT_OUT = REPO / "v2-experiments" / "states" / "emerald"

# ("a"|"start"|"right"|..., n) presses that button n times; ("wait", n) advances
# n frames with nothing held. One press is hold 12 + gap 24 = 36 frames.
#
# Waits are generous on purpose. The whole replay is under a minute of wall
# clock, and a wait 100 frames too short becomes a press landing on the wrong
# screen fifty steps later.

TO_TRUCK: list[tuple[str, int]] = [
    # 1. Cold boot, the Game Freak logo, the Rayquaza intro, the title screen.
    ("wait", 1800),
    # 2. Title -> the "NEW GAME" flow. With no save data there is no menu to
    #    choose from, so one START is enough to leave the title.
    ("start", 1), ("wait", 240),
    # 3. Birch's whole speech, the gender prompt (cursor starts on BOY) and on
    #    to "All right. What's your name?". One press per line; 45 covers it
    #    with room to spare, and the extra presses land harmlessly on the next
    #    line of the same speech.
    *[step for _ in range(45) for step in (("a", 1), ("wait", 90))],
    # 4. Into the naming screen, then START to accept it EMPTY. Emerald fills in
    #    its own default rather than refusing, the same verb FireRed uses.
    ("a", 1), ("wait", 150),
    ("start", 1), ("wait", 150),
    # 5. "So it's TERRY?" and the rest of the intro, down into the truck.
    *[step for _ in range(24) for step in (("a", 1), ("wait", 120))],
    ("wait", 900),
    # The A-mash overshoots into the truck's scenery -- the last few presses
    # examine a box and leave "The box is printed with a POKeMON logo." on
    # screen. B closes it. Without this the state is saved MID-DIALOGUE and the
    # three rights below are swallowed by the text box instead of walking out,
    # which is exactly what happened on the first run: two identical states.
    ("b", 2), ("wait", 120),
]

# From inside the truck to standing in the house with control. The three rights
# are the exit; the A presses are Mom's greeting outside and again indoors.
TRUCK_TO_PROBE: list[tuple[str, int]] = [
    ("b", 2), ("wait", 120),
    ("right", 3), ("wait", 300),
    *[step for _ in range(24) for step in (("a", 1), ("wait", 120))],
    ("wait", 600),
]

# Mean RGB of the TOP 100 rows. Crude on purpose: it is the only oracle
# available on a cartridge whose addresses are not known yet, and it only has to
# separate two very different scenes.
#
# The top rows, not the whole frame, because a dialogue box is a big pale
# rectangle across the bottom third and it moves the full-frame mean by more
# than the scene does -- the truck reads 19.8 with no box and 76.9 with one,
# which straddles any threshold that would separate the truck from the house
# (110.6). Measured over rows 0..100 the same two frames both read 26.7, against
# 86.5 indoors and 136.0 outdoors.
TRUCK_MAX_BRIGHTNESS = 50.0
LEFT_TRUCK_MIN_BRIGHTNESS = 60.0
SCENE_ROWS = 100


def brightness(png: bytes) -> float:
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    px = list(im.crop((0, 0, im.width, SCENE_ROWS)).getdata())
    return sum(sum(p) for p in px) / (3 * len(px))


def frame_cost(sequence) -> int:
    return sum(n if verb == "wait" else n * 36 for verb, n in sequence)


def play(emu: SkyEmu, sequence, label: str) -> int:
    frames = 0
    for i, (verb, n) in enumerate(sequence):
        if verb == "wait":
            emu.step(n)
            frames += n
        else:
            for _ in range(n):
                emu.press(verb)
                frames += 36
        if i % 25 == 0 or i == len(sequence) - 1:
            print(f"  {label} {i + 1}/{len(sequence)}  {frames} frames", flush=True)
    return frames


def can_walk(emu: SkyEmu, state: Path) -> dict:
    """Do the four directions do anything from this state?

    Not "does x change" -- no address on this cartridge is known when this runs.
    Just whether the frame differs from standing still, which is enough to tell
    a state with control from one behind a text box.
    """
    out = {}
    emu.load_state(state)
    emu.step(40)
    still = emu.screen()
    for name, button in (("R", "right"), ("L", "left"), ("U", "up"), ("D", "down")):
        emu.load_state(state)
        emu.step(40)
        for _ in range(3):
            emu.press(button)
        emu.step(60)
        out[name] = emu.screen() != still
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--port", type=int, default=8198)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"planned frames: {frame_cost(TO_TRUCK)} to the truck, "
          f"{frame_cost(TRUCK_TO_PROBE)} more to the probe state")
    wall0 = time.time()
    failures = []

    with tempfile.TemporaryDirectory(prefix="emerald-coldboot-") as tmp:
        rom = Path(tmp) / "emerald.gba"
        shutil.copy2(args.rom, rom)
        with SkyEmu(rom, port=args.port) as emu:
            play(emu, TO_TRUCK, "intro")
            truck_png = emu.screen()
            emu.save_state(args.out / "truck.state")
            (args.out / "truck.png").write_bytes(truck_png)
            b = brightness(truck_png)
            print(f"\ntruck state: brightness {b:.1f} (want < {TRUCK_MAX_BRIGHTNESS})")
            if b >= TRUCK_MAX_BRIGHTNESS:
                failures.append("the truck state is not the truck interior")

            play(emu, TRUCK_TO_PROBE, "probe")
            probe_png = emu.screen()
            emu.save_state(args.out / "probe.state")
            (args.out / "probe.png").write_bytes(probe_png)
            b = brightness(probe_png)
            print(f"probe state: brightness {b:.1f} (want > {LEFT_TRUCK_MIN_BRIGHTNESS})")
            if b <= LEFT_TRUCK_MIN_BRIGHTNESS:
                failures.append("the probe state never left the truck")

            walk = can_walk(emu, args.out / "probe.state")
            print(f"probe state responds to: "
                  f"{' '.join(k for k, v in walk.items() if v) or 'NOTHING'}")
            if not all(walk.values()):
                failures.append(
                    "the probe state is not free in all four directions "
                    f"({walk}) -- find_addresses.py needs it to be")

    print(f"\nwall clock: {time.time() - wall0:.1f}s")
    print(f"wrote {args.out}/truck.state, probe.state and their previews")
    for f in failures:
        print(f"FAIL: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
