#!/usr/bin/env python3
"""Find a route that crosses a map boundary, without knowing where the map is.

`find_addresses.py --stage map` needs an `--out-route`: a press sequence that
leaves the current map. Those have been hand-found by screenshot
(`p-a-results.md` §6.3), which is an operator step, does not scale to four DS
games, and fails on any room whose exit is not obvious from one frame — the
Platinum opening puts an NPC in the road north and starts a conversation with
anyone who walks into them.

The signal this uses instead is a side effect nobody has to look up. **Loading a
map reallocates the game's heap.** Walking one tile in Platinum changes about 25
bytes of its 4 MB of main RAM (measured); crossing into another map changes tens
of thousands. Three orders of magnitude apart, so the test needs no threshold
tuning and no knowledge of the cartridge — which means it works the same on
Black 2, where nothing is documented, as on a game we could have checked by eye.

Each candidate starts from a fresh `/load` of the same state, so a route that
walks into a wall or into a conversation cannot displace the ones after it.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
        v2-experiments/scout_transition.py --rom platinum \
        --state v2-experiments/states/platinum/probe.state --port 8250
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app.roms import load_roms  # noqa: E402
from src.emulator import make_emulator  # noqa: E402

# A SAMPLE of work RAM, not all of it — but SPREAD, not the first slice.
#
# The first version took one contiguous 256 KB window at the bottom of NDS main
# RAM and reported 0 bytes changed for forty routes, several of which provably
# walked the player across the screen. That window is static: it holds loaded
# code and fixed data, and the game's mutable state lives higher up (Platinum's
# position candidates sit around 0x021b-0x0234). A contiguous sample assumes
# work RAM is uniform, and no work RAM is uniform.
#
# Striping fixes it for the same money: CHUNK bytes every STRIDE bytes covers
# the whole space at a sixteenth of the wire cost. A heap reallocation touches
# memory everywhere, so every stripe sees it; a single tile of walking touches
# almost nothing, so almost no stripe does. That gap is the whole method.
REGION = {"NDS": (0x02000000, 0x400000), "GBA": (0x02000000, 0x40000), "GB": (0xC000, 0x2000)}
CHUNK, STRIDE = 4096, 65536
DIRS = ["up", "down", "left", "right"]


def machine(rom, port: int):
    stage = f"local/scout/{rom.id}"
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


def routes(max_legs: int, lengths: list[int]) -> list[list[str]]:
    """One-leg routes first, then two-leg. Ordered shortest-first so the cheapest
    answer is found first and the search can stop there — a short route is also
    a BETTER answer, because every extra press is another chance for an NPC to
    start talking in the middle of a probe."""
    out = [[d] * n for n in lengths for d in DIRS]
    if max_legs >= 2:
        for n1, n2 in itertools.product(lengths, repeat=2):
            for d1, d2 in itertools.product(DIRS, repeat=2):
                if d1 == d2 or DIRS.index(d1) // 2 == DIRS.index(d2) // 2:
                    continue  # same axis: that is just a longer one-leg route
                out.append([d1] * n1 + [d2] * n2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rom", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--port", type=int, default=8250)
    ap.add_argument("--lengths", default="3,6,10")
    ap.add_argument("--legs", type=int, default=2)
    ap.add_argument("--stop-after", type=int, default=3,
                    help="stop once this many transitions are found")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    rom = next((r for r in load_roms() if r.id == args.rom), None)
    if rom is None or not rom.exists():
        raise SystemExit(f"Unknown or missing rom {args.rom!r}")
    lengths = [int(n) for n in args.lengths.split(",")]

    emu = machine(rom, args.port)
    emu.start_server()
    found: list[dict] = []
    try:
        emu.wait_for_connection(timeout=300.0)
        emu.load_state(args.state)
        emu.wait_for_stable_screen()
        base, span = REGION[emu.system]
        stride = min(STRIDE, span)
        chunk = min(CHUNK, stride)
        windows = [(a, chunk) for a in range(base, base + span, stride)]

        def sample() -> bytes:
            return b"".join(emu.read_memory(a, n) for a, n in windows)

        origin = sample()
        print(f"  {rom.name} on {emu.system}: {len(windows)} stripes of {chunk // 1024} KB "
              f"across {span // 1024} KB ({len(origin) // 1024} KB sampled)")

        plan = routes(args.legs, lengths)
        print(f"  {len(plan)} candidate routes\n")
        for i, r in enumerate(plan, 1):
            t0 = time.time()
            emu.load_state(args.state)
            emu.wait_for_stable_screen()
            emu.press_button_list(r)
            emu.wait_for_stable_screen()
            after = sample()
            delta = sum(1 for a, b in zip(origin, after) if a != b)
            pct = 100.0 * delta / len(origin)
            tag = ""
            if pct > 5.0:
                tag = "  <<< MAP CHANGED"
                found.append({"route": r, "changed_bytes": delta, "pct": round(pct, 1)})
            print(f"  [{i:>3}/{len(plan)}] {','.join(r):<34} {delta:>7} B ({pct:5.1f}%) "
                  f"{time.time()-t0:4.1f}s{tag}")
            if len(found) >= args.stop_after:
                print("\n  enough — stopping the search")
                break
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass

    print()
    if not found:
        print("  No route crossed a map boundary. Widen --lengths or --legs, or start "
              "from a state nearer an exit.")
    for f in found:
        print(f"  --out-route {','.join('UDLR'['up down left right'.split().index(d)] for d in f['route'])}"
              f"   ({f['pct']}% of the sample changed)")
    if args.json:
        Path(args.json).write_text(json.dumps(found, indent=2))
    return 0 if found else 2


if __name__ == "__main__":
    sys.exit(main())
