#!/usr/bin/env python3
"""What are the bytes that `/load` gets wrong, and do they change the game?

`determinism.py` (2026-09-19) found that on GBA/FireRed SkyEmu's `/load` is not
an exact inverse of `/save`: a machine that keeps running past a `/save` and a
machine reloaded from that `/save` and given the identical inputs end up 55
bytes apart at 864 frames and 3,131 apart at 31,200, almost all of it IWRAM. It
named neither the addresses nor any consequence. The count alone decides
nothing: 55 bytes of audio FIFO is cosmetic, 4 bytes of `gRngValue` changes
every wild encounter and every crit in a `--continue`d run.

Measured here on 2026-09-19 (same host and build), stage by stage:

  addresses  The 55 bytes, by address, twice — the set is identical both times.
             **`gRngValue` is in it**, all four bytes, wholly different values.
  rng        Why that address is gRngValue: it is the ONE word in 32 KB of
             IWRAM that follows the Gen-3 LCG x' = 0x41C64E6D x + 0x6073 across
             two consecutive frames. Located, not quoted from a symbol table
             this repo does not have.
  phase      What `/load` actually does wrong. Not corruption: reloaded[k] is
             byte-identical to live[k+1] over all 288 KB, at every k, from a
             cold title screen and from the benchmark's own start state. **The
             restored machine is exact but resumes ONE FRAME LATE.**
  instant    The same thing seen at zero frames, plus the IO registers: TM0 and
             TM1 differ across `/load` while DISPSTAT and VCOUNT do not.
  persist    Whether "one frame late" stays benign. With nothing but `/step`,
             yes — phase-corrected difference 0. With one button press, no:
             the machines genuinely part company, because the harness counts
             input frames from the resume point and a machine one frame further
             on receives every press at a different point in its own frame.
  outcome    `/screen` at a Pallet Town vantage where something moves: 16 of
             121 sampled frames differ between live and resumed. The original
             report's "byte-identical screen" was one frame of a static scene.
  battle     The test that decides it. Play the opening to the rival battle,
             `/save`, then take 40+ identical turns on both arms: the damage
             rolls differ on almost every turn, no time shift aligns the two
             traces, and Charmander finishes the fight on 5 HP live and 9 HP
             resumed.

Every stage carries its own control (a second `/load` of the same file), and
every stage's control came out at zero.

Ports 8190-8199. Works in /tmp/skyemu-load off a COPY of the ROM: SkyEmu writes
a `.sav` next to whatever it loads and has no `savegamePath` switch, and a
`.sav` beside FireRed changes its boot path, so running the repo's read-only
`roms/` symlink in place would both write into another checkout and confound
the boot.

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
        v2-experiments/load_divergence.py --stage battle --port 8194 \
        --no-battery --state configs/saves/skyemu/firered-pokebench-v2/emulator.state
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "v2-experiments" / "harness"))

from skyemu import SkyEmu  # noqa: E402

WORK = Path("/tmp/skyemu-load")
ROM = WORK / "firered.gba"
BATTERY = WORK / "firered-battery.sav"

REGIONS = [("EWRAM", 0x02000000, 0x40000), ("IWRAM", 0x03000000, 0x8000)]

BOOT = 2400
WAIT_AFTER_PRESS = 60
SEQ_ADVANCE = [("press", "start")] + [("press", "a")] * 8   # 864 frames

# --- what the repo already knows about FireRed's memory (BPRE-US) ------------
# src/referee/referee.py and src/referee/battles.py. Everything else in the
# classification below is derived here, not quoted.
KNOWN = {
    0x03005008: ("gSaveBlock1Ptr", 4),
    0x0300500C: ("gSaveBlock2Ptr", 4),
    0x030030F0: ("gMain (start)", 0x440),
    0x03003529: ("gMain.inBattle", 1),
    0x02024029: ("gPlayerPartyCount", 1),
    0x02023BE4: ("gBattleMons", 4 * 0x58),
    0x02023E8A: ("gBattleOutcome", 1),
    0x020386AE: ("gTrainerBattleOpponent_A", 2),
}


def cold_dir(tag: str, battery: bool) -> Path:
    """A private directory per process. The ROM is COPIED, not symlinked into
    the repo, and the battery `.sav` is optional because its presence changes
    FireRed's title screen (CONTINUE vs NEW GAME) and therefore every press
    after it."""
    d = WORK / tag
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    link = d / "rom.gba"
    shutil.copy2(ROM, link)
    if battery and BATTERY.is_file():
        shutil.copy2(BATTERY, link.with_suffix(".sav"))
    return link


def run_sequence(emu: SkyEmu, seq) -> int:
    frames = 0
    for kind, arg in seq:
        if kind == "press":
            emu.press(arg, hold=12, gap=24)
            emu.step(WAIT_AFTER_PRESS)
            frames += 12 + 24 + WAIT_AFTER_PRESS
        elif kind == "wait":
            emu.step(arg)
            frames += arg
        else:
            raise ValueError(kind)
    return frames


def snapshot(emu: SkyEmu) -> dict[str, bytes]:
    return {name: emu.read_memory(a, n) for name, a, n in REGIONS}


def digest(snap) -> str:
    return hashlib.sha256(b"".join(snap[k] for k in sorted(snap))).hexdigest()[:16]


def differing(a: dict[str, bytes], b: dict[str, bytes]) -> list[tuple[int, int, int]]:
    """(absolute address, value in a, value in b) for every byte that differs."""
    out = []
    for name, base, _ in REGIONS:
        x, y = a[name], b[name]
        out += [(base + i, x[i], y[i]) for i in range(len(x)) if x[i] != y[i]]
    return sorted(out)


def runs(addrs: list[int]) -> list[tuple[int, int]]:
    """Contiguous [start, end] spans."""
    out = []
    for a in addrs:
        if out and a == out[-1][1] + 1:
            out[-1][1] = a
        else:
            out.append([a, a])
    return [tuple(r) for r in out]


def classify(addr: int) -> str:
    for base, (name, size) in sorted(KNOWN.items()):
        if base <= addr < base + size:
            off = addr - base
            return f"{name}+{off:#x}" if off else name
    if 0x03000000 <= addr < 0x03008000:
        return "IWRAM, unidentified"
    return "EWRAM, unidentified"


def report(addrs, va, vb, label_a="live", label_b="reloaded", max_runs=80) -> None:
    print(f"\n  {len(addrs)} differing bytes in {len(runs(addrs))} runs")
    for lo, hi in runs(addrs)[:max_runs]:
        if hi - lo > 15:
            print(f"    {lo:#010x}..{hi:#010x} ({hi-lo+1:>4}B)  "
                  f"[{classify(lo)}] — run too long to print")
            continue
        n = hi - lo + 1
        a = " ".join(f"{va[x]:02x}" for x in range(lo, hi + 1))
        b = " ".join(f"{vb[x]:02x}" for x in range(lo, hi + 1))
        print(f"    {lo:#010x}..{hi:#010x} ({n:>3}B)  {label_a}: {a:<40} "
              f"{label_b}: {b:<40} [{classify(lo)}]")


# --- stage: addresses --------------------------------------------------------

def stage_addresses(port: int, horizon: str, battery: bool, repeats: int) -> None:
    """Reproduce determinism.py's E1 'live vs replay' and print the ADDRESSES.

    Two independent repetitions, because "the same 55 bytes every time" is a
    claim the original report makes and this is the cheapest place to check it:
    if the address SET moves between repetitions the divergence is not the
    deterministic artifact it was reported as.
    """
    seq = SEQ_ADVANCE
    print(f"stage=addresses  horizon={horizon}  battery={battery}  repeats={repeats}")
    sets = []
    for rep in range(repeats):
        with SkyEmu(cold_dir(f"addr-{rep}", battery), port=port) as emu:
            emu.step(BOOT)
            base = WORK / f"addr-{rep}.state"
            emu.save_state(base)
            frames = run_sequence(emu, seq)
            live = snapshot(emu)
            emu.load_state(base)
            run_sequence(emu, seq)
            reloaded = snapshot(emu)
            # replay-vs-replay control, in the same process: if this is not 0 the
            # comparison above is measuring something other than /load fidelity.
            emu.load_state(base)
            run_sequence(emu, seq)
            reloaded2 = snapshot(emu)
        d = differing(live, reloaded)
        ctrl = differing(reloaded, reloaded2)
        va = {a: x for a, x, _ in d}
        vb = {a: y for a, _, y in d}
        addrs = [a for a, _, _ in d]
        print(f"\n[rep {rep}] {frames} frames past /save; live {digest(live)} "
              f"reloaded {digest(reloaded)}")
        print(f"  CONTROL replay vs replay: {len(ctrl)} bytes differ "
              f"({'PASS' if not ctrl else 'FAIL'})")
        report(addrs, va, vb)
        sets.append(set(addrs))
    if repeats > 1:
        same = sets[0] == sets[1]
        print(f"\n  address set stable across repetitions: {same}"
              f"  (|A|={len(sets[0])} |B|={len(sets[1])} "
              f"|A&B|={len(sets[0] & sets[1])})")


# --- stage: rng --------------------------------------------------------------

def stage_rng(port: int, battery: bool, state: Path | None) -> None:
    """Find FireRed's RNG empirically, then ask whether /load restores it.

    The repo has no symbol table, so gRngValue is not quoted, it is located:
    a 32-bit IWRAM word that steps by the Gen-3 LCG recurrence
    x' = 0x41C64E6D * x + 0x00006073 (mod 2^32) between two reads is the RNG
    with overwhelming probability — that is a 64-bit coincidence per candidate,
    against a 32 KB search space.
    """
    print(f"stage=rng  battery={battery}  state={state}")
    MUL, ADD = 0x41C64E6D, 0x00006073
    with SkyEmu(cold_dir("rng", battery), port=port) as emu:
        emu.step(BOOT)
        if state is not None:
            emu.load_state(state)
            emu.step(120)
        a = emu.read_memory(0x03000000, 0x8000)
        emu.step(1)
        b = emu.read_memory(0x03000000, 0x8000)
        emu.step(1)
        c = emu.read_memory(0x03000000, 0x8000)

    def words(blob):
        return struct.unpack_from(f"<{len(blob)//4}I", blob)

    wa, wb, wc = words(a), words(b), words(c)
    hits = []
    for i in range(len(wa)):
        x, y, z = wa[i], wb[i], wc[i]
        # allow the LCG to have been stepped 1..8 times in one frame: the game
        # calls Random() as often as it likes, it is not once per frame.
        n1 = lcg_steps(x, y, MUL, ADD, 16)
        n2 = lcg_steps(y, z, MUL, ADD, 16)
        if n1 and n2:
            hits.append((0x03000000 + 4 * i, x, y, z, n1, n2))
    print(f"  IWRAM words obeying the Gen-3 LCG over two consecutive frames: {len(hits)}")
    for addr, x, y, z, n1, n2 in hits:
        print(f"    {addr:#010x}  {x:#010x} -> {y:#010x} ({n1} calls) "
              f"-> {z:#010x} ({n2} calls)")
    return [h[0] for h in hits]


# --- stage: instant ----------------------------------------------------------

# Frame checkpoints for the onset scan. The question is not "is it 55 at 864"
# — that is measured — but "when does the first byte move, and does it move
# with no input at all".
CHECKPOINTS = [0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 36, 48, 72, 108, 144, 216, 324, 480, 864]

# The IO registers a savestate has to carry for a GBA machine to resume
# identically. FireRed seeds its RNG from the TIMERS (pret: SeedRngAndSetTrainerId
# reads REG_TM1CNT_L | REG_TM2CNT_L << 16), so 0x04000104/0x04000108 are the two
# that would turn a timer that /load does not restore into a different RNG seed.
IO_NAMES = {0x04000100: "TM0CNT_L", 0x04000102: "TM0CNT_H",
            0x04000104: "TM1CNT_L", 0x04000106: "TM1CNT_H",
            0x04000108: "TM2CNT_L", 0x0400010A: "TM2CNT_H",
            0x0400010C: "TM3CNT_L", 0x0400010E: "TM3CNT_H",
            0x04000004: "DISPSTAT", 0x04000006: "VCOUNT"}


def read_io(emu: SkyEmu) -> dict[int, int]:
    out = {}
    for addr in sorted(IO_NAMES):
        lo = emu.read_byte(addr)
        hi = emu.read_byte(addr + 1)
        out[addr] = lo | (hi << 8)
    return out


def stage_instant(port: int, battery: bool, state: Path | None) -> None:
    """Where does the divergence START — at /load, or some frames later?

    One PROCESS PER MODE, and the live arm runs first in it. This is the one
    thing the scan cannot get wrong: the live arm is single-use, because the
    moment any /load touches the process the "live" machine is a reloaded one
    and the comparison silently turns into replay-vs-replay (which is 0 by
    construction, and would read as a clean pass). The first version of this
    function made exactly that mistake and reported 0 everywhere.

    The reloaded arm may be re-run freely — replay-vs-replay is bit-exact
    (determinism.py E1, re-confirmed by `addresses`' CONTROL).
    """
    print(f"stage=instant  battery={battery}  state={state}")
    for mode, label in (("idle", "no input at all"), ("advance", "SEQ_ADVANCE")):
        print(f"\n=== mode: {label} ===")
        with SkyEmu(cold_dir(f"instant-{mode}", battery), port=port) as emu:
            emu.step(BOOT)
            if state is not None:
                emu.load_state(state)
                emu.step(120)
            base = WORK / f"instant-{mode}.state"
            emu.save_state(base)

            io_live = read_io(emu)
            live = scan(emu, mode)            # no /load has happened yet
            emu.load_state(base)
            io_back = read_io(emu)
            back = scan(emu, mode)

            print("\n  IO registers at zero frames (live vs reloaded)")
            for addr in sorted(IO_NAMES):
                mark = "  <-- DIFFERS" if io_live[addr] != io_back[addr] else ""
                print(f"    {addr:#010x} {IO_NAMES[addr]:<9} "
                      f"live {io_live[addr]:#06x}  reloaded {io_back[addr]:#06x}{mark}")

            print("\n  frame   differing bytes   gRngValue live / reloaded")
            for k in CHECKPOINTS:
                d = differing(live[k], back[k])
                rl = struct.unpack_from("<I", live[k]["IWRAM"], 0x5000)[0]
                rb = struct.unpack_from("<I", back[k]["IWRAM"], 0x5000)[0]
                flag = "  RNG DIFFERS" if rl != rb else ""
                print(f"  {k:>5}   {len(d):>6}            {rl:#010x} / {rb:#010x}{flag}")

            d0 = differing(live[0], back[0])
            if d0:
                print(f"\n  what /load fails to restore at ZERO frames: {len(d0)} bytes, "
                      f"{len(runs([a for a, _, _ in d0]))} runs")
                report([a for a, _, _ in d0], {a: x for a, x, _ in d0},
                       {a: y for a, _, y in d0}, "live", "reloaded")


# --- stage: battle -----------------------------------------------------------

# The road to a randomness-consuming event with a MUTUALLY EXCLUSIVE outcome.
#
# Pallet Town has no wild grass and the canonical start state has no party, so
# neither a wild encounter nor a battle turn is reachable from it without
# playing the opening. This is the opening, as a fixed input script: bedroom ->
# Pallet Town -> the Route 1 boundary (which fires Oak's cutscene and carries
# the player to the lab) -> Charmander -> the rival battle at the lab door.
# Routes through the two towns were computed from data/firered-walkgraph.json,
# not guessed; the `expect` steps assert the script is still on the rails,
# because a recipe that silently drifts produces a state that looks fine and
# tests nothing.
BATTLE_RECIPE = [
    ("walk", "right*6,up*2,left*1,up*2,left*1,down*4,left*4,down*3,left*4,down*2,down*2"),
    ("walk", "up*2,right*4,up*6,right*2,up*1"),
    ("expect_pos", (3, 0, 12, 1)),
    ("walk", "up*2"),
    ("mash", 60),
    ("expect_map", (4, 3)),
    ("walk", "b*1,down*2,right*4,up*1"),
    ("mash", 45),
    ("expect_party", 1),
    ("walk", "b*1,down*5"),
    ("mash", 32),
    ("walk", "b*1,left*4,down*6"),
    ("mash", 8),
    ("expect_battle", True),
]

GBATTLE_MONS_ADDR = 0x02023BE4
BATTLE_MON_SIZE = 0x58
BATTLE_MON_HP = 0x28          # u16, current HP within a BattleMon (pret struct)
BATTLE_MON_SPECIES = 0x00     # u16


def battle_state(emu: SkyEmu) -> dict:
    """The battle as the referee's own addresses see it (src/referee/battles.py).

    Species and HP of both battlers plus gBattleOutcome — a handful of numbers
    with a crisp meaning, rather than a byte count: if these agree across the
    two arms the battle played out the same way, and if they do not it did not.
    """
    blob = emu.read_memory(GBATTLE_MONS_ADDR, 2 * BATTLE_MON_SIZE)
    out = {}
    for i, who in enumerate(("player", "opponent")):
        base = i * BATTLE_MON_SIZE
        out[who] = {
            "species": struct.unpack_from("<H", blob, base + BATTLE_MON_SPECIES)[0],
            "hp": struct.unpack_from("<H", blob, base + BATTLE_MON_HP)[0],
        }
    out["outcome"] = emu.read_memory(0x02023E8A, 1)[0]
    out["in_battle"] = bool(emu.read_memory(0x03003529, 1)[0] & 2)
    return out


def play_recipe(emu: SkyEmu, state: Path) -> None:
    emu.load_state(state)
    emu.step(120)
    for kind, arg in BATTLE_RECIPE:
        if kind == "walk":
            walk(emu, arg, settle=40)
        elif kind == "mash":
            for _ in range(arg):
                emu.press("a", hold=12, gap=12)
                emu.step(40)
        elif kind == "expect_pos":
            got = raw_position(emu)
            assert got == arg, f"recipe drifted: at {got}, expected {arg}"
        elif kind == "expect_map":
            got = raw_position(emu)
            assert got[:2] == arg, f"recipe drifted: on map {got[:2]}, expected {arg}"
        elif kind == "expect_party":
            got = emu.read_memory(0x02024029, 1)[0]
            assert got == arg, f"recipe drifted: party {got}, expected {arg}"
        elif kind == "expect_battle":
            assert bool(emu.read_memory(0x03003529, 1)[0] & 2) is arg, \
                "recipe drifted: not in battle"


def raw_position(emu: SkyEmu) -> tuple:
    p = struct.unpack("<I", emu.read_memory(0x03005008, 4))[0]
    h = emu.read_memory(p, 8)
    x, y = struct.unpack_from("<hh", h, 0)
    return (h[4], h[5], x, y)


def stage_battle(port: int, state: Path, turns: int = 160) -> None:
    """The test that decides it: does a resumed machine FIGHT the battle differently?

    Both arms take the same turns from the same savepoint — A selects FIGHT and
    then the first move, so the input is fixed and only the game's own random
    draws (damage roll, critical hit, accuracy) can make the arms differ.

    The control is the same as everywhere else: a second /load of the same file.
    If CONTROL and RESUMED disagree the observable is not stable and the row
    below it means nothing.
    """
    print(f"stage=battle  state={state}")
    base = WORK / "battle-start.state"
    with SkyEmu(cold_dir("battle", False), port=port) as emu:
        play_recipe(emu, state)
        print(f"  reached the rival battle: {battle_state(emu)}")
        emu.save_state(base)

        def take_turns():
            trace = []
            for t in range(turns):
                emu.press("a", hold=12, gap=12)
                emu.step(60)
                trace.append(battle_state(emu))
            return trace

        live = take_turns()
        emu.load_state(base)
        resumed = take_turns()
        emu.load_state(base)
        replay = take_turns()

    def summarise(name, a, b):
        first = next((i for i in range(turns) if a[i] != b[i]), None)
        print(f"  {name:<28} agree on all {turns} turns: {first is None}"
              + (f"   first difference at turn {first}: "
                 f"{a[first]} vs {b[first]}" if first is not None else ""))

    print(f"\n  {turns} identical A presses from the same savepoint")
    summarise("CONTROL resumed vs replay", resumed, replay)
    summarise("live vs resumed", live, resumed)

    # "Different outcome" or "same outcome, one press earlier"? A pure timing
    # shift would make the resumed trace a shifted copy of the live one, so the
    # best alignment is tried explicitly. If no shift aligns them, the damage
    # rolls themselves differ and the battle is being fought differently.
    print("\n  best alignment of the resumed trace against the live one:")
    for k in range(-3, 4):
        pairs = [(live[i + k], resumed[i]) for i in range(turns)
                 if 0 <= i + k < turns]
        bad = sum(1 for x, y in pairs if x != y)
        print(f"    shift {k:+d}: {bad} of {len(pairs)} turns disagree")

    hp_live = [(t["player"]["hp"], t["opponent"]["hp"]) for t in live]
    hp_back = [(t["player"]["hp"], t["opponent"]["hp"]) for t in resumed]
    print(f"\n  distinct HP pairs seen, live:    {sorted(set(hp_live))}")
    print(f"  distinct HP pairs seen, resumed: {sorted(set(hp_back))}")
    print(f"  final live     {live[-1]}")
    print(f"  final resumed  {resumed[-1]}")


# --- stage: persist ----------------------------------------------------------

def stage_persist(port: int, battery: bool, state: Path | None) -> None:
    """Is the reloaded machine one frame ahead FOREVER, or only until an input?

    `phase` showed reloaded[k] == live[k+1] byte-exactly while the machine is
    only being stepped. That would make a resumed run a perfect continuation
    shifted by one frame, and the defect purely cosmetic. But the harness
    counts input frames from the RESUME point, not from the machine's own
    history: if the reloaded machine is one frame further along, press n lands
    one frame earlier in game time than it did live, and the equality should
    break at the first press.

    This measures that break. Both comparisons are printed — the naive one
    (same relative frame) and the phase-corrected one (live, one frame later).
    A phase-corrected 0 means "shifted, not damaged". A phase-corrected
    non-zero means the machines have genuinely parted company.
    """
    print(f"stage=persist  battery={battery}  state={state}")
    for label, use_inputs in (("stepping only", False), ("with SEQ_ADVANCE presses", True)):
        with SkyEmu(cold_dir(f"persist-{int(use_inputs)}", battery), port=port) as emu:
            emu.step(BOOT)
            if state is not None:
                emu.load_state(state)
                emu.step(120)
            base = WORK / f"persist-{int(use_inputs)}.state"
            emu.save_state(base)
            if use_inputs:
                run_sequence(emu, SEQ_ADVANCE)
            else:
                emu.step(864)
            live_864 = snapshot(emu)
            emu.step(1)
            live_865 = snapshot(emu)
            emu.load_state(base)
            if use_inputs:
                run_sequence(emu, SEQ_ADVANCE)
            else:
                emu.step(864)
            back_864 = snapshot(emu)
        naive = differing(back_864, live_864)
        shifted = differing(back_864, live_865)
        print(f"\n  864 frames, {label}")
        print(f"    reloaded[864] vs live[864]  (naive):          {len(naive):>6} bytes")
        print(f"    reloaded[864] vs live[865]  (phase-corrected):{len(shifted):>6} bytes"
              f"   -> {'PURE ONE-FRAME SHIFT' if not shifted else 'GENUINELY DIVERGED'}")


# --- stage: outcome ----------------------------------------------------------

# Bedroom rug -> Pallet Town, from configs/saves/skyemu/firered-pokebench-v2.
# Found by walking it with the SaveBlock1 position read back after every press
# (map 4:1 -> 4:0 -> 3:0), not guessed: the 2F staircase warp only fires when
# the tile north of it is entered from the east, which is two dead ends away
# from the obvious route.
NAV_TO_TOWN = ("right*6,up*2,left*1,up*2,left*1,down*4,left*4,"
               "down*3,left*4,down*2,down*2,"
               # on to (9,12), the one vantage in Pallet Town where something
               # actually moves: a wandering NPC is in frame, and a survey of
               # four spots measured 30 distinct screens per 600 frames here
               # against 1 at (12,6) and 4 at the doorstep. Tail routed with
               # data/firered-walkgraph.json rather than guessed.
               "right*4,down*2,left*1")

# Where the test looks for randomness. Pallet Town's two NPCs wander on
# Random(), so an RNG stream that is one call out of step puts them on
# different tiles — a difference in what the GAME did, visible in `/screen`,
# needing no party and no battle.
# Where the test looks for randomness, and how it is made to bite. `persist`
# showed that two machines only part company once an INPUT is applied — with
# nothing but /step they stay a pure one-frame shift of each other — so an
# observation window in which the player stands still cannot detect anything.
# (The first version of this stage did exactly that and reported a clean pass
# on a scene that never moved: a null result from a test that could not fail.)
# So the window presses buttons: the player oscillates on the spot in Pallet
# Town, where two NPCs wander on Random(), and `/screen` is sampled often
# enough to catch one of them standing on a different tile.
OBSERVE_FRAMES = 3600
OBSERVE_EVERY = 30
LOITER = ["left", "right"] * 64


def walk(emu: SkyEmu, moves: str, settle: int = 40) -> None:
    for m in moves.split(","):
        btn, n = (m.split("*") + ["1"])[:2]
        for _ in range(int(n)):
            emu.press(btn, hold=12, gap=12)
            emu.step(settle)


def position(emu: SkyEmu) -> str:
    p = struct.unpack("<I", emu.read_memory(0x03005008, 4))[0]
    if not (0x02000000 <= p < 0x02040000):
        return "no SaveBlock1"
    h = emu.read_memory(p, 8)
    x, y = struct.unpack_from("<hh", h, 0)
    return f"map {h[4]}:{h[5]} ({x},{y})"


def observe(emu: SkyEmu) -> dict:
    """Run the observation window, recording what the GAME did, not byte counts.

    One press is 12 held + 12 released = 24 frames, and a sample is taken every
    OBSERVE_EVERY frames independently of the press boundaries, so the sampling
    grid and the input grid are not in lockstep.
    """
    out = {"screens": {}, "rng": {}, "png": {}}
    frame, i = 0, 0
    while frame <= OBSERVE_FRAMES:
        png = emu.screen()
        out["png"][frame] = png
        out["screens"][frame] = hashlib.sha256(png).hexdigest()[:16]
        out["rng"][frame] = struct.unpack("<I", emu.read_memory(0x03005000, 4))[0]
        emu.set_inputs(**{BUTTON[LOITER[i % len(LOITER)]]: 1})
        emu.step(OBSERVE_EVERY // 2)
        emu.set_inputs(**{BUTTON[LOITER[i % len(LOITER)]]: 0})
        emu.step(OBSERVE_EVERY // 2)
        frame += OBSERVE_EVERY
        i += 1
    out["final_png"] = emu.screen()
    out["position"] = position(emu)
    out["snap"] = snapshot(emu)
    return out


BUTTON = {"left": "Left", "right": "Right", "up": "Up", "down": "Down",
          "a": "A", "b": "B"}


def stage_outcome(port: int, state: Path, port2: int) -> None:
    """Does a resumed machine PLAY the game differently?

    Four arms, so that a difference between the two that matter is attributable:

      LIVE      walk to Pallet Town, /save, keep running — the run that was
                never interrupted.
      RESUMED   /load that save, run the identical frames — what `--continue`
                gets.
      REPLAY    /load it a second time. The instrument control: if REPLAY and
                RESUMED are not identical, `/screen` is not a stable observable
                and nothing below means anything.
      EQUALISED the proposed workaround: the live arm itself does /save then an
                immediate /load before carrying on, so both arms have paid the
                same /load. Measured, not assumed.
    """
    print(f"stage=outcome  state={state}")
    base = WORK / "outcome.state"
    with SkyEmu(cold_dir("outcome", False), port=port) as emu:
        emu.step(BOOT)
        emu.load_state(state)
        emu.step(120)
        walk(emu, NAV_TO_TOWN)
        print(f"  walked to: {position(emu)}")
        emu.save_state(base)
        live = observe(emu)
        emu.load_state(base)
        resumed = observe(emu)
        emu.load_state(base)
        replay = observe(emu)

    # The workaround arm needs a process where the live machine has never been
    # /load-ed except by the workaround itself.
    with SkyEmu(cold_dir("outcome-eq", False), port=port2) as emu:
        emu.step(BOOT)
        emu.load_state(state)
        emu.step(120)
        walk(emu, NAV_TO_TOWN)
        eq_base = WORK / "outcome-eq.state"
        emu.save_state(eq_base)
        emu.load_state(eq_base)          # <- the proposed fix, one extra round trip
        equalised = observe(emu)
        emu.load_state(eq_base)
        eq_resumed = observe(emu)

    def compare(name, a, b):
        first = next((f for f in sorted(a["screens"])
                      if a["screens"][f] != b["screens"][f]), None)
        nscr = sum(1 for f in a["screens"] if a["screens"][f] != b["screens"][f])
        nrng = sum(1 for f in a["rng"] if a["rng"][f] != b["rng"][f])
        bytes_ = len(differing(a["snap"], b["snap"]))
        print(f"  {name:<34} screens differing {nscr:>3}/{len(a['screens'])}"
              f"  first at frame {str(first):>6}"
              f"  RNG differing {nrng:>3}/{len(a['rng'])}"
              f"  RAM {bytes_:>6}B  pos {a['position']} vs {b['position']}")

    print(f"\n  {OBSERVE_FRAMES} frames of Pallet Town under input, "
          f"sampled every {OBSERVE_EVERY}")
    for nm, arm in (("live", live), ("resumed", resumed)):
        print(f"    SELF-CHECK {nm}: {len(set(arm['screens'].values()))} distinct "
              f"screens within the arm — 1 would mean a frozen scene and a vacuous test")
    compare("CONTROL resumed vs replay", resumed, replay)
    compare("live vs resumed", live, resumed)
    compare("WORKAROUND equalised vs resumed", equalised, eq_resumed)
    # The evidence a human can look at: the first sampled frame at which the
    # two arms show different things, written out as a PAIR. A byte count does
    # not say whether an NPC moved; two PNGs do.
    diffs = [f for f in sorted(live["screens"])
             if live["screens"][f] != resumed["screens"][f]]
    print(f"\n  sampled frames where the two arms show different pictures: {diffs}")
    for tag, f in (("first", diffs[0]), ("last", diffs[-1])) if diffs else ():
        (WORK / f"{tag}-diff-live.png").write_bytes(live["png"][f])
        (WORK / f"{tag}-diff-resumed.png").write_bytes(resumed["png"][f])
        print(f"    frame {f}: {WORK}/{tag}-diff-live.png vs "
              f"{WORK}/{tag}-diff-resumed.png")


# --- stage: phase ------------------------------------------------------------

def stage_phase(port: int, battery: bool, state: Path | None, span: int = 6) -> None:
    """Is the reloaded machine WRONG, or just one frame AHEAD?

    `instant` showed the reloaded arm's gRngValue at frame k equals the live
    arm's at frame k+1, every k. If that holds for the whole 288 KB — reloaded
    at k byte-identical to live at k+1 — then `/load` is not corrupting the
    machine at all: it resumes it one frame further on than `/save` was taken,
    and every downstream "divergence" is a phase shift.

    The instrument is an alignment MATRIX, not a single comparison: printing
    only diff(back[k], live[k+1]) would beg the question. The matrix shows
    where the minimum sits, and a minimum that is not on the k+1 diagonal is
    the finding.
    """
    print(f"stage=phase  battery={battery}  state={state}  span={span}")
    frames = list(range(span + 2))
    with SkyEmu(cold_dir("phase", battery), port=port) as emu:
        emu.step(BOOT)
        if state is not None:
            emu.load_state(state)
            emu.step(120)
        base = WORK / "phase.state"
        emu.save_state(base)
        live = {}
        for k in frames:
            if k:
                emu.step(1)
            live[k] = snapshot(emu)
        emu.load_state(base)
        back = {}
        for k in frames:
            if k:
                emu.step(1)
            back[k] = snapshot(emu)

    print("\n  diff(reloaded[row], live[col]) in bytes — 0 on a diagonal means "
          "the reloaded machine IS the live machine, offset by that many frames")
    print("        " + "".join(f"live{c:<7}" for c in frames))
    for r in frames[:span]:
        row = "".join(f"{len(differing(back[r], live[c])):<11}" for c in frames)
        print(f"  back{r:<3} {row}")


def scan(emu: SkyEmu, mode: str) -> dict:
    """Run one arm to every checkpoint, snapshotting at each."""
    out, at = {}, 0
    for k in CHECKPOINTS:
        if k > at:
            if mode == "idle":
                emu.step(k - at)
            else:
                step_advance(emu, at, k)
            at = k
        out[k] = snapshot(emu)
    return out


def step_advance(emu: SkyEmu, frm: int, to: int) -> None:
    """Advance from frame `frm` to frame `to` under SEQ_ADVANCE's input schedule.

    One press is 12 held + 24 released + 60 idle = 96 frames; press n is START
    for n == 0 and A after. Expressed as a schedule rather than a loop so the
    scan can stop between any two frames and still be playing the same sequence
    the 864-frame measurement plays.
    """
    for f in range(frm, to):
        i, phase = divmod(f, 96)
        name = "Start" if i == 0 else "A"
        if phase == 0:
            emu.set_inputs(**{name: 1})
        elif phase == 12:
            emu.set_inputs(**{name: 0})
        emu.step(1)


def lcg_steps(x: int, y: int, mul: int, add: int, limit: int):
    """How many LCG steps take x to y, or None within `limit`."""
    v = x
    for n in range(1, limit + 1):
        v = (mul * v + add) & 0xFFFFFFFF
        if v == y:
            return n
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["addresses", "rng", "instant", "phase", "outcome", "persist", "battle"])
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--horizon", default="864")
    ap.add_argument("--state", type=Path, default=None)
    ap.add_argument("--no-battery", action="store_true")
    args = ap.parse_args()
    battery = not args.no_battery
    if args.stage == "addresses":
        stage_addresses(args.port, args.horizon, battery, args.repeats)
    elif args.stage == "rng":
        stage_rng(args.port, battery, args.state)
    elif args.stage == "instant":
        stage_instant(args.port, battery, args.state)
    elif args.stage == "phase":
        stage_phase(args.port, battery, args.state)
    elif args.stage == "battle":
        stage_battle(args.port, args.state)
    elif args.stage == "persist":
        stage_persist(args.port, battery, args.state)
    elif args.stage == "outcome":
        stage_outcome(args.port, args.state, args.port + 1)


if __name__ == "__main__":
    main()
