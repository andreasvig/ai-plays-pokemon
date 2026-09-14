"""Per-input trace: where the player stood and whether a battle was on after
EVERY button of a turn — the exact record behind overworld steps.

Plan §3.2, ``artifacts/battle-and-movement-fidelity/plan.md`` (2026-09-14). The
Lua bridge stays dumb: Python hands it a TRACE SPEC — raw memory ranges, with
an optional pointer dereference — and after each input's gap the bridge samples
those ranges and keeps ``name|hex;hex;hex`` rows until ``TRACE`` collects them
(:meth:`EmulatorClient.fetch_trace`). All game knowledge is here.

Spec entry grammar (one string per range, ``;``-joined on the wire):
    ``<addr>:<len>``            absolute bus address
    ``*<ptr>+<off>:<len>``      dereference the u32 at ``ptr`` (must land in EWRAM), then read at +off

Samples per input, in spec order:
    0. SaveBlock1 +0..6 — player x (s16), y (s16), map_group (u8), map_num (u8)
    1. gMain byte holding ``inBattle`` (bit 1) — src/referee/battles.py
    2. SaveBlock1 +0x121C — gameStats[7] (total battles), XOR-encrypted; decoded
       with the SaveBlock2 key the referee read at its last poll

Derived per turn (rule set of the plan, decision 2B recorded alongside 2A):
- ``overworld_steps`` — inputs after which the tile changed while no battle was
  on before or after (a warp counts as one step, like the walk graph).
- ``inputs_lost`` — direction inputs that moved nothing outside a battle:
  bumps, facing turns, presses eaten by dialogue.
- ``battle_inputs`` — inputs pressed while a battle was on.
- ``battle_started_at`` — index of the first input after which a battle was on
  (None when none).
"""

from __future__ import annotations

import struct
from typing import Any, Optional

from src.referee.battles import GAME_STAT_TOTAL_BATTLES, GMAIN_IN_BATTLE_BYTE, SB1_GAME_STATS, in_battle_from_byte

GSAVEBLOCK1_PTR = 0x03005008

TRACE_SPEC: list[str] = [
    f"*{GSAVEBLOCK1_PTR:#x}+0:6",
    f"{GMAIN_IN_BATTLE_BYTE:#x}:1",
    f"*{GSAVEBLOCK1_PTR:#x}+{SB1_GAME_STATS + 4 * GAME_STAT_TOTAL_BATTLES:#x}:4",
]

DIRECTIONS = {"U": "up", "D": "down", "L": "left", "R": "right", "UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right"}


def decode_samples(rows: list[tuple[str, list[bytes]]], key: Optional[int] = None) -> list[dict[str, Any]]:
    """``[(input name, [bytes per spec entry])]`` → one dict per input."""
    out: list[dict[str, Any]] = []
    for i, (name, samples) in enumerate(rows):
        pos = samples[0] if len(samples) > 0 else b""
        batt = samples[1] if len(samples) > 1 else b""
        stat = samples[2] if len(samples) > 2 else b""
        d: dict[str, Any] = {"i": i, "input": name, "map_group": None, "map_num": None, "x": None, "y": None,
                             "in_battle": None, "battles_total": None}
        if len(pos) >= 6:
            d["x"], d["y"], d["map_group"], d["map_num"] = struct.unpack_from("<hhBB", pos, 0)
        if len(batt) >= 1:
            d["in_battle"] = in_battle_from_byte(batt[0])
        if len(stat) >= 4 and key is not None:
            d["battles_total"] = struct.unpack_from("<I", stat, 0)[0] ^ (key & 0xFFFFFFFF)
        out.append(d)
    return out


def _tile(d: dict[str, Any]) -> Optional[tuple[int, int, int, int]]:
    if d.get("x") is None:
        return None
    return (d["map_group"], d["map_num"], d["x"], d["y"])


def derive(samples: list[dict[str, Any]], start_tile: Optional[tuple[int, int, int, int]],
           start_in_battle: Optional[bool]) -> dict[str, Any]:
    """Turn-level figures from the per-input samples.

    ``start_tile`` / ``start_in_battle`` are the referee's last poll before this
    turn (the state the turn began in). Unknown starts leave the first input's
    step uncounted rather than guessed.
    """
    steps = lost = battle_inputs = 0
    battle_started_at: Optional[int] = None
    prev_tile, prev_batt = start_tile, start_in_battle
    for d in samples:
        tile, batt = _tile(d), d.get("in_battle")
        if batt:
            battle_inputs += 1
            if battle_started_at is None and not prev_batt:
                battle_started_at = d["i"]
        quiet = (prev_batt is False) and (batt is False)
        if quiet and tile is not None and prev_tile is not None:
            if tile != prev_tile:
                steps += 1
            elif str(d.get("input", "")).upper() in DIRECTIONS:
                lost += 1
        if tile is not None:
            prev_tile = tile
        if batt is not None:
            prev_batt = batt
    return {
        "inputs": len(samples),
        "overworld_steps": steps,
        "inputs_lost": lost,
        "battle_inputs": battle_inputs,
        "battle_started_at": battle_started_at,
        "end_in_battle": prev_batt,
    }


__all__ = ["TRACE_SPEC", "decode_samples", "derive"]
