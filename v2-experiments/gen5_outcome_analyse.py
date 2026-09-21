#!/usr/bin/env python3
"""Find gen 5's battle RESULT in the BATTLE_SETUP_PARAM, and refuse a confounded one.

The param is the caller's, not the battle's: the field script allocates it,
hands it to the battle proc and reads the answer out of it afterwards. That is
why the result survives a teardown that frees everything else, and why it is at
a different address in every battle — only the chase at the battle proc's +0x0c
reaches it.

The law is a byte that takes ONE value per outcome class across every drive in
that class and a DIFFERENT one across the others, read at the frame the battle
flag first goes clear (which is where ``src/app/route.py`` reads it).

And the reason this script prints the CROSS-TABULATION before it prints a
candidate: on Platinum an outcome byte scored 6 for 6 and was wrong, because
every win and escape in that corpus was a WILD battle and both losses were
TRAINER battles — the outcome was perfectly correlated with the KIND, and the
byte was reading the kind. Any candidate whose classes line up with the kind,
or the species, or the map, is refused here rather than shipped.

    ./venv/bin/python v2-experiments/gen5_outcome_analyse.py \\
        local/gen5-battle/o_trainer_win:trainer:won ...
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def drives(specs: list[str]) -> list[dict]:
    out = []
    for spec in specs:
        path, kind, outcome = spec.split(":")
        rows = json.loads((Path(path) / "samples.json").read_text())
        out.append({"name": Path(path).name, "kind": kind, "outcome": outcome,
                    "rows": rows})
    return out


def close_sample(rows: list[dict]) -> tuple[int, dict] | tuple[None, None]:
    """The FIRST row after the battle flag goes clear — route.py's own rule."""
    seen = False
    for r in rows:
        if r.get("foe"):
            seen = True
        elif seen:
            return r["n"], r
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drives", nargs="+", help="DIR:kind:outcome")
    ap.add_argument("--region", default="setup")
    ap.add_argument("--length", type=int, default=0x60)
    args = ap.parse_args()
    ds = drives(args.drives)

    print("corpus:")
    for d in ds:
        n, row = close_sample(d["rows"])
        print(f"  {d['name']:20s} {d['kind']:8s} {d['outcome']:5s} "
              f"{len(d['rows']):3d} samples, flag clears at n={n}")
    by_outcome = defaultdict(list)
    for d in ds:
        by_outcome[d["outcome"]].append(d["kind"])
    print("\ncross-tabulation (outcome x kind) — a candidate is only evidence "
          "about the OUTCOME if this table is not diagonal:")
    for o, kinds in sorted(by_outcome.items()):
        print(f"  {o:5s} {sorted(kinds)}")
    confounded = all(len(set(k)) == 1 for k in by_outcome.values()) and len(by_outcome) > 1
    if confounded:
        print("  !! every outcome class has ONE kind — this corpus cannot tell "
              "an outcome byte from a kind byte. That is the Platinum 6/6.")

    closes = []
    for d in ds:
        _, row = close_sample(d["rows"])
        if row is None or not row.get(args.region):
            print(f"\n{d['name']}: no readable {args.region} at the close "
                  f"(ptr {row.get(args.region + '_ptr') if row else None})")
            closes.append((d, None))
        else:
            closes.append((d, bytes.fromhex(row[args.region])))
    have = [(d, b) for d, b in closes if b is not None]
    if len(have) < len(closes):
        print("\n-- some drives have no reading at the close; scoring the rest --")
    if not have:
        return 1
    n = min(len(b) for _, b in have)
    print(f"\noffsets whose value partitions the outcome classes "
          f"({len(have)} drives, {n} bytes):")
    groups = defaultdict(list)
    for d, b in have:
        groups[d["outcome"]].append(b)
    for off in range(n):
        vals = {o: {bs[off] for bs in blobs} for o, blobs in groups.items()}
        if any(len(v) != 1 for v in vals.values()):
            continue
        flat = [next(iter(v)) for v in vals.values()]
        if len(set(flat)) != len(flat):
            continue
        print(f"  +{off:#05x}  " + "  ".join(f"{o}={next(iter(v))}"
                                             for o, v in sorted(vals.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
