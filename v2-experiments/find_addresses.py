#!/usr/bin/env python3
"""Find a game's player-position and map addresses by experiment, not by decomp.

This is P-A of `artifacts/skyemu-backend/cross-game-plan.md`, and it is the gate
for every other game. A shallow ladder ("got a Pokemon", "reached the first
city") needs four values out of a cartridge nobody has mapped: player x, player
y, map id, party count. This script finds them on a running machine.

The method is the one that already worked once. `gRngValue` was located by
searching all 8,192 IWRAM words for the single one obeying the Gen-3 LCG across
consecutive frames (`load_divergence.py --stage rng`) -- exactly one hit, found
rather than quoted from a symbol table this repo does not have. The same shape
works here, with one difference: a position has no arithmetic law to test, so
its law is BEHAVIOURAL. x is the thing that goes up when you walk right, goes
down when you walk left, and does not move when you walk up.

Why this is not just a memory diff
----------------------------------
A single before/after diff over one step returns thousands of changed bytes --
frame counters, sprite state, audio, the RNG. Every claim here is instead a
CONJUNCTION over independent probes:

    x must rise walking right, fall walking left, AND hold still walking up and
    walking down.

Which part of that conjunction does the work is measured, not asserted --
`--control` removes each condition in turn and prints the candidate count.
Measured on FireRed, distinct addresses:

                   all   without magnitude   without reverses   without nulls
    player x         4                  24                  4               5
    player y         3                  20                  5               3

The load-bearing condition is the MAGNITUDE BOUND -- the delta is between one
and four, the size of a step in tiles. Neither of the conditions this file
originally credited does much: the two null probes are worth one candidate on x
and none on y, and the reversal is worth none on x and two on y. Both stay,
because they cost one comparison each and they are the conditions that keep the
search honest on a game whose walking speed is not FireRed's -- but the comment
that used to call the nulls "the whole discriminating power" was a guess, and
the number says otherwise.

(Single-condition ablation measures marginal value, not sufficiency. The
magnitude bound alone is not the method; it is the one whose removal costs the
most.)

Why each probe reloads the save state
-------------------------------------
Probes are independent, not a route. Each starts from `/load` of the same state,
so a wall that blocks one direction cannot displace the ones after it, and no
error accumulates. `/load` is exact for this purpose: `reloaded[k]` is
byte-identical to `live[k+1]` over all of RAM (`load_divergence.py --stage
phase`), and the one frame of slippage is the RNG, not the player's coordinates.

Why presses come in threes
--------------------------
On many games a direction press from a standing start TURNS the character and
only a second one moves it -- that is the whole of the input-timing question in
cross-game-plan.md section 3. Three presses means the finder works whether the
first one turned or walked, and the conditions below are written as inequalities
rather than exact deltas for the same reason. A wall that stops the walk after
two tiles is also survivable; a wall that blocks it entirely is reported as
"0 candidates after <probe>", which names its own fix.

Usage
-----
    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
        v2-experiments/find_addresses.py --stage xy --port 8171 \
        --state configs/saves/skyemu/firered-pokebench-v2/emulator.state

    # and the one that says whether this thing works at all:
    ... --stage verify
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "v2-experiments" / "harness"))

from skyemu import SkyEmu  # noqa: E402

DEFAULT_ROM = REPO / "roms" / "Pokemon - FireRed Version (USA, Europe) (Rev 1).gba"
DEFAULT_STATE = REPO / "configs" / "saves" / "skyemu" / "firered-pokebench-v2" / "emulator.state"
WORK = Path("/tmp/skyemu-find")

# Where a console keeps the work RAM a game's variables live in. Scanned whole,
# because the point of this script is not to know where to look.
#
# NDS main RAM is 4 MB -- 16x the GBA's -- so an NDS scan is 16x the requests.
# That is a wait, not a wall, and it is the only thing here that is per-console
# rather than per-game.
CONSOLE_REGIONS = {
    "GBA": [("EWRAM", 0x02000000, 0x40000), ("IWRAM", 0x03000000, 0x8000)],
    "GB": [("WRAM", 0xC000, 0x2000), ("HRAM", 0xFF80, 0x7F)],
    "NDS": [("MAIN", 0x02000000, 0x400000)],
}

# FireRed's answers, used ONLY by --stage verify. This script must never read
# them while searching: the whole question P-A asks is whether the search finds
# them on its own, and a finder that consults the answer key proves nothing
# about Black 2.
FIRERED_TRUTH = {
    "sb1_ptr": 0x03005008,
    "x_offset": 0x0000,
    "y_offset": 0x0002,
    "map_group_offset": 0x0004,
    "map_num_offset": 0x0005,
    "party_count": 0x02024029,
}

HOLD, GAP, SETTLE = 12, 24, 30

# The project spells the d-pad U/D/L/R (configs/config-*.yaml `valid_inputs`).
# The harness spells it the way SkyEmu does, where "l" and "r" are the SHOULDER
# buttons -- so passing "R" straight through presses a shoulder and the probe
# silently measures nothing. Translated here, once.
DPAD = {"U": "up", "D": "down", "L": "left", "R": "right",
        "A": "a", "B": "b", "START": "start", "SELECT": "select"}


def cold_rom(rom: Path, tag: str) -> Path:
    """A private copy of the ROM per run.

    SkyEmu writes a `.sav` next to whatever ROM it loads and has no
    `savegamePath` switch, so running the repo's `roms/` symlink in place would
    write into another checkout. The battery is deliberately NOT copied: this
    script always starts from an explicit savestate, and a `.sav` beside FireRed
    changes its boot path.
    """
    d = WORK / tag
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    dst = d / ("rom" + rom.suffix)
    shutil.copy2(rom, dst)
    return dst


def snapshot(emu: SkyEmu, regions) -> dict[str, bytes]:
    return {name: emu.read_memory(a, n) for name, a, n in regions}


def run_probe(emu: SkyEmu, state: Path, presses: list[str], regions,
              prelude: list[str] = ()) -> dict[str, bytes]:
    """Load, walk to the probe tile, press, settle, snapshot. The unit of evidence.

    `prelude` exists because the tile a save state happens to sit on is rarely
    free in all four directions, and the null probes are the whole method: if
    walking up is blocked by a bed, "y did not move when I pressed up" is true
    of every byte in the machine and the y search returns nothing. The prelude
    is the operator's job -- scout a tile with a clear square around it -- and
    it is prepended to every probe including `origin`, so all five share one
    frame of reference.
    """
    emu.load_state(state)
    emu.step(SETTLE)
    for button in prelude:
        emu.press(DPAD[button], hold=HOLD, gap=GAP)
    for button in presses:
        emu.press(DPAD[button], hold=HOLD, gap=GAP)
    emu.step(SETTLE)
    return snapshot(emu, regions)


# --- reading a candidate out of a snapshot -----------------------------------

def read_at(buf: bytes, off: int, width: int, signed: bool) -> int:
    return int.from_bytes(buf[off:off + width], "little", signed=signed)


# The four interpretations a position or an id plausibly takes. Unaligned
# offsets are scanned too -- alignment is a fact about a particular compiler's
# struct, not something a finder pointed at an unknown game may assume.
FORMS = [("u8", 1, False), ("s8", 1, True), ("u16", 2, False), ("s16", 2, True)]

# A coordinate moves by tiles. A candidate that jumps by 16 is a pixel or a
# camera, not a tile coordinate, and a candidate that jumps by 3000 is noise.
MAX_TILE_DELTA = 4

# How many candidates are worth printing. Past this the list is a diagnosis.
LIST_CAP = 40

# How much of a block a round trip may rewrite before the word pointing at it
# stops looking like a pointer to a persistent block.
AGREEMENT_FLOOR = 0.8


# The conditions the axis search is a conjunction of. Named so each can be
# switched off independently and its worth measured -- see --control.
AXIS_CONDITIONS = ("magnitude", "reverses", "nulls")


def scan_axis(snaps: dict[str, dict[str, bytes]], regions, axis: str,
              conditions=AXIS_CONDITIONS) -> list[dict]:
    """Every (address, form) whose behaviour matches the axis, with its trace.

    `axis` is "x" (responds to L/R, still under U/D) or "y" (the mirror). The
    sign is NOT assumed -- Gen 3 counts y downward, another game may not -- so
    both orientations are accepted and the one observed is reported.

    Four conditions, and which of them earns its keep is a measured question,
    not an assumed one (`--control` reports the ablation):

      magnitude  the forward and backward deltas are both 1..MAX_TILE_DELTA.
                 A candidate that jumps by 16 is a pixel or a camera; one that
                 jumps by 3000 is noise.
      reverses   walking back undoes it -- the two deltas have opposite signs.
      nulls      the two perpendicular probes moved it by exactly zero.

    The module docstring carries the measured table. The short version is that
    `magnitude` is the one doing the work (x: 4 -> 24 without it) and the other
    two are nearly free riders on FireRed (0 to 2 candidates each). They are
    kept because they cost one comparison and because they, not the magnitude
    bound, are what would still be true on a game that moves two tiles per press.
    """
    fwd, back, null_a, null_b = {
        "x": ("R3", "L3", "U3", "D3"),
        "y": ("D3", "U3", "R3", "L3"),
    }[axis]

    out = []
    for name, base, length in regions:
        o = snaps["origin"][name]
        for label, width, signed in FORMS:
            for off in range(length - width + 1):
                v0 = read_at(o, off, width, signed)
                d_fwd = read_at(snaps[fwd][name], off, width, signed) - v0
                d_back = read_at(snaps[back][name], off, width, signed) - v0
                if d_fwd == 0:
                    continue  # the probe that was meant to move it did not
                if "magnitude" in conditions:
                    if not (1 <= abs(d_fwd) <= MAX_TILE_DELTA):
                        continue
                    if not (1 <= abs(d_back) <= MAX_TILE_DELTA):
                        continue
                if "reverses" in conditions:
                    if d_back == 0 or (d_fwd > 0) == (d_back > 0):
                        continue
                if "nulls" in conditions:
                    if read_at(snaps[null_a][name], off, width, signed) != v0:
                        continue
                    if read_at(snaps[null_b][name], off, width, signed) != v0:
                        continue
                out.append({
                    "addr": base + off,
                    "region": name,
                    "form": label,
                    "origin": v0,
                    "d_fwd": d_fwd,
                    "d_back": d_back,
                    "orientation": "+" if d_fwd > 0 else "-",
                })
    return out


def ablate(snaps, regions, log=print) -> dict:
    """What each condition is worth, measured one removal at a time.

    A conjunction of filters that all look reasonable can still have one doing
    all the work and the rest riding along -- and the one you would name first
    is not reliably the one. Removing a condition and finding the answer
    unchanged means it was not the cause, whatever the comment above it says.
    """
    rows = {}
    for axis in ("x", "y"):
        full = len({c["addr"] for c in scan_axis(snaps, regions, axis)})
        rows[axis] = {"all conditions": full}
        for drop in AXIS_CONDITIONS:
            kept = tuple(c for c in AXIS_CONDITIONS if c != drop)
            rows[axis][f"without {drop}"] = len(
                {c["addr"] for c in scan_axis(snaps, regions, axis, kept)})
    log("\ncontrol -- distinct candidate addresses with one condition removed:")
    names = ["all conditions"] + [f"without {c}" for c in AXIS_CONDITIONS]
    log("  " + "".join(f"{n:>22}" for n in names))
    for axis in ("x", "y"):
        log(f"  {axis}" + "".join(f"{rows[axis][n]:>22}" for n in names))
    return rows


def region_of(regions, addr: int):
    for name, base, length in regions:
        if base <= addr < base + length:
            return name, base, length
    return None


def deref(snap: dict[str, bytes], regions, ptr: int) -> int:
    """The 4-byte value at a bus address, out of a snapshot."""
    hit = region_of(regions, ptr)
    if hit is None:
        return -1
    name, base, _ = hit
    return int.from_bytes(snap[name][ptr - base:ptr - base + 4], "little")


def choose_block_pointer(snaps, regions, ptrs, log=print):
    """Which of the candidate pointer words actually tracks the moving block.

    A map transition re-runs the save-block DMA, so the raw address of the
    player's coordinates changes: measured on FireRed, gSaveBlock1 sits at
    0x0202554c in the bedroom and 0x02025570 after a round trip through the
    floor below; measured on Emerald, 0x02025a54 downstairs and 0x02025a64
    upstairs. Comparing raw addresses across a transition therefore finds
    nothing, which is exactly the failure plan section 1 predicts and the reason
    the per-game contract needs a pointer+offset form at all.

    The right pointer is picked by evidence, not by name, on two criteria:

      * **it moved.** A word whose value is the same on both sides of the
        transition is not tracking anything -- following it is identical to
        using the raw address, which the other scan already does. This is the
        criterion the first version of this function lacked, and it chose a
        static word in EWRAM that scored a perfect 1.0 precisely BECAUSE
        nothing it pointed at ever changed.
      * **agreement.** Dereference it on both sides and count how many bytes of
        a 0x1300-byte window agree at the same offset from its own value. The
        block is mostly unchanged by a round trip -- flags, party, name, money
        -- so a pointer really tracking it scores near 1.0, while a word that
        merely happens to hold a nearby address scores like noise.

    Agreement is a FLOOR, not the ranking. FireRed's SaveBlock1 and SaveBlock2
    move together, so gSaveBlock2Ptr + 0xFA4 reaches the player's x just as
    gSaveBlock1Ptr + 0 does -- and SaveBlock2 scores HIGHER (0.982 against
    0.865) precisely because its contents did not change, which is the opposite
    of what identifies the block the coordinates live in. So among the movers
    that clear the floor, the block is the one whose base sits closest below the
    anchor.

    A game with no shuffle has no movers, and that is a finding rather than a
    failure: the function says so and the raw scan is the whole answer.
    """
    elsewhere = [k for k in ("map_out", "map_alt", "map_back") if k in snaps]
    scored = []
    for cand in ptrs:
        a = deref(snaps["origin"], regions, cand["ptr"])
        b = deref(snaps["map_back"], regions, cand["ptr"])
        # Movement is looked for across EVERY map probe, not just the round
        # trip. Both games measured here happen to leave the block displaced
        # after walking back -- FireRed's DMA drifts continuously, so map_back
        # is never quite where origin was -- but that is a property of those two
        # cartridges, not of map transitions. A game that restores the block
        # exactly on return would make map_back alone say "nothing moved" and
        # the pointer form would be thrown away on a game that needs it.
        moved = max((deref(snaps[k], regions, cand["ptr"]) - a for k in elsewhere),
                    key=abs, default=0)
        ra, rb = region_of(regions, a), region_of(regions, b)
        if ra is None or rb is None:
            continue
        na, ba, la = ra
        nb, bb, lb = rb
        n = min(0x1300, la - (a - ba), lb - (b - bb))
        if n <= 0:
            continue
        wa = snaps["origin"][na][a - ba:a - ba + n]
        wb = snaps["map_back"][nb][b - bb:b - bb + n]
        agree = sum(1 for i in range(n) if wa[i] == wb[i]) / n
        # Agreement is still measured against the round trip, which is the
        # state most like the origin and so the fairest window.
        scored.append({**cand, "moved": moved, "agreement": round(agree, 4)})

    movers = [c for c in scored if c["moved"] != 0 and c["agreement"] >= AGREEMENT_FLOOR]
    movers.sort(key=lambda c: (c["offset"], -c["agreement"]))
    if not movers:
        log("  no candidate pointer moved across the transition -- this game does not "
            "shuffle its block, so the raw addresses are the answer")
        return scored
    for c in movers[:5]:
        log(f"  *{c['ptr']:#010x} -> {c['value']:#010x} moved {c['moved']:+#x} "
            f"agreement {c['agreement']:.3f}")
    return movers


def scan_map(snaps, regions, block_ptr=None) -> list[dict]:
    """Every value that changes on a map transition and ONLY then.

    Three conditions, and the second is free because the xy probes already ran:

      1. it is back to its original value after walking there and back,
      2. it did not move for any of the four ordinary walking probes,
      3. it differs in at least one of the other maps visited.

    Condition 2 is what separates a map id from the thousands of bytes a map
    load rewrites -- tile buffers, sprite tables, the camera. Those move when
    you walk, too. Condition 1 is what separates it from every counter a
    transition increments, since those do not come back.

    Condition 3 is "at least one", not "both", and that is deliberate. FireRed's
    bedroom and the floor below it are map 4:1 and 4:0 -- the same map GROUP. A
    round trip between them cannot reveal the group byte at all, because it
    never changes. The second, farther transition (outdoors, map 3:0) is what
    makes the group visible, and requiring both to move would have thrown the
    group away to buy a filter the other conditions already provide.

    Scanned twice over. Once at RAW addresses, which is what a game without a
    DMA shuffle needs and what fixed-address values like the party count use.
    Once at offsets RELATIVE to `block_ptr`, which is the only way to see a
    value inside a block that moved -- and both games measured so far move it.
    """
    out = []
    probes = [p for p in ("R3", "L3", "U3", "D3") if p in snaps]
    elsewhere = [p for p in ("map_out", "map_alt") if p in snaps]
    walked = [(w, w[:-len("_walked")]) for w in ("map_out_walked", "map_alt_walked")
              if w in snaps]

    for name, base, length in regions:
        o = snaps["origin"][name]
        for label, width, signed in FORMS:
            for off in range(length - width + 1):
                v0 = read_at(o, off, width, signed)
                if not any(read_at(snaps[p][name], off, width, signed) != v0
                           for p in elsewhere):
                    continue
                if read_at(snaps["map_back"][name], off, width, signed) != v0:
                    continue
                if any(read_at(snaps[p][name], off, width, signed) != v0 for p in probes):
                    continue
                if any(read_at(snaps[w][name], off, width, signed)
                       != read_at(snaps[m][name], off, width, signed) for w, m in walked):
                    continue
                out.append({
                    "spelling": "raw", "addr": base + off, "region": name,
                    "form": label, "origin": v0,
                    "away": [read_at(snaps[p][name], off, width, signed) for p in elsewhere],
                })

    if block_ptr is not None:
        bases = {k: deref(snaps[k], regions, block_ptr) for k in snaps}
        spans = {k: region_of(regions, b) for k, b in bases.items()}
        if all(spans.values()):
            limit = min(sp[1] + sp[2] - bases[k] for k, sp in spans.items())
            for label, width, signed in FORMS:
                for k in range(min(limit, 0x1400) - width + 1):
                    def at(probe, _k=k, _w=width, _s=signed):
                        nm, bs, _ = spans[probe]
                        return read_at(snaps[probe][nm], bases[probe] - bs + _k, _w, _s)
                    v0 = at("origin")
                    if not any(at(p) != v0 for p in elsewhere):
                        continue
                    if at("map_back") != v0:
                        continue
                    if any(at(p) != v0 for p in probes):
                        continue
                    if any(at(w) != at(m) for w, m in walked):
                        continue
                    out.append({
                        "spelling": "pointer", "ptr": block_ptr, "offset": k,
                        "addr": bases["origin"] + k, "region": "block",
                        "form": label, "origin": v0,
                        "away": [at(p) for p in elsewhere],
                    })
    return out


def rank_by_run(cands: list[dict]) -> list[dict]:
    """Group map-id candidates into contiguous runs and put the short ones first.

    The filters leave two kinds of survivor and they are not equally likely to
    be an id. A map id is a short isolated field -- FireRed's is two bytes, the
    map group and the map number, sitting alone. The rest is map CONTENT:
    object-event templates, per-map script state, the buffers a map load
    rewrites. Content is bulk, and bulk is contiguous.

    So the discriminator is shape, not value. Two refinements matter:

      * **runs are computed per SPELLING.** A raw address and a
        pointer-relative one are both absolute EWRAM addresses and they
        interleave, which merged the answer's 3-byte run into a 70-byte one and
        made the ranking useless. They are different address spaces as far as
        this question goes.
      * **a run repeated at a constant stride is an array**, not a field. On
        FireRed ten identical 4-byte runs 0x14 apart survive every filter --
        they are per-map script state, and no id is ever the 7th element of
        something. Demoted, not dropped.

    This is a ranking. Nothing is discarded: a game that buries its map id in
    the middle of a struct ranks low but is still in the list.
    """
    out = []
    for spelling in sorted({c.get("spelling", "raw") for c in cands}):
        group = [c for c in cands if c.get("spelling", "raw") == spelling]
        addrs = sorted({c["addr"] for c in group})
        runs, i = [], 0
        while i < len(addrs):
            j = i
            while j + 1 < len(addrs) and addrs[j + 1] == addrs[j] + 1:
                j += 1
            runs.append((addrs[i], j - i + 1))
            i = j + 1
        # An arithmetic progression of same-length runs is an array.
        strides: dict[tuple, int] = {}
        for k in range(len(runs) - 1):
            if runs[k][1] == runs[k + 1][1]:
                strides[(runs[k + 1][0] - runs[k][0], runs[k][1])] = \
                    strides.get((runs[k + 1][0] - runs[k][0], runs[k][1]), 0) + 1
        arrayish = {st for st, n in strides.items() if n >= 3}
        start_of, len_of, array_of = {}, {}, {}
        for idx, (start, n) in enumerate(runs):
            in_array = any(
                (runs[idx + d][0] - start, n) in arrayish
                for d in (1, -1) if 0 <= idx + d < len(runs) and runs[idx + d][1] == n)
            for a in range(start, start + n):
                start_of[a], len_of[a], array_of[a] = start, n, in_array
        for c in group:
            c["run"] = len_of[c["addr"]]
            c["run_start"] = start_of[c["addr"]]
            c["in_array"] = array_of[c["addr"]]
        out += group
    return sorted(out, key=lambda c: (c["in_array"], c["run"], c["addr"]))


def summarise_runs(cands: list[dict], label: str, block_ptr=None, log=print) -> None:
    """A map scan's result is a list of RUNS, because that is what it means.

    Printed per byte, FireRed's answer is 3 lines lost among 116. Printed as
    runs it is one line out of 27, and the line says
    `+0x0003..+0x0005  [1024, 4, 1] -> [.., 4, 3] / [.., 0, 0]` -- the map group
    holding at 4 and dropping to 3 outdoors, the map number going 1 -> 0.
    """
    if not cands:
        log(f"\n{label}: NO CANDIDATES.")
        return
    seen: dict[tuple, dict] = {}
    for c in cands:
        seen.setdefault((c.get("spelling", "raw"), c["run_start"]), c)
    # In-block runs first. On a game whose block shuffles they are the only
    # spelling that means anything -- a raw address inside the block is valid
    # for one frame of one boot -- and they are where the answer is.
    runs = sorted(seen.values(),
                  key=lambda c: (c.get("spelling") != "pointer", c["in_array"],
                                 c["run"], c["addr"]))
    n_block = sum(1 for c in runs if c.get("spelling") == "pointer")
    log(f"\n{label}: {n_block} run(s) inside the block, {len(runs) - n_block} at raw "
        f"addresses outside it")
    by = {c["addr"]: c for c in cands if c["form"] == "u8"}
    for c in runs[:LIST_CAP]:
        a, n = c["run_start"], c["run"]
        cells = [by.get(a + i) for i in range(n)]
        here = [x["origin"] if x else "." for x in cells]
        away = list(zip(*[x["away"] if x else ["."] * 2 for x in cells]))
        where = (f"*{block_ptr:#010x}+{a - (c['addr'] - c['offset']):#06x}"
                 if c.get("spelling") == "pointer" else f"{a:#010x}")
        tag = " (array)" if c["in_array"] else ""
        log(f"  {where:<22} x{n:<2} here={here} -> " +
            " / ".join(str(list(w)) for w in away) + tag)
    if len(runs) > LIST_CAP:
        log(f"  ... and {len(runs) - LIST_CAP} more")


def find_pointer_forms(snaps, regions, addr: int) -> list[dict]:
    """The pointer+offset spellings of a raw address, derived not assumed.

    Gen 3 DMA-shuffles its save blocks, so a raw address is not stable across
    boots -- FireRed's player x reads 0x0202552C pre-game and 0x02025594 in the
    bedroom (plan section 3.4). The per-game contract therefore has to be able to
    say "dereference this word, then add this offset".

    This does not assume the pointer exists. It reports every word in RAM whose
    value is a plausible base for `addr`, together with the offset -- and a word
    that holds the same value in every probe (which a pointer does, and a
    coordinate does not) is the one worth believing.
    """
    # Stability is judged over the WALKING probes only. The map probes are where
    # the block moves -- that is the whole reason a pointer form is needed -- so
    # asking a pointer to hold the same value across them would reject exactly
    # the pointer being looked for.
    walking = [k for k in ("origin", "R3", "L3", "U3", "D3") if k in snaps]
    hits = []
    for name, base, length in regions:
        o = snaps["origin"][name]
        for off in range(0, length - 3):
            ptr_value = int.from_bytes(o[off:off + 4], "little")
            delta = addr - ptr_value
            if not (0 <= delta < 0x4000):
                continue
            stable = all(int.from_bytes(snaps[k][name][off:off + 4], "little") == ptr_value
                         for k in walking)
            hits.append({"ptr": base + off, "value": ptr_value, "offset": delta,
                         "stable": stable})
    return hits


def collect_party(emu, pairs, null_pairs, regions, prelude=(), log=print):
    """Save-state pairs that straddle the event, each with its own null probes.

    The party count is the one value of the four that no route can produce on
    demand -- reaching the starter is twenty minutes of scripted play, and a
    finder that needs that before it can answer anything is not a tool. So it is
    found the way it will be found on any game: from two save states that
    straddle the event, which is a thing any run of the benchmark produces for
    free.

    More than one pair is worth the seconds it costs. A single pair is two
    arbitrary moments in one run and hundreds of counters differ by one between
    them for reasons that have nothing to do with a Pokemon; two pairs from
    DIFFERENT runs agree only where the difference is caused by the event.

    The FireRed pairs are savepoints of real v2 runs: turn_10 (Oak's lab, party
    0) and turn_20 (same map, party 1).

    The walking probes are pure null tests here -- the party count does not
    change when you walk, and almost everything else in a machine mid-run does.
    A direction blocked by a wall simply tests less; it does not invalidate.
    """
    snaps = {}
    for idx, (before, after) in enumerate(pairs):
        for tag, state in (("before", before), ("after", after)):
            for name, presses in [("", []), ("R3", ["R"] * 3), ("L3", ["L"] * 3),
                                  ("U3", ["U"] * 3), ("D3", ["D"] * 3)]:
                key = f"{idx}_{tag}{'_' + name if name else ''}"
                t0 = time.time()
                snaps[key] = run_probe(emu, state, presses, regions, prelude)
                log(f"  probe {key:<14} {time.time() - t0:5.1f}s")
    # The negative control: a pair of states from the same run across which the
    # party did NOT change. No walking probes -- all it has to do is catch a
    # counter that goes up by one between any two moments, and it catches a lot.
    for idx, (before, after) in enumerate(null_pairs):
        for tag, state in (("nullbefore", before), ("nullafter", after)):
            snaps[f"{idx}_{tag}"] = run_probe(emu, state, [], regions, prelude)
            log(f"  probe {idx}_{tag:<13} (control)")
    return snaps


# A count is a count. The signed readings are dropped for this scan because
# 0xFF -> 0x00 is "+1" as s8 and is a WRAP, not an increment -- 900 of the 935
# candidates the first version returned were exactly that.
COUNT_FORMS = [(label, w, sg) for label, w, sg in FORMS if not sg]


def scan_party(snaps, regions, n_pairs: int, n_null: int = 0) -> list[dict]:
    """Every value that is one higher after the Pokemon, in every pair, and
    still while walking.

    Per pair:
      1. it reads exactly one more after the Pokemon was received,
      2. walking does not change it in the first state,
      3. walking does not change it in the second.

    Condition 1 alone matches every counter that happened to advance by one
    between two unrelated save points, and there are thousands. Conditions 2 and
    3 are what make it an answer: a counter that moves with the game clock, the
    step count or the frame does not survive being walked on twice. Requiring
    all of it in two independent runs is the replication.

    And one negative control, which is the condition that earns the most here
    (935 candidates -> 322 with unsigned-only, -> 81 with this):
    across a pair of states the party did NOT cross, the value must not move
    either. Two arbitrary moments in a run differ by one in hundreds of places
    -- a dense strided array in scratch EWRAM accounted for most of what
    survived everything else -- and almost none of those hold still when the
    event they are supposedly counting does not happen.
    """
    out = []
    for name, base, length in regions:
        for label, width, signed in COUNT_FORMS:
            for off in range(length - width + 1):
                vb = None
                # Cross-run agreement, when there is more than one pair. The party
                # count is a FUNCTION OF THE PARTY: two runs that both have one
                # Pokemon must read the same number, whatever else differs about
                # where they are and what they have done.
                #
                # UNTESTED. Measured on FireRed it changes nothing -- 81
                # candidates with it and 81 without -- and the reason is not that
                # the condition is weak but that the evidence is: the three v2
                # runs it was given are the same model from the same start state
                # and they took the SAME PATH, reading map 4:3 at (9,5) at turn 10
                # in all three. Three copies of one trajectory are one
                # observation. Re-measure this against runs from different models
                # before believing it does anything.
                if n_pairs > 1:
                    firsts = {read_at(snaps[f"{i}_before"][name], off, width, signed)
                              for i in range(n_pairs)}
                    lasts = {read_at(snaps[f"{i}_after"][name], off, width, signed)
                             for i in range(n_pairs)}
                    if len(firsts) != 1 or len(lasts) != 1:
                        continue
                for i in range(n_pairs):
                    b = read_at(snaps[f"{i}_before"][name], off, width, signed)
                    a = read_at(snaps[f"{i}_after"][name], off, width, signed)
                    if a - b != 1:
                        break
                    if any(read_at(snaps[f"{i}_before_{d}"][name], off, width, signed) != b
                           for d in ("R3", "L3", "U3", "D3")):
                        break
                    if any(read_at(snaps[f"{i}_after_{d}"][name], off, width, signed) != a
                           for d in ("R3", "L3", "U3", "D3")):
                        break
                    vb = b
                else:
                    if any(read_at(snaps[f"{i}_nullafter"][name], off, width, signed)
                           != read_at(snaps[f"{i}_nullbefore"][name], off, width, signed)
                           for i in range(n_null)):
                        continue
                    out.append({"spelling": "raw", "addr": base + off, "region": name,
                                "form": label, "origin": vb, "away": [vb + 1]})
    return out


# --- the probe set ------------------------------------------------------------

def collect(emu, state: Path, regions, out_route, back_route, alt_route=(),
            settle_route=(), prelude=(), log=print):
    """Every probe, each from a fresh `/load`. Returns {probe: {region: bytes}}."""
    plan = [
        ("origin", []),
        ("R3", ["R", "R", "R"]),
        ("L3", ["L", "L", "L"]),
        ("U3", ["U", "U", "U"]),
        ("D3", ["D", "D", "D"]),
    ]
    if out_route:
        plan.append(("map_out", out_route))
        plan.append(("map_back", out_route + back_route))
    if alt_route:
        plan.append(("map_alt", alt_route))
    if out_route and settle_route:
        plan.append(("map_out_walked", out_route + settle_route))
    if alt_route and settle_route:
        plan.append(("map_alt_walked", alt_route + settle_route))
    snaps = {}
    for name, presses in plan:
        t0 = time.time()
        snaps[name] = run_probe(emu, state, presses, regions, prelude)
        log(f"  probe {name:<9} {len(presses):>2} presses  {time.time() - t0:5.1f}s")
    return snaps


def summarise(cands: list[dict], label: str, log=print) -> None:
    if not cands:
        log(f"\n{label}: NO CANDIDATES. Either the probe that was supposed to move it "
            f"did not, or a null probe moved it. The usual cause is a start tile that "
            f"is not free in all four directions -- see --prelude.")
        return
    by_addr: dict[tuple, list[dict]] = {}
    for c in cands:
        by_addr.setdefault((c.get("spelling", "raw"), c["addr"]), []).append(c)
    keys = sorted(by_addr, key=lambda k: (by_addr[k][0].get("run", 0), k[0], k[1]))
    log(f"\n{label}: {len(keys)} distinct address(es), {len(cands)} (address, form) pair(s)")
    if len(keys) > LIST_CAP:
        log(f"  too many to be an answer -- showing the first {LIST_CAP}. A result this "
            f"wide means a filter is missing, not that the value is ambiguous.")
        keys = keys[:LIST_CAP]
    for key in keys:
        forms = by_addr[key]
        c = forms[0]
        where = (f"*{c['ptr']:#010x}+{c['offset']:#06x}" if c.get("spelling") == "pointer"
                 else f"{c['addr']:#010x}")
        extra = (f"origin={c['origin']} d_fwd={c['d_fwd']:+d} d_back={c['d_back']:+d}"
                 if "d_fwd" in c else f"origin={c['origin']} away={c['away']}")
        log(f"  {where:<22} {c['region']:<5} [{'/'.join(f['form'] for f in forms)}]  {extra}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="verify",
                    choices=["xy", "map", "party", "verify"])
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--state-after", type=Path, default=None,
                    help="--stage party: a state from the same game AFTER the party grew "
                         "by one. Any run's savepoints straddle the starter. Comma-"
                         "separated for several pairs, which is the replication control")
    ap.add_argument("--port", type=int, default=8171)
    ap.add_argument("--console", default=None, help="GBA/GB/NDS; measured if omitted")
    ap.add_argument("--out-route", default="",
                    help="comma-separated presses that leave the current map, e.g. D,D,L,L,U")
    ap.add_argument("--back-route", default="",
                    help="presses that return from it")
    ap.add_argument("--walk-route", default="",
                    help="a few presses to take AFTER arriving, inside the destination "
                         "map. A map id does not move when you walk around in a map; "
                         "the tile buffers and scratch that a map load rewrote do. This "
                         "is the filter that turns tens of thousands of candidates into "
                         "a handful, so it is worth scouting")
    ap.add_argument("--alt-route", default="",
                    help="presses to a DIFFERENT map, ideally across a map-group "
                         "boundary; without one, a byte that is constant between the "
                         "two maps of the round trip cannot be seen at all")
    ap.add_argument("--prelude", default="",
                    help="presses that walk from the save state's tile to one free in "
                         "all four directions; prepended to every probe")
    ap.add_argument("--null-before", default="",
                    help="--stage party: states across which the party did NOT change, "
                         "paired with --null-after. The negative control")
    ap.add_argument("--null-after", default="")
    ap.add_argument("--control", action="store_true",
                    help="re-run the axis scan with each condition removed in turn and "
                         "report what each one is worth")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    prelude = [b.strip().upper() for b in args.prelude.split(",") if b.strip()]
    out_route = [b.strip().upper() for b in args.out_route.split(",") if b.strip()]
    alt_route = [b.strip().upper() for b in args.alt_route.split(",") if b.strip()]
    settle_route = [b.strip().upper() for b in args.walk_route.split(",") if b.strip()]
    back_route = [b.strip().upper() for b in args.back_route.split(",") if b.strip()]
    if args.stage == "party" and not args.state_after:
        print("--stage party needs --state-after: the count is found from a pair of "
              "states that straddle the event, not from a route.", file=sys.stderr)
        return 2
    if args.stage in ("map", "verify") and not out_route:
        print("--stage map needs --out-route: the finder cannot invent a route out of a "
              "room it has never seen. One transition, and the presses that undo it.",
              file=sys.stderr)
        return 2

    rom = cold_rom(args.rom, f"find-{args.port}")
    with SkyEmu(rom, port=args.port) as emu:
        console = args.console or emu.system()
        regions = CONSOLE_REGIONS[console]
        total = sum(n for _, _, n in regions)
        print(f"console {console}, scanning {total/1024:.0f} KB across "
              f"{len(regions)} region(s)")

        if args.stage == "party":
            befores = [Path(x) for x in str(args.state).split(",")]
            afters = [Path(x) for x in str(args.state_after).split(",")]
            if len(befores) != len(afters):
                print("--state and --state-after must list the same number of paths",
                      file=sys.stderr)
                return 2
            pairs = list(zip(befores, afters))
            nb = [Path(x) for x in args.null_before.split(",") if x]
            na = [Path(x) for x in args.null_after.split(",") if x]
            null_pairs = list(zip(nb, na))
            snaps = collect_party(emu, pairs, null_pairs, regions, prelude)
            cands = rank_by_run(scan_party(snaps, regions, len(pairs), len(null_pairs)))
            summarise_runs(cands, "party count")
            if args.json:
                args.json.write_text(json.dumps({"console": console, "party": cands},
                                                indent=2))
                print(f"\nwrote {args.json}")
            want = FIRERED_TRUTH["party_count"]
            got = {c["addr"] for c in cands}
            print(f"\nFireRed keeps it at {want:#010x}: "
                  f"{'FOUND' if want in got else 'MISSING'} among {len(got)} candidate(s)")
            return 0

        snaps = collect(emu, args.state, regions, out_route, back_route,
                        alt_route, settle_route, prelude)

        t0 = time.time()
        xs = scan_axis(snaps, regions, "x")
        ys = scan_axis(snaps, regions, "y")
        result_control = ablate(snaps, regions) if args.control else None
        print(f"\nscanned the two axes in {time.time() - t0:.1f}s")
        summarise(xs, "player x")
        summarise(ys, "player y")
        result = {"console": console, "x": xs, "y": ys, "map": []}
        if result_control:
            result["condition_ablation"] = result_control

        # The pointer spellings of the x candidate. A raw address is what the
        # contract stores when it survives; this is what it stores when it does
        # not, and the shuffle is common enough that the finder looks every time.
        block_ptr = None
        if xs:
            anchor = min(c["addr"] for c in xs)
            ptrs = [q for q in find_pointer_forms(snaps, regions, anchor) if q["stable"]]
            result["x_pointer_forms"] = ptrs
            print(f"\n{len(ptrs)} pointer spelling(s) of {anchor:#010x}, stable across "
                  f"every walking probe:")
            for q in sorted(ptrs, key=lambda q: q["offset"])[:10]:
                print(f"  *{q['ptr']:#010x} = {q['value']:#010x}  + {q['offset']:#06x}")
            if out_route and ptrs:
                print("\nwhich of them tracks the block across the map transition:")
                scored = choose_block_pointer(snaps, regions, ptrs)
                result["block_pointer_scores"] = scored
                if scored and scored[0].get("moved"):
                    block_ptr = scored[0]["ptr"]
                    print(f"  -> using *{block_ptr:#010x} (moved "
                          f"{scored[0]['moved']:+#x} across the round trip)")
                elif scored:
                    print("  -> no pointer agrees above 0.9; scanning raw addresses only")

        if out_route:
            t0 = time.time()
            maps = rank_by_run(scan_map(snaps, regions, block_ptr))
            print(f"\nscanned for the map id in {time.time() - t0:.1f}s")
            summarise_runs(maps, "map id", block_ptr)
            result["map"] = maps
            result["block_pointer"] = block_ptr
        else:
            maps = []

        if args.stage == "verify":
            result["verdict"] = verify(snaps, regions, xs, ys, maps, block_ptr)

        if args.json:
            args.json.write_text(json.dumps(result, indent=2))
            print(f"\nwrote {args.json}")
    return 0


# A candidate list wider than this is not an answer, however right it is. The
# number is a judgement, not a measurement: a per-game contract is written by
# hand from this output, and a human can read sixty lines and pick the pair of
# adjacent bytes that look like an id. Thirty thousand is a different activity.
WIDTH_CAP = 64

# An id is a short field. Longer contiguous runs are map CONTENT -- see
# rank_by_run. Used only to say where the verdict is taken, not to drop
# anything.
MAX_ID_RUN = 8


def verify(snaps, regions, xs, ys, maps, block_ptr) -> dict:
    """Does the finder rediscover what FireRed is already known to keep where?

    P-A is a rehearsal with the answers in the back of the book, and this is the
    only place the book is opened -- nothing in the search above reads
    FIRERED_TRUTH. A finder that cannot recover FireRed's addresses is not ready
    to be pointed at a cartridge we cannot check.

    Two things are asserted, and the second is the one that bites:

      * **inclusion.** The known address is among the candidates. Inclusion, not
        uniqueness: several addresses legitimately hold the player's x -- the
        save block, the sprite, a camera anchor -- and the referee needs a
        correct one, not the only one.
      * **width.** The candidate list is no wider than WIDTH_CAP. Without this
        the test cannot fail: an earlier revision returned 28,813 addresses for
        the map id and reported PASS, because a list that large contains the
        right answer by construction. A check that a defect cannot break is not
        a check.

    The map id is judged on the POINTER spelling, not the raw address, because
    on a game whose block shuffles the raw address is meaningless -- the same
    earlier revision "found" map_group at a raw address that held the byte 4 for
    unrelated reasons.
    """
    sb1 = deref(snaps["origin"], regions, FIRERED_TRUTH["sb1_ptr"])
    print(f"\n--- verify against FireRed ---\n"
          f"gSaveBlock1Ptr (*{FIRERED_TRUTH['sb1_ptr']:#010x}) -> {sb1:#010x}")
    verdict: dict = {}

    def check(key, expected, found_addrs, spelling):
        ok = expected in found_addrs
        wide = len(found_addrs) > WIDTH_CAP
        verdict[key] = {"expected": expected, "found": ok, "distinct": len(found_addrs),
                        "within_width_cap": not wide, "spelling": spelling}
        note = "FOUND" if ok else "MISSING"
        if wide:
            note += f", but {len(found_addrs)} candidates is not an answer"
        print(f"  {key:<11} expect {expected:#010x}  {note}"
              f"   ({len(found_addrs)} distinct, via {spelling})")
        return ok and not wide

    raw_x = {c["addr"] for c in xs}
    raw_y = {c["addr"] for c in ys}
    ok = check("x", sb1 + FIRERED_TRUTH["x_offset"], raw_x, "raw address")
    ok &= check("y", sb1 + FIRERED_TRUTH["y_offset"], raw_y, "raw address")

    block_cands = [c for c in maps if c.get("spelling") == "pointer"] or list(maps)
    spelling = f"*{block_ptr:#010x}" if block_ptr else "raw address (no block pointer)"
    # Judged on the SHORT-RUN tier only -- the ranking above is what makes the
    # output an answer rather than a haystack, so the test has to be taken at
    # the place a human would actually read.
    short = [c for c in block_cands
             if c.get("run", 99) <= MAX_ID_RUN and not c.get("in_array")]
    in_block = {c["addr"] for c in short}
    n_runs = len({c["run_start"] for c in short})
    print(f"  (of {len({c['addr'] for c in block_cands})} in-block addresses, "
          f"{len(in_block)} sit in {n_runs} run(s) of {MAX_ID_RUN} bytes or fewer)")
    ok &= check("map_group", sb1 + FIRERED_TRUTH["map_group_offset"], in_block, spelling)
    ok &= check("map_num", sb1 + FIRERED_TRUTH["map_num_offset"], in_block, spelling)

    # And the pointer form, which is the thing the per-game contract stores.
    forms = find_pointer_forms(snaps, regions, sb1 + FIRERED_TRUTH["x_offset"])
    ptr_ok = any(q["ptr"] == FIRERED_TRUTH["sb1_ptr"] and q["offset"] == 0 for q in forms)
    chose_it = block_ptr == FIRERED_TRUTH["sb1_ptr"]
    verdict["sb1_pointer"] = {"expected": FIRERED_TRUTH["sb1_ptr"], "found": ptr_ok,
                              "chosen": block_ptr, "chose_it": chose_it}
    chosen = f"*{block_ptr:#010x}" if block_ptr else "none"
    print(f"  {'sb1_pointer':<11} expect *{FIRERED_TRUTH['sb1_ptr']:#010x}+0  "
          f"{'FOUND' if ptr_ok else 'MISSING'}   (chose {chosen})")
    ok &= ptr_ok and chose_it

    verdict["pass"] = bool(ok)
    print(f"\nP-A {'PASSES' if ok else 'FAILS'} on FireRed.")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
