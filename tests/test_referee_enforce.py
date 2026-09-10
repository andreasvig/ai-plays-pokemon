"""Tests for Referee gate enforcement (Phase 5).

Reuses the fake-emulator harness pattern from tests/test_referee.py. The fake
emulator answers the three reads the referee issues per poll (SB1 pointer deref,
SB1 data block, party-count byte). We drive deadline gates by choosing turn
numbers relative to each checkpoint's deadline_turn and controlling which
checkpoints are stamped via the scripted SaveBlock1 image.

Ladder deadlines (see make_ladder):
  left_bedroom   map (4,0)           deadline 30
  left_house     map (3,0)           deadline 50
  starter_chosen flag 0x828          deadline 120
  parcel_delivered var 0x4057>=2     deadline 260
  party_one      party>=1            deadline None (observed-only)
"""

from __future__ import annotations

import struct

import pytest

from src.referee.checkpoints import Checkpoint
from src.referee.referee import (
    GSAVEBLOCK1_PTR,
    PLAYER_PARTY_COUNT,
    SB1_FLAGS,
    SB1_MAP_GROUP,
    SB1_MAP_NUM,
    SB1_VARS,
    VAR_BASE_ID,
    Referee,
    _SB1_READ_LEN,
)

DEFAULT_PTR = 0x02025734


def build_sb1(
    *,
    map_group: int = 0,
    map_num: int = 0,
    flags: dict[int, bool] | None = None,
    vars_: dict[int, int] | None = None,
) -> bytes:
    block = bytearray(_SB1_READ_LEN)
    block[SB1_MAP_GROUP] = map_group & 0xFF
    block[SB1_MAP_NUM] = map_num & 0xFF
    for flag_id, on in (flags or {}).items():
        if on:
            byte_index = SB1_FLAGS + (flag_id >> 3)
            block[byte_index] |= 1 << (flag_id & 7)
    for var_id, value in (vars_ or {}).items():
        offset = SB1_VARS + (var_id - VAR_BASE_ID) * 2
        struct.pack_into("<H", block, offset, value & 0xFFFF)
    return bytes(block)


class FakeImage:
    def __init__(self, *, ptr: int = DEFAULT_PTR, block: bytes | None = None,
                 party_count: int = 0):
        self.ptr = ptr
        self.block = block if block is not None else build_sb1()
        self.party_count = party_count
        self._ptr_reads = 0


class FakeEmulator:
    def __init__(self, image: FakeImage):
        self.image = image

    def set_image(self, image: FakeImage):
        self.image = image
        image._ptr_reads = 0

    def read_memory(self, addr: int, length: int) -> bytes:
        img = self.image
        if addr == GSAVEBLOCK1_PTR and length == 4:
            img._ptr_reads += 1
            return struct.pack("<I", img.ptr)
        if addr == PLAYER_PARTY_COUNT and length == 1:
            return bytes([img.party_count & 0xFF])
        if addr == img.ptr and length == _SB1_READ_LEN:
            return img.block
        raise AssertionError(f"unexpected read addr={addr:#x} len={length}")


class FakeLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log_event(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


def make_ladder() -> list[Checkpoint]:
    return [
        Checkpoint("left_bedroom", "Left bedroom", "map",
                   {"map_group": 4, "map_num": 0}, 30),
        Checkpoint("left_house", "Left house", "map",
                   {"map_group": 3, "map_num": 0}, 50),
        Checkpoint("starter_chosen", "Chose starter", "flag",
                   {"flag_id": 0x828}, 120),
        Checkpoint("parcel_delivered", "Delivered parcel", "var",
                   {"var_id": 0x4057, "min_value": 2}, 260),
        Checkpoint("party_one", "Party has one", "party",
                   {"min_count": 1}, None),
    ]


def make_referee(tmp_path, emu, logger=None, *, enforce=False):
    return Referee(
        make_ladder(), emu, logger or FakeLogger(), tmp_path, enforce=enforce
    )


def gate_missed_events(logger):
    return [d for t, d in logger.events if t == "referee_gate_missed"]


# --- (a) enforce=True, deadline passes unstamped -> terminate ------------------

def test_enforce_missed_gate_terminates(tmp_path):
    logger = FakeLogger()
    # Player sits in a map that satisfies NO checkpoint (e.g. (4,1)).
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref = make_referee(tmp_path, emu, logger, enforce=True)

    # Turn 29: before left_bedroom's deadline (30) — no termination yet.
    assert ref.poll(29) is False
    assert ref.should_terminate() is False
    assert ref.termination_reason is None

    # Turn 30: left_bedroom (deadline 30) still unstamped -> terminate.
    assert ref.poll(30) is True
    assert ref.should_terminate() is True
    assert ref.termination_reason == "missed_gate:left_bedroom"

    # The FIRST missed gate is reported (left_bedroom, not left_house).
    missed = gate_missed_events(logger)
    assert len(missed) == 1
    assert missed[0]["checkpoint_id"] == "left_bedroom"
    assert missed[0]["deadline_turn"] == 30
    assert missed[0]["turn"] == 30


def test_enforce_first_missed_gate_when_both_overdue(tmp_path):
    # Jump straight to a turn past BOTH the first two deadlines while unstamped;
    # the lowest-ladder-order missed gate must be the reason.
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref = make_referee(tmp_path, emu, logger, enforce=True)

    assert ref.poll(60) is True  # past 30 (left_bedroom) and 50 (left_house)
    assert ref.termination_reason == "missed_gate:left_bedroom"
    missed = gate_missed_events(logger)
    assert len(missed) == 1  # latched once, first gate only
    assert missed[0]["checkpoint_id"] == "left_bedroom"


# --- (b) stamped on/before deadline -> no termination; pre-satisfied -----------

def test_enforce_stamped_on_deadline_does_not_terminate(tmp_path):
    logger = FakeLogger()
    # In (4,0): satisfies left_bedroom exactly when polled.
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = make_referee(tmp_path, emu, logger, enforce=True)

    # Poll exactly ON the deadline — stamping runs first, so it's satisfied.
    assert ref.poll(30) is False
    assert ref.stamps["left_bedroom"] == 30
    assert ref.should_terminate() is False
    assert gate_missed_events(logger) == []


def test_enforce_stamped_before_deadline_then_later_gate_not_yet_due(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = make_referee(tmp_path, emu, logger, enforce=True)

    # left_bedroom stamped well before its deadline.
    assert ref.poll(5) is False
    assert ref.stamps["left_bedroom"] == 5

    # Turn 40: left_house deadline (50) hasn't arrived yet -> no termination.
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    assert ref.poll(40) is False
    assert ref.should_terminate() is False


def test_enforce_early_out_of_order_stamp_backfills_and_pre_satisfies(tmp_path):
    logger = FakeLogger()
    # Reach left_house (deadline 50) EARLY at turn 10, before left_bedroom.
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref = make_referee(tmp_path, emu, logger, enforce=True)

    # Turn 10: left_house detected. The safety net back-fills left_bedroom
    # (deadline 30) because 10 <= 30 — reaching left_house proves it was passed.
    assert ref.poll(10) is False
    assert ref.stamps["left_house"] == 10
    assert ref.stamps["left_bedroom"] == 10
    assert "left_bedroom" in ref.autofilled

    # Turn 30 (left_bedroom's deadline) and 50 (left_house's): both already
    # stamped, so neither gate trips — no termination.
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    assert ref.poll(30) is False
    assert ref.poll(50) is False
    assert ref.should_terminate() is False
    assert gate_missed_events(logger) == []


# --- (c) enforce=False -> never terminates (observe-only preserved) ------------

def test_observe_only_never_terminates(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref = make_referee(tmp_path, emu, logger, enforce=False)

    # Sail well past every deadline with nothing stamped.
    assert ref.poll(30) is False
    assert ref.poll(50) is False
    assert ref.poll(300) is False
    assert ref.should_terminate() is False
    assert ref.termination_reason is None
    assert gate_missed_events(logger) == []
    assert ref.scorecard()["termination_reason"] is None


def test_observe_only_is_p4_behavior_unchanged(tmp_path):
    # enforce defaults to False; stamping + scorecard must match P4 exactly.
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = Referee(make_ladder(), emu, logger, tmp_path)  # no enforce kwarg
    ref.poll(2)
    assert ref.stamps["left_bedroom"] == 2
    sc = ref.scorecard()
    assert sc["furthest"] == "left_bedroom"
    assert sc["termination_reason"] is None


# --- (d) scorecard reflects termination_reason --------------------------------

def test_scorecard_terminated_and_non_terminated(tmp_path):
    # Non-terminated enforced run: reason stays None.
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = make_referee(tmp_path, emu, logger, enforce=True)
    ref.poll(10)  # left_bedroom stamped, no deadline missed
    assert ref.scorecard()["termination_reason"] is None

    # Terminated enforced run: reason set to the missed gate.
    logger2 = FakeLogger()
    emu2 = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref2 = make_referee(tmp_path / "r2", emu2, logger2, enforce=True)
    ref2.poll(30)
    sc = ref2.scorecard()
    assert sc["termination_reason"] == "missed_gate:left_bedroom"
    # Other scorecard fields still well-formed.
    assert sc["checkpoints"]["left_bedroom"] is None
    assert sc["furthest"] is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --- (e) per-leg turn caps (PokeBench v1.1, 2026-09-09) -----------------------
#
# Cumulative deadlines let a fast opening bank headroom that one section then
# burns; `leg_cap_turns` bounds the turns spent walking INTO a gate, counted from
# the previous rung's completion. Independent of the deadline: first to fire wins.


def make_capped_ladder() -> list[Checkpoint]:
    return [
        Checkpoint("left_bedroom", "Left bedroom", "map", {"map_group": 4, "map_num": 0}, 30,
                   leg_cap_turns=30),
        # loose cumulative deadline (200) but a tight leg cap (10)
        Checkpoint("left_house", "Left house", "map", {"map_group": 3, "map_num": 0}, 200,
                   leg_cap_turns=10),
        # tight deadline (40), loose cap (100): the deadline fires first here
        Checkpoint("starter_chosen", "Chose starter", "flag", {"flag_id": 0x828}, 40,
                   leg_cap_turns=100),
    ]


def leg_cap_events(logger):
    return [d for t, d in logger.events if t == "referee_leg_cap_spent"]


def test_leg_cap_counts_from_the_previous_gates_stamp(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))  # bedroom
    ref = Referee(make_capped_ladder(), emu, logger, tmp_path, enforce=True)
    assert ref.poll(12) is False            # left_bedroom stamped at 12; leg to left_house opens
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=1)))   # wandering, not outside
    assert ref.poll(21) is False            # 9 turns on the leg — inside the cap of 10
    assert ref.poll(22) is True             # 10 turns — cap spent, deadline (200) far away
    assert ref.termination_reason == "leg_cap:left_house"
    (ev,) = leg_cap_events(logger)
    assert ev["leg_start_turn"] == 12 and ev["leg_turns"] == 10 and ev["leg_cap_turns"] == 10
    assert ev["deadline_turn"] == 200


