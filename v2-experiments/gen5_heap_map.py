#!/usr/bin/env python3
"""Turn a Gen 5 RAM dump into a LABELLED map of the heap.

Gen 5 ships its debug allocator in the retail cartridge: every block is preceded
by a header carrying the magic 0x5544, the block size, the doubly-linked
neighbours, and then the SOURCE FILE NAME of whoever asked for it as a
NUL-terminated string. So a battle dump does not have to be searched byte by
byte for a flag (``local/battleflag/black/NOTES.md``: 18 hours, 14,670
indistinguishable candidates) — the blocks announce themselves, and a field is
an OFFSET into a named allocation.

Header layout, read off Black's block at 0x0226d89c (the one whose data carries
the already-verified foe species at 0x0226d8d4):

    +0x00  u32   0x5544
    +0x04  u32   size
    +0x08  u32   prev block header, 0 at the head
    +0x0c  u32   next block header, 0 at the tail
    +0x10  u16   line number in that file
    +0x12  u16   (unidentified, stable per call site)
    +0x14  char* NUL-terminated file name; the DATA begins after it, rounded
                 up to 4 bytes

    ./venv/bin/python v2-experiments/gen5_heap_map.py DUMP.npy [--grep btl]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np

BASE = 0x02000000
MAGIC = 0x5544
NAME = re.compile(rb"[0-9A-Za-z_./\\-]{2,63}\x00")


def blocks(buf: np.ndarray, base: int = BASE) -> list[dict]:
    """Every allocator header in the dump, with its tag and its data address."""
    w = buf[: len(buf) // 4 * 4].view("<u4")
    out = []
    for idx in np.flatnonzero(w == MAGIC):
        h = int(idx) * 4
        size, prev, nxt = (int(v) for v in w[idx + 1: idx + 4])
        if size == 0 or size > 0x400000:
            continue
        if not all(v == 0 or base <= v < base + len(buf) for v in (prev, nxt)):
            continue
        m = NAME.match(buf[h + 0x14: h + 0x14 + 64].tobytes())
        if not m:
            continue
        name = m.group()[:-1].decode()
        data = h + 0x14 + len(m.group())
        data += (-data) % 4
        out.append({"hdr": base + h, "size": size, "prev": prev, "next": nxt,
                    "line": int(buf[h + 0x10]) | int(buf[h + 0x11]) << 8,
                    "tag": name, "data": base + data})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path)
    ap.add_argument("--grep", default="")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=None,
                    help="print the block CONTAINING this address")
    args = ap.parse_args()
    buf = np.load(args.dump)
    bs = blocks(buf)
    if args.addr is not None:
        near = [b for b in bs if b["data"] <= args.addr < b["data"] + b["size"]]
        near = near or [b for b in bs if b["hdr"] <= args.addr < b["hdr"] + b["size"] + 0x40]
        for b in near:
            print(f"{b['hdr']:#010x} size={b['size']:#x} data={b['data']:#010x} "
                  f"+{args.addr - b['data']:#x} {b['tag']}:{b['line']}")
        return 0
    for b in bs:
        if args.grep and args.grep not in b["tag"]:
            continue
        print(f"{b['hdr']:#010x} size={b['size']:#08x} data={b['data']:#010x} "
              f"{b['tag']}:{b['line']}")
    print(f"# {len(bs)} blocks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
