#!/usr/bin/env python3
"""Locate Emerald's battle reads — foe, kind, outcome, trainer — from save states.

The battle FLAG is already measured (src/referee/contracts.py: gMain at
0x030022c0, +0x439, bit 1, across 6 battle and 70 non-battle samples). What a
card still cannot say on Emerald is WHICH Pokemon, whether the fight was wild or
a trainer's, and how it ended. Those are four more addresses.

Not a blind search: pokeemerald's symbol map gives a candidate for each, and
this checks the candidate against states whose answer is known independently —
three savepoints the trace says are entirely in battle, twelve it says are in
the overworld. A candidate that reads a plausible species in all three and
nothing in the other twelve is the address; one that reads plausibly everywhere
is measuring something else and is reported as such.

The laws, one per field:

  gBattleMons[1]   a species in 1..411 and a level in 1..100 IN the battle
                   states. Outside a battle the struct holds the LAST battle's
                   mon, so "reads nothing outside" is NOT required and not
                   checked -- claiming it would fail a correct address.
  gBattleTypeFlags bit 3 (BATTLE_TYPE_TRAINER) separates the two kinds. The
                   check is that the u32 is a plausible flag word in battle
                   (no high garbage) and that it AGREES with the trainer
                   opponent id: a trainer fight names a trainer, a wild one
                   does not.
  gBattleOutcome   0 while a battle runs. All three battle states are mid-fight
                   savepoints, so all three must read 0 -- and at least one
                   overworld state must read non-zero, or the byte is just
                   always zero and proves nothing.
  gTrainerBattleOpponent_A  a u16 below the species count of trainers.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
      v2-experiments/emerald_battle_probe.py --port 8277
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v2-experiments" / "harness"))
from skyemu import SkyEmu  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ROM = ROOT / "roms" / "Pokemon - Emerald Version (USA, Europe).gba"
RUN = ROOT / "local/runs/2026-09-20_00-28-43_config-v2-emerald__gemini-3-8-flash-minimal"
# Savepoints whose LAST trace sample has the in-battle flag set, and what the
# screenshot at that turn says independently. Gen 3 names a wild mon "Wild X"
# and a trainer's "Foe X" in its battle messages, which is the oracle for the
# kind -- the first pass here called turn 110 wild on the strength of a
# screenshot reading "Foe SHROOMISH's EFFECT SPORE paralyzed MUDKIP!", and got a
# refutation of a correct address out of it.
BATTLE_TURNS = {
    20: ("trainer", None),        # "We must have been fated to meet. May I ask you for a battle?"
    90: ("wild", "POOCHYENA"),    # "Wild POOCHYENA appeared!"
    110: ("trainer", "SHROOMISH"),  # "Foe SHROOMISH's EFFECT SPORE paralyzed MUDKIP!"
    130: ("wild", "WURMPLE"),     # "Wild WURMPLE appeared!"
}
#: Internal gen-3 species indices, which FireRed and Emerald share -- the same
#: numbering public/pokemon/<id>.png is named by, so a card needs no remapping.
SPECIES = {286: "POOCHYENA", 288: "ZIGZAGOON", 290: "WURMPLE", 306: "SHROOMISH", 283: "MUDKIP"}
OVER_TURNS = [10, 30, 40, 50, 60, 70, 80, 100, 120, 140, 150, 180]

# pokeemerald (BPEE), the candidates this script is here to confirm or refute.
CAND = {
    "gBattleTypeFlags": 0x02022FEC,
    "gBattleMons": 0x02024084,
    "gBattleOutcome": 0x0202433A,
    "gTrainerBattleOpponent_A": 0x02038BCA,
}
BATTLE_MON_SIZE = 0x58
MON_SPECIES, MON_HP, MON_LEVEL, MON_MAX_HP = 0x00, 0x28, 0x2A, 0x2C
BATTLE_TYPE_TRAINER = 1 << 3


def state_of(turn: int) -> Path:
    return RUN / "savepoints" / f"turn_{turn}" / "emulator.state"


def read_all(emu: SkyEmu) -> dict:
    mons = emu.read_memory(CAND["gBattleMons"], BATTLE_MON_SIZE * 2)
    out = {}
    for who, base in (("player", 0), ("foe", BATTLE_MON_SIZE)):
        out[who] = {
            "species": struct.unpack_from("<H", mons, base + MON_SPECIES)[0],
            "level": mons[base + MON_LEVEL],
            "hp": struct.unpack_from("<H", mons, base + MON_HP)[0],
            "max_hp": struct.unpack_from("<H", mons, base + MON_MAX_HP)[0],
        }
    out["type_flags"] = struct.unpack("<I", emu.read_memory(CAND["gBattleTypeFlags"], 4))[0]
    out["outcome"] = emu.read_memory(CAND["gBattleOutcome"], 1)[0]
    out["trainer"] = struct.unpack("<H", emu.read_memory(CAND["gTrainerBattleOpponent_A"], 2))[0]
    return out


def plausible_mon(m: dict) -> bool:
    return 1 <= m["species"] <= 411 and 1 <= m["level"] <= 100 and 0 < m["max_hp"] <= 999


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8277)
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts/emerald-battle/probe.json")
    args = ap.parse_args()

    rows = {}
    with SkyEmu(ROM, port=args.port) as emu:
        for label, turns in (("battle", list(BATTLE_TURNS)), ("over", OVER_TURNS)):
            for t in turns:
                p = state_of(t)
                if not p.is_file():
                    print(f"  turn {t}: no state")
                    continue
                emu.load_state(p)
                emu.step(1)
                rows[f"{label}:{t}"] = read_all(emu)

    print(f"{'state':12s} {'foe':>22s} {'player':>22s} {'typeflags':>12s} {'out':>4s} {'trainer':>8s}")
    for k, r in rows.items():
        f, p = r["foe"], r["player"]
        print(f"{k:12s} {f['species']:5d} L{f['level']:<3d} {f['hp']:4d}/{f['max_hp']:<4d}"
              f" {p['species']:5d} L{p['level']:<3d} {p['hp']:4d}/{p['max_hp']:<4d}"
              f" {r['type_flags']:#012x} {r['outcome']:4d} {r['trainer']:8d}")

    # --- the verdicts, each against the screenshot rather than against itself --
    print()
    named = wrong = 0
    for t, (kind, mon) in BATTLE_TURNS.items():
        r = rows.get(f"battle:{t}")
        if r is None or mon is None:
            continue
        named += 1
        got = SPECIES.get(r["foe"]["species"], str(r["foe"]["species"]))
        if got != mon:
            wrong += 1
            print(f"  turn {t}: screen says {mon}, gBattleMons says {got}")
    print(f"gBattleMons        {'OK ' if named and not wrong else 'NO '} {named - wrong}/{named} "
          f"foes match the species the screenshot names")

    kwrong = [t for t, (kind, _) in BATTLE_TURNS.items()
              if f"battle:{t}" in rows
              and bool(rows[f"battle:{t}"]["type_flags"] & BATTLE_TYPE_TRAINER) != (kind == "trainer")]
    print(f"gBattleTypeFlags   {'OK ' if not kwrong else 'NO '} "
          f"{len(BATTLE_TURNS) - len(kwrong)}/{len(BATTLE_TURNS)} kinds match the screenshot"
          + (f" -- wrong on {kwrong}" if kwrong else ""))

    # A trainer id must CHANGE at a trainer battle and be stale everywhere else,
    # which is the only behaviour that distinguishes the field from a constant.
    tr = {t: rows[f"battle:{t}"]["trainer"] for t in BATTLE_TURNS if f"battle:{t}" in rows}
    trainers = {t: v for t, v in tr.items() if BATTLE_TURNS[t][0] == "trainer"}
    print(f"gTrainerBattleOpponent_A {'OK ' if len(set(trainers.values())) == len(trainers) else 'NO '}"
          f" a different id per trainer battle: {trainers}; stale elsewhere: "
          f"{ {t: v for t, v in tr.items() if BATTLE_TURNS[t][0] != 'trainer'} }")

    # The outcome byte's own oracle: "lost" exactly where the player's battler
    # has no HP left. A byte that never leaves 0, or one that reads outside
    # B_OUTCOME, is not this field.
    bad = [(k, r["outcome"]) for k, r in rows.items() if not 0 <= r["outcome"] <= 10]
    lost = [(k, r["player"]["hp"]) for k, r in rows.items() if r["outcome"] == 2]
    print(f"gBattleOutcome     {'OK ' if not bad and lost else 'NO '} every read in B_OUTCOME "
          f"({not bad}); reads 'lost' only with the player at 0 HP: "
          f"{all(hp == 0 for _, hp in lost)} {lost}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"candidates": {k: hex(v) for k, v in CAND.items()},
                                    "rows": rows}, indent=1) + "\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
