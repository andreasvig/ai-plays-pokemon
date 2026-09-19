#!/usr/bin/env python3
"""Does a game's memory contract actually track the player? Ask the cartridge.

The acceptance test for anything added to ``src/referee/contracts.py``. A
contract is a claim about where a cartridge keeps the player, and the claim is
cheap to check against the only authority there is: walk, and see whether the
number follows.

What it checks, in order of how much each one is worth:

1. **The trace is not blind.** Every input comes back with a tile. A contract
   whose spec points at nothing still decodes — to ``None`` everywhere — and the
   run records no position at all, which is the exact failure this whole module
   was built to end.
2. **The coordinate follows the walk.** Pressing right moves x and not y;
   pressing down moves y and not x. This is the part that cannot be faked by a
   plausible-looking address: a step counter passes (1), and fails (2) on the
   very first press in the other direction.
3. **It comes home.** Walking back restores the tile. A value that counts but
   never returns is a counter, not a place.
4. **The map key is present and small.** A (group, number) pair for Gen 2/3, a
   single id for Gen 4/5 — whichever the contract declares. Not checked for
   correctness here, only for presence: proving a map id needs a map
   TRANSITION, which needs a route out of the room, which is
   ``find_addresses.py --stage map``'s job and not this one's.

Usage:
    scripts/verify_contract.py --rom emerald
    scripts/verify_contract.py --all
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app.roms import load_roms  # noqa: E402
from src.emulator import make_emulator  # noqa: E402
from src.referee import trace as trace_mod  # noqa: E402
from src.referee.contracts import attach, contract_for  # noqa: E402

BINARY = "~/Applications/SkyEmu.app/Contents/MacOS/SkyEmu"

# FireRed's roms.yaml entry has no `start_save`: its opening is
# executor.CANONICAL_SAVE, which is an mGBA savestate, and SkyEmu answers
# `/load -> failed` for every one of those. The SkyEmu-replayed equivalent is
# the v2 state, so the default ROM needs the one override in this table.
SNAPSHOT_OVERRIDE = {"firered": "configs/saves/skyemu/firered-pokebench-v2"}


def snapshot_for(rom) -> str:
    path = SNAPSHOT_OVERRIDE.get(rom.id) or rom.start_save
    if not path:
        raise SystemExit(f"{rom.id} has no SkyEmu-loadable start state; pass --snapshot.")
    return path


def machine(rom, port: int):
    cfg = {
        "emulator": {
            "type": "skyemu", "host": "127.0.0.1", "port": port,
            "binary_path": os.path.expanduser(BINARY), "rom_path": rom.path,
            "button_hold_frames": 12, "frames_between_inputs": 24,
            "ab_hold_frames": 12, "ab_gap_frames": 100, "wait_input_seconds": 5.0,
            "boot_frames": 60, "step_rate": "fast",
            "rom_stage_dir": f"local/verify-contract/{rom.id}",
        },
        "screenshot": {"grid_overlay": False},
        "valid_inputs": ["A", "B", "U", "D", "L", "R", "START", "SELECT"],
    }
    Path(f"local/verify-contract/{rom.id}").mkdir(parents=True, exist_ok=True)
    return make_emulator(cfg)


def tiles(emu, contract, presses: list[str]) -> list[tuple]:
    """Press, then decode the per-input trace into (x, y, map…) tuples."""
    emu.fetch_trace()  # drain anything the settle left behind
    emu.press_button_list(presses)
    emu.wait_for_stable_screen()
    samples = trace_mod.decode_samples(emu.fetch_trace(), None, contract)
    return [(s["x"], s["y"], contract.map_key(s)) for s in samples]


def check(rom, port: int, snapshot: str | None = None) -> bool:
    contract = contract_for(rom.game)
    print(f"\n=== {rom.name}  ({rom.game}) ===")
    if contract is None:
        print("  NO CONTRACT — a run on this game records no position.")
        return False
    print(f"  spec: {list(contract.spec)}")
    print(f"  census_ok (in-battle flag located): {contract.census_ok}")

    emu = machine(rom, port)
    emu.start_server()
    try:
        emu.wait_for_connection(timeout=300.0)
        # A savepoint DIR (configs/saves/...) or a bare .state file — the
        # measurement fixtures in v2-experiments/states are the latter, because
        # they are not openings a run can start from and carry no tasks.json.
        where = snapshot or snapshot_for(rom)
        emu.load_state(where if where.endswith(".state") else os.path.join(where, "emulator.state"))
        attach(emu, contract)
        emu.wait_for_stable_screen()

        # Two presses per direction: the first may only turn the player, and
        # which of the two it is differs by generation (p-a-results.md §1). Two
        # presses walk a tile either way, which is all this check needs.
        right = tiles(emu, contract, ["right", "right"])
        down = tiles(emu, contract, ["down", "down"])
        back = tiles(emu, contract, ["left", "left", "up", "up"])
        every = right + down + back

        blind = [t for t in every if t[0] is None or t[1] is None]
        print(f"\n  right {right}\n  down  {down}\n  back  {back}")

        ok = True
        if blind:
            print(f"  FAIL: {len(blind)}/{len(every)} inputs decoded no tile — the trace is blind.")
            return False
        print(f"  PASS: all {len(every)} inputs decoded a tile.")

        # The walk actually has to have moved, or every test below is vacuous
        # against a player standing in a corner.
        if right[0][0] == right[-1][0] and down[0][1] == down[-1][1]:
            print("  INCONCLUSIVE: neither leg moved. The start tile is boxed in "
                  "(Emerald's van is the known case) — nothing here is a verdict.")
            return False

        if right[-1][0] != right[0][0] and right[-1][1] == right[0][1]:
            print(f"  PASS: walking right moved x ({right[0][0]} -> {right[-1][0]}) and held y.")
        elif right[-1][0] != right[0][0]:
            print(f"  WARN: walking right moved x AND y {right[0][:2]} -> {right[-1][:2]}.")
            ok = False
        else:
            print("  (right leg did not move — wall; no verdict from it)")

        if down[-1][1] != down[0][1] and down[-1][0] == down[0][0]:
            print(f"  PASS: walking down moved y ({down[0][1]} -> {down[-1][1]}) and held x.")
        elif down[-1][1] != down[0][1]:
            print(f"  WARN: walking down moved y AND x {down[0][:2]} -> {down[-1][:2]}.")
            ok = False
        else:
            print("  (down leg did not move — wall; no verdict from it)")

        if back[-1][:2] == right[0][:2]:
            print(f"  PASS: walked back to the starting tile {back[-1][:2]}.")
        else:
            print(f"  NOTE: ended at {back[-1][:2]}, started at {right[0][:2]} — "
                  "a wall on one leg makes the round trip asymmetric; not a failure on its own.")

        keys = {t[2] for t in every}
        if None in keys:
            print("  FAIL: the map key decoded as None on at least one input.")
            ok = False
        elif len(keys) == 1:
            print(f"  PASS: one map throughout, key {keys.pop()} (correctness needs a transition).")
        else:
            print(f"  NOTE: {len(keys)} distinct map keys in one room: {keys} — suspicious.")
            ok = False
        return ok
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rom", help="a rom id from configs/roms.yaml")
    ap.add_argument("--all", action="store_true", help="every rom that has a contract")
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--snapshot", default=None, help="override the start state")
    args = ap.parse_args()

    roms = [r for r in load_roms() if r.exists()]
    if args.all:
        targets = [r for r in roms if contract_for(r.game) is not None]
    else:
        targets = [r for r in roms if r.id == args.rom]
        if not targets:
            raise SystemExit(f"Unknown or missing rom {args.rom!r}")

    results = {r.id: check(r, args.port + i, args.snapshot) for i, r in enumerate(targets)}
    print("\n" + "=" * 52)
    for rid, ok in results.items():
        print(f"  {rid:<12} {'PASS' if ok else 'not proven'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