def test_leg_cap_does_not_start_before_the_previous_gate(tmp_path):
    """The leg into left_house has not opened while left_bedroom is unstamped:
    only left_bedroom's own bounds apply. (Otherwise every cap would count from
    turn 0 and the whole ladder would collapse onto the first leg.)"""
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))  # satisfies nothing
    ref = Referee(make_capped_ladder(), emu, logger, tmp_path, enforce=True)
    assert ref.poll(25) is False            # > left_house's cap of 10 since turn 0, yet nothing fires
    assert ref.poll(30) is True
    # cap 30 == deadline 30 on the first rung: the cumulative deadline is
    # checked first on the same poll, so it is the one reported.
    assert ref.termination_reason == "missed_gate:left_bedroom"
    # The first rung's leg starts at turn 0: with a loose deadline its cap fires.
    ladder = make_capped_ladder()
    ladder[0] = Checkpoint("left_bedroom", "Left bedroom", "map", {"map_group": 4, "map_num": 0}, 100,
                           leg_cap_turns=30)
    ref2 = Referee(ladder, FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1))),
                   FakeLogger(), tmp_path, enforce=True)
    assert ref2.poll(29) is False
    assert ref2.poll(30) is True and ref2.termination_reason == "leg_cap:left_bedroom"


def test_deadline_still_wins_when_it_is_the_tighter_bound(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = Referee(make_capped_ladder(), emu, logger, tmp_path, enforce=True)
    assert ref.poll(1) is False                                          # bedroom at 1
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    assert ref.poll(5) is False                                          # outside at 5 (leg 4 ≤ 10)
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=1)))    # no starter
    assert ref.poll(39) is False                                         # leg 34 ≤ 100, T39 < 40
    assert ref.poll(40) is True
    assert ref.termination_reason == "missed_gate:starter_chosen"
    assert leg_cap_events(logger) == []


def test_leg_cap_is_not_enforced_when_the_referee_only_observes(tmp_path):
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = Referee(make_capped_ladder(), emu, FakeLogger(), tmp_path, enforce=False)
    ref.poll(1)
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    assert ref.poll(80) is False and ref.termination_reason is None


def test_scorecard_reports_turns_per_leg_against_the_cap(tmp_path):
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = Referee(make_capped_ladder(), emu, FakeLogger(), tmp_path, enforce=True)
    ref.poll(6)
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref.poll(9)
    gates = {g["id"]: g for g in ref.scorecard()["gates"]}
    assert gates["left_bedroom"]["leg_turns"] == 6 and gates["left_bedroom"]["leg_cap_turns"] == 30
    assert gates["left_house"]["leg_turns"] == 3 and gates["left_house"]["leg_cap_turns"] == 10
    # the leg the run is ON reports turns spent so far (leg opened at 9, last poll 9)
    assert gates["starter_chosen"]["leg_turns"] == 0
    assert gates["starter_chosen"]["leg_cap_turns"] == 100
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=1)))
    ref.poll(15)
    gates = {g["id"]: g for g in ref.scorecard()["gates"]}
    assert gates["starter_chosen"]["leg_turns"] == 6 and gates["starter_chosen"]["turn"] is None


