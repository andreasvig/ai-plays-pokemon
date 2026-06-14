"""Tests for the any-order MultiGate rung (loader + referee semantics).

A multigate is a set of gates completable in ANY order, paced by a list of
progressive deadlines: the k-th completion among the members must occur by
``deadline_turns[k-1]``. Members are latched individually; the group is one
rung for scoring/enforcement.

Two halves:
  - loader validation (shape, length match, member deadline ban, ordering);
  - referee behaviour (any-order completion, progressive deadline misses,
    no back-fill into the group, node-oriented scorecard).
"""

from __future__ import annotations

import struct
import textwrap
from pathlib import Path

import pytest

from src.referee.checkpoints import Checkpoint, MultiGate, load_ladder
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


# --- loader validation ----------------------------------------------------

_HEADER = """\
benchmark_version: test-mg
game: firered-us
rom_sha1:
  v1_0: aaa
checkpoints:
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "ladder.yaml"
    p.write_text(textwrap.dedent(body))
    return p


_GOOD_MG = """\
  - multigate:
      deadline_turns: [20, 40]
      gates:
        - id: a
          name: "A"
          type: flag
          signature: {flag_id: 0x100}
        - id: b
          name: "B"
          type: map
          signature: {map_group: 3, map_num: 44}
"""


def test_multigate_parses_into_node_and_flattened_members(tmp_path):
    ladder = load_ladder(_write(tmp_path, _HEADER + _GOOD_MG))
    assert len(ladder.nodes) == 1
    mg = ladder.nodes[0]
    assert isinstance(mg, MultiGate)
    assert [g.id for g in mg.gates] == ["a", "b"]
    assert mg.deadline_turns == [20, 40]
    # synthesised id + name from members
    assert mg.id == "a+b"
    assert "any order" in mg.name
    # flattened members exposed for the per-gate latch, hex parsed
    assert [c.id for c in ladder.checkpoints] == ["a", "b"]
    assert ladder.checkpoints[0].signature["flag_id"] == 0x100


def test_multigate_deadline_count_must_match_gate_count(tmp_path):
    body = _HEADER + """\
  - multigate:
      deadline_turns: [20]
      gates:
        - id: a
          name: "A"
          type: flag
          signature: {flag_id: 0x100}
        - id: b
          name: "B"
          type: map
          signature: {map_group: 3, map_num: 44}
"""
    with pytest.raises(ValueError, match="one deadline per required completion"):
        load_ladder(_write(tmp_path, body))


def test_multigate_member_may_not_set_deadline_turn(tmp_path):
    body = _HEADER + """\
  - multigate:
      deadline_turns: [20, 40]
      gates:
        - id: a
          name: "A"
          type: flag
          signature: {flag_id: 0x100}
          deadline_turn: 30
        - id: b
          name: "B"
          type: map
          signature: {map_group: 3, map_num: 44}
"""
    with pytest.raises(ValueError, match="must not set 'deadline_turn'"):
        load_ladder(_write(tmp_path, body))


def test_multigate_deadlines_must_strictly_increase(tmp_path):
    body = _HEADER + """\
  - multigate:
      deadline_turns: [40, 20]
      gates:
        - id: a
          name: "A"
          type: flag
          signature: {flag_id: 0x100}
        - id: b
          name: "B"
          type: map
          signature: {map_group: 3, map_num: 44}
"""
    with pytest.raises(ValueError, match="strictly increasing"):
        load_ladder(_write(tmp_path, body))


def test_multigate_null_deadline_entry_allowed(tmp_path):
    body = _HEADER + """\
  - multigate:
      deadline_turns: [20, null]
      gates:
        - id: a
          name: "A"
          type: flag
          signature: {flag_id: 0x100}
        - id: b
          name: "B"
          type: map
          signature: {map_group: 3, map_num: 44}
"""
    mg = load_ladder(_write(tmp_path, body)).nodes[0]
    assert mg.deadline_turns == [20, None]


def test_multigate_member_id_collision_with_single_rejected(tmp_path):
    body = _HEADER + """\
  - id: a
    name: "single A"
    type: map
    signature: {map_group: 1, map_num: 0}
    deadline_turn: 5
