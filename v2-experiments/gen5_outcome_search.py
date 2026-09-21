#!/usr/bin/env python3
"""Search the CLOSE frame for a byte that says how a gen-5 battle ended.

The close is the first sample after the battle flag goes clear, because that is
where ``src/app/route.py`` reads an outcome and therefore the only frame a
candidate has to be right on.

THE LAW IS THE CONFOUND BREAK, not a filter applied afterwards. Every outcome
class in the corpus must contain BOTH a trainer battle and a wild one, and a
candidate must take one value across the whole of a class. A byte that reads the
KIND then cannot survive: it differs inside each class. That is the control the
Platinum candidate never had — it scored 6 for 6 on a corpus where every win and
escape was wild and both losses were trainer battles, so it was reading the kind
and nobody could tell (commit a377f67, and the gen-4 block in contracts.py).

    ./venv/bin/python v2-experiments/gen5_outcome_search.py \\
        --won DIR:kind DIR:kind --lost DIR:kind DIR:kind [--ran DIR:kind]

``--gen4`` reads a gen-4 corpus instead (``gen4_battle_end.py`` output) and
changes two things, both because gen 4 FREES its battle heap at the close:

* the frame is the LAST sample INSIDE the fight, not the first one after it.
  After the close the addresses under test belong to whatever the allocator
  handed them to (0x78 on Platinum), so a search there is a search of somebody
  else's object.
* the haystack is the two battle ALLOCATIONS that every sample already
  carries — BattleSystem 0x2494 and BattleContext 0x3168 — rather than 4 MB.
  Gen 5's allocator names its blocks in retail and gen 4's does not, so there
  is no map to place a 4 MB hit into; what the gen-4 corpus can say is "inside
  these two blocks, at these offsets", and what it CANNOT see is anything
  outside them. Say so rather than implying a sweep.

    ./venv/bin/python v2-experiments/gen5_outcome_search.py --gen4 \\
        --won /tmp/g4/wild_won:wild /tmp/g4/trainer_won:trainer \\
        --lost /tmp/g4/wild_lost:wild /tmp/g4/trainer_lost:trainer \\
        --ran /tmp/g4/wild_ran:wild
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments"))
from gen5_heap_map import blocks  # noqa: E402

BASE = 0x02000000
#: The two gen-4 battle allocations as gen4_battle_end.py captures them:
#: (region name, allocator header address). The struct begins at header + 0x20.
GEN4_REGIONS = (("bsys", 0x022BF950), ("bctx", 0x022C29CC))
GEN4_STRUCT = 0x20


def gen4_close(d: Path, after: int = 0) -> tuple[str, np.ndarray, list[tuple]]:
    """The LAST in-battle sample's two blocks, concatenated, plus their layout.

    ``after`` walks BACKWARD from that sample, because on gen 4 there is no
    forward direction: the block is gone.
    """
    rows = json.loads((d / "samples.json").read_text())
    inb = [i for i, r in enumerate(rows) if r.get("in_battle")]
    if not inb:
        raise SystemExit(f"{d.name}: no in-battle sample")
    i = inb[-1 - after]
    r = rows[i]
    parts, layout, off = [], [], 0
    for name, hdr in GEN4_REGIONS:
        blob = bytes.fromhex(r[name])
        parts.append(blob)
        layout.append((name, hdr, off, len(blob)))
        off += len(blob)
    tag = f"n{r['n']}.{r.get('sub', 0)}"
    return tag, np.frombuffer(b"".join(parts), dtype=np.uint8), layout


def close_dump(d: Path, after: int = 0) -> tuple[int, np.ndarray]:
    """The dump taken ``after`` samples past the close, and its index."""
    rows = json.loads((d / "samples.json").read_text())
    closed = None
    for i, r in enumerate(rows):
        if r.get("foe"):
            closed = None
        elif closed is None and i and rows[i - 1].get("foe"):
            closed = r["n"]
    n = closed + after
    f = d / f"d{n:05d}.npy"
    if not f.exists():
        raise SystemExit(f"{d.name}: no dump at n={n} (close={closed})")
    return n, np.load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--won", nargs="+", default=[])
    ap.add_argument("--lost", nargs="+", default=[])
    ap.add_argument("--ran", nargs="+", default=[])
    ap.add_argument("--after", type=int, default=0)
    ap.add_argument("--show", type=int, default=60)
    ap.add_argument("--gen4", action="store_true",
                    help="read a gen-4 corpus at the LAST IN-BATTLE sample, over "
                         "the two battle allocations. See the module docstring.")
    ap.add_argument("--max-value", type=int, default=0,
                    help="keep only candidates whose values are all below this. An "
                         "OUTCOME is a small enum; a byte that separates the classes "
                         "with values like 204 vs 12 is separating the SCENE, and "
                         "after a loss the scene is a Pokemon Center because gen 5 "
                         "warps the player there — a confound no corpus can break, "
                         "because it is what losing MEANS.")
    args = ap.parse_args()

    classes: dict[str, list[tuple[str, np.ndarray]]] = defaultdict(list)
    kinds: dict[str, list[str]] = defaultdict(list)
    layouts: list[list[tuple]] = []
    for outcome, specs in (("won", args.won), ("lost", args.lost), ("ran", args.ran)):
        for spec in specs:
            path, _, kind = spec.partition(":")
            if args.gen4:
                n, buf, layout = gen4_close(Path(path), args.after)
                layouts.append(layout)
            else:
                n, buf = close_dump(Path(path), args.after)
                n = f"n{n}"
            classes[outcome].append((Path(path).name, buf))
            kinds[outcome].append(kind)
            print(f"  {Path(path).name:18s} {outcome:5s} {kind:8s} close-{args.after} = {n}")
    print("\ncross-tabulation (outcome x kind):")
    for o, ks in sorted(kinds.items()):
        print(f"  {o:5s} {sorted(ks)}")
    bad = [o for o, ks in kinds.items() if len(set(ks)) < 2]
    if bad:
        print(f"  !! {bad} contain only one KIND — a candidate found against this "
              f"corpus cannot be told from a kind byte. Get the missing cell first.")

    same = None
    for o, members in classes.items():
        first = members[0][1]
        agree = np.ones(len(first), bool)
        for _, b in members[1:]:
            agree &= first == b
        same = agree if same is None else (same & agree)
    reps = {o: members[0][1] for o, members in classes.items()}
    names = sorted(reps)
    diff = same.copy()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            diff &= reps[names[i]] != reps[names[j]]
    idx = np.flatnonzero(diff)
    print(f"\nbytes constant inside every outcome class and pairwise different "
          f"between them: {len(idx)}")

    if args.gen4:
        # No 4 MB map to place a hit into: the offset is already relative to a
        # named allocation, because that allocation is all we read.
        layout = layouts[0]
        assert all(l == layout for l in layouts), "the blocks moved between dumps"

        def where(i):
            for name, hdr, off, n in layout:
                if off <= i < off + n:
                    return name, i - off - GEN4_STRUCT, hdr + i - off
            return None, None, None
        rows = []
        for i in idx:
            name, soff, addr = where(int(i))
            rows.append((name, soff, addr,
                         {o: int(reps[o][i]) for o in names}))
        print(f"of those, inside the two battle allocations: {len(rows)}")
        if args.max_value:
            rows = [r for r in rows if all(v < args.max_value for v in r[3].values())]
            print(f"of those, all values below {args.max_value}: {len(rows)}")
        for name, soff, addr, vals in sorted(rows)[: args.show]:
            print(f"  {addr:#010x}  {name} struct+{soff:#x}  " +
                  "  ".join(f"{o}={v}" for o, v in sorted(vals.items())))
        return 0

    maps = [{(b["data"], b["size"]): b for b in blocks(buf)}
            for members in classes.values() for _, buf in members]

    def place(m, a):
        for (d, s), b in m.items():
            if d <= a < d + s:
                return b["tag"], a - d
        return None, None

    rows = []
    for i in idx:
        a = BASE + int(i)
        ps = [place(m, a) for m in maps]
        if ps[0][0] is None or any(p != ps[0] for p in ps):
            continue
        rows.append((ps[0][0], ps[0][1], a, {o: int(reps[o][i]) for o in names}))
    print(f"of those, inside the SAME named allocation at the same offset in "
          f"every dump: {len(rows)}")
    if args.max_value:
        rows = [r for r in rows if all(v < args.max_value for v in r[3].values())]
        print(f"of those, all values below {args.max_value}: {len(rows)}")
    for tag, off, a, vals in sorted(rows)[: args.show]:
        print(f"  {a:#010x}  {tag}+{off:#x}  " +
              "  ".join(f"{o}={v}" for o, v in sorted(vals.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
