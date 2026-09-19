#!/usr/bin/env python3
"""Find a game's map id from TWO STATES on different maps — no route needed.

`find_addresses.py --stage map` finds the map id by walking a ROUTE that leaves
the current map and back again. That works, and it has one hard prerequisite:
somebody has to know a route. On Platinum the opening gates the player in a
bedroom behind a cutscene, the road north is blocked by an NPC who starts
talking to anyone who walks into them, and forty scouted routes from an outdoor
state (`scout_transition.py`) did not leave the map. Routes are the expensive
part, and they do not generalise to four DS games.

They are also unnecessary. The evidence a map id has to satisfy is:

    constant while the player walks around inside a map,
    different in a different map.

A route produces both halves in one sequence. Two SAVE STATES on different maps
produce them just as well — and save states are free, because every run already
writes one every ten turns (`local/runs/*/savepoints/`). The player being
somewhere interesting is what a run is for.

Ranking, not filtering
----------------------
Thousands of addresses are constant-within-map and different-between-maps —
that describes the whole of the loaded map's DATA as well as its id. The prior
that makes the list readable is the one `p-a-results.md` §10 found on three
cartridges: **the map id sits next to the coordinates.** Crystal keeps group,
number, y, x in four adjacent bytes; FireRed and Emerald keep them at +0x0000,
+0x0002, +0x0004, +0x0005 of one block. So candidates are ranked by distance to
the x/y addresses the axis search already found, and nothing is dropped for
being far away — a game that buries its id ranks low and is still listed.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \\
      v2-experiments/find_map_id.py --rom platinum \\
      --state bedroom=configs/saves/skyemu/platinum/emulator.state \\
      --state twinleaf=v2-experiments/states/platinum/probe.state \\
      --anchors local/addr-hunt/platinum-probe-xy.json --port 8260
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app.roms import load_roms  # noqa: E402
from src.emulator import make_emulator  # noqa: E402

REGION = {"NDS": (0x02000000, 0x400000), "GBA": (0x02000000, 0x40000), "GB": (0xC000, 0x2000)}
# Four short walks, each from a fresh /load so nothing accumulates. They are the
# "constant while walking" half of the evidence, and four directions rather than
# one because a value that happens to hold still through three lefts is common
# and one that holds through all four while the tile changes is not.
PROBES = [[], ["right"] * 3, ["left"] * 3, ["up"] * 3, ["down"] * 3]
# A map id is a small number. Gen 4 has a few hundred maps; gen 2/3 have a
# (group, number) pair of bytes. Nothing real needs 16 bits of range, and the
# cap removes the bulk of the map DATA, which is pointers and coordinates.
MAX_PLAUSIBLE_ID = 2048


def machine(rom, port: int):
    stage = f"local/mapid/{rom.id}"
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


def constant_mask(emu, state: str, base: int, span: int) -> tuple[np.ndarray, np.ndarray]:
    """Walk the probes from ``state``; return (bytes at rest, constant-everywhere mask)."""
    first = None
    same = None
    for i, presses in enumerate(PROBES):
        t0 = time.time()
        emu.load_state(state)
        emu.wait_for_stable_screen()
        if presses:
            emu.press_button_list(presses)
            emu.wait_for_stable_screen()
        buf = np.frombuffer(emu.read_memory(base, span), dtype=np.uint8)
        if first is None:
            first, same = buf, np.ones(span, dtype=bool)
        else:
            same &= (buf == first)
        print(f"    probe {i} ({len(presses)} presses) {time.time()-t0:5.1f}s  "
              f"{int(same.sum()):,} bytes still constant")
    return first, same


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rom", required=True)
    ap.add_argument("--state", action="append", required=True,
                    help="MAP=path. Repeat the same MAP name for two states on the "
                         "SAME map — that is the discriminator: a real id agrees "
                         "there and differs across names.")
    ap.add_argument("--min-anchor-dist", type=int, default=0,
                    help="drop candidates within N bytes of a coordinate anchor. "
                         "A coordinate's own high byte is constant while walking "
                         "a few tiles, differs between maps and sits next to the "
                         "anchor, so proximity ranking promotes it first. 4 "
                         "removes a 32-bit field, 2 a 16-bit one.")
    ap.add_argument("--anchors", default=None,
                    help="a find_addresses --stage xy --json, to rank by proximity")
    ap.add_argument("--port", type=int, default=8260)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    rom = next((r for r in load_roms() if r.id == args.rom), None)
    if rom is None or not rom.exists():
        raise SystemExit(f"Unknown or missing rom {args.rom!r}")
    states = [s.split("=", 1) for s in args.state]
    maps: dict[str, list[str]] = {}
    for label, path in states:
        maps.setdefault(label, []).append(path)
    if len(maps) < 2:
        raise SystemExit("Two DIFFERENT map names are the whole method — pass --state twice.")

    anchors: list[int] = []
    if args.anchors:
        d = json.loads(Path(args.anchors).read_text())
        anchors = sorted({c["addr"] for k in ("x", "y") for c in d.get(k, [])})

    emu = machine(rom, args.port)
    emu.start_server()
    try:
        emu.wait_for_connection(timeout=300.0)
        base, span = REGION[emu.system]
        print(f"  {rom.name} on {emu.system}: {span // 1024} KB of work RAM, "
              f"{len(states)} states, {len(PROBES)} probes each\n")

        # One representative buffer per map, plus the constant-everywhere mask.
        # Two states on the SAME map must AGREE, which is what kills a value
        # that merely happens to differ between two snapshots.
        rest, const, agree = {}, None, np.ones(span, dtype=bool)
        for label, paths in maps.items():
            for i, path in enumerate(paths):
                print(f"  [{label}{'' if len(paths) == 1 else f' #{i + 1}'}] {path}")
                buf, same = constant_mask(emu, path, base, span)
                const = same if const is None else (const & same)
                if label in rest:
                    agree &= (buf == rest[label])
                else:
                    rest[label] = buf
                print()
        if any(len(v) > 1 for v in maps.values()):
            print(f"  {int(agree.sum()):,} bytes agree across the repeated states "
                  f"of the same map")

        labels = list(maps)
        # Constant inside EVERY map, the same in every state OF a map, and not
        # the same value across maps.
        differs = np.zeros(span, dtype=bool)
        for other in labels[1:]:
            differs |= (rest[labels[0]] != rest[other])
        cand = np.nonzero(const & agree & differs)[0]
        print(f"  {len(cand):,} bytes are constant while walking in every map "
              f"AND differ between maps")

        rows = []
        for off in cand.tolist():
            addr = base + off
            vals = {l: int(rest[l][off]) for l in labels}
            u16 = {l: int(struct.unpack_from("<H", rest[l].tobytes(), off)[0])
                   for l in labels} if off + 2 <= span else None
            plausible = all(v < MAX_PLAUSIBLE_ID for v in (u16 or vals).values())
            near = min((abs(addr - a) for a in anchors), default=None)
            rows.append({"addr": addr, "u8": vals, "u16": u16,
                         "near_anchor": near, "plausible": plausible})

        rows = [r for r in rows if r["plausible"]]
        if args.min_anchor_dist:
            n = len(rows)
            rows = [r for r in rows if r["near_anchor"] is None
                    or r["near_anchor"] >= args.min_anchor_dist]
            print(f"  dropped {n - len(rows):,} candidate(s) within "
                  f"{args.min_anchor_dist} bytes of a coordinate anchor")
        rows.sort(key=lambda r: (r["near_anchor"] if r["near_anchor"] is not None else 1 << 30))
        print(f"  {len(rows):,} of them hold a plausible id (< {MAX_PLAUSIBLE_ID})\n")
        print(f"  ranked by distance to the {len(anchors)} coordinate address(es):")
        for r in rows[:args.top]:
            vals = "  ".join(f"{l}={r['u8'][l]}" for l in labels)
            u16 = "  ".join(f"{l}={r['u16'][l]}" for l in labels) if r["u16"] else ""
            print(f"    {r['addr']:#010x}  +{r['near_anchor']:<6} u8[{vals}]   u16[{u16}]")
        if args.json:
            Path(args.json).write_text(json.dumps(rows[:400], indent=2))
            print(f"\n  wrote {args.json}")
        return 0 if rows else 2
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
