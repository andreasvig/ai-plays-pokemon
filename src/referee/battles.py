"""Battle telemetry: was the player in a battle, how many battles, against whom.

Plan: ``artifacts/battle-and-movement-fidelity/plan.md`` (2026-09-14). The
referee polls once per turn, AFTER the turn's inputs ran, and hands this module
three things it reads from the emulator:

- ``gBattleMons`` — the four battler structs at EWRAM ``0x02023BE4``, 0x58
  bytes each: battler 0 is the player's mon, battler 1 the opponent's. Species
  is the u16 at +0x00 and level the u8 at +0x2A (pret ``include/pokemon.h``).
  **Found by search, not by lookup** (2026-09-15): across 146 mid-battle save
  states from every run, this is the only address where battler 0 AND battler 1
  are both coherent, and every one of the 146 opponents is explained — 59 by the
  ROM roster of the trainer the referee had already identified, 87 by the wild
  encounter table of the map the player was standing on (the last four needed
  FireRed's table rather than LeafGreen's, which is the same map with different
  mons).
- ``gBattleOutcome`` — the u8 at EWRAM ``0x02023E8A``. Zero while a battle runs
  (``BattleStartClearSetData``), then one of ``B_OUTCOME``. It agreed with the
  referee's own won/lost verdict on 4 of 4 trainer battles that could be
  checked, and gives what nothing else here can: whether a WILD battle was won,
  fled or caught. Read it at the poll where the battle CLOSES — during the
  intro, ``inBattle`` is already set while the byte still holds the previous
  battle's result (seen on 35 of 146 mid-battle states).
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

# The trainer the player is (or was last) fighting: gTrainerBattleOpponent_A,
# u16 in EWRAM. Set when a trainer battle starts and kept until the next one,
# so read at the poll where the trainer counter stepped it names that fight —
# won OR lost — with no flag needed. Verified 2026-09-14 on mid-fight save
# states of six runs (142 during Liam, 414 during Brock, 104 after Sammy).
GTRAINER_BATTLE_OPPONENT_A = 0x020386AE

# gBattleMons[MAX_BATTLERS_COUNT] and the fields of one struct BattlePokemon.
GBATTLE_MONS = 0x02023BE4
BATTLE_MON_SIZE = 0x58
BATTLE_MON_SPECIES = 0x00   # u16
BATTLE_MON_HP = 0x28        # u16
BATTLE_MON_LEVEL = 0x2A     # u8
BATTLE_MON_MAX_HP = 0x2C    # u16
BATTLER_PLAYER, BATTLER_OPPONENT = 0, 1

GBATTLE_OUTCOME = 0x02023E8A  # u8
# include/constants/battle.h. The high bit (link battles) cannot occur here.
BATTLE_OUTCOMES = {0: None, 1: "won", 2: "lost", 3: "drew", 4: "ran", 5: "teleported",
                   6: "mon_fled", 7: "caught", 8: "no_safari_balls", 9: "forfeited", 10: "mon_teleported"}


def decode_battle_mon(raw: Optional[bytes], battler: int = BATTLER_OPPONENT) -> Optional[dict[str, int]]:
    """``{species, level, hp, max_hp}`` for one battler, or None when unreadable.

    ``raw`` is ``gBattleMons`` from the start; a species of 0 (or one past the
    last FireRed species) means the slot is empty, which is what a poll outside
    a battle reads.
    """
    off = battler * BATTLE_MON_SIZE
    if raw is None or len(raw) < off + BATTLE_MON_MAX_HP + 2:
        return None
    species = struct.unpack_from("<H", raw, off + BATTLE_MON_SPECIES)[0]
    level = raw[off + BATTLE_MON_LEVEL]
    if not (1 <= species <= 411) or not (1 <= level <= 100):
        return None
    return {"species": species, "level": level,
            "hp": struct.unpack_from("<H", raw, off + BATTLE_MON_HP)[0],
            "max_hp": struct.unpack_from("<H", raw, off + BATTLE_MON_MAX_HP)[0]}


def decode_battle_outcome(byte: Optional[int]) -> Optional[str]:
    """The ``B_OUTCOME`` name, or None for 0 (no battle has ended) / unknown."""
    if byte is None:
        return None
    return BATTLE_OUTCOMES.get(int(byte) & 0x7F)

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
Record = tuple[int, bool, int, int, int, tuple[int, ...], Optional[int]]  # …, opponent id (None when unread)


class BattleTracker:
    """Per-poll battle state → battle segments, attempts per trainer, turn costs."""

    def __init__(self) -> None:
        self.records: list[Record] = []
        # Per-turn reads that are not part of the Record tuple: the outcome byte
        # and the two battlers. They ride alongside rather than widening the
        # tuple, which three other modules unpack and two persist.
        self.details: dict[int, dict[str, Any]] = {}
        # Optional fallback: the turn a trainer battle began → trainer id, from
        # the run's OCR text ("BUG CATCHER RICK would like to battle!"). Used
        # only when the record carries no opponent id (pre-2026-09-14 data).
        self.identity_hints: dict[int, int] = {}

    # --- input ----------------------------------------------------------------

    def record(self, turn: int, in_battle: bool, total: int, wild: int, trainer: int,
               trainers: list[int] | tuple[int, ...], opponent: Optional[int] = None,
               outcome: Optional[str] = None, foe: Optional[dict] = None,
               own: Optional[dict] = None) -> dict[str, Any]:
        rec: Record = (int(turn), bool(in_battle), int(total), int(wild), int(trainer), tuple(sorted(int(t) for t in trainers)),
                       int(opponent) if opponent is not None else None)
        # A re-poll of the same turn (retry) replaces the earlier record.
        if self.records and self.records[-1][0] == rec[0]:
            self.records[-1] = rec
        else:
            self.records.append(rec)
        detail = {k: v for k, v in (("outcome", outcome), ("foe", foe), ("own", own)) if v is not None}
        if detail:
            self.details[rec[0]] = detail
        else:
            self.details.pop(rec[0], None)
        prev = self.records[-2] if len(self.records) > 1 else None
        return {
            "turn": rec[0], "in_battle": rec[1], "battles_total": rec[2], "wild_battles": rec[3],
            "trainer_battles": rec[4], "opponent": rec[6],
            "new_battles": rec[2] - (prev[2] if prev else 0),
            "trainers_new": [t for t in rec[5] if not prev or t not in prev[5]],
            **detail,
        }

    def export_state(self) -> dict[str, Any]:
        return {"battle_records": [[r[0], r[1], r[2], r[3], r[4], list(r[5]), r[6]] for r in self.records],
                "battle_details": {str(k): v for k, v in sorted(self.details.items())}}

    def load_state(self, data: Any) -> None:
        """Restore records (everything else is recomputed). Tolerant of junk."""
        out: list[Record] = []
        raw = data.get("battle_records") if isinstance(data, dict) else None
        for entry in raw or []:
            try:
                if len(entry) not in (6, 7):
                    continue
                opp = entry[6] if len(entry) == 7 and entry[6] is not None else None
                out.append((int(entry[0]), bool(entry[1]), int(entry[2]), int(entry[3]), int(entry[4]),
                            tuple(sorted(int(t) for t in entry[5])), int(opp) if opp is not None else None))
            except (TypeError, ValueError):
                continue
        self.records = out
        self.details = {}
        raw_d = data.get("battle_details") if isinstance(data, dict) else None
        for turn, detail in (raw_d or {}).items():
            try:
                if isinstance(detail, dict):
                    self.details[int(turn)] = detail
            except (TypeError, ValueError):
                continue

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

        def settle_flags(new_flags: list[int], turn: int) -> None:
            # A defeated-flag with no counter move is NOT a fight. Seen once,
            # gpt-6-astra medium: Camper Liam's flag rose in the same window as
            # Brock's with one counted battle, and the screenshots show the run
            # walking past Liam straight to Brock. The segment is kept, marked
            # `uncounted`, so the record explains the flag; summary() skips it.
            for tid in new_flags:
                # A flag landing after a NAMED attempt with that id (the backfill
                # places flags at a savepoint, the opponent id / OCR placed the
                # fight) confirms that attempt as the win instead of adding a
                # phantom one.
                hinted = [x for x in segs if x["kind"] == "trainer" and x["trainer_id"] == tid and x["won"] is not True]
                if hinted:
                    hinted[-1]["won"] = True
                    continue
                # Likewise an UNIDENTIFIED closed attempt just before it: that
                # fight is the one the flag rewards (backfill puts the flag on
                # the savepoint turn, up to a window after the fight closed).
                unnamed = [x for x in segs if x["kind"] == "trainer" and x["trainer_id"] is None and x["won"] is not True]
                if unnamed:
                    unnamed[-1]["trainer_id"] = tid; unnamed[-1]["won"] = True
                    continue
                seg = new_segment("trainer", turn); seg["closed_turn"] = turn
                seg["trainer_id"] = tid; seg["won"] = True; seg["uncounted"] = True
                segs.append(seg)

        for rec in self.records:
            turn, in_battle, total, wild, trainer, flags, opponent = rec
            p_total, p_wild, p_trainer, p_flags = (prev[2], prev[3], prev[4], prev[5]) if prev else (0, 0, 0, ())
            # Who the trainer fight(s) started this turn were against: the
            # opponent id read at this poll names the LAST one started (it is
            # set at battle start and kept), the OCR hint is the fallback.
            named = opponent if opponent is not None else self.identity_hints.get(turn)
            new_total, new_wild, new_trainer = total - p_total, wild - p_wild, trainer - p_trainer
            new_flags = [t for t in flags if t not in p_flags]
            if open_seg is not None:
                # This turn STARTED inside the open battle: it is charged to it (rule A).
                open_seg["turns"] += 1
                open_seg["turn_list"].append(turn)
                if new_total == 0 and in_battle:
                    # Same battle, still going. A flag landing now belongs to an
                    # EARLIER fight (a backfill window puts flags on its savepoint
                    # turn, which can fall inside the next fight) — settle it.
                    if new_flags:
                        settle_flags([f for f in new_flags if f != open_seg["trainer_id"]], turn)
                    prev = rec
                    continue
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
                n_contained = max(contained_trainer, 0)
                for i in range(n_contained):
                    seg = new_segment("trainer", turn); seg["closed_turn"] = turn
                    last_started = open_kind != "trainer" and i == n_contained - 1
                    if last_started and named is not None:
                        seg["trainer_id"] = named
                        if named in new_flags:
                            new_flags.remove(named); seg["won"] = True
                        else:
                            seg["won"] = False
                    elif new_flags:
                        seg["trainer_id"] = new_flags.pop(0); seg["won"] = True
                    segs.append(seg)
                if open_kind is not None:
                    open_seg = new_segment(open_kind, turn)
                    if open_kind == "trainer" and named is not None:
                        open_seg["trainer_id"] = named
            settle_flags(new_flags, turn)
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
        self._attach_details(segs)
        return segs

    def _attach_details(self, segs: list[dict[str, Any]]) -> None:
        """Hang the memory reads on the segments they describe.

        ``outcome`` comes from the poll where the battle CLOSED: the byte is
        cleared when a battle starts and set when it ends, so any earlier poll
        either reads 0 or the PREVIOUS battle's result. ``foe`` comes from the
        last poll taken while the battle was still running, because by the
        closing poll the battler slots have been torn down. Both stay absent on
        a run that never recorded them, which is every run before 2026-09-15 —
        absent is not "unknown outcome", it is "we did not look".
        """
        if not self.details:
            return
        for seg in segs:
            closed = seg.get("closed_turn")
            if closed is not None:
                outcome = (self.details.get(closed) or {}).get("outcome")
                if outcome is not None:
                    seg["outcome"] = outcome
            inside = [t for t in seg.get("turn_list") or [] if t != closed]
            inside = [t for t in ([seg.get("opened_turn")] + inside) if t is not None]
            for turn in reversed(inside):
                foe = (self.details.get(turn) or {}).get("foe")
                if foe:
                    seg["foe"] = foe
                    break

    def summary(self) -> dict[str, Any]:
        if not self.records:
            return {"available": False}
        segs = self.segments()
        last = self.records[-1]
        wild = [s for s in segs if s["kind"] == "wild"]
        trainers: dict[str, dict[str, Any]] = {}
        flags_without_battle = [s["trainer_id"] for s in segs if s.get("uncounted")]
        for s in segs:
            if s["kind"] != "trainer" or s.get("uncounted"):
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
            "flags_without_battle": flags_without_battle,
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
