#!/usr/bin/env python3
"""find_map_id with a PAIRWISE-DISTINCT rule, and a cache so the walk is paid once.

`find_map_id.py` ranks by distance to the coordinate anchors, because on gen 2/3/4
the map id sits inside the same block as x and y. Gen 5 does not: the coordinates
this repo found for Black live in the player's overworld ACTOR (a VecFx32 at
0x0224f910), and nothing near it is an id -- run the ranked list and the top forty
rows are sprite ids, flags and zeroes.

So this drops the prior and uses a stronger piece of evidence instead. With THREE
maps rather than two, a real map id must take three DIFFERENT values, and
`find_map_id`'s `differs` mask only asks that the first label differ from each
other one (an OR). Requiring every pair to differ is the whole of the change.

The 4 MB x 6 states of reads are cached to an .npz, so the ranking can be
re-cut -- different plausibility caps, different rules -- without walking again.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \\
      v2-experiments/black_mapid_pairwise.py --port 8264 \\
      --state bedroom=... --state bedroom=... --state downstairs=... ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.app.roms import load_roms  # noqa: E402
from find_map_id import REGION, constant_mask, machine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default="black")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", action="append", required=True, help="MAP=path")
    ap.add_argument("--cache", default="local/addr-hunt/black-mapid-cache.npz")
    ap.add_argument("--max-id", type=int, default=2048)
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()

    states = [s.split("=", 1) for s in args.state]
    labels = list(dict.fromkeys(l for l, _ in states))
    cache = Path(args.cache)

    if cache.exists():
        z = np.load(cache)
        print(f"  using cached reads from {cache}")
        rest = {l: z[f"rest_{l}"] for l in labels}
        const = z["const"]
        agree = z["agree"]
    else:
        rom = next((r for r in load_roms() if r.id == args.rom), None)
        if rom is None or not rom.exists():
            raise SystemExit(f"Unknown or missing rom {args.rom!r}")
        emu = machine(rom, args.port)
        emu.start_server()
        try:
            emu.wait_for_connection(timeout=300.0)
            base, span = REGION[emu.system]
            rest, const, agree = {}, None, np.ones(span, dtype=bool)
            for label, path in states:
                print(f"  [{label}] {path}")
                buf, same = constant_mask(emu, path, base, span)
                const = same if const is None else (const & same)
                if label in rest:
                    agree &= (buf == rest[label])
                else:
                    rest[label] = np.array(buf)
                print()
        finally:
            try:
                emu.disconnect()
            except Exception:
                pass
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, const=const, agree=agree,
                            **{f"rest_{l}": rest[l] for l in labels})
        print(f"  cached reads to {cache}")

    base, span = 0x02000000, len(const)
    distinct = np.ones(span, dtype=bool)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            distinct &= (rest[a] != rest[b])
    cand = np.nonzero(const & agree & distinct)[0]
    print(f"  {int((const & agree).sum()):,} bytes constant-while-walking and agreeing "
          f"within each map")
    print(f"  {len(cand):,} of those take a DIFFERENT value in all "
          f"{len(labels)} maps")

    rows = []
    for off in cand.tolist():
        u8 = {l: int(rest[l][off]) for l in labels}
        u16 = None
        if off + 2 <= span:
            u16 = {l: int(rest[l][off]) | (int(rest[l][off + 1]) << 8) for l in labels}
        rows.append({"addr": base + off, "u8": u8, "u16": u16})

    small8 = [r for r in rows if all(v < 256 for v in r["u8"].values())]
    small16 = [r for r in rows if r["u16"] and all(v < args.max_id for v in r["u16"].values())
               and len({*r["u16"].values()}) == len(labels)]
    print(f"  {len(small16):,} also hold a plausible u16 id (< {args.max_id}) "
          f"distinct in every map\n")
    for r in small16[:args.top]:
        u8 = "  ".join(f"{l}={r['u8'][l]}" for l in labels)
        u16 = "  ".join(f"{l}={r['u16'][l]}" for l in labels)
        print(f"    {r['addr']:#010x}  u8[{u8}]   u16[{u16}]")
    print(f"\n  ({len(small8):,} rows if only the u8 has to be a byte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
