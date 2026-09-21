#!/usr/bin/env python3
"""Score Crystal's battle addresses against the words on the screen.

Input is whatever ``crystal_battle_walk.py`` wrote. Every sample carries the
tilemap it was read beside, so the oracle for each claim is the game's own
printed text and never another memory address:

  kind      "Wild <SPECIES> appeared!" is a wild encounter and
            "<NAME> wants to battle!" is a trainer's. "Got away safely!" is a
            second, independent wild oracle -- gen 2 refuses to let a player
            run from a trainer battle at all.
  species   the enemy's name on tilemap row 0.
  level     the enemy's level on tilemap row 1.
  trainer   the class name printed in the intro ("YOUNGSTER MIKEY").

A sample whose SVBK register is not 1 is UNREADABLE, not "mode 0": 0xd000-0xdfff
is the GBC's switchable WRAM bank and a read taken while another bank is paged
in returns that bank's bytes. Those samples are bridged over rather than
counted, and counted separately as the hazard they are.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v2-experiments"))
from crystal_text import rows as tile_rows  # noqa: E402

D200 = 0xD200
D0C0 = 0xD0C0
#: National Dex numbers -- gen 2's species indices ARE National Dex order.
DEX = {"BULBASAUR": 1, "CATERPIE": 10, "METAPOD": 11, "WEEDLE": 13, "KAKUNA": 14,
       "PIDGEY": 16, "RATTATA": 19, "SPEAROW": 21, "EKANS": 23, "ZUBAT": 41,
       "ODDISH": 43, "POLIWAG": 60, "BELLSPROUT": 69, "GEODUDE": 74,
       "CHIKORITA": 152, "CYNDAQUIL": 155, "TOTODILE": 158, "SENTRET": 161,
       "HOOTHOOT": 163, "LEDYBA": 165, "SPINARAK": 167, "MAREEP": 179, "WOOPER": 194}
#: pokecrystal trainer class constants, for the classes this corpus reaches.
CLASS = {"YOUNGSTER": 22, "BUG": 36, "RIVAL1": 9}

#: The screen's own words, and nothing else, decide a segment's kind.
#:   wild     "Wild ZUBAT appeared!", and two messages gen 2 prints ONLY in a
#:            wild battle: "Got away safely!" and "Can't escape!" -- it refuses
#:            to let the player run from a trainer at all.
#:   trainer  "<NAME> wants to battle!", "<NAME> sent out <MON>!" (a wild mon is
#:            never sent out by anybody) and "<NAME> was defeated!". Three
#:            markers rather than one because a replay can begin MID-battle,
#:            past the intro -- the Bug Catcher Don segment does -- and because
#:            the rival is called "???", which no [A-Z] pattern matches.
WILD = re.compile(r"Wild ([A-Z]+)\s+appeared|Got away safely|Can. escape")
TRAINER = re.compile(r"([A-Z?][A-Z? ]+?)\s+(?:wants to battle|sent out|was defeated)")
FLED = re.compile(r"Got away safely")


def load() -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(ROOT / "v2-experiments/crystal-battle/walk2_*.json"))):
        for r in json.load(open(f)):
            r["src"] = Path(f).stem
            t = bytes.fromhex(r["tiles"])
            r["rows"] = tile_rows(t)
            out.append(r)
    return out


def enemy_name(r: dict) -> str | None:
    """The enemy's species as the HUD prints it, row 0, or None."""
    m = re.match(r"\s*([A-Z]{3,10})\s*$", r["rows"][0].replace("·", " "))
    return m.group(1) if m and m.group(1) in DEX else None


def enemy_level(r: dict) -> int | None:
    """The enemy's level, row 1 -- the level glyph then digits."""
    m = re.search(r"(\d{1,3})", r["rows"][1].replace("·", " "))
    return int(m.group(1)) if m else None


def segments(rows: list[dict]) -> list[dict]:
    """Maximal battle spans, bridging samples whose bank is wrong."""
    out = []
    by_src: dict[str, list[dict]] = {}
    for r in rows:
        by_src.setdefault(r["src"], []).append(r)
    for src, rs in by_src.items():
        val = [(r["d22d"] if r["svbk"] == 1 else None) for r in rs]
        i = 0
        while i < len(val):
            if not val[i]:
                i += 1
                continue
            k = i
            while True:
                j = k + 1
                while j < len(val) and val[j] is None:
                    j += 1
                if j < len(val) and val[j]:
                    k = j
                else:
                    break
            txt = " ".join(" ".join(x.strip() for x in r["rows"] if x.strip())
                           for r in rs[max(0, i - 2):k + 2])
            txt = txt.replace("·", " ")
            out.append({"src": src, "rows": rs[i:k + 1],
                        "values": sorted({v for v in val[i:k + 1] if v}),
                        "unreadable": sum(1 for v in val[i:k + 1] if v is None),
                        "wild": WILD.search(txt), "trainer": TRAINER.search(txt),
                        "fled": bool(FLED.search(txt))})
            i = k + 1
    return out


