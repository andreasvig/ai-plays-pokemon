#!/usr/bin/env python3
"""Replay a Gen 5 run's own recorded inputs from a savepoint, sampling every press.

The method is ``crystal_battle_walk.py``'s and ``gen4_battle_walk.py``'s: a
savepoint lands every ten turns, so the states it gives are a handful of
arbitrary moments inside a fight, while the run's own ``button_sequence``
events walk the same stretch again at INPUT resolution. On Black that is the
difference between one wild battle in the whole corpus and four.

The oracle is the FRAME, never another byte. Gen 5 announces the kind in
words — "A wild Lillipup appeared!" against "You are challenged by <class>
<name>!" — so every sample carries its PNG.

Two passes, because a 4 MB dump costs ~12 s and a press costs ~0.3 s:

    --probe   every press: the frame plus the named battle blocks (cheap)
    --dump-at I,J,K   re-run the same deterministic replay and write the whole
              of main RAM at those sample indices

    PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \\
      v2-experiments/gen5_battle_walk.py --game black --start 50 --turns 30 \\
        --port 8442 --out /tmp/g5/walk_50
"""
from __future__ import annotations

import argparse
import ast
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v2-experiments" / "harness"))
from gen5_battle_probe import GAMES, MAIN_BASE, MAIN_SIZE  # noqa: E402
from skyemu import SkyEmu  # noqa: E402

# configs/config-v2-black + config-v2-black2, as recorded in each run's
# config.json: button_hold_frames 12, frames_between_inputs 24, ab_hold_frames
# 12, ab_gap_frames 100, wait_input_seconds 5.0 at 60 fps.
HOLD, GAP, AB_HOLD, AB_GAP, WAIT = 12, 24, 12, 100, 300

#: Deliberately wider than the fields under test, and every range is a NAMED
#: allocation from ``gen5_heap_map.py`` rather than a hand-picked address.
REGIONS = {
    "black": {
        "actor": (0x0224F90C, 16),
        # btl_pokeparam.c x4 — the four battler slots, 0x224 apart. The
        # opponent's is the second, and the already-shipped foe species is its
        # data + 0x14.
        "mons": (0x0226D69C, 0x224 * 3 + 0x40),
        # btl_server.c (hdr 0x0226df08, size 0xce0)
        "server": (0x0226DF2C, 0x200),
        # btl_field.c (hdr 0x02269be0, size 0x184)
        "field": (0x02269C00, 0x184),
        # The BATTLE PROC's work struct: procsys.c allocates a proc's work, so
        # the tag is the proc system's and the CONTENT is the battle's. +0x0c
        # is the BATTLE_SETUP_PARAM it was called with and +0x58 the opponent
        # trainer's name buffer.
        "procwork": (0x02269760, 0x80),
        # field_encount_st.c — the overworld encounter module's own state.
        "fenc": (0x02257004, 0x3C),
        # The BATTLE_SETUP_PARAM, through the pointer at the battle proc's
        # +0x0c. The CALLER allocates it, so it is at a different address in
        # every battle and only the chase reaches it — which is the whole
        # reason the backend's pointer window had to stop being the GBA's.
        "setup": ("*", 0x0226976C, 0, 0x60),
    },
    "black2": {
        "actor": (0x0223B444, 16),
        "mons": (0x0225B1F0, 0x224 * 3 + 0x40),
        "flag": (0x0213B2E0, 4),
        "procwork": (0x02257294, 0x80),
        "setup": ("*", 0x022572A0, 0, 0x60),
    },
}


def sequences(run: Path) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    turn = None
    for line in (run / "events.jsonl").open():
        e = json.loads(line)
        if e.get("type") == "turn_start":
            turn = e["turn"]
        elif e.get("type") == "button_sequence" and turn is not None:
            out.setdefault(turn, ast.literal_eval(e["sequence"]))
    return out


