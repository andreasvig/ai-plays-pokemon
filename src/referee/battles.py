"""Battle telemetry: was the player in a battle, how many battles, against whom.

Plan: ``artifacts/battle-and-movement-fidelity/plan.md`` (2026-09-14). The
referee polls once per turn, AFTER the turn's inputs ran, and hands this module
three things it reads from the emulator:

- ``gMain.inBattle`` — bit 1 of the byte at IWRAM ``0x03003529`` (gMain at
  ``0x030030F0`` + 0x439; pret ``include/main.h``). Verified 27/27 against
  screenshots on the Fable medium run's savepoints.
- the game's own battle counters — ``gameStats[]`` at SaveBlock1 + 0x1200,
  u32 each, XOR-encrypted with ``encryptionKey`` at SaveBlock2 + 0xF20.
  Index 7 total, 8 wild, 9 trainer battles (``constants/game_stat.h``).
- trainer-defeated flags — flag ids 0x500..0x7FF inside the flag bitfield the
  referee already reads. A flag is set when the trainer is BEATEN; a lost
  attempt moves the trainer counter and sets nothing.

Everything derived is a pure function of the per-turn record list, so a
resumed run (``load_state``) lands on the same numbers as the live one and the
savepoint backfill (``scripts/backfill_battles.py``) can feed the same tracker.

Turn attribution follows decision 2A/5a of the plan: a turn belongs to the
state it STARTED in, i.e. the state the previous poll left behind; turn 1
starts in the overworld. A battle that begins and ends inside one turn costs
zero turns. Losses and rematches are ATTEMPTS, summed per trainer (decision 6):
an unidentified attempt (counter moved, no flag) is attributed to the next
trainer whose flag appears — a loss followed by the rematch — else to
``"unknown"``.
"""

from __future__ import annotations

import struct
from typing import Any, Optional

GMAIN = 0x030030F0
GMAIN_IN_BATTLE_BYTE = GMAIN + 0x439  # 0x03003529
IN_BATTLE_BIT = 1

SB1_GAME_STATS = 0x1200  # u32[64]
NUM_GAME_STATS = 64
SB2_ENCRYPTION_KEY = 0xF20  # u32 in SaveBlock2
GAME_STAT_TOTAL_BATTLES = 7
GAME_STAT_WILD_BATTLES = 8
GAME_STAT_TRAINER_BATTLES = 9

TRAINER_FLAGS_START = 0x500
TRAINER_FLAGS_END = 0x7FF  # inclusive

# The trainers a first-badge run can meet (pret map data + constants/opponents.h).
# The rival's id depends on the starter he holds; all three ids share a name.
TRAINER_NAMES: dict[int, str] = {
    102: "Bug Catcher Rick", 103: "Bug Catcher Doug", 104: "Bug Catcher Sammy",
    531: "Bug Catcher Anthony", 532: "Bug Catcher Charlie",
    142: "Camper Liam",
    326: "Rival (Oak's Lab)", 327: "Rival (Oak's Lab)", 328: "Rival (Oak's Lab)",
    329: "Rival (Route 22)", 330: "Rival (Route 22)", 331: "Rival (Route 22)",
    414: "Leader Brock",
}
# Rival ids collapse to one battle identity per location (which starter he
# carries is the run's choice, not a different opponent).
TRAINER_GROUP: dict[int, str] = {
    **{i: str(i) for i in TRAINER_NAMES},
    326: "rival_oaks_lab", 327: "rival_oaks_lab", 328: "rival_oaks_lab",
    329: "rival_route22", 330: "rival_route22", 331: "rival_route22",
}
MANDATORY_TRAINERS = {"rival_oaks_lab", "414"}


def trainer_name(trainer_id: int | str) -> str:
    try:
        return TRAINER_NAMES.get(int(trainer_id), f"Trainer {trainer_id}")
    except (TypeError, ValueError):
        return str(trainer_id)


