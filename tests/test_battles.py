"""Battle telemetry (src/referee/battles.py) and its wiring into the referee.

Plan: artifacts/battle-and-movement-fidelity/plan.md (2026-09-14). Rule A: a
turn belongs to the state it STARTED in; a battle contained in one turn costs
0 turns (5a); losses and rematches are attempts summed per trainer (6).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from src.referee import battles

from src.referee.battles import (
    GTRAINER_BATTLE_OPPONENT_A,
    GAME_STAT_TOTAL_BATTLES,
    GAME_STAT_TRAINER_BATTLES,
    GAME_STAT_WILD_BATTLES,
    GMAIN_IN_BATTLE_BYTE,
    SB1_GAME_STATS,
    SB2_ENCRYPTION_KEY,
    BattleTracker,
    decode_game_stat,
    decode_trainer_flags,
    in_battle_from_byte,
)
from src.referee.referee import GSAVEBLOCK1_PTR, GSAVEBLOCK2_PTR, PLAYER_PARTY_COUNT, SB1_FLAGS, Referee, _SB1_READ_LEN
from tests.test_referee import DEFAULT_PTR, FakeLogger, build_sb1, make_ladder

KEY = 0x9E3779B9
SB2_PTR = 0x02024000


def stats_block(*, total: int, wild: int, trainer: int, trainers: tuple[int, ...] = (), **kw) -> bytes:
    """A SaveBlock1 block with encrypted battle counters and trainer flags set."""
    block = bytearray(build_sb1(**kw))
    for idx, val in ((GAME_STAT_TOTAL_BATTLES, total), (GAME_STAT_WILD_BATTLES, wild), (GAME_STAT_TRAINER_BATTLES, trainer)):
        struct.pack_into("<I", block, SB1_GAME_STATS + 4 * idx, val ^ KEY)
    for tid in trainers:
        f = 0x500 + tid
        block[SB1_FLAGS + (f >> 3)] |= 1 << (f & 7)
    return bytes(block)


def test_decoders_read_the_encrypted_counters_the_flags_and_the_bit():
    block = stats_block(total=37, wild=30, trainer=7, trainers=(102, 414))
    assert len(block) == _SB1_READ_LEN == 0x1300
    assert decode_game_stat(block, GAME_STAT_TOTAL_BATTLES, KEY) == 37
    assert decode_game_stat(block, GAME_STAT_WILD_BATTLES, KEY) == 30
    assert decode_game_stat(block, GAME_STAT_TRAINER_BATTLES, KEY) == 7
    assert decode_game_stat(block[:0x1200], GAME_STAT_TOTAL_BATTLES, KEY) is None  # the old, short read
    assert decode_trainer_flags(block, SB1_FLAGS) == [102, 414]
    assert in_battle_from_byte(0x02) is True and in_battle_from_byte(0x01) is False and in_battle_from_byte(0x00) is False


def _feed(tracker: BattleTracker, rows):
    for turn, in_battle, total, wild, trainer, flags in rows:
        tracker.record(turn, in_battle, total, wild, trainer, flags)


def test_rule_a_charges_the_turns_that_started_inside_a_battle():
    """A wild battle begins during turn 5 (counter moves, still in battle),
    turns 6 and 7 start inside it, the poll after turn 7 is overworld again:
    2 turns, and turn 7 (which began in battle and ended it) is one of them."""
    t = BattleTracker()
    _feed(t, [(1, False, 0, 0, 0, ()), (2, False, 0, 0, 0, ()), (3, False, 0, 0, 0, ()), (4, False, 0, 0, 0, ()),
              (5, True, 1, 1, 0, ()), (6, True, 1, 1, 0, ()), (7, False, 1, 1, 0, ()), (8, False, 1, 1, 0, ())])
    segs = t.segments()
    assert [(s["kind"], s["opened_turn"], s["closed_turn"], s["turns"], s["turn_list"]) for s in segs] == [
        ("wild", 5, 7, 2, [6, 7])]
    assert t.state_at_start(5) is False and t.state_at_start(6) is True and t.state_at_start(8) is False
    assert t.state_at_start(1) is False and t.state_at_start(50) is None  # unknown: turn 49 never polled
    s = t.summary()
    assert s["wild"] == {"count": 1, "turns": 2, "segments": 1}
    assert s["turns_started_in_battle"] == 2


def test_a_battle_contained_in_one_turn_counts_but_costs_nothing():
    t = BattleTracker()
    _feed(t, [(1, False, 0, 0, 0, ()), (2, False, 2, 2, 0, ()), (3, False, 2, 2, 0, ())])
    segs = t.segments()
    assert [(s["kind"], s["turns"]) for s in segs] == [("wild", 0), ("wild", 0)]
    assert t.summary()["wild"] == {"count": 2, "turns": 0, "segments": 2}


def test_trainer_identity_comes_from_the_flag_set_when_the_battle_closes():
    t = BattleTracker()
    _feed(t, [(1, False, 0, 0, 0, ()), (2, True, 1, 0, 1, ()), (3, True, 1, 0, 1, ()), (4, False, 1, 0, 1, (327,))])
    seg, = t.segments()
    assert (seg["kind"], seg["trainer_id"], seg["won"], seg["turns"], seg["group"], seg["name"]) == (
        "trainer", 327, True, 2, "rival_oaks_lab", "Rival (Oak's Lab)")
    g, = t.summary()["trainers"]
    assert (g["group"], g["attempts"], g["turns"], g["won"], g["mandatory"]) == ("rival_oaks_lab", 1, 2, True, True)


def test_a_loss_is_an_attempt_attributed_to_the_rematch_it_precedes():
    """Brock: lose over turns 10-12 (counter moves, no flag), heal, rematch over
    turns 20-22 with the flag. One trainer, two attempts, 3 + 3 turns."""
    t = BattleTracker()
    rows = [(9, False, 0, 0, 0, ()), (10, True, 1, 0, 1, ()), (11, True, 1, 0, 1, ()), (12, True, 1, 0, 1, ()),
            (13, False, 1, 0, 1, ()), (19, False, 1, 0, 1, ()),
            (20, True, 2, 0, 2, ()), (21, True, 2, 0, 2, ()), (22, True, 2, 0, 2, ()), (23, False, 2, 0, 2, (414,))]
    _feed(t, rows)
    segs = [s for s in t.segments() if s["kind"] == "trainer"]
    assert [(s["trainer_id"], s["won"], s["turns"]) for s in segs] == [(414, False, 3), (414, True, 3)]
    g, = t.summary()["trainers"]
    assert (g["id"], g["attempts"], g["turns"], g["won"]) == (414, 2, 6, True)


def test_a_wild_and_a_trainer_battle_in_one_turn_leave_the_trainer_open():
    """Both counters move in turn 3 and the player is still in battle: the wild
    one was contained (fled or won inside the turn), the trainer battle goes on."""
    t = BattleTracker()
    _feed(t, [(2, False, 0, 0, 0, ()), (3, True, 2, 1, 1, ()), (4, False, 2, 1, 1, (102,))])
    segs = t.segments()
    assert [(s["kind"], s["turns"], s["trainer_id"]) for s in segs] == [("wild", 0, None), ("trainer", 1, 102)]


def test_an_unfinished_battle_and_an_unmatched_loss_stay_honest():
    t = BattleTracker()
    _feed(t, [(1, False, 0, 0, 0, ()), (2, True, 1, 0, 1, ()), (3, True, 1, 0, 1, ())])  # run ended mid-battle
    seg, = t.segments()
    assert seg["closed_turn"] is None and seg["turns"] == 1 and seg["trainer_id"] is None and seg["won"] is False
    assert seg["group"] == "unknown"
    g, = t.summary()["trainers"]
    assert g["id"] is None and g["name"] == "Unknown trainer" and g["won"] is False


def test_two_flags_landing_together_identify_the_earlier_unflagged_trainer_too():
    """Backfill windows are 10 turns wide, so Liam and Brock can close inside one
    window with both flags arriving at once: the earlier segment is Liam (lower
    id, met first), the closing one Brock — not two Liam attempts and a
    zero-turn Brock."""
    t = BattleTracker()
    _feed(t, [(1, False, 0, 0, 0, ()), (2, True, 1, 0, 1, ()), (3, True, 1, 0, 1, ()), (4, False, 1, 0, 1, ()),
              (5, True, 2, 0, 2, ()), (6, True, 2, 0, 2, ()), (7, False, 2, 0, 2, (142, 414))])
    segs = [s for s in t.segments() if s["kind"] == "trainer"]
    assert [(s["trainer_id"], s["turns"], s["won"]) for s in segs] == [(142, 2, True), (414, 2, True)]
    assert [(g["name"], g["attempts"], g["turns"]) for g in t.summary()["trainers"]] == [("Camper Liam", 1, 2), ("Leader Brock", 1, 2)]


def test_a_flag_without_a_counter_move_is_recorded_but_is_not_a_fight():
    """gpt-6-astra(medium) ended with 6 defeated flags for 5 counted trainer
    battles: Camper Liam's flag rose with Brock's, and the screenshots show the
    run walked past Liam. The segment stays for the record; no attempt is scored."""
    t = BattleTracker()
    _feed(t, [(1, False, 0, 0, 0, ()), (2, False, 0, 0, 0, (142,))])
    seg, = t.segments()
    assert (seg["trainer_id"], seg["won"], seg["turns"], seg.get("uncounted")) == (142, True, 0, True)
    summ = t.summary()
    assert summ["trainers"] == [] and summ["flags_without_battle"] == [142] and summ["trainer"]["turns"] == 0


def test_identity_hints_name_a_lost_attempt_and_a_late_flag_confirms_the_win():
    """Backfill: the OCR says Rick's fight began at 183 (lost) and again at 200
    (won); Rick's flag only lands at the next savepoint, 210. Two attempts, no
    phantom third, and the loss keeps its name without guessing."""
    t = BattleTracker()
    t.identity_hints = {183: 102, 200: 102}
    _feed(t, [(182, False, 0, 0, 0, ()), (183, True, 1, 0, 1, ()), (184, True, 1, 0, 1, ()), (185, False, 1, 0, 1, ()),
              (199, False, 1, 0, 1, ()), (200, True, 2, 0, 2, ()), (201, True, 2, 0, 2, ()), (202, False, 2, 0, 2, ()),
              (210, False, 2, 0, 2, (102,))])
    segs = [s for s in t.segments() if s["kind"] == "trainer"]
    assert [(s["trainer_id"], s["won"], s["turns"]) for s in segs] == [(102, False, 2), (102, True, 2)]
    g, = t.summary()["trainers"]
    assert (g["id"], g["attempts"], g["turns"], g["won"]) == (102, 2, 4, True)



    t = BattleTracker()
    _feed(t, [(1, False, 0, 0, 0, ()), (2, True, 1, 1, 0, ()), (2, False, 1, 1, 0, ())])
    assert len(t.records) == 2 and t.records[-1][1] is False
    t2 = BattleTracker(); t2.load_state(t.export_state())
    assert t2.records == t.records
    t3 = BattleTracker(); t3.load_state({"battle_records": [[1, False, 0], "junk", None]})
    assert t3.records == [] and t3.summary() == {"available": False}


class BattleFakeEmulator:
    """Serves the referee's reads including the three battle reads. Set
    ``serve_battle=False`` to raise on them like a pre-2026-09-14 fake."""

    def __init__(self, block: bytes, in_battle_byte: int = 0, serve_battle: bool = True, opponent: int = 0):
        self.opponent = opponent  # gTrainerBattleOpponent_A
        self.block = block
        self.in_battle_byte = in_battle_byte
        self.serve_battle = serve_battle

    def read_memory(self, addr: int, length: int) -> bytes:
        if addr == GSAVEBLOCK1_PTR and length == 4:
            return struct.pack("<I", DEFAULT_PTR)
        if addr == PLAYER_PARTY_COUNT and length == 1:
            return b"\x01"
        if addr == DEFAULT_PTR and length == _SB1_READ_LEN:
            return self.block
        if not self.serve_battle:
            raise AssertionError(f"unexpected read addr={addr:#x} len={length}")
        if addr == GSAVEBLOCK2_PTR and length == 4:
            return struct.pack("<I", SB2_PTR)
        if addr == SB2_PTR + SB2_ENCRYPTION_KEY and length == 4:
            return struct.pack("<I", KEY)
        if addr == GMAIN_IN_BATTLE_BYTE and length == 1:
            return bytes([self.in_battle_byte])
        if addr == GTRAINER_BATTLE_OPPONENT_A and length == 2:
            return struct.pack("<H", self.opponent)
        raise AssertionError(f"unexpected read addr={addr:#x} len={length}")


def test_referee_records_battle_state_every_poll_and_persists_it(tmp_path):
    emu = BattleFakeEmulator(stats_block(total=0, wild=0, trainer=0, map_group=4, map_num=0))
    log = FakeLogger()
    ref = Referee(make_ladder(), emu, log, tmp_path)
    ref.poll(1)
    emu.block = stats_block(total=1, wild=1, trainer=0, map_group=4, map_num=0); emu.in_battle_byte = 0x02; emu.opponent = 327
    ref.poll(2)
    emu.block = stats_block(total=1, wild=1, trainer=0, map_group=4, map_num=0); emu.in_battle_byte = 0x00
    ref.poll(3)
    battle_events = [d for t, d in log.events if t == "referee_battle_state"]
    assert [(e["turn"], e["in_battle"], e["wild_battles"], e["new_battles"]) for e in battle_events] == [
        (1, False, 0, 0), (2, True, 1, 1), (3, False, 1, 0)]
    state = ref.export_state()
    assert state["battle_records"] == [[1, False, 0, 0, 0, [], 0], [2, True, 1, 1, 0, [], 327], [3, False, 1, 1, 0, [], 327]]  # opponent id read each poll
    card = ref.scorecard()["battles"]
    assert card["available"] and card["wild"] == {"count": 1, "turns": 1, "segments": 1}
    # A fresh referee restores the records from the persisted file.
    ref2 = Referee(make_ladder(), emu, FakeLogger(), tmp_path)
    assert ref2.battles.records == ref.battles.records


def test_referee_without_battle_reads_still_stamps_and_reports_unavailable(tmp_path):
    emu = BattleFakeEmulator(stats_block(total=5, wild=5, trainer=0, map_group=4, map_num=0), serve_battle=False)
    log = FakeLogger()
    ref = Referee(make_ladder(), emu, log, tmp_path)
    ref.poll(1)
    assert "left_bedroom" in ref.stamps  # the gate latch is untouched by the failed battle reads
    assert not [t for t, _ in log.events if t == "referee_battle_state"]
    assert ref.scorecard()["battles"] == {"available": False}


def test_a_flag_landing_after_an_unnamed_closed_attempt_rewards_that_attempt():
    """Backfill: Liam's fight 135-139 had no OCR name; his flag lands on the
    savepoint record 140, where Brock's fight is already opening. The flag
    names the closed fight — no phantom zero-turn win."""
    t = BattleTracker()
    t.identity_hints = {140: 414}
    _feed(t, [(134, False, 0, 0, 0, ()), (135, True, 1, 0, 1, ()), (136, True, 1, 0, 1, ()), (139, False, 1, 0, 1, ()),
              (140, True, 2, 0, 2, (142,)), (141, True, 2, 0, 2, (142,))])
    segs = [s for s in t.segments() if s["kind"] == "trainer"]
    assert [(s["opened_turn"], s["trainer_id"], s["won"], s.get("uncounted")) for s in segs] == [(135, 142, True, None), (140, 414, False, None)]


def test_the_opponent_id_names_a_lost_fight_and_its_rematch_without_any_flag():
    """Live 2026-09-14 design (option A): gTrainerBattleOpponent_A read at every
    poll. Turn 2: Rick's fight starts (trainer counter 0→1, opponent 102), turn 3
    it ends with no flag → LOST. Turn 4: the counter steps again with the same
    opponent → rematch, flag lands at 5 → won. Two attempts on Rick, 2 turns,
    won — with no OCR hint and no guess from the next flag."""
    t = BattleTracker()
    for r in [(1, False, 0, 0, 0, (), None), (2, True, 1, 0, 1, (), 102), (3, False, 1, 0, 1, (), 102),
              (4, True, 2, 0, 2, (), 102), (5, False, 2, 0, 2, (102,), 102)]:
        t.record(*r)
    segs = [s for s in t.segments() if s["kind"] == "trainer"]
    assert [(s["opened_turn"], s["trainer_id"], s["won"], s["turns"]) for s in segs] == [(2, 102, False, 1), (4, 102, True, 1)]
    g, = t.summary()["trainers"]
    assert (g["id"], g["attempts"], g["turns"], g["won"]) == (102, 2, 2, True)
    # A contained trainer fight (started and over inside one turn) is named too, and lost when no flag lands.
    t2 = BattleTracker()
    for r in [(1, False, 0, 0, 0, (), None), (2, False, 1, 0, 1, (), 103)]:
        t2.record(*r)
    seg, = t2.segments()
    assert (seg["trainer_id"], seg["won"], seg["turns"]) == (103, False, 0)
    # Six-field records (pre-opponent state files) load with opponent None.
    t3 = BattleTracker(); t3.load_state({"battle_records": [[1, False, 0, 0, 0, []], [2, True, 1, 0, 1, [], 327]]})
    assert [r[6] for r in t3.records] == [None, 327]


# -- what was on the field, and how it ended (2026-09-15) ----------------------
# gBattleMons and gBattleOutcome. Both addresses were found by searching every
# run's save states and checked against ground truth we already had; these tests
# guard the DECODING and how a segment picks which poll to believe.

def battle_mon_bytes(species: int, level: int, hp: int = 20, max_hp: int = 20) -> bytes:
    buf = bytearray(battles.BATTLE_MON_SIZE)
    struct.pack_into("<H", buf, battles.BATTLE_MON_SPECIES, species)
    struct.pack_into("<H", buf, battles.BATTLE_MON_HP, hp)
    buf[battles.BATTLE_MON_LEVEL] = level
    struct.pack_into("<H", buf, battles.BATTLE_MON_MAX_HP, max_hp)
    return bytes(buf)


def test_a_battler_decodes_to_species_level_and_hp():
    raw = battle_mon_bytes(7, 6, 18, 21) + battle_mon_bytes(13, 9, 3, 26)
    assert battles.decode_battle_mon(raw, battles.BATTLER_PLAYER) == {"species": 7, "level": 6, "hp": 18, "max_hp": 21}
    assert battles.decode_battle_mon(raw, battles.BATTLER_OPPONENT) == {"species": 13, "level": 9, "hp": 3, "max_hp": 26}


def test_an_empty_or_impossible_battler_reads_as_nothing():
    # Outside a battle the slots are zeroed; a level of 0 or a species past the
    # last one is not a Pokemon, and must not be shown as one.
    assert battles.decode_battle_mon(bytes(battles.BATTLE_MON_SIZE * 2)) is None
    assert battles.decode_battle_mon(battle_mon_bytes(13, 9) + battle_mon_bytes(9999, 5)) is None
    assert battles.decode_battle_mon(battle_mon_bytes(13, 9) + battle_mon_bytes(13, 0)) is None
    assert battles.decode_battle_mon(None) is None
    assert battles.decode_battle_mon(b"\x00" * 4) is None


def test_the_outcome_byte_reads_as_the_games_own_words():
    assert battles.decode_battle_outcome(0) is None      # no battle has ended
    assert battles.decode_battle_outcome(1) == "won"
    assert battles.decode_battle_outcome(2) == "lost"
    assert battles.decode_battle_outcome(4) == "ran"
    assert battles.decode_battle_outcome(7) == "caught"
    assert battles.decode_battle_outcome(None) is None
    assert battles.decode_battle_outcome(200) is None    # nothing the enum names


def test_a_segment_takes_its_outcome_from_the_poll_the_battle_CLOSED_on():
    # The byte is cleared when a battle starts and set when it ends, so a poll
    # taken DURING the battle holds the previous battle's result — 35 of 146
    # real save states did. Believing it would label this fight "won".
    t = battles.BattleTracker()
    t.record(1, False, 0, 0, 0, [], outcome="won")
    t.record(2, True, 1, 1, 0, [], outcome="won", foe={"species": 13, "level": 4})
    t.record(3, True, 1, 1, 0, [], outcome="won", foe={"species": 13, "level": 4})
    t.record(4, False, 1, 1, 0, [], outcome="ran")
    seg, = t.segments()
    assert seg["kind"] == "wild"
    assert seg["outcome"] == "ran"
    assert seg["foe"] == {"species": 13, "level": 4}


def test_a_run_that_never_read_them_carries_neither_key():
    # Absent must not read as "unknown outcome": it means nobody looked.
    t = battles.BattleTracker()
    t.record(1, False, 0, 0, 0, [])
    t.record(2, True, 1, 1, 0, [])
    t.record(3, False, 1, 1, 0, [])
    seg, = t.segments()
    assert "outcome" not in seg and "foe" not in seg


def test_the_reads_survive_a_save_and_restore():
    t = battles.BattleTracker()
    t.record(2, True, 1, 1, 0, [], outcome="won", foe={"species": 16, "level": 3})
    t.record(3, False, 1, 1, 0, [], outcome="caught")
    back = battles.BattleTracker()
    back.load_state(t.export_state())
    assert back.segments() == t.segments()


# -- the reads against real memory ---------------------------------------------

def test_the_decoders_read_four_real_save_states():
    """tests/fixtures/firered_battle_reads.json is gBattleMons and gBattleOutcome
    lifted verbatim out of four mGBA save states of one published run. Unit
    tests above prove the arithmetic; this proves the ADDRESSES, which is the
    part that could be wrong, and it does so against facts from outside the
    read: the trainer's ROM roster, and the HP the fight left behind.
    """
    fx = json.loads((Path(__file__).resolve().parent / "fixtures" / "firered_battle_reads.json").read_text())
    states = fx["states"]

    def read(turn):
        s = states[str(turn)]
        raw = bytes.fromhex(s["gBattleMons"])
        return (battles.decode_battle_mon(raw, battles.BATTLER_OPPONENT),
                battles.decode_battle_outcome(s["gBattleOutcome"]), s["in_battle"])

    foe, outcome, in_battle = read(70)
    assert (outcome, in_battle) == ("caught", False)
    assert foe["hp"] == foe["max_hp"], "a caught Pokemon is not a fainted one"

    foe, outcome, _ = read(90)
    assert outcome == "won" and foe["hp"] == 0, "won by knocking it out"

    foe, outcome, _ = read(100)
    assert outcome == "lost" and foe["hp"] == foe["max_hp"], "the run lost; the foe was untouched"

    foe, outcome, in_battle = read(200)
    assert in_battle and outcome is None, "the byte is cleared while a battle runs"
    # Bug Catcher Sammy's whole party, straight out of src/data/trainer_parties.h
    assert (foe["species"], foe["level"]) == (13, 9)


def test_the_referee_state_carries_the_reads_across_a_continue():
    """A continued run restores the referee from referee_state.json. The first
    live run of these reads (2026-09-15) wrote them into the events and NOT into
    that file, so a continue would have kept every battle's count and lost who
    it was fighting.
    """
    from src.referee.referee import Referee
    ref = Referee.__new__(Referee)          # no emulator: only the state matters
    ref.stamps, ref.autofilled = {}, set()
    ref.battles = battles.BattleTracker()
    ref.battles.record(4, True, 1, 1, 0, [], outcome="won", foe={"species": 74, "level": 10})

    class _P:
        def export_state(self):
            return {"positions": [], "traced_steps": {}, "traced_end": {}}
    ref.progress = _P()

    state = ref.export_state()
    assert state["battle_details"] == {"4": {"outcome": "won", "foe": {"species": 74, "level": 10}}}
    back = battles.BattleTracker()
    back.load_state(state)
    assert back.details == {4: {"outcome": "won", "foe": {"species": 74, "level": 10}}}
