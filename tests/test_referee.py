"""Tests for the observe-only Referee (Phase 4).

Drives the Referee against a scripted FAKE emulator whose
``read_memory(addr, length)`` answers the three read kinds the referee issues
per poll:
  1. the gSaveBlock1Ptr deref (addr 0x03005008, len 4) -> the chosen EWRAM ptr,
  2. the SaveBlock1 data block (addr == that ptr, len 0x1200) -> a built block,
  3. the fixed party-count byte (addr 0x02024029, len 1).

The tear-guard re-reads the pointer after the data read, so the fake supports a
per-poll "torn" mode where the pointer differs on its second read.
"""

from __future__ import annotations

import json
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

DEFAULT_PTR = 0x02025734  # a realistic in-EWRAM SaveBlock1 pointer


def build_sb1(
    *,
    map_group: int = 0,
    map_num: int = 0,
    flags: dict[int, bool] | None = None,
    vars_: dict[int, int] | None = None,
) -> bytes:
    """Build a 0x1200-byte SaveBlock1 block with the given map/flags/vars."""
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
    """One poll's scripted memory image."""

    def __init__(
        self,
        *,
        ptr: int = DEFAULT_PTR,
        block: bytes | None = None,
        party_count: int = 0,
        torn_ptr: int | None = None,
    ):
        # torn_ptr, if set, is what the SECOND pointer read returns (tear).
        self.ptr = ptr
        self.block = block if block is not None else build_sb1()
        self.party_count = party_count
        self.torn_ptr = torn_ptr
        self._ptr_reads = 0


class FakeEmulator:
    """Serves a sequence of FakeImages, one consumed per ``poll`` (advanced by
    the test). All reads within a poll see the current image."""

    def __init__(self, image: FakeImage):
        self.image = image

    def set_image(self, image: FakeImage):
        self.image = image
        image._ptr_reads = 0

    def read_memory(self, addr: int, length: int) -> bytes:
        img = self.image
        if addr == GSAVEBLOCK1_PTR and length == 4:
            img._ptr_reads += 1
            # torn mode: the post-read pointer (every 2nd read in an attempt)
            # differs from the pre-read, so each attempt's tear guard trips.
            if img.torn_ptr is not None and img._ptr_reads % 2 == 0:
                return struct.pack("<I", img.torn_ptr)
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


# --- ladder fixture -----------------------------------------------------------

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


def make_referee(tmp_path, emu, logger=None):
    return Referee(make_ladder(), emu, logger or FakeLogger(), tmp_path)


# --- (a) map latch + backtrack ------------------------------------------------

def test_map_latch_and_backtrack(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref = make_referee(tmp_path, emu, logger)

    # t1: in (4,1) — neither (4,0) nor (3,0) yet.
    ref.poll(1)
    assert ref.stamps == {}

    # t2: (4,0) -> left_bedroom stamped at 2.
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref.poll(2)
    assert ref.stamps.get("left_bedroom") == 2

    # t3: (3,0) -> left_house stamped at 3.
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref.poll(3)
    assert ref.stamps.get("left_house") == 3

    # t4: backtrack to (4,0). left_bedroom must NOT re-stamp / be cleared.
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref.poll(4)
    assert ref.stamps["left_bedroom"] == 2  # unchanged
    assert ref.stamps["left_house"] == 3    # unchanged

    # exactly one event per stamped checkpoint
    cp_events = [d for t, d in logger.events if t == "referee_checkpoint"]
    by_id = {}
    for d in cp_events:
        by_id.setdefault(d["id"], []).append(d["turn"])
    assert by_id["left_bedroom"] == [2]
    assert by_id["left_house"] == [3]


# --- (b) flag -----------------------------------------------------------------

def test_flag_latch_once(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(flags={0x828: False})))
    ref = make_referee(tmp_path, emu, logger)

    ref.poll(1)
    assert "starter_chosen" not in ref.stamps

    emu.set_image(FakeImage(block=build_sb1(flags={0x828: True})))
    ref.poll(2)
    assert ref.stamps["starter_chosen"] == 2

    # still set on a later poll — no second event.
    emu.set_image(FakeImage(block=build_sb1(flags={0x828: True})))
    ref.poll(3)
    starter_events = [
        d for t, d in logger.events
        if t == "referee_checkpoint" and d["id"] == "starter_chosen"
    ]
    assert len(starter_events) == 1
    assert starter_events[0]["turn"] == 2