def test_loader_validates_leg_cap_turns(tmp_path):
    import yaml
    from src.referee.checkpoints import load_ladder

    def write(gate_extra, multigate=False):
        gate = {"id": "a", "name": "A", "type": "map", "signature": {"map_group": 1, "map_num": 0},
                "deadline_turn": 10, **gate_extra}
        if multigate:
            doc = {"benchmark_version": "test", "game": "firered", "rom_sha1": {"firered": "test"}, "checkpoints": [{"multigate": {"gates": [gate], "deadline_turns": [10]}}]}
        else:
            doc = {"benchmark_version": "test", "game": "firered", "rom_sha1": {"firered": "test"}, "checkpoints": [gate]}
        p = tmp_path / "l.yaml"
        p.write_text(yaml.safe_dump(doc))
        return p

    assert load_ladder(write({"leg_cap_turns": 25})).nodes[0].leg_cap_turns == 25
    assert load_ladder(write({})).nodes[0].leg_cap_turns is None
    for bad in (0, -5, "20", True):
        with pytest.raises(ValueError, match="leg_cap_turns"):
            load_ladder(write({"leg_cap_turns": bad}))
    gate = {"id": "a", "name": "A", "type": "map", "signature": {"map_group": 1, "map_num": 0},
            "leg_cap_turns": 5}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"benchmark_version": "test", "game": "firered", "rom_sha1": {"firered": "test"}, "checkpoints": [{"multigate": {"gates": [gate], "deadline_turns": [10]}}]}))
    with pytest.raises(ValueError, match="leg_cap_turns"):
        load_ladder(p)


def test_first_badge_ladder_is_caps_only():
    """Structural, not numeric (the values are Andreas's calibration): every
    rung carries a leg cap and none carries a cumulative deadline — the per-leg
    cap is the only bound (Andreas, 2026-09-10: "kill the old cumulative ladder
    fully, only use this new one")."""
    from src.referee.checkpoints import load_ladder

    ladder = load_ladder("configs/checkpoints-firered-firstbadge.yaml")
    assert ladder.nodes and all(isinstance(n, Checkpoint) for n in ladder.nodes)
    assert all(n.leg_cap_turns for n in ladder.nodes)
    assert all(n.deadline_turn is None for n in ladder.nodes)


def test_leg_cap_enforces_without_a_deadline(tmp_path):
    """A gate with a cap and no deadline is enforced: the run dies on the cap.
    Before 2026-09-10 the deadline-None branch returned early as "observed-only"
    and skipped the cap check, which would have made the caps-only ladder
    unbounded."""
    ladder = make_capped_ladder()
    for cp in ladder:
        cp.deadline_turn = None
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))  # satisfies nothing
    ref = Referee(ladder, emu, logger, tmp_path, enforce=True)
    assert ref.poll(29) is False
    assert ref.poll(30) is True
    assert ref.termination_reason == "leg_cap:left_bedroom"
    assert gate_missed_events(logger) == []
    assert len(leg_cap_events(logger)) == 1 and leg_cap_events(logger)[0]["deadline_turn"] is None


def test_first_badge_lab_gate_is_oaks_trigger_not_the_lab_map():
    """'Entered Oak's Lab' stamps on VAR_MAP_SCENE_PALLET_TOWN_OAK (0x4050) >= 1 —
    Oak's coord_event on Pallet Town's north edge — not on standing in the lab's
    map. As a map gate it stamped for a run that walked into the empty lab on
    its own (glm-5.3-flash(low) continue, 2026-09-10 T70), then burned the
    starter leg with no Oak and no starter. The cross-check is the lab scene
    var the same trigger sets (0x4055 >= 1); the locus still targets the trigger."""
    from src.referee.checkpoints import load_ladder

    ladder = load_ladder("configs/checkpoints-firered-firstbadge.yaml")
    lab = next(n for n in ladder.nodes if n.id == "oaks_lab_entered")
    assert lab.type == "var" and lab.signature == {"var_id": 0x4050, "min_value": 1}
    assert lab.cross_check == {"type": "var", "var_id": 0x4055, "min_value": 1}
    assert lab.locus == {"map_group": 3, "map_num": 0, "tiles": [[12, 1], [13, 1]]}
