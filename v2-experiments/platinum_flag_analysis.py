#!/usr/bin/env python3
"""Second-pass analysis of the Platinum battle-flag samples.

`find_battle_flag.py analyze` answers "which (address, bit) is set in every
battle sample and clear in every overworld one". On a 4 MB NDS main RAM that
question alone returns thousands of rows, most of them battle-overlay data that
is simply not resident in the overworld. This adds the discriminators that are
specific to this cartridge:

  * the FieldSystem POINTER hunt. pret/pokeplatinum's FieldSystem carries
    `Location *location` at +0x1C, and the Location struct is the known
    0x0227F408. So every main-RAM word equal to 0x0227F408 is a candidate
    FieldSystem base, and +0x68 of it is `runningFieldMap`. That address is a
    DECOMP-DERIVED PREDICTION; whether the byte behaves is measured here.
  * a WIDTH view: for a shortlisted address, print the whole byte in every
    sample, so a MODE byte (Crystal's 0/1/2) is distinguishable from a bit.
  * stability: how many distinct values the byte takes inside each class.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[1]
LOCATION = 0x0227F408
FS_LOCATION_OFF = 0x1C
FS_RUNNING_FIELD_MAP_OFF = 0x68


def load(rom: str):
    out = REPO / "local" / "battleflag" / rom
    meta = json.loads((out / "meta.json").read_text())
    store = np.load(out / "samples.npz")
    kinds = {m["tag"]: m["kind"] for m in meta["samples"]}
    return out, meta, store, kinds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default="platinum")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--show", action="append", default=[],
                    help="print this address byte in every sample")
    args = ap.parse_args()
    out, meta, store, kinds = load(args.rom)
    (rname, base, span), = [tuple(r) for r in meta["regions"]]
    tags = list(kinds)
    bufs = {t: store[f"{t}|{rname}"] for t in tags}
    bat = [bufs[t] for t in tags if kinds[t] == "battle"]
    ovr = [bufs[t] for t in tags if kinds[t] == "over"]
    print(f"  {len(bat)} battle / {len(ovr)} overworld samples of {rname} "
          f"{base:#x}+{span:#x}")

    # --- FieldSystem pointer hunt -------------------------------------------
    words = {t: b.view("<u4") for t, b in bufs.items()}
    hits = {t: (np.nonzero(w == LOCATION)[0] * 4 + base).tolist()
            for t, w in words.items()}
    common = set.intersection(*(set(v) for v in hits.values()))
    print(f"\n  words holding {LOCATION:#x} (a FieldSystem.location candidate):")
    for t in sorted(hits)[:4]:
        print(f"    {t:22s} {[hex(a) for a in hits[t]]}")
    print(f"    in ALL {len(tags)} samples: {[hex(a) for a in sorted(common)]}")
    for a in sorted(common):
        fs = a - FS_LOCATION_OFF
        rfm = fs + FS_RUNNING_FIELD_MAP_OFF
        print(f"    -> FieldSystem base {fs:#x}, runningFieldMap {rfm:#x}: " +
              " ".join(f"{t}={int(bufs[t][rfm - base])}"
                       for t in sorted(tags, key=lambda x: (kinds[x], x))))

    # --- the standard two-class law, with a per-byte view --------------------
    and_bat = np.bitwise_and.reduce(bat); or_bat = np.bitwise_or.reduce(bat)
    and_ovr = np.bitwise_and.reduce(ovr); or_ovr = np.bitwise_or.reduce(ovr)
    set_in_battle = and_bat & ~or_ovr
    clear_in_battle = and_ovr & ~or_bat
    print(f"\n  bytes with >=1 bit set in every battle and clear in every "
          f"overworld: {int(np.count_nonzero(set_in_battle))}")
    print(f"  the mirror (clear in battle, set in overworld):        "
          f"{int(np.count_nonzero(clear_in_battle))}")

    # CONSTANT within each class is the stronger law: a real flag takes ONE
    # value in battle and ONE value out of it.
    bat_stack = np.stack(bat); ovr_stack = np.stack(ovr)
    bat_const = (bat_stack == bat_stack[0]).all(0)
    ovr_const = (ovr_stack == ovr_stack[0]).all(0)
    differs = bat_stack[0] != ovr_stack[0]
    strict = bat_const & ovr_const & differs
    idx = np.nonzero(strict)[0]
    print(f"  bytes CONSTANT across all battles, CONSTANT across all "
          f"overworld, and different between the two: {len(idx)}")
    rows = [{"addr": int(base + i), "battle": int(bat_stack[0][i]),
             "over": int(ovr_stack[0][i]),
             "d_loc": abs(int(base + i) - LOCATION)} for i in idx]
    rows.sort(key=lambda r: r["d_loc"])
    for r in rows[:args.top]:
        print(f"    {r['addr']:#010x}  battle={r['battle']:#04x} "
              f"over={r['over']:#04x}   |loc-d|={r['d_loc']:#x}")
    (out / "strict.json").write_text(json.dumps(rows, indent=2))
    print(f"  wrote {out/'strict.json'} ({len(rows)} rows)")

    for a in args.show:
        addr = int(a, 0); off = addr - base
        print(f"\n  {addr:#x} per sample:")
        for t in sorted(tags, key=lambda x: (kinds[x], x)):
            print(f"    {kinds[t]:7s} {t:22s} {int(bufs[t][off]):#04x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
