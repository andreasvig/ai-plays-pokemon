#!/usr/bin/env python3
"""Byte-level companion to find_battle_flag.py's per-BIT analyze.

`analyze` asks for a BIT that is 1 in every battle sample and 0 in every
overworld one. That is the right question for FireRed and the wrong one for
Crystal, whose flag is a MODE byte (0 overworld / 1 wild / 2 trainer) where no
single bit is set in every battle. This asks the weaker, mode-safe question:

    the set of values the byte takes in battle and the set it takes in the
    overworld are DISJOINT.

and reports both sets, so a mode byte is visible as a mode byte rather than as
whichever of its bits the corpus happened to share.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True)
    ap.add_argument("--anchor", action="append", default=[])
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--over-must-be-zero", action="store_true",
                    help="only bytes whose overworld value is 0 everywhere")
    a = ap.parse_args()

    out = REPO / "local" / "battleflag" / a.rom
    meta = json.loads((out / "meta.json").read_text())
    store = np.load(out / "samples.npz")
    kinds = {m["tag"]: m["kind"] for m in meta["samples"]}
    rows = []
    for rname, base, span in meta["regions"]:
        bat = [store[f"{t}|{rname}"] for t, k in kinds.items() if k == "battle"]
        ovr = [store[f"{t}|{rname}"] for t, k in kinds.items() if k == "over"]
        B = np.stack(bat)          # (nb, span)
        O = np.stack(ovr)          # (no, span)
        # A real flag's overworld value is CONSTANT (and normally 0), so the
        # mode-safe test is: overworld constant, and no battle sample equals it.
        nb = B.shape[0]
        o_const = (O == O[0]).all(axis=0)
        b_all = (B == B[0]).all(axis=0)
        cand = o_const & (B != O[0]).all(axis=0)
        if a.over_must_be_zero:
            cand &= (O[0] == 0)
        for off in np.nonzero(cand)[0].tolist():
            rows.append({
                "region": rname, "addr": base + off,
                "over": int(O[0][off]),
                "battle": sorted({int(B[i][off]) for i in range(nb)}),
                "battle_constant": bool(b_all[off]),
            })
    anchors = [int(x, 0) for x in a.anchor]
    for r in rows:
        r["near_anchor"] = min((abs(r["addr"] - x) for x in anchors), default=None)
    rows.sort(key=lambda r: (not r["battle_constant"],
                             r["near_anchor"] if r["near_anchor"] is not None else 1 << 30,
                             r["addr"]))
    print(f"  {len(rows)} byte candidates "
          f"(overworld CONSTANT and never equal to any battle value)")
    for r in rows[:a.top]:
        near = "" if r["near_anchor"] is None else f" anchor+{r['near_anchor']}"
        print(f"    {r['addr']:#010x} over={r['over']:#04x} battle={[hex(v) for v in r['battle']]}"
              f"{' CONST' if r['battle_constant'] else ''}{near}")
    (out / "byte_candidates.json").write_text(json.dumps(rows, indent=2))
    print(f"\n  wrote {out / 'byte_candidates.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
