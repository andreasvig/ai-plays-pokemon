#!/usr/bin/env python3
"""Is a SkyEmu run reproducible? Four experiments and a control.

    venv/bin/python v2-experiments/determinism.py                       # FireRed
    venv/bin/python v2-experiments/determinism.py --system NDS \
        --rom "v2-experiments/roms/Pokemon - Platinum Version (USA).nds"

Risk #2 in `artifacts/skyemu-backend/plan.md` §6: v1 (mGBA driven by wall-clock
sleeps) is not reproducible, and v2 *ought* to be, because `/step?frames=N` is an
exact frame count rather than a `time.sleep`. "Ought to" is not a measurement, and
a benchmark that claims reproducibility it does not have is worse than one that
claims nothing. Every number below was measured by this file on 2026-09-19 (macOS
25.6, arm64, SkyEmu built from `01516d6` + the repo patch). The snapshot is
EWRAM+IWRAM on GBA (288 KB) and two main-RAM windows on NDS (512 KB).

The short answer: **a replay is reproducible on both systems, and each system has
exactly one defect — mirror images of each other.** On GBA `/load` is not an exact
inverse of `/save`; on NDS two cold processes are nine bytes apart.

**CONTROL — does the snapshot move at all?**  Two DIFFERENT input sequences of the
SAME frame count from the SAME savestate. Identical frame counts matter: anything
advancing per frame (the RNG, vblank counters, animation timers) reaches the same
value in both arms, so every byte that differs differs *because of the inputs*. If
these came out equal the snapshot region would not be where the game lives and
every "identical" below would be vacuous.
*FireRed 7,463 of 294,912 (2.53%); Platinum 20,977 of 524,288 (4.00%).*
The control earned its keep twice — see SEQ_CONTRAST and BOOT.

**E1 — savestate round trip, one process.** Two different claims, reported
separately because on this backend they disagree:
 - *replay vs replay* — load, run, load, run. The reproducibility the benchmark
   needs: every episode started from a start-state gets the same machine.
   **0 bytes on both systems, at both horizons.**
 - *live vs replay* — the sequence run straight on from `/save`, against the same
   sequence after `/load`. Fidelity: is `/load` an exact inverse of `/save`?
   **Platinum 0. FireRed NO: 55 bytes at 864 frames (12 EWRAM, 43 IWRAM) and
   3,131 at 31,200 frames (28 EWRAM, 3,103 IWRAM) — it grows.** The screen stays
   byte-identical throughout, and the live arm agrees with E2's cold-booted arm
   exactly, so the defect is in `/load`, not in `/save` or in stepping.

**E2 — cold start, two processes.** Two SkyEmu processes, each from a cold ROM
load, each running boot frames + the identical sequence. Each arm gets its own
directory holding a symlink to the ROM and a private copy of the `.sav`: SkyEmu
writes that battery file, and on FireRed a `.sav` beside the ROM changes the boot
path, so two arms sharing one would have a channel between them shaped exactly
like nondeterminism.
*FireRed: **0** of 294,912, `/screen` PNG byte-identical.*
*Platinum: **7-10** of 524,288 — six stable locations inside the 378-byte window
0x02101d2c..0x02101ea5, `/screen` still byte-identical. Within a pair every
differing location is off by the SAME constant (0x44, 0x50, 0x28 measured),
including a 16-bit little-endian field, so this is one value seen through several
views, not six faults. It appears with no inputs at all and does not scale with
the gap between launches — 90 s apart gave a smaller offset than back-to-back.
What the value is: not identified.*

**E3 — the savestate file itself.** A SkyEmu savestate is a PNG, so a raw file
diff is a bad instrument: change one byte early in the zlib stream and the rest of
the file moves, which is how a difference of tens of bytes reads as "178 KB of a
180 KB file differ". E3 decompresses to the pixel payload and brackets the
comparison with three controls:
 - *writer* — two `/save` from one frozen machine, 2 s apart: **byte-identical
   files** on both systems. No timestamp, no nondeterministic compression.
 - *E2's two files* — differ. FireRed ~110-170 of 3,840,800 payload bytes, tens of
   runs, inside 5 of 800 image rows, every delta <= 4. Platinum ~1.03 M of
   25,168,896, which is uninterpretable while E2 itself is 10 bytes apart.
 - *frame 0* — four cold processes, `/save` at **zero frames emulated**: already
   16-29 payload bytes apart, ~7-15 offsets differing in every pair. The difference
   predates emulation. It is not the ROM path either: two processes given the
   identical path still differ.
 - *propagation* — reload both and run 3,864 further identical frames. The test is
   GROWTH, not identity: FireRed 0 -> 0, Platinum 10 -> 10.

**E4 — long horizon.** E1 and E2 again at ~10 minutes of console time (200 presses
across six buttons plus a 12,000-frame tail).
*FireRed 33,600 frames: cold-vs-cold still 0; live-vs-replay 55 -> 3,131.*
*Platinum 38,400 frames: cold-vs-cold still 7, same six locations; live-vs-replay
still 0.*

What this does NOT cover: ~38,400 frames. A 30-hour FireRed run is ~6.5 million,
two orders of magnitude further out. It does not cover a battery save, a battle, a
second host, a second SkyEmu build, or any GBA title with an RTC (FireRed has
none; Emerald does). The snapshot is RAM: VRAM, OAM, palette and audio are
untested except as they show through `/screen`. And emulator reproducibility is
not run reproducibility — the model's sampling is the other half.

Needs no API key. ~5 minutes per system; writes only under `--workdir`.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import shutil
import struct
import sys
import time
import zlib
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "harness"
sys.path.insert(0, str(HARNESS))

from skyemu import SkyEmu  # noqa: E402

# The memory a GBA game lives in. EWRAM holds the save blocks and most game
# state; IWRAM holds the hot structures the referee reads (GSAVEBLOCK1_PTR is
# 0x03005008). 288 KB together — one `read_memory` call each, ~0.7 s.
#
# On NDS 0x02000000 is 4 MB of shared main RAM; two 256 KB windows are taken
# instead of all of it — one at the base and one at 0x02100000, which
# emulator-research.md already measured as dense, changing memory rather than the
# static ARM9 binary the page boundaries hold. `map=9` reads the ARM9 view.
REGIONS = {
    "GBA": [("EWRAM", 0x02000000, 0x40000, None),
            ("IWRAM", 0x03000000, 0x08000, None)],
    "NDS": [("MAIN-RAM@0", 0x02000000, 0x40000, 9),
            ("MAIN-RAM@1M", 0x02100000, 0x40000, 9)],
}

# Frames from a cold ROM load to a screen where input does something. FireRed's
# title screen is up well before 2,400. Platinum's is at 7,200 — at 3,000 it is
# still in the unskippable Giratina cinematic, where the control fails because
# nothing the player presses changes anything (measured: 6 of 524,288 bytes).
BOOT = {"GBA": 2400, "NDS": 7200}

# The sequences. Both arms of the control are nine presses with a 60-frame wait
# after each — 9 * (12 hold + 24 gap + 60) = 864 frames, identical in both, so the
# arms differ only in which buttons were held.
SEQ_ADVANCE = [("press", "start")] + [("press", "a")] * 8
SEQ_CONTRAST = {
    # FireRed: START leaves the title screen into the CONTROLS tutorial, A walks
    # forward through it into Oak's "Hello, there!" intro, and B backs out of it —
    # verified by screenshot, two different screens rather than two framings of one.
    "GBA": [("press", "start"), ("press", "a"), ("press", "a")] + [("press", "b")] * 6,
    # Platinum needs its own, because the GBA one is a NO-OP here: B advances
    # Pokemon dialogue exactly as A does, and start/a/a/b*6 comes out
    # BYTE-IDENTICAL to start/a*8 (measured: 0 of 524,288). Withholding START
    # instead leaves arm B on the title screen's copyright card while arm A
    # reaches "My name is Rowan." — 4.00% of the snapshot apart.
    "NDS": [("press", "b")] * 9,
}
WAIT_AFTER_PRESS = 60

# E4's sequence: six buttons, so a direction that does nothing on one screen does
# something on the next. 200 presses = 19,200 frames.
SEQ_LONG = [("press", b) for b in ["a", "b", "down", "a", "right",
                                   "a", "up", "b", "left", "a"] * 20]


# --- mechanics -----------------------------------------------------------

def run_sequence(emu: SkyEmu, seq) -> int:
    """Play `seq`. Returns the frame count, which the caller asserts is equal
    across the control's two arms — an unequal count would make the control
    measure elapsed time instead of input."""
    frames = 0
    for kind, arg in seq:
        if kind != "press":
            raise ValueError(kind)
        emu.press(arg, hold=12, gap=24)
        emu.step(WAIT_AFTER_PRESS)
        frames += 12 + 24 + WAIT_AFTER_PRESS
    return frames


def snapshot(emu: SkyEmu, system: str) -> dict[str, bytes]:
    return {name: emu.read_memory(addr, length, map_)
            for name, addr, length, map_ in REGIONS[system]}


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()[:16]


def digest(snap: dict[str, bytes]) -> str:
    return sha(b"".join(snap[k] for k in sorted(snap)))


def diff(a: dict[str, bytes], b: dict[str, bytes]) -> dict:
    """How many bytes differ, and where — not merely whether any do.

    "not identical" is a much weaker finding than "17 bytes differ, all inside a
    32-byte region", so the count, the extent and the number of contiguous runs
    are all reported. Runs matter: 8,000 bytes in 4 runs is a few moved
    structures, 8,000 bytes in 4,000 runs is noise everywhere.
    """
    out = {"total_bytes": 0, "differing": 0, "regions": {}}
    for name in sorted(a):
        x, y = a[name], b[name]
        assert len(x) == len(y), name
        offs = [i for i in range(len(x)) if x[i] != y[i]]
        out["total_bytes"] += len(x)
        out["differing"] += len(offs)
        out["regions"][name] = {
            "length": len(x), "differing": len(offs), "runs": count_runs(offs),
            "first": offs[0] if offs else None, "last": offs[-1] if offs else None,
            "sha_a": sha(x), "sha_b": sha(y),
        }
    return out


def count_runs(offs: list[int]) -> int:
    return sum(1 for n, i in enumerate(offs) if n == 0 or i != offs[n - 1] + 1)


def report_diff(label: str, d: dict, expect: str) -> bool:
    pct = 100.0 * d["differing"] / d["total_bytes"]
    print(f"  {label}: {d['differing']:,} / {d['total_bytes']:,} bytes differ ({pct:.2f}%)")
    for name, r in d["regions"].items():
        where = "" if r["first"] is None else \
            f", first {r['first']:#x}, last {r['last']:#x}, {r['runs']} runs"
        print(f"    {name:<12} {r['differing']:>7,} / {r['length']:>7,}"
              f"  sha {r['sha_a']} vs {r['sha_b']}{where}")
    ok = (d["differing"] == 0) if expect == "identical" else (pct > 1.0)
    print(f"    -> expected {expect}: {'PASS' if ok else 'FAIL'}")
    return ok


def png_payload(path: Path) -> tuple[tuple, bytes]:
    """A SkyEmu savestate is a PNG. Comparing the FILE compares a zlib stream, in
    which one changed byte moves everything after it — which is how a difference
    of tens of bytes reads as "178 KB of a 180 KB file differ". Decompress first;
    then the number means something."""
    d = path.read_bytes()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    i, idat, ihdr = 8, b"", None
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        typ, data = d[i + 4:i + 8], d[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", data)
        elif typ == b"IDAT":
            idat += data
        i += 12 + ln
    return ihdr, zlib.decompress(idat)


def report_state_pair(label: str, pa: Path, pb: Path) -> tuple[bool, list[int]]:
    a, b = pa.read_bytes(), pb.read_bytes()
    _, ra = png_payload(pa)
    _, rb = png_payload(pb)
    offs = [i for i in range(min(len(ra), len(rb))) if ra[i] != rb[i]]
    deltas = sorted({min(abs(ra[i] - rb[i]), 256 - abs(ra[i] - rb[i])) for i in offs})
    print(f"  {label}")
    print(f"    file    {len(a):,} vs {len(b):,} bytes, {'IDENTICAL' if a == b else 'differ'}"
          f"  sha {sha(a)} / {sha(b)}")
    if a == b:
        return True, []
    print(f"    payload {len(ra):,} bytes, {len(offs)} differ in {count_runs(offs)} runs"
          + (f", first {offs[0]:#x}, last {offs[-1]:#x}" if offs else "")
          + (f"; |delta| in {deltas}" if deltas else ""))
    return False, offs


def cold_dir(workdir: Path, rom: Path, tag: str) -> Path:
    """A private working directory for one cold process.

    The ROM is symlinked (SkyEmu only reads it) and the battery `.sav` beside it
    is COPIED, because SkyEmu writes that file — and a `.sav` changes FireRed's
    boot path, so two arms sharing one would be a confound shaped exactly like
    nondeterminism. It also keeps every write off the repo's `roms/`, which is a
    read-only symlink into another checkout.
    """
    d = workdir / tag
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    link = d / ("rom" + rom.suffix)
    link.symlink_to(rom.resolve())
    sav = rom.with_suffix(".sav")
    if sav.is_file():
        shutil.copy2(sav, link.with_suffix(".sav"))
    return link


# --- the experiments -----------------------------------------------------

def control_and_e1(rom, port, system, workdir, seq=SEQ_ADVANCE, idle=0,
                   tag="warm", label="", do_control=True) -> tuple[bool, bool, bool, int]:
    """CONTROL and E1 in one process, because both hang off one base savestate.

    E1 splits in two, because the two halves are different claims and on this
    backend they do not agree:

      * **replay vs replay** — load, run, load, run. This is the reproducibility
        the benchmark needs: every episode started from a start-state gets the
        same machine.
      * **live vs replay** — the sequence run straight on from `/save`, against
        the same sequence after `/load`. This is fidelity: whether `/load` is an
        exact inverse of `/save`. It is the comparison the brief asked for, and
        rolling it into the first would hide a real result.
    """
    base = workdir / f"{tag}.state"
    with SkyEmu(cold_dir(workdir, rom, tag), port=port) as emu:
        emu.step(BOOT[system])
        emu.save_state(base)

        fa = run_sequence(emu, seq) + idle
        emu.step(idle)
        snap_live = snapshot(emu, system)
        # The control is only meaningful when the two arms are the SAME length, so
        # it runs with SEQ_ADVANCE and is skipped when E1 is re-run at length
        # against SEQ_LONG. (The assert below caught exactly that mistake.)
        ctrl_ok = True
        if do_control:
            print(f"\nCONTROL{label} — two different sequences from one state (must DIFFER)")
            emu.load_state(base)
            fb = run_sequence(emu, SEQ_CONTRAST[system]) + idle
            emu.step(idle)
            snap_b = snapshot(emu, system)
            assert fa == fb, f"arms ran {fa} vs {fb} frames — control would measure time"
            print(f"  both arms: {fa} frames, {len(seq)} presses, same frame count")
            ctrl_ok = report_diff("advance vs contrast", diff(snap_live, snap_b),
                                  "substantially different")

        print(f"\nE1{label} — savestate round trip, one process")
        emu.load_state(base)
        run_sequence(emu, seq)
        emu.step(idle)
        snap_1 = snapshot(emu, system)
        emu.load_state(base)
        run_sequence(emu, seq)
        emu.step(idle)
        snap_2 = snapshot(emu, system)
        e1_ok = report_diff("replay vs replay — load, run, load, run",
                            diff(snap_1, snap_2), "identical")
        print(f"    digest {digest(snap_1)} / {digest(snap_2)}")
        d = diff(snap_live, snap_1)
        resume_ok = report_diff("live vs replay — is /load an exact inverse of /save?",
                                d, "identical")
    return ctrl_ok, e1_ok, resume_ok, d["differing"]


def cold_run(rom, port, system, workdir, tag, seq, boot=None, state=None, screen=False):
    with SkyEmu(cold_dir(workdir, rom, tag), port=port) as emu:
        emu.step(BOOT[system] if boot is None else boot)
        run_sequence(emu, seq)
        snap = snapshot(emu, system)
        png = emu.screen() if screen else None
        if state is not None:
            emu.save_state(state)
    return snap, png


def e2_and_e3(rom, ports, system, workdir) -> tuple[bool, bool]:
    """One cold process per arm. `/save` happens after the snapshot, so E3 costs
    E2 nothing and both look at the same two runs."""
    snaps, pngs, states = [], [], []
    for tag, port in zip(("cold-a", "cold-b"), ports):
        st = workdir / f"{tag}.state"
        snap, png = cold_run(rom, port, system, workdir, tag, SEQ_ADVANCE,
                             state=st, screen=True)
        snaps.append(snap), pngs.append(png), states.append(st)

    print("\nE2 — two cold processes, same sequence (must be IDENTICAL)")
    e2_diff = diff(*snaps)
    e2_ok = report_diff("process A vs process B", e2_diff, "identical")
    print(f"    digest {digest(snaps[0])} / {digest(snaps[1])}")
    same_screen = pngs[0] == pngs[1]
    print(f"    /screen PNG {len(pngs[0]):,} bytes, "
          f"{'byte-identical' if same_screen else 'DIFFERS'}: "
          f"{'PASS' if same_screen else 'FAIL'}")

    print("\nE3 — the savestate FILE (a PNG; compare the payload, not the zlib stream)")
    # Control 1: the writer. Two /save calls from one frozen machine, seconds
    # apart. If these differed, E3 would be measuring a timestamp.
    w1, w2 = workdir / "writer-1.state", workdir / "writer-2.state"
    with SkyEmu(cold_dir(workdir, rom, "writer"), port=ports[0]) as emu:
        emu.step(600)
        emu.save_state(w1)
        time.sleep(2.0)
        emu.save_state(w2)
    writer_ok, _ = report_state_pair(
        "control, two /save from ONE frozen machine 2 s apart (must be IDENTICAL)", w1, w2)

    # The measurement.
    files_ok, offs = report_state_pair("E2's two files", *states)

    # Control 2: frame 0. Zero frames emulated — any difference here predates
    # emulation and is not accumulated drift.
    zeros = []
    for n in range(4):
        z = workdir / f"frame0-{n}.state"
        with SkyEmu(cold_dir(workdir, rom, f"frame0-{n}"), port=ports[0]) as emu:
            emu.save_state(z)
        zeros.append(z)
    sets = []
    _, base = png_payload(zeros[0])
    for z in zeros[1:]:
        _, other = png_payload(z)
        sets.append({i for i in range(len(base)) if base[i] != other[i]})
    union = functools.reduce(set.union, sets)
    always = functools.reduce(set.intersection, sets)
    print(f"    control, 4 cold processes /save at FRAME 0 (0 frames emulated): "
          f"{[len(s) for s in sets]} payload bytes differ vs run 0; "
          f"union {len(union)}, always-differing {len(always)}")

    # Control 3: does it propagate? Reload both and run identical further frames.
    prop = []
    for tag, st in zip(("prop-a", "prop-b"), states):
        with SkyEmu(cold_dir(workdir, rom, tag), port=ports[0]) as emu:
            emu.load_state(st)
            extra = run_sequence(emu, SEQ_ADVANCE)
            emu.step(3000)
            prop.append(snapshot(emu, system))
    # The question is GROWTH, not identity: on a system where E2 already left a
    # few bytes differing, reloading both states cannot produce zero, and asking
    # for zero would only restate E2. What matters is whether the residue spreads.
    d = diff(*prop)
    prop_ok = d["differing"] <= e2_diff["differing"]
    print(f"    control, both states reloaded + {extra + 3000:,} identical further frames: "
          f"{d['differing']} / {d['total_bytes']:,} bytes of memory differ, against "
          f"{e2_diff['differing']} at E2 — {'does not grow' if prop_ok else 'GROWS'}: "
          f"{'PASS' if prop_ok else 'FAIL'}")

    verdict = writer_ok and prop_ok
    print(f"    -> the FILE is {'reproducible' if files_ok else 'NOT byte-reproducible'} "
          f"across processes; the writer is deterministic; the residue is present at "
          f"frame 0 and does not grow in game memory: {'PASS' if verdict else 'FAIL'}")
    return e2_ok and same_screen, verdict


def e4_long(rom, ports, system, workdir, idle, short_resume) -> tuple[bool, bool]:
    """E2 and E1 again at ~10 minutes of console time. The question for each is
    whether its residue GROWS with the horizon — a fixed handful of bytes is a
    quirk, a number that climbs is drift."""
    frames = BOOT[system] + len(SEQ_LONG) * (12 + 24 + WAIT_AFTER_PRESS) + idle
    print(f"\nE4 — long horizon: {frames:,} frames ({frames / 3600:.1f} min of "
          f"console), {len(SEQ_LONG)} presses")
    snaps = []
    for tag, port in zip(("long-a", "long-b"), ports):
        with SkyEmu(cold_dir(workdir, rom, tag), port=port) as emu:
            emu.step(BOOT[system])
            run_sequence(emu, SEQ_LONG)
            emu.step(idle)
            snaps.append(snapshot(emu, system))
    e2_long = report_diff("E2 at length — two cold processes", diff(*snaps), "identical")

    _, _, resume_ok, n = control_and_e1(rom, ports[0], system, workdir, SEQ_LONG,
                                        idle, "long-warm", " at length", do_control=False)
    print(f"    live-vs-replay residue: {short_resume} at "
          f"{len(SEQ_ADVANCE) * 96} frames -> {n} at "
          f"{len(SEQ_LONG) * 96 + idle:,} frames — "
          f"{'does not grow' if n <= max(short_resume, 0) else 'GROWS'}")
    return e2_long, resume_ok


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", type=Path,
                    default=repo / "roms/Pokemon - FireRed Version (USA, Europe) (Rev 1).gba")
    ap.add_argument("--system", choices=sorted(REGIONS), default="GBA")
    ap.add_argument("--port", type=int, default=8131)
    ap.add_argument("--port2", type=int, default=8132)
    ap.add_argument("--workdir", type=Path, default=Path("/tmp/skyemu-determinism"))
    ap.add_argument("--long-idle", type=int, default=12000)
    ap.add_argument("--skip-long", action="store_true",
                    help="skip E4 — it is ~2 min on GBA and several on NDS")
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(f"{args.rom.name} — {args.system}, boot {BOOT[args.system]} frames, "
          f"regions {[r[0] for r in REGIONS[args.system]]}")

    ctrl_ok, e1_ok, resume_ok, resume_n = control_and_e1(
        args.rom, args.port, args.system, args.workdir)
    if not ctrl_ok:
        print("\nCONTROL FAILED — the snapshot region does not move when the game runs, "
              "so every 'identical' below would be vacuous. Stopping.")
        return 1
    ports = (args.port, args.port2)
    e2_ok, e3_ok = e2_and_e3(args.rom, ports, args.system, args.workdir)
    results = [("control", ctrl_ok), ("E1-replay", e1_ok), ("E1-resume", resume_ok),
               ("E2", e2_ok), ("E3", e3_ok)]
    if not args.skip_long:
        a, b = e4_long(args.rom, ports, args.system, args.workdir,
                       args.long_idle, resume_n)
        results += [("E4-cold", a), ("E4-resume", b)]

    print(f"\n{time.time() - t0:.0f}s  "
          + " / ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results))
    return 0 if all(v for _, v in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
