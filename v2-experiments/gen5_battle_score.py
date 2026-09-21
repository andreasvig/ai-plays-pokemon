#!/usr/bin/env python3
"""Score Black's battle fields against what the SCREEN said, sample by sample.

The labels below are read off each sample's own frame — the "<SPECIES> Lv<N>"
plate the battle HUD draws, and for the kind the gen-5 wording and its
consequences: a lone Pokemon walked into from tall grass with no trainer on the
field against a trainer sprite, an intro naming a trainer, and the prize payout
that only a trainer battle prints. The frame and the memory come off the SAME
loaded state, so there is no pairing question between a screenshot file and the
bytes that were live when it was taken.

Composition is part of the result and is printed with it: a wild-only corpus
cannot refute a wild/trainer discriminator (SoulSilver, and the Emerald false
refutation before it), and neither can a trainer-only one.

    ./venv/bin/python v2-experiments/gen5_battle_score.py --dir local/gen5-battle
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np

BASE = 0x02000000
FOE_SPECIES = 0x0226D8D4          # btl_pokeparam.c data 0x0226d8c0 + 0x14
FOE_LEVEL = FOE_SPECIES + 0xC
BATTLE_KIND = 0x022697B8          # the battle proc's work (procsys.c 0x02269760) + 0x58

#: (dump, kind, species, level, what the frame shows). level None = the frame
#: does not show the foe's plate, so the level is not scored on it.
BLACK: list[tuple[str, str, int, int | None, str]] = [
    ("black_sp/t0010.npy", "trainer", 495, 5, "Bianca's Snivy Lv5, command menu"),
    ("black_ow/t0020.npy", "trainer", 501, None, "Cheren on the field, 'A got P500 for winning!'"),
    ("black_sp/t0130.npy", "trainer", 509, 7, "N's Purrloin Lv7, command menu"),
    ("black_sp/t0212.npy", "trainer", 504, 7, "Youngster's Patrat Lv7, command menu"),
    ("black_sp/t0060.npy", "wild", 506, 2, "wild Lillipup Lv2, command menu"),
]
_HUNT_LEVELS = [3, 3, 4, 4, 4, 3, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]
_HUNT = (["hunt70c/w01.npy", "hunt70c/w02.npy"]
         + [f"hunt70d/w{i:02d}.npy" for i in (1, 2, 3)]
         + [f"hunt70e/w{i:02d}.npy" for i in range(1, 17)])
BLACK += [(p, "wild", 506, lv, "wild Lillipup walked into from Route 1 grass")
          for p, lv in zip(_HUNT, _HUNT_LEVELS)]
# Route 2 — map 319, the SAME map the Youngster stands on, which is what
# decouples "wild" from "Route 1" in the corpus. Each intro frame was captured
# before any press and reads "A wild <SPECIES> appeared!".
BLACK += [
    ("hunt210/w01.npy", "wild", 506, 4, "A wild Lillipup appeared!, Route 2"),
    ("hunt210/w02.npy", "wild", 504, 4, "A wild Patrat appeared!, Route 2"),
    ("hunt210/w03.npy", "wild", 504, 5, "A wild Patrat appeared!, Route 2"),
    ("hunt210/w04.npy", "wild", 504, 5, "A wild Patrat appeared!, Route 2"),
    ("hunt210/w05.npy", "wild", 504, 5, "A wild Patrat appeared!, Route 2"),
    ("hunt_intro2/w01.npy", "wild", 506, 3, "A wild Lillipup appeared!, Route 1"),
    ("hunt_intro2/w02.npy", "wild", 506, 4, "A wild Lillipup appeared!, Route 1"),
    ("hunt_intro2/w03.npy", "wild", 506, 4, "A wild Lillipup appeared!, Route 1"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    args = ap.parse_args()
    ok = {"species": [0, 0], "level": [0, 0], "kind": [0, 0]}
    for rel, kind, species, level, shot in BLACK:
        b = np.load(args.dir / rel)
        sp = struct.unpack_from("<H", b[FOE_SPECIES - BASE:FOE_SPECIES - BASE + 2].tobytes())[0]
        lv = int(b[FOE_LEVEL - BASE])
        raw = struct.unpack_from("<I", b[BATTLE_KIND - BASE:BATTLE_KIND - BASE + 4].tobytes())[0]
        got = "trainer" if raw else "wild"
        for key, want, have in (("species", species, sp), ("level", level, lv),
                                ("kind", kind, got)):
            if want is None:
                continue
            ok[key][1] += 1
            ok[key][0] += want == have
            if want != have:
                print(f"  MISS {rel} {key}: screen={want} memory={have}  ({shot})")
    n_tr = sum(1 for r in BLACK if r[1] == "trainer")
    print(f"corpus: {len(BLACK)} battle states — {n_tr} TRAINER, {len(BLACK) - n_tr} WILD")
    for k, (good, total) in ok.items():
        print(f"  {k:8s} {good}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