def trainer_group(trainer_id: int) -> str:
    return TRAINER_GROUP.get(trainer_id, str(trainer_id))


def decode_game_stat(block: bytes, index: int, key: int) -> Optional[int]:
    """``gameStats[index]`` from a SaveBlock1 read long enough to hold it."""
    off = SB1_GAME_STATS + 4 * index
    if len(block) < off + 4:
        return None
    return struct.unpack_from("<I", block, off)[0] ^ (key & 0xFFFFFFFF)


def decode_trainer_flags(block: bytes, flags_offset: int) -> list[int]:
    """Trainer ids whose defeated-flag is set, ascending."""
    out = []
    for f in range(TRAINER_FLAGS_START, TRAINER_FLAGS_END + 1):
        byte_index = flags_offset + (f >> 3)
        if byte_index < len(block) and (block[byte_index] >> (f & 7)) & 1:
            out.append(f - TRAINER_FLAGS_START)
    return out


def in_battle_from_byte(b: int) -> bool:
    return bool((b >> IN_BATTLE_BIT) & 1)


# One record per poll: [turn, in_battle, total, wild, trainer, [trainer ids set]]
Record = tuple[int, bool, int, int, int, tuple[int, ...]]


class BattleTracker:
    """Per-poll battle state → battle segments, attempts per trainer, turn costs."""

    def __init__(self) -> None:
        self.records: list[Record] = []
        # Optional: the turn a trainer battle began → trainer id, from a source
        # other than the flags (the backfill reads it off the run's OCR text:
        # "BUG CATCHER RICK would like to battle!"). Names a LOST attempt, which
        # sets no flag, instead of guessing from the next win.
        self.identity_hints: dict[int, int] = {}

    # --- input ----------------------------------------------------------------

    def record(self, turn: int, in_battle: bool, total: int, wild: int, trainer: int,
               trainers: list[int] | tuple[int, ...]) -> dict[str, Any]:
        rec: Record = (int(turn), bool(in_battle), int(total), int(wild), int(trainer), tuple(sorted(int(t) for t in trainers)))
        # A re-poll of the same turn (retry) replaces the earlier record.
        if self.records and self.records[-1][0] == rec[0]:
            self.records[-1] = rec
        else:
            self.records.append(rec)
        prev = self.records[-2] if len(self.records) > 1 else None
        return {
            "turn": rec[0], "in_battle": rec[1], "battles_total": rec[2], "wild_battles": rec[3],
            "trainer_battles": rec[4],
            "new_battles": rec[2] - (prev[2] if prev else 0),
            "trainers_new": [t for t in rec[5] if not prev or t not in prev[5]],
        }

    def export_state(self) -> dict[str, Any]:
        return {"battle_records": [[r[0], r[1], r[2], r[3], r[4], list(r[5])] for r in self.records]}

    def load_state(self, data: Any) -> None:
        """Restore records (everything else is recomputed). Tolerant of junk."""
        out: list[Record] = []
        raw = data.get("battle_records") if isinstance(data, dict) else None
        for entry in raw or []:
            try:
                if len(entry) != 6:
                    continue
                out.append((int(entry[0]), bool(entry[1]), int(entry[2]), int(entry[3]), int(entry[4]),
                            tuple(sorted(int(t) for t in entry[5]))))
            except (TypeError, ValueError):
                continue
        self.records = out

    # --- derived ----------------------------------------------------------------

    def state_at_start(self, turn: int) -> Optional[bool]:
        """Was the player in a battle when ``turn`` began? The previous poll's
        state; turn 1 (or the first turn before any record) is the overworld.
        None when the previous turn was never polled (gap → unknown)."""
        if turn <= 1:
            return False
        prev = {r[0]: r for r in self.records}.get(turn - 1)
        return None if prev is None else prev[1]

    def segments(self) -> list[dict[str, Any]]:
        """Battles as segments over the record list.

        A segment opens at the first record whose counters moved (the battle
        started during that turn) and closes at the first following record with
        ``in_battle`` False. Its kind is the counter that moved — trainer when
        both did (a trainer battle cannot be fled, so it is the one still going).
        ``turns`` are the turns that STARTED inside it (rule A): the records
        strictly after the opening record up to and INCLUDING the closing one
        (that turn began inside the battle too) — a battle contained in one turn
        costs 0.
        """
        segs: list[dict[str, Any]] = []
        prev: Optional[Record] = None
        open_seg: Optional[dict[str, Any]] = None

        def new_segment(kind: str, turn: int) -> dict[str, Any]:
            return {"kind": kind, "opened_turn": turn, "closed_turn": None, "turns": 0, "turn_list": [],
                    "trainer_id": None, "won": None}

        for rec in self.records:
            turn, in_battle, total, wild, trainer, flags = rec
            p_total, p_wild, p_trainer, p_flags = (prev[2], prev[3], prev[4], prev[5]) if prev else (0, 0, 0, ())
            new_total, new_wild, new_trainer = total - p_total, wild - p_wild, trainer - p_trainer
            new_flags = [t for t in flags if t not in p_flags]
            if open_seg is not None:
                # This turn STARTED inside the open battle: it is charged to it (rule A).
                open_seg["turns"] += 1
                open_seg["turn_list"].append(turn)
                if new_total == 0 and in_battle:
                    prev = rec
                    continue  # same battle, still going
                open_seg["closed_turn"] = turn
                if open_seg["kind"] == "trainer" and new_flags and open_seg["trainer_id"] in new_flags:
                    new_flags.remove(open_seg["trainer_id"]); open_seg["won"] = True  # hinted id confirmed by its flag
                elif open_seg["kind"] == "trainer" and new_flags and open_seg["trainer_id"] is not None:
                    open_seg["won"] = False  # hinted id, the flag that landed is someone else's
                elif open_seg["kind"] == "trainer" and new_flags:
                    # Several flags landing together (a coarse backfill window
                    # holding back-to-back trainer fights) identify the earlier,
                    # still-unidentified trainer segments too: earliest segment ↔
                    # lowest id, which is the roster's encounter order on the
                    # first-badge route. Leftover flags become zero-turn wins below.
                    pending = [x for x in segs if x["kind"] == "trainer" and x["trainer_id"] is None]
                    targets = pending[-(len(new_flags) - 1):] if len(new_flags) > 1 and pending else []
                    for x in targets:
                        x["trainer_id"] = new_flags.pop(0); x["won"] = True
                    open_seg["trainer_id"] = new_flags.pop(0)
                    open_seg["won"] = True
                segs.append(open_seg)
                open_seg = None
            if new_total > 0:
                # The battle still going (if any) is the LAST one started this
                # turn: trainer when the trainer counter moved — a trainer battle
                # cannot be fled, so it is the one that outlasts the turn.
                open_kind = ("trainer" if new_trainer > 0 else "wild") if in_battle else None
                contained_trainer = new_trainer - (1 if open_kind == "trainer" else 0)
                contained_wild = new_wild - (1 if open_kind == "wild" else 0)
                # Battles that began and ended inside this turn cost 0 turns (5a).
                for _ in range(max(contained_wild, 0)):
                    seg = new_segment("wild", turn); seg["closed_turn"] = turn; segs.append(seg)
                for _ in range(max(contained_trainer, 0)):
                    seg = new_segment("trainer", turn); seg["closed_turn"] = turn
                    if new_flags:
                        seg["trainer_id"] = new_flags.pop(0); seg["won"] = True
                    elif turn in self.identity_hints:
                        seg["trainer_id"] = self.identity_hints[turn]; seg["won"] = False
                    segs.append(seg)
                if open_kind is not None:
                    open_seg = new_segment(open_kind, turn)
                    if open_kind == "trainer" and turn in self.identity_hints:
                        open_seg["trainer_id"] = self.identity_hints[turn]
            # A defeated-flag with no counter move (seen once: gpt-6-astra medium,
            # 6 flags for 5 counted trainer battles) is still a win: record it as a
            # zero-turn attempt rather than lose the trainer.
            for tid in new_flags:
                # A flag landing after a HINTED attempt with that id (the backfill
                # places flags at a savepoint, the OCR placed the fight) confirms
                # that attempt as the win instead of adding a phantom one.
                hinted = [x for x in segs if x["kind"] == "trainer" and x["trainer_id"] == tid and x["won"] is not True]
                if hinted:
                    hinted[-1]["won"] = True
                    continue
                seg = new_segment("trainer", turn); seg["closed_turn"] = turn
                seg["trainer_id"] = tid; seg["won"] = True; seg["uncounted"] = True
                segs.append(seg)
            prev = rec
        if open_seg is not None:
            segs.append(open_seg)  # still fighting when the run ended
        # Attempts without a flag: attribute to the next identified trainer (a
        # loss then the rematch), else "unknown". Segments are in time order.
        # A hinted attempt already has its id; without a flag it was lost.
        pending: list[dict[str, Any]] = []
        for seg in segs:
            if seg["kind"] != "trainer":
                continue
            if seg["trainer_id"] is not None and seg["won"] is None:
                seg["won"] = False
            if seg["trainer_id"] is None:
                seg["won"] = False
                pending.append(seg)
            else:
                for p in pending:
                    p["trainer_id"] = seg["trainer_id"]
                pending = []
        for seg in segs:
            if seg["kind"] == "trainer":
                tid = seg["trainer_id"]
                seg["group"] = trainer_group(tid) if tid is not None else "unknown"
                seg["name"] = trainer_name(tid) if tid is not None else "Unknown trainer"
        return segs

    def summary(self) -> dict[str, Any]:
        if not self.records:
            return {"available": False}
        segs = self.segments()
        last = self.records[-1]
        wild = [s for s in segs if s["kind"] == "wild"]
        trainers: dict[str, dict[str, Any]] = {}
        for s in segs:
            if s["kind"] != "trainer":
                continue
            g = trainers.setdefault(s["group"], {"group": s["group"], "id": s["trainer_id"], "name": s["name"],
                                                 "attempts": 0, "turns": 0, "won": False,
                                                 "mandatory": s["group"] in MANDATORY_TRAINERS})
            g["attempts"] += 1
            g["turns"] += s["turns"]
            g["won"] = g["won"] or bool(s["won"])
        started_in_battle = sum(1 for r in self.records[:-1] if r[1])  # each record is the start of the next turn
        return {
            "available": True,
            "polls": len(self.records),
            "battles_total": last[2],
            "wild": {"count": last[3], "turns": sum(s["turns"] for s in wild),
                     "segments": len(wild)},
            "trainer": {"count": last[4], "turns": sum(s["turns"] for s in segs if s["kind"] == "trainer"),
                        "defeated": list(last[5])},
            "trainers": sorted(trainers.values(), key=lambda g: (g["id"] is None, g["id"] or 0)),
            "turns_started_in_battle": started_in_battle,
            "turns_polled": len(self.records),
            "segments": segs,
        }


__all__ = [
    "BattleTracker", "GMAIN_IN_BATTLE_BYTE", "SB1_GAME_STATS", "NUM_GAME_STATS", "SB2_ENCRYPTION_KEY",
    "GAME_STAT_TOTAL_BATTLES", "GAME_STAT_WILD_BATTLES", "GAME_STAT_TRAINER_BATTLES",
    "decode_game_stat", "decode_trainer_flags", "in_battle_from_byte", "trainer_name", "trainer_group",
    "TRAINER_NAMES", "MANDATORY_TRAINERS",
]
