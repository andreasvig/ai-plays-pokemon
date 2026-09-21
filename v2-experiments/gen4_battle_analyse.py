#!/usr/bin/env python3
"""Score the gen-4 battle candidates against the screen, segment by segment.

The scoring rule is the one the Crystal work used and the Emerald work learned
the hard way: a battle's KIND is settled by the words the game prints, never by
another byte. So every segment is reported with the frame that opens it, and
``--montage`` writes those frames out side by side to be read — "A wild BIDOOF
appeared!" against "Youngster Tristan would like to battle!".

    ./venv/bin/python v2-experiments/gen4_battle_analyse.py --game platinum \\
        --walks /tmp/walk/* --montage /tmp/intros.png
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

#: Platinum, and SoulSilver at its own bases. Both are offsets into two heap
#: ALLOCATIONS whose header and size are identical on the two cartridges —
#: BattleSystem (size 0x24a0/0x2494) and BattleContext (size 0x3168) — which is
#: what makes them struct fields rather than addresses that happen to correlate.
FIELDS = {
    "platinum": {
        "flagkey": "overlay", "flag": lambda v: v == 16,
        "bsys": 0x022BF950, "bctx": 0x022C29CC,
        "battle_type": 0x022BF99C, "trainer_id": 0x022BFA12,
        "mons": 0x022C572C,
    },
    "soulsilver": {
        "flagkey": "species", "flag": lambda v: (v & 0xFFFF) != 0,
        "bsys": 0x022C01EC, "bctx": 0x022C32B8,
        "battle_type": 0x022C0238, "trainer_id": 0x022C04AE,
        "mons": 0x022C6018,
    },
}


def read(row: dict, f: dict, key: str, addr: int, fmt: str):
    base = f[key]
    blob = bytes.fromhex(row[key])
    off = addr - base
    if off < 0 or off + struct.calcsize(fmt) > len(blob):
        return None
    return struct.unpack_from(fmt, blob, off)[0]


def segments(rows: list[dict], f: dict) -> list[tuple[int, int]]:
    out, cur = [], None
    for n, r in enumerate(rows):
        raw = struct.unpack("<I", bytes.fromhex(r[f["flagkey"]])[:4])[0] \
            if f["flagkey"] in r else r.get("flag", 0)
        if f["flag"](raw):
            cur = [n, n] if cur is None else [cur[0], n]
        elif cur is not None:
            out.append((cur[0], cur[1]))
            cur = None
    if cur is not None:
        out.append((cur[0], cur[1]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(FIELDS), required=True)
    ap.add_argument("--walks", nargs="+", type=Path, required=True)
    ap.add_argument("--montage", type=Path, default=None)
    args = ap.parse_args()

    f = FIELDS[args.game]
    intros = []
    for d in args.walks:
        sf = d / "samples.json"
        if not sf.is_file():
            continue
        rows = json.loads(sf.read_text())
        if rows and any(k not in rows[0] for k in ("bsys", "bctx")):
            print(f"{d.name}: skipped, sampled before the block regions were widened")
            continue
        for a, b in segments(rows, f):
            ra, rb = rows[a], rows[b]
            bt = read(ra, f, "bsys", f["battle_type"], "<I")
            tid = read(ra, f, "bsys", f["trainer_id"], "<H")
            sp = read(rb, f, "bctx", f["mons"] + 0xC0, "<H")
            lv = read(rb, f, "bctx", f["mons"] + 0xC0 + 0x34, "<B")
            print(f"{d.name:14s} {a:4d}-{b:<4d} n={b - a + 1:3d}  "
                  f"battle_type={bt} trainer={tid}  foe={sp} L{lv}")
            png = ra.get("png") or f"{a:04d}.png"
            intros.append((f"{d.name} {a} bt={bt} tid={tid}", d / "png" / png))
    if args.montage:
        from PIL import Image, ImageDraw
        keep = [(t, p) for t, p in intros if p.is_file()]
        H = 100
        out = Image.new("RGB", (256 + 260, H * len(keep)), (30, 30, 30))
        dr = ImageDraw.Draw(out)
        for i, (t, p) in enumerate(keep):
            im = Image.open(p).convert("RGB").crop((0, 92, 256, 192))
            out.paste(im, (260, i * H))
            dr.text((4, i * H + 40), t, fill=(255, 255, 120))
        args.montage.parent.mkdir(parents=True, exist_ok=True)
        out.save(args.montage)
        print(f"\n{len(keep)} intro frames -> {args.montage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
