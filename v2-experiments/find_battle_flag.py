#!/usr/bin/env python3
"""Find a game's IN-BATTLE bit from save states that are in a battle and ones that are not.

Same shape as ``find_map_id.py`` — evidence from save states rather than from a
route — with the law changed. A map id is *constant while walking, different
between maps*; an in-battle flag is:

    SET in every state that is in a battle,
    CLEAR in every state that is not,
    and it stays clear while the player walks around the overworld.

The third clause is what separates the flag from the thousands of bytes that
merely happen to differ between two snapshots taken twenty turns apart: party
HP, the RNG, sprite state, the frame counter. Walk probes are taken from a
fresh ``/load`` each time (``find_addresses.py``'s reason: probes are
independent, not a route), and a battle state is probed by STEPPING FRAMES
rather than pressing buttons, because a press in a battle can end the battle
and then the "in battle" sample is not one.

Candidates are reported per BIT, not per byte: the contract in
``src/referee/contracts.py`` wants a mask, and mask-vs-bit-index is the one
typo there that is silent.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \\
      v2-experiments/find_battle_flag.py collect --rom emerald --port 8265 \\
      --battle b190=local/runs/.../savepoints/turn_190/emulator.state \\
      --over   o110=local/runs/.../savepoints/turn_110/emulator.state
    ... analyze --rom emerald
    ... verify  --rom emerald --addr 0x030026f9 --mask 0x02 --port 8265 \\
      --battle ... --over ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.app.roms import load_roms  # noqa: E402
from src.emulator import make_emulator  # noqa: E402

# Work RAM per console. The GBA gets IWRAM as well as EWRAM: FireRed's
# gMain.inBattle lives at 0x03003529, i.e. in IWRAM, and a scan that skipped it
# would be unable to find the one flag anybody has already found.
REGIONS = {
    "GBA": [("IWRAM", 0x03000000, 0x8000), ("EWRAM", 0x02000000, 0x40000)],
    "GB": [("WRAM", 0xC000, 0x2000), ("HRAM", 0xFF80, 0x7F)],
    "NDS": [("MAIN", 0x02000000, 0x400000)],
}
WALKS = {"": [], "r": ["right"] * 3, "l": ["left"] * 3, "u": ["up"] * 3, "d": ["down"] * 3}


def machine(rom, port: int):
    stage = f"local/battleflag/{rom.id}/stage"
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


def outdir(rom_id: str) -> Path:
    d = REPO / "local" / "battleflag" / rom_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_all(emu, regions) -> dict[str, np.ndarray]:
    return {n: np.frombuffer(emu.read_memory(b, s), dtype=np.uint8) for n, b, s in regions}


def sample(emu, out: Path, tag: str, state: str, regions, presses, steps: int) -> dict:
    t0 = time.time()
    emu.load_state(state)
    emu.wait_for_stable_screen()
    if presses:
        emu.press_button_list(presses)
        emu.wait_for_stable_screen()
    if steps:
        emu.step(steps)
    emu.capture_screenshot().save(out / f"shot_{tag}.png")
    bufs = read_all(emu, regions)
    print(f"    {tag:16s} {time.time() - t0:5.1f}s")
    return bufs


def cmd_collect(args) -> int:
    rom = next((r for r in load_roms() if r.id == args.rom), None)
    if rom is None or not rom.exists():
        raise SystemExit(f"Unknown or missing rom {args.rom!r}")
    out = outdir(rom.id)
    labelled = [("battle", s) for s in (args.battle or [])] + \
               [("over", s) for s in (args.over or [])]
    if not labelled:
        raise SystemExit("pass at least one --battle and one --over")

    emu = machine(rom, args.port)
    emu.start_server()
    store: dict[str, np.ndarray] = {}
    meta: list[dict] = []
    try:
        emu.wait_for_connection(timeout=300.0)
        regions = [(n, b, s) for n, b, s in REGIONS[emu.system]
                   if not args.regions or n in args.regions]
        print(f"  {rom.name} on {emu.system}: "
              f"{', '.join(f'{n} {s // 1024}KB' for n, b, s in regions)}")
        for kind, spec in labelled:
            name, path = spec.split("=", 1)
            print(f"  [{kind}] {name}  {path}")
            probes = {"": []} if kind == "battle" else WALKS
            for suffix, presses in probes.items():
                tag = f"{kind}_{name}" + (f"_{suffix}" if suffix else "")
                steps = args.battle_steps if kind == "battle" else 0
                bufs = sample(emu, out, tag, path, regions, presses, steps)
                for rname, buf in bufs.items():
                    store[f"{tag}|{rname}"] = buf
                meta.append({"tag": tag, "kind": kind, "state": path,
                             "presses": presses, "steps": steps})
            if kind == "battle" and args.battle_steps:
                # a SECOND sample of the same battle state after more frames:
                # a bit that is part of an animation will not survive both.
                tag = f"battle_{name}_late"
                bufs = sample(emu, out, tag, path, regions, [], args.battle_steps * 4)
                for rname, buf in bufs.items():
                    store[f"{tag}|{rname}"] = buf
                meta.append({"tag": tag, "kind": "battle", "state": path,
                             "presses": [], "steps": args.battle_steps * 4})
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass
    np.savez_compressed(out / "samples.npz", **store)
    (out / "meta.json").write_text(json.dumps(
        {"rom": rom.id, "system": emu.system,
         "regions": [[n, b, s] for n, b, s in REGIONS[emu.system]
                     if not args.regions or n in args.regions],
         "samples": meta}, indent=2))
    print(f"\n  wrote {out / 'samples.npz'} ({len(store)} buffers) and meta.json")
    return 0


def cmd_analyze(args) -> int:
    out = outdir(args.rom)
    meta = json.loads((out / "meta.json").read_text())
    store = np.load(out / "samples.npz")
    tags = {m["tag"]: m["kind"] for m in meta["samples"]}
    rows = []
    for rname, base, span in meta["regions"]:
        bat = [store[f"{t}|{rname}"] for t, k in tags.items() if k == "battle"]
        ovr = [store[f"{t}|{rname}"] for t, k in tags.items() if k == "over"]
        if not bat or not ovr:
            continue
        # bitwise AND over battle samples, OR over overworld samples: a bit that
        # is 1 in every battle sample and 0 in every overworld one survives
        # `and_bat & ~or_ovr`. The opposite polarity is the mirror.
        and_bat = np.bitwise_and.reduce(bat)
        or_bat = np.bitwise_or.reduce(bat)
        and_ovr = np.bitwise_and.reduce(ovr)
        or_ovr = np.bitwise_or.reduce(ovr)
        set_in_battle = and_bat & ~or_ovr
        clear_in_battle = and_ovr & ~or_bat
        for pol, mask_arr in (("set_in_battle", set_in_battle),
                              ("clear_in_battle", clear_in_battle)):
            for off in np.nonzero(mask_arr)[0].tolist():
                bits = int(mask_arr[off])
                for bit in range(8):
                    if bits & (1 << bit):
                        rows.append({
                            "region": rname, "addr": base + off, "bit": bit,
                            "mask": 1 << bit, "polarity": pol,
                            "battle_bytes": sorted({int(b[off]) for b in bat}),
                            "over_bytes": sorted({int(o[off]) for o in ovr}),
                        })
    print(f"  {len(rows)} (address, bit) candidates over "
          f"{sum(1 for k in tags.values() if k == 'battle')} battle and "
          f"{sum(1 for k in tags.values() if k == 'over')} overworld samples")
    anchors = [int(a, 0) for a in (args.anchor or [])]
    for r in rows:
        r["near_anchor"] = min((abs(r["addr"] - a) for a in anchors), default=None)
    rows.sort(key=lambda r: (r["polarity"] != "set_in_battle",
                             r["near_anchor"] if r["near_anchor"] is not None else 1 << 30,
                             r["addr"]))
    for r in rows[:args.top]:
        near = "" if r["near_anchor"] is None else f" +{r['near_anchor']}"
        print(f"    {r['addr']:#010x} bit {r['bit']} mask {r['mask']:#04x} "
              f"{r['polarity']:15s}{near:8s} battle={r['battle_bytes']} over={r['over_bytes']}")
    (out / "candidates.json").write_text(json.dumps(rows, indent=2))
    print(f"\n  wrote {out / 'candidates.json'}")
    return 0


def cmd_verify(args) -> int:
    """Read ONE byte in both directions on the live cartridge, with screenshots."""
    rom = next((r for r in load_roms() if r.id == args.rom), None)
    out = outdir(rom.id)
    addr, mask = int(args.addr, 0), int(args.mask, 0)
    emu = machine(rom, args.port)
    emu.start_server()
    results = []
    try:
        emu.wait_for_connection(timeout=300.0)
        for kind, specs in (("battle", args.battle or []), ("over", args.over or [])):
            for spec in specs:
                name, path = spec.split("=", 1)
                probes = {"": []} if kind == "battle" else WALKS
                for suffix, presses in probes.items():
                    tag = f"verify_{kind}_{name}" + (f"_{suffix}" if suffix else "")
                    emu.load_state(path)
                    emu.wait_for_stable_screen()
                    if presses:
                        emu.press_button_list(presses)
                        emu.wait_for_stable_screen()
                    emu.capture_screenshot().save(out / f"{tag}.png")
                    b = emu.read_memory(addr, 1)[0]
                    ok = bool(b & mask) == (kind == "battle")
                    results.append({"tag": tag, "kind": kind, "byte": b,
                                    "bit_set": bool(b & mask), "expected_ok": ok})
                    print(f"    {tag:28s} byte={b:#04x} bit={'SET ' if b & mask else 'clear'} "
                          f"{'OK' if ok else 'MISMATCH'}")
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass
    (out / f"verify_{addr:#x}_{mask:#x}.json").write_text(json.dumps(results, indent=2))
    bad = [r for r in results if not r["expected_ok"]]
    print(f"\n  {len(results) - len(bad)}/{len(results)} samples agree with "
          f"{addr:#x} & {mask:#x}")
    return 0 if not bad else 1



def cmd_grind(args) -> int:
    """Make new states: press a loop from a base state, saving a state+shot per batch.

    The point is the states the overnight runs did not happen to write — an
    overworld START menu, a bag, a battle the searcher triggered itself. There
    is no programmatic "am I in a battle?" here on purpose: that is the thing
    being looked for, and a detector for it would make the corpus circular. The
    shots get read by eye and the states get labelled from what they show.
    """
    rom = next((r for r in load_roms() if r.id == args.rom), None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    presses = [p.strip() for p in args.presses.split(",") if p.strip()]
    emu = machine(rom, args.port)
    emu.start_server()
    try:
        emu.wait_for_connection(timeout=300.0)
        emu.load_state(args.state)
        emu.wait_for_stable_screen()
        emu.capture_screenshot().save(out / "batch_00.png")
        emu.save_state(str(out / "batch_00.state"))
        for i in range(1, args.batches + 1):
            emu.press_button_list(presses * args.per_batch)
            emu.wait_for_stable_screen()
            emu.capture_screenshot().save(out / f"batch_{i:02d}.png")
            emu.save_state(str(out / f"batch_{i:02d}.state"))
            print(f"    batch {i:02d} saved")
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass
    print(f"  wrote {args.batches + 1} state/shot pairs to {out}")
    return 0



def cmd_sequence(args) -> int:
    """One press at a time from a base state, sampling RAM and the screen after each.

    This is the test the two-class corpus cannot do: it crosses the boundary.
    A byte that is merely *correlated* with battles — a per-turn scratch value, a
    counter the battle engine leaves behind, a menu flag — changes on a different
    step from the one where the battle screen gives way to the overworld. The
    shots say which step that is; nothing here guesses it.
    """
    rom = next((r for r in load_roms() if r.id == args.rom), None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    emu = machine(rom, args.port)
    emu.start_server()
    store: dict[str, np.ndarray] = {}
    try:
        emu.wait_for_connection(timeout=300.0)
        regions = [(n, b, s) for n, b, s in REGIONS[emu.system]
                   if not args.regions or n in args.regions]
        emu.load_state(args.state)
        emu.wait_for_stable_screen()
        emu.capture_screenshot().save(out / "step_00.png")
        for rn, buf in read_all(emu, regions).items():
            store[f"00|{rn}"] = buf
        for i in range(1, args.steps + 1):
            emu.press_button_list([args.button])
            emu.wait_for_stable_screen()
            emu.capture_screenshot().save(out / f"step_{i:02d}.png")
            for rn, buf in read_all(emu, regions).items():
                store[f"{i:02d}|{rn}"] = buf
        print(f"    {args.steps} presses of {args.button!r} recorded")
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass
    np.savez_compressed(out / "seq.npz", **store)
    (out / "seq_meta.json").write_text(json.dumps(
        {"rom": rom.id, "state": args.state, "button": args.button,
         "steps": args.steps,
         "regions": [[n, b, s] for n, b, s in REGIONS[emu.system]
                     if not args.regions or n in args.regions]}, indent=2))
    print(f"  wrote {out / 'seq.npz'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "analyze", "verify", "grind", "sequence"):
        p = sub.add_parser(name)
        p.add_argument("--rom", required=True)
        if name != "analyze":
            p.add_argument("--port", type=int, default=8265)
            p.add_argument("--battle", action="append", help="NAME=path")
            p.add_argument("--over", action="append", help="NAME=path")
        if name == "collect":
            p.add_argument("--regions", action="append")
            p.add_argument("--battle-steps", type=int, default=30)
        if name == "analyze":
            p.add_argument("--anchor", action="append", help="rank by distance to this addr")
            p.add_argument("--top", type=int, default=40)
        if name == "verify":
            p.add_argument("--addr", required=True)
            p.add_argument("--mask", required=True)
        if name == "sequence":
            p.add_argument("--state", required=True)
            p.add_argument("--button", required=True)
            p.add_argument("--steps", type=int, default=20)
            p.add_argument("--regions", action="append")
            p.add_argument("--out", required=True)
        if name == "grind":
            p.add_argument("--state", required=True)
            p.add_argument("--presses", required=True, help="comma list, e.g. up,down")
            p.add_argument("--per-batch", type=int, default=4)
            p.add_argument("--batches", type=int, default=8)
            p.add_argument("--out", required=True)
    args = ap.parse_args()
    return {"collect": cmd_collect, "analyze": cmd_analyze, "verify": cmd_verify,
            "grind": cmd_grind, "sequence": cmd_sequence}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