def settle(emu: SkyEmu, max_frames: int = 900, chunk: int = 12) -> None:
    prev = emu.screen()
    same = 0
    for _ in range(max_frames // chunk):
        emu.step(chunk)
        now = emu.screen()
        same = same + 1 if now == prev else 0
        prev = now
        if same >= 2:
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=sorted(GAMES), required=True)
    ap.add_argument("--run", default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--state", type=Path, default=None,
                    help="drive THIS state with --press instead of replaying a savepoint")
    ap.add_argument("--press", default="",
                    help="with --state: comma-separated buttons, one sample each")
    ap.add_argument("--turns", type=int, default=15)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dump-at", default="", help="comma-separated sample indices to npy")
    ap.add_argument("--dump-on-close", type=int, default=0,
                    help="dump the whole of main RAM on the LAST in-battle sample and "
                         "the first N after the flag clears. That is where "
                         "src/app/route.py reads an outcome, so it is the only frame "
                         "a candidate has to be right on.")
    ap.add_argument("--poke", default="",
                    help="ADDR=VALUE,... written after the state loads. The honest "
                         "use is REACHING a state the run cannot reach in the time "
                         "available — dropping the player's current HP so the "
                         "opponent's next hit ends the fight. The LOSS is then the "
                         "game's own: it runs its own faint and whiteout path and "
                         "writes its own result. Nothing here writes a result.")
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()

    g = GAMES[args.game]
    regions = REGIONS[args.game]
    run_key = args.run or sorted(g["runs"])[0]
    run = ROOT / "local/runs" / g["runs"][run_key]
    seqs = sequences(run)
    state = args.state or run / f"savepoints/turn_{args.start}/emulator.state"
    args.out.mkdir(parents=True, exist_ok=True)
    pngs = args.out / "png"
    if not args.no_png:
        pngs.mkdir(exist_ok=True)
    dump_at = {int(x) for x in args.dump_at.split(",") if x.strip()}
    if dump_at or args.dump_on_close:
        import numpy as np
    rows: list[dict] = []

    def take(emu, turn, i, name):
        nonlocal dump_at
        r = {"n": len(rows), "turn": turn, "i": i, "input": name}
        for k, spec in regions.items():
            if spec[0] == "*":
                _, ptr, off, n = spec
                base = int.from_bytes(emu.read_memory(ptr, 4), "little")
                r[k + "_ptr"] = base
                # The same window the backend now applies per console; a stale
                # pointer after the battle proc is freed has to read as nothing
                # rather than as a number from wherever it happens to land.
                r[k] = (emu.read_memory(base + off, n).hex()
                        if 0x02000000 <= base < 0x02400000 else "")
            else:
                a, n = spec
                r[k] = emu.read_memory(a, n).hex()
        mid, x, _h, y = struct.unpack("<Iiii", bytes.fromhex(r["actor"]))
        r["map"], r["x"], r["y"] = mid, x >> 16, y >> 16
        if "mons" in r:
            r["foe"] = struct.unpack_from("<H", bytes.fromhex(r["mons"]), 0x224 + 0x14)[0]
        if not args.no_png:
            p = pngs / f"{r['n']:05d}.png"
            p.write_bytes(emu.screen())
            r["png"] = p.name
        if args.dump_on_close:
            # The close is the first sample after the flag goes clear, which is
            # where src/app/route.py reads an outcome — so that frame and the
            # few after it are the only ones a candidate has to be right on.
            if getattr(take, "closed_at", None) is None:
                if rows and rows[-1].get("foe") and not r.get("foe"):
                    take.closed_at = r["n"]
            if (getattr(take, "closed_at", None) is not None
                    and r["n"] - take.closed_at < args.dump_on_close):
                dump_at |= {r["n"]}
        if r["n"] in dump_at:
            buf = emu.read_memory(MAIN_BASE, MAIN_SIZE)
            np.save(args.out / f"d{r['n']:05d}.npy", np.frombuffer(buf, dtype=np.uint8))
            r["dump"] = f"d{r['n']:05d}.npy"
        rows.append(r)
        print(f"  n={r['n']:4d} t{turn} {name:6s} map={r['map']} ({r['x']},{r['y']}) "
              f"foe={r.get('foe')}", flush=True)

    with SkyEmu(g["rom"], port=args.port) as emu:
        emu.load_state(state)
        emu.step(4)
        for one in (x for x in args.poke.split(",") if x.strip()):
            a, _, v = one.partition("=")
            emu.write_byte(int(a, 0), int(v, 0) & 0xFF)
            print(f"  poke {int(a, 0):#010x} = {int(v, 0) & 0xFF}", flush=True)
        take(emu, args.start, -1, "<loaded>")
        if args.press:
            for i, btn in enumerate(b.strip().lower()
                                    for b in args.press.split(",") if b.strip()):
                if btn == "wait":
                    emu.step(WAIT)
                else:
                    ab = btn in ("a", "b")
                    emu.press(btn, hold=AB_HOLD if ab else HOLD,
                              gap=AB_GAP if ab else GAP)
                take(emu, args.start, i, btn)
            (args.out / "samples.json").write_text(json.dumps(rows, indent=0) + "\n")
            print(f"{len(rows)} samples -> {args.out}")
            return 0
        for turn in range(args.start + 1, args.start + 1 + args.turns):
            for i, btn in enumerate(seqs.get(turn, [])):
                b = btn.strip().lower()
                if b == "wait":
                    emu.step(WAIT)
                else:
                    ab = b in ("a", "b")
                    emu.press(b, hold=AB_HOLD if ab else HOLD, gap=AB_GAP if ab else GAP)
                take(emu, turn, i, b)
            settle(emu)
            take(emu, turn, 99, "<settled>")
    (args.out / "samples.json").write_text(json.dumps(rows, indent=0) + "\n")
    print(f"{len(rows)} samples -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
