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


def test_enforce_early_out_of_order_stamp_pre_satisfies_its_deadline(tmp_path):
    logger = FakeLogger()
    # Reach left_house (deadline 50) EARLY, before left_bedroom — out of order.
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref = make_referee(tmp_path, emu, logger, enforce=True)

    # Turn 10: left_house stamped early. left_bedroom (deadline 30) still unmet
    # but its deadline hasn't arrived -> no termination yet.
    assert ref.poll(10) is False
    assert ref.stamps["left_house"] == 10
    assert "left_bedroom" not in ref.stamps

    # Satisfy left_bedroom before its deadline so it doesn't trip at turn 30.
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    assert ref.poll(20) is False
    assert ref.stamps["left_bedroom"] == 20

    # Turn 50: left_house's deadline — but it was stamped early at turn 10, so
    # it's pre-satisfied and must NOT terminate.
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=1)))
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
