#!/usr/bin/env python3
"""Find the ENEMY SPECIES u16 from two battle samples against DIFFERENT species.

The law: a u16 that reads species A in every sample of battle A and species B in
every sample of battle B, where A and B are the national dex numbers read off
the two battle SCREENSHOTS. The overworld samples are the control — a species id
that is also present in the overworld corpus at the same address is a party
member, a save-block roster entry or a coincidence, not the ENEMY.

Both battles being wild is the reason the overworld control matters: the enemy
slot is written at encounter time and is not cleared afterwards, so plenty of
addresses hold "the last thing fought" and are useless for "am I fighting one".
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True)
    ap.add_argument("--a", required=True, help="tag prefix of battle A, e.g. battle_t60")
    ap.add_argument("--a-species", type=int, required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--b-species", type=int, required=True)
    ap.add_argument("--anchor", action="append", default=[])
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()

    out = REPO / "local" / "battleflag" / args.rom
    meta = json.loads((out / "meta.json").read_text())
    store = np.load(out / "samples.npz")
    kinds = {m["tag"]: m["kind"] for m in meta["samples"]}
    rows = []
    for rname, base, span in meta["regions"]:
        def u16(tag):
            b = store[f"{tag}|{rname}"]
            return b[:-1].astype(np.uint16) | (b[1:].astype(np.uint16) << 8)
        A = [u16(t) for t in kinds if t.startswith(args.a)]
        B = [u16(t) for t in kinds if t.startswith(args.b)]
        O = [u16(t) for t, k in kinds.items() if k == "over"]
        assert A and B and O, (len(A), len(B), len(O))
        ok = np.ones(A[0].shape, dtype=bool)
        for s in A:
            ok &= (s == args.a_species)
        for s in B:
            ok &= (s == args.b_species)
        # control: the address must not hold either species in the overworld
        ctrl = np.ones(A[0].shape, dtype=bool)
        for s in O:
            ctrl &= (s != args.a_species) & (s != args.b_species)
        for off in np.nonzero(ok)[0].tolist():
            rows.append({"region": rname, "addr": base + off,
                         "clean_in_overworld": bool(ctrl[off]),
                         "over_values": sorted({int(s[off]) for s in O})[:8]})
    anchors = [int(x, 0) for x in args.anchor]
    for r in rows:
        r["near_anchor"] = min((abs(r["addr"] - x) for x in anchors), default=None)
    rows.sort(key=lambda r: (not r["clean_in_overworld"],
                             r["near_anchor"] if r["near_anchor"] is not None else 1 << 30,
                             r["addr"]))
    print(f"  {len(rows)} u16 addresses read {args.a_species} in {args.a} and "
          f"{args.b_species} in {args.b}; "
          f"{sum(1 for r in rows if r['clean_in_overworld'])} hold neither in the overworld")
    for r in rows[:args.top]:
        near = "" if r["near_anchor"] is None else f" anchor+{r['near_anchor']}"
        print(f"    {r['addr']:#010x} {'clean' if r['clean_in_overworld'] else 'DIRTY'}"
              f" over={r['over_values']}{near}")
    (out / "species_candidates.json").write_text(json.dumps(rows, indent=2))
    print(f"\n  wrote {out / 'species_candidates.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