def main() -> int:
    rows = load()
    print(f"{len(rows)} samples from {len({r['src'] for r in rows})} replays\n")

    # --- the bank hazard, first, because it conditions everything else --------
    bank = Counter(r["svbk"] for r in rows)
    bad = [r for r in rows if r["svbk"] != 1]
    print(f"SVBK: {dict(bank)} -- {len(bad)} of {len(rows)} samples "
          f"({100*len(bad)/len(rows):.1f}%) read the wrong WRAM bank")
    print(f"  values of 0xd22d with SVBK=1: {sorted({r['d22d'] for r in rows if r['svbk']==1})}")
    print(f"  values of 0xd22d with SVBK!=1: {sorted({r['d22d'] for r in bad})}")
    ph = [r for r in bad if bytes.fromhex(r["dcb0"])[5:9] == b"\x00\x00\x00\x00"]
    print(f"  of the wrong-bank samples, {len(ph)} also read the phantom (0,0) map\n")

    # --- A: the kind -----------------------------------------------------------
    segs = segments(rows)
    ok = wrong = 0
    print(f"{len(segs)} battle segments:")
    for s in segs:
        kind = "trainer" if s["trainer"] else ("wild" if s["wild"] else "?")
        want = {"wild": 1, "trainer": 2}.get(kind)
        good = want is not None and s["values"] == [want]
        ok += bool(good)
        wrong += bool(want is not None and not good)
        who = s["trainer"].group(1).strip() if s["trainer"] else (
            s["wild"].group(1) or "" if s["wild"] else "")
        print(f"  {s['src']:16s} n={len(s['rows']):3d} unreadable={s['unreadable']:2d} "
              f"d22d={s['values']} screen={kind:7s} {who:18s} fled={s['fled']} "
              f"{'OK' if good else 'MISMATCH'}")
    print(f"\n  kind: {ok}/{ok+wrong} segments agree with the screen's own wording")

    inbattle = [r for r in rows if r["svbk"] == 1 and r["d22d"]]
    over = [r for r in rows if r["svbk"] == 1 and not r["d22d"]]
    inseg = {id(r) for s in segs for r in s["rows"]}
    print(f"  false positives: {sum(1 for r in inbattle if id(r) not in inseg)} "
          f"of {len(inbattle)} non-zero samples sit outside a screen-confirmed battle")
    print(f"  negative class: {len(over)} readable samples read 0")

    # --- C: species and level ---------------------------------------------------
    named = [r for r in inbattle if enemy_name(r)]
    print(f"\n{len(named)} in-battle samples name the enemy on row 0")
    for label, off, size in (("0xd204", 0xD204 - D200, 1), ("0xd206", 0xD206 - D200, 1),
                             ("0xd236", 0xD236 - D200, 1)):
        hit = sum(1 for r in named if bytes.fromhex(r["dump"])[off] == DEX[enemy_name(r)])
        print(f"  species {label}: {hit}/{len(named)}")
    lvl = [r for r in named if enemy_level(r)]
    scores = Counter()
    for r in lvl:
        d = bytes.fromhex(r["dump"])
        for o in range(len(d)):
            if d[o] == enemy_level(r):
                scores[o] += 1
    print(f"  {len(lvl)} of those also print a level; best level candidates:")
    for o, c in scores.most_common(6):
        print(f"    0x{D200+o:04x}: {c}/{len(lvl)}")

    # --- C: the trainer ---------------------------------------------------------
    print("\n trainer class byte, per trainer segment:")
    for s in segs:
        if not s["trainer"]:
            continue
        who = s["trainer"].group(1).strip()
        vals = Counter(bytes.fromhex(r["dump"])[0xD22F - D200] for r in s["rows"])
        ids = Counter(bytes.fromhex(r["dump"])[0xD231 - D200] for r in s["rows"])
        print(f"  {s['src']:16s} {who:20s} 0xd22f={dict(vals)} 0xd231={dict(ids)}")

    # --- C: the outcome ---------------------------------------------------------
    print("\n 0xd0ee (wBattleResult) around each segment's close:")
    by_src: dict[str, list[dict]] = {}
    for r in rows:
        by_src.setdefault(r["src"], []).append(r)
    for s in segs:
        rs = by_src[s["src"]]
        last = rs.index(s["rows"][-1])
        after = [r for r in rs[last + 1:last + 5] if r["svbk"] == 1]
        kind = "trainer" if s["trainer"] else "wild"
        # Readable samples only: 0xd0ee shares the switchable bank with
        # everything else here, so a wrong-bank read of it is garbage too (127).
        during = sorted({bytes.fromhex(r["d0c0"])[0xD0EE - D0C0]
                         for r in s["rows"] if r["svbk"] == 1})
        print(f"  {s['src']:16s} {kind:7s} fled={str(s['fled']):5s} "
              f"during={during} "
              f"after={[bytes.fromhex(r['d0c0'])[0xD0EE-D0C0] for r in after]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