""" + _GOOD_MG
    with pytest.raises(ValueError, match="duplicate"):
        load_ladder(_write(tmp_path, body))


# --- referee behaviour harness --------------------------------------------


def build_sb1(*, map_group=0, map_num=0, flags=None, vars_=None) -> bytes:
    block = bytearray(_SB1_READ_LEN)
    block[SB1_MAP_GROUP] = map_group & 0xFF
    block[SB1_MAP_NUM] = map_num & 0xFF
    for flag_id, on in (flags or {}).items():
        if on:
            block[SB1_FLAGS + (flag_id >> 3)] |= 1 << (flag_id & 7)
    for var_id, value in (vars_ or {}).items():
        struct.pack_into(
            "<H", block, SB1_VARS + (var_id - VAR_BASE_ID) * 2, value & 0xFFFF
        )
    return bytes(block)


class FakeEmulator:
    def __init__(self):
        self.block = build_sb1()
        self.party_count = 0

    def set(self, **kw):
        self.block = build_sb1(**kw)

    def read_memory(self, addr: int, length: int) -> bytes:
        if addr == GSAVEBLOCK1_PTR and length == 4:
            return struct.pack("<I", DEFAULT_PTR)
        if addr == PLAYER_PARTY_COUNT and length == 1:
            return bytes([self.party_count & 0xFF])
        if addr == DEFAULT_PTR and length == _SB1_READ_LEN:
            return self.block
        raise AssertionError(f"unexpected read addr={addr:#x} len={length}")


class FakeLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log_event(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


def make_nodes():
    """start_map (single) -> {a, b} multigate [20,40] -> after (single)."""
    return [
        Checkpoint("start_map", "Start", "map", {"map_group": 4, "map_num": 0}, 10),
        MultiGate(
            "grp",
            "A / B (any order)",
            [
                Checkpoint("a", "A (Misty-like flag)", "flag", {"flag_id": 0x100}, None),
                Checkpoint("b", "B (Bill-like map)", "map",
                           {"map_group": 3, "map_num": 44}, None),
            ],
            [20, 40],
        ),
        Checkpoint("after", "After", "map", {"map_group": 3, "map_num": 5}, 60),
    ]


def make_ref(tmp_path, emu, logger=None, *, enforce=False):
    return Referee(make_nodes(), emu, logger or FakeLogger(), tmp_path, enforce=enforce)


def missed(logger):
    return [d for t, d in logger.events if t == "referee_gate_missed"]


def test_multigate_completes_in_listed_order(tmp_path):
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, enforce=True)
    emu.set(map_group=4, map_num=0)
    ref.poll(5)  # start_map
    emu.set(map_group=4, map_num=0, flags={0x100: True})
    ref.poll(15)  # a (1st completion, within T20)
    emu.set(map_group=3, map_num=44)
    assert ref.poll(35) is False  # b (2nd completion, within T40) — no miss
    assert ref.stamps["a"] == 15 and ref.stamps["b"] == 35
    assert ref.termination_reason is None


def test_multigate_completes_in_reverse_order(tmp_path):
    """Doing the SECOND-listed member first still satisfies the k=1 deadline."""
    logger = FakeLogger()
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, logger, enforce=True)
    emu.set(map_group=3, map_num=44)
    ref.poll(10)  # b first (1st completion, within T20)
    emu.set(map_group=4, map_num=0, flags={0x100: True})
    assert ref.poll(30) is False  # a second, within T40
    assert ref.termination_reason is None
    assert missed(logger) == []


def test_multigate_first_deadline_missed_terminates(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, logger, enforce=True)
    emu.set(map_group=4, map_num=0)
    assert ref.poll(15) is False  # start_map only; 0/2 done, before T20
    assert ref.poll(20) is True  # k=1 deadline T20, still 0 done -> terminate
    assert ref.termination_reason == "missed_gate:grp"
    ev = missed(logger)[-1]
    assert ev["checkpoint_type"] == "multigate"
    assert ev["needed"] == 1 and ev["completed"] == 0 and ev["deadline_turn"] == 20


def test_multigate_second_deadline_missed_terminates(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, logger, enforce=True)
    emu.set(map_group=4, map_num=0, flags={0x100: True})
    assert ref.poll(15) is False  # a done (1/2) within T20
    emu.set(map_group=4, map_num=0)  # b never done
    assert ref.poll(40) is True  # k=2 deadline T40, only 1 done -> terminate
    ev = missed(logger)[-1]
    assert ev["needed"] == 2 and ev["completed"] == 1 and ev["deadline_turn"] == 40


def test_multigate_both_in_time_no_termination(tmp_path):
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, enforce=True)
    emu.set(map_group=4, map_num=0, flags={0x100: True})
    ref.poll(18)  # a
    emu.set(map_group=3, map_num=44)
    ref.poll(39)  # b — both within their deadlines
    assert ref.should_terminate() is False
    assert ref.scorecard()["termination_reason"] is None


def test_backfill_does_not_fill_multigate_members(tmp_path):
    """Reaching `after` back-fills the earlier single but NOT group members."""
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, enforce=False)
    emu.set(map_group=3, map_num=5)  # `after` (idx 2)
    ref.poll(5)
    assert "after" in ref.stamps
    assert "start_map" in ref.stamps and "start_map" in ref.autofilled  # single back-filled
    assert "a" not in ref.stamps and "b" not in ref.stamps  # group NOT back-filled


def test_scorecard_multigate_node_shape(tmp_path):
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, enforce=False)
    emu.set(map_group=4, map_num=0, flags={0x100: True})
    ref.poll(15)  # start_map + a
    card = ref.scorecard()
    grp = next(g for g in card["gates"] if g.get("kind") == "multigate")
    assert grp["id"] == "grp"
    assert grp["deadline_turns"] == [20, 40]
    assert grp["deadline_turn"] == 40  # final deadline for HUD/compat
    assert grp["completed_count"] == 1 and grp["required_count"] == 2
    assert grp["status"] == "partial"
    members = {m["id"]: m for m in grp["members"]}
    assert members["a"]["status"] == "done" and members["a"]["turn"] == 15
    assert members["b"]["status"] == "pending" and members["b"]["turn"] is None
    # furthest / first_unmet are NODE ids; the group is the first unmet rung.
    assert card["furthest"] == "grp"
    assert card["first_unmet"] == "grp"


def test_scorecard_multigate_done_when_all_members_stamped(tmp_path):
    emu = FakeEmulator()
    ref = make_ref(tmp_path, emu, enforce=False)
    emu.set(map_group=4, map_num=0, flags={0x100: True})
    ref.poll(15)
    emu.set(map_group=3, map_num=44)
    ref.poll(30)
    grp = next(g for g in ref.scorecard()["gates"] if g.get("kind") == "multigate")
    assert grp["status"] == "done"
    assert grp["turn"] == 30  # latest member completion
    assert ref.scorecard()["first_unmet"] == "after"
