#!/usr/bin/env python3
"""Intersect labelled Gen 5 battle dumps for a wild/trainer discriminator.

The law is the strict one from ``local/battleflag/black/NOTES.md``: ONE value
across every TRAINER sample and ONE OTHER value across every WILD sample. The
per-bit "set in every A, clear in every B" law is what returned 1.6M rows on
this cartridge and is not used.

Every surviving byte is then placed inside the allocation it lives in
(``gen5_heap_map.py``), and a candidate that is not at the same offset of the
same NAMED block in every dump is reported as such — an address that only
happens to line up is the 14,670-candidate failure wearing a smaller number.

    ./venv/bin/python v2-experiments/gen5_kind_analyse.py \\
        --trainer a.npy b.npy --wild c.npy d.npy
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments"))
from gen5_heap_map import blocks  # noqa: E402

BASE = 0x02000000


def agree(paths: list[Path]) -> tuple[np.ndarray, np.ndarray]:
    first = np.load(paths[0])
    same = np.ones(len(first), bool)
    for p in paths[1:]:
        same &= first == np.load(p)
    return first, same


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trainer", nargs="+", type=Path, required=True)
    ap.add_argument("--wild", nargs="+", type=Path, required=True)
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    t0, tsame = agree(args.trainer)
    w0, wsame = agree(args.wild)
    cand = tsame & wsame & (t0 != w0)
    idx = np.flatnonzero(cand)
    print(f"trainer samples {len(args.trainer)}  wild samples {len(args.wild)}")
    print(f"bytes constant in both classes and different between them: {len(idx)}")
    if args.out:
        np.save(args.out, idx)

    # Place each survivor in a NAMED allocation, and demand the placement hold
    # in every dump — same tag, same offset from the block's data.
    maps = [{ (b["data"], b["size"]): b for b in blocks(np.load(p)) }
            for p in (list(args.trainer) + list(args.wild))]
    def place(m, a):
        for (d, s), b in m.items():
            if d <= a < d + s:
                return b["tag"], a - d
        return None, None
    rows = []
    for i in idx:
        a = BASE + int(i)
        places = [place(m, a) for m in maps]
        if places[0][0] is None or any(p != places[0] for p in places):
            continue
        rows.append((places[0][0], places[0][1], a, int(t0[i]), int(w0[i])))
    print(f"of those, INSIDE the same named allocation at the same offset in "
          f"every dump: {len(rows)}")
    for tag, off, a, tv, wv in sorted(rows)[: args.show]:
        print(f"  {a:#010x}  {tag}+{off:#x}  trainer={tv:<4d} wild={wv}")
    if len(rows) > args.show:
        print(f"  ... {len(rows) - args.show} more")
    print("tags:", Counter(r[0] for r in rows).most_common(12))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