# --- (c) var ------------------------------------------------------------------

def test_var_threshold(tmp_path):
    emu = FakeEmulator(FakeImage(block=build_sb1(vars_={0x4057: 1})))
    ref = make_referee(tmp_path, emu)

    ref.poll(1)
    assert "parcel_delivered" not in ref.stamps  # 1 < 2

    emu.set_image(FakeImage(block=build_sb1(vars_={0x4057: 2})))
    ref.poll(5)
    assert ref.stamps["parcel_delivered"] == 5


# --- (d) torn pointer ---------------------------------------------------------

def test_torn_pointer_retry(tmp_path):
    # Poll 1: pointer changes between the two reads on EVERY attempt -> give up
    # gracefully (no crash, no stamp).
    emu = FakeEmulator(
        FakeImage(
            block=build_sb1(map_group=3, map_num=0),
            torn_ptr=0x02026000,
        )
    )
    ref = make_referee(tmp_path, emu)
    ref.poll(1)  # must not raise
    assert ref.stamps == {}

    # Poll 2: stable pointer -> stamps as normal.
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref.poll(2)
    assert ref.stamps["left_house"] == 2


# --- (e) out-of-range pointer -------------------------------------------------

def test_out_of_range_pointer_noop(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(
        FakeImage(ptr=0x00000000, block=build_sb1(map_group=3, map_num=0))
    )
    ref = make_referee(tmp_path, emu, logger)
    ref.poll(1)  # title screen / not in-game — must not raise
    assert ref.stamps == {}
    assert [d for t, d in logger.events if t == "referee_checkpoint"] == []


# --- (f) events + scorecard ---------------------------------------------------

def test_events_and_scorecard(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = make_referee(tmp_path, emu, logger)

    ref.poll(2)  # left_bedroom @ 2
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref.poll(7)  # left_house @ 7 (deepest stamped in ladder order)

    # event correctness
    cp_events = [d for t, d in logger.events if t == "referee_checkpoint"]
    assert {"id", "name", "type", "turn"} <= set(cp_events[0])
    assert cp_events[0]["id"] == "left_bedroom" and cp_events[0]["turn"] == 2
    assert cp_events[1]["id"] == "left_house" and cp_events[1]["turn"] == 7

    sc = ref.scorecard()
    assert sc["checkpoints"]["left_bedroom"] == 2
    assert sc["checkpoints"]["left_house"] == 7
    assert sc["checkpoints"]["starter_chosen"] is None
    assert sc["furthest"] == "left_house"
    assert sc["termination_reason"] is None


def test_furthest_is_ladder_order_not_first_seen(tmp_path):
    # Stamp a LATER rung first (out of order), then an EARLIER one — furthest
    # must follow ladder order, i.e. the later rung.
    emu = FakeEmulator(FakeImage(block=build_sb1(flags={0x828: True})))
    ref = make_referee(tmp_path, emu)
    ref.poll(1)  # starter_chosen (rung 3) stamped first
    emu.set_image(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref.poll(2)  # left_bedroom (rung 1) stamped later
    assert ref.scorecard()["furthest"] == "starter_chosen"


# --- persistence --------------------------------------------------------------

def test_persistence_restores_latch(tmp_path):
    logger1 = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=0)))
    ref = make_referee(tmp_path, emu, logger1)
    ref.poll(3)
    assert ref.stamps["left_bedroom"] == 3

    state_file = tmp_path / "referee_state.json"
    assert state_file.exists()
    saved = json.loads(state_file.read_text())
    assert saved["stamps"]["left_bedroom"] == 3

    # New Referee from the same dir restores the latch.
    logger2 = FakeLogger()
    ref2 = Referee(make_ladder(), emu, logger2, tmp_path)
    assert ref2.stamps["left_bedroom"] == 3

    # Re-polling an already-stamped checkpoint emits NO new event and keeps the
    # original first-seen turn.
    ref2.poll(9)
    cp_events = [d for t, d in logger2.events if t == "referee_checkpoint"]
    assert cp_events == []
    assert ref2.stamps["left_bedroom"] == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
