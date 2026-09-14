"""Backfill battle telemetry for runs that predate the live referee reads.

Plan §4.1, ``artifacts/battle-and-movement-fidelity/plan.md`` (2026-09-14).
Every run keeps an mGBA save state every 10 turns plus the final turn
(``savepoints/turn_N/emulator.state``), taken AFTER turn N's inputs — the same
moment the live referee polls. The state is a PNG whose ``gbAs`` chunk
zlib-decompresses to the 0x61000-byte GBA state with IWRAM at +0x19000 and
EWRAM at +0x21000. From it this script decodes exactly what the live poll reads
(``src/referee/battles.py``): gMain.inBattle, the game's battle counters,
trainer-defeated flags — and writes ``battle_backfill.json`` in the run dir:

    {"source": "savepoint", "resolution_turns": 10, "records": [[turn, in_battle,
     total, wild, trainer, [trainer ids]], ...], "chain": [run ids], "summary": {...}}

``records`` is the ``BattleTracker`` record shape, so the projection can feed
the backfill and a live ``referee_state.json`` to the same code. A continued
run's chain is followed through ``run_summary.continued_from``: the source's
savepoints up to the resume turn come first. Resolution is 10 turns: a turn
between savepoints has no state of its own (``scripts/backfill_states.py``
fills those from screenshots).

Usage: venv/bin/python scripts/backfill_battles.py [--runs-root local/runs] [run_id ...]
       (no run_id → every run dir that has savepoints). Read-only except for the
       one JSON it writes per run.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.referee.battles import (  # noqa: E402
    GAME_STAT_TOTAL_BATTLES, GAME_STAT_TRAINER_BATTLES, GAME_STAT_WILD_BATTLES, GMAIN_IN_BATTLE_BYTE,
    SB2_ENCRYPTION_KEY, BattleTracker, decode_game_stat, decode_trainer_flags, in_battle_from_byte,
)
from src.referee.referee import GSAVEBLOCK1_PTR, GSAVEBLOCK2_PTR, SB1_FLAGS  # noqa: E402

STATE_IWRAM = 0x19000
STATE_EWRAM = 0x21000
STATE_SIZE = 0x61000


def load_state(path: Path) -> bytes | None:
    """The raw GBA state from an mGBA PNG savestate, or None if not one."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    i = 8
    while i + 8 <= len(data):
        ln, typ = struct.unpack(">I4s", data[i:i + 8])
        if typ == b"gbAs":
            state = zlib.decompress(data[i + 8:i + 8 + ln])
            return state if len(state) >= STATE_SIZE else None
        i += 12 + ln
    return None


def _u32(state: bytes, bus_addr: int) -> int:
    return struct.unpack_from("<I", state, _off(state, bus_addr))[0]


def _off(state: bytes, bus_addr: int) -> int:
    if 0x03000000 <= bus_addr < 0x03008000:
        return STATE_IWRAM + (bus_addr - 0x03000000)
    if 0x02000000 <= bus_addr < 0x02040000:
        return STATE_EWRAM + (bus_addr - 0x02000000)
    raise ValueError(f"address {bus_addr:#x} is not IWRAM/EWRAM")


def decode(state: bytes) -> dict:
    """The live poll's battle kwargs, read from a save state instead of the emulator."""
    sb1 = _u32(state, GSAVEBLOCK1_PTR)
    sb2 = _u32(state, GSAVEBLOCK2_PTR)
    block = state[_off(state, sb1):_off(state, sb1) + 0x1300]
    key = _u32(state, sb2 + SB2_ENCRYPTION_KEY)
    return {
        "in_battle": in_battle_from_byte(state[_off(state, GMAIN_IN_BATTLE_BYTE)]),
        "total": decode_game_stat(block, GAME_STAT_TOTAL_BATTLES, key),
        "wild": decode_game_stat(block, GAME_STAT_WILD_BATTLES, key),
        "trainer": decode_game_stat(block, GAME_STAT_TRAINER_BATTLES, key),
        "trainers": decode_trainer_flags(block, SB1_FLAGS),
    }


def savepoint_turns(run_dir: Path) -> list[tuple[int, Path]]:
    out = []
    for d in run_dir.glob("savepoints/turn_*"):
        try:
            t = int(d.name.split("_", 1)[1])
        except ValueError:
            continue
        if (d / "emulator.state").is_file():
            out.append((t, d / "emulator.state"))
    return sorted(out)


def chain(run_dir: Path, runs_root: Path) -> list[tuple[Path, int | None]]:
    """[(run_dir, upto_turn)] oldest first; a source contributes savepoints <= the resume turn."""
    out: list[tuple[Path, int | None]] = [(run_dir, None)]
    seen = {run_dir.name}
    cur = run_dir
    while True:
        try:
            summary = json.loads((cur / "run_summary.json").read_text())
        except (OSError, json.JSONDecodeError):
            break
        src = summary.get("continued_from")
        if not src or src in seen or not (runs_root / src).is_dir():
            break
        resumed_at = ((summary.get("session") or {}).get("segment") or {}).get("resumed_at_turn")
        if resumed_at is None:
            # The dir name carries it: ..._continued_from_turn_N
            tail = cur.name.rsplit("continued_from_turn_", 1)
            resumed_at = int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else None
        out.insert(0, (runs_root / src, resumed_at))
        seen.add(src)
        cur = runs_root / src
    return out


def backfill(run_dir: Path, runs_root: Path) -> dict | None:
    tracker = BattleTracker()
    links = chain(run_dir, runs_root)
    used = []
    for d, upto in links:
        turns = savepoint_turns(d)
        for t, path in turns:
            if upto is not None and t > upto:
                continue
            state = load_state(path)
            if state is None:
                continue
            try:
                tracker.record(t, **decode(state))
            except (ValueError, struct.error, IndexError):
                continue
        used.append(d.name)
    if not tracker.records:
        return None
    turns = [r[0] for r in tracker.records]
    gaps = [b - a for a, b in zip(turns, turns[1:])]
    return {
        "source": "savepoint",
        "resolution_turns": max(gaps) if gaps else None,
        "chain": used,
        "records": tracker.export_state()["battle_records"],
        "summary": tracker.summary(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--runs-root", default=str(REPO_ROOT / "local" / "runs"))
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = ap.parse_args(argv)
    root = Path(args.runs_root)
    dirs = [root / r for r in args.run_ids] if args.run_ids else sorted(d for d in root.iterdir() if (d / "savepoints").is_dir())
    for d in dirs:
        result = backfill(d, root)
        if result is None:
            print(f"skip  {d.name}: no decodable savepoint")
            continue
        s = result["summary"]
        print(f"ok    {d.name[:70]:70} polls={s['polls']:3} wild={s['wild']['count']:3} trainer={s['trainer']['count']:2} "
              f"defeated={s['trainer']['defeated']} started_in_battle={s['turns_started_in_battle']}")
        if not args.dry_run:
            (d / "battle_backfill.json").write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
