"""src/referee/progress.py — between-gate progress on the walk graph.

A hand-built graph pins the semantics: leg windows, monotone d_min, the
fraction formula, D from the leg's opening position, off-graph handling,
cross-map distance through a warp, one-way ledges, efficiency on a closed leg,
unscored legs, no-graph degradation, export/load determinism. Then the
referee-level integration: ``referee_position`` every poll and
``scorecard()["progress"]``.

Toy graph
---------
Map (3,0) "Corridor": x = 0..9 on y = 0 (nodes 0..9), plus a ledge-top tile
(5,1) (node 14) that drops one-way onto (5,0). Map (4,3) "Room": x = 0..3 on
y = 0 (nodes 10..13). Corridor (9,0) <-> Room (0,0) is a door warp.

    corridor: 0-1-2-3-4-5-6-7-8-9 =warp= 10-11-12-13 :room
                        ^
                       14 (ledge, one way down)
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from src.cli.runner import _restore_referee_state
from src.referee.checkpoints import Checkpoint, MultiGate, load_ladder
from src.referee.progress import ProgressTracker
from src.referee.referee import (
    GSAVEBLOCK1_PTR,
    PLAYER_PARTY_COUNT,
    SB1_FLAGS,
    SB1_MAP_GROUP,
    SB1_MAP_NUM,
    SB1_PLAYER_X,
    SB1_PLAYER_Y,
    Referee,
    _SB1_READ_LEN,
)
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph

REPO = Path(__file__).resolve().parents[1]


# --- toy graph + ladder -------------------------------------------------------

def toy_graph() -> WalkGraph:
    nodes = [(3, 0, x, 0) for x in range(10)] + [(4, 3, x, 0) for x in range(4)] + [(3, 0, 5, 1)]
    idx = {c: i for i, c in enumerate(nodes)}
    adj: list[set[int]] = [set() for _ in nodes]

    def link(a, b, both=True):
        adj[idx[a]].add(idx[b])
        if both:
            adj[idx[b]].add(idx[a])

    for x in range(9):
        link((3, 0, x, 0), (3, 0, x + 1, 0))
    for x in range(3):
        link((4, 3, x, 0), (4, 3, x + 1, 0))
    link((3, 0, 9, 0), (4, 3, 0, 0))            # door, both ways
    link((3, 0, 5, 1), (3, 0, 5, 0), both=False)  # ledge: down only
    return WalkGraph(
        nodes, [sorted(a) for a in adj],
        {"3:0": {"name": "Corridor"}, "4:3": {"name": "Room"}},
        {"source": "toy"},
    )


ENTER_ROOM = Checkpoint("enter_room", "Entered room", "map", {"map_group": 4, "map_num": 3}, None)
# NPC stands on room (3,0); the only passable neighbour is (2,0) = node 12.
TALK_NPC = Checkpoint("talk_npc", "Talked to NPC", "flag", {"flag_id": 1}, None,
                      locus={"map_group": 4, "map_num": 3, "near": [[3, 0]]})
NO_LOCUS = Checkpoint("no_locus", "No locus", "flag", {"flag_id": 2}, None)
LEDGE_TOP = Checkpoint("ledge_top", "On the ledge", "flag", {"flag_id": 3}, None,
                       locus={"map_group": 3, "map_num": 0, "tiles": [[5, 1]]})

LADDER = [ENTER_ROOM, TALK_NPC, NO_LOCUS]


def tracker(nodes=LADDER, graph=None):
    return ProgressTracker(toy_graph() if graph is None else graph, nodes)


def current(tr):
    return tr.summary()["current_leg"]


# --- leg 0: fraction, monotone d_min, distance_now ----------------------------

def test_leg0_fraction_and_monotone_dmin():
    tr = tracker()
    r = tr.record(1, 3, 0, 0, 0)
    assert r == {"on_graph": True, "distance": 10, "next_gate": "enter_room"}
    cur = current(tr)
    assert cur["d_open"] == 10 and cur["d_min"] == 10 and cur["fraction"] == 0.0
    assert cur["opened_turn"] == 1 and cur["status"] == "open"

    assert tr.record(2, 3, 0, 3, 0)["distance"] == 7
    assert current(tr)["fraction"] == pytest.approx(0.3)

    # Walking back raises distance_now but never d_min or the fraction.
    assert tr.record(3, 3, 0, 1, 0)["distance"] == 9
    cur = current(tr)
    assert cur["d_min"] == 7 and cur["distance_now"] == 9
    assert cur["fraction"] == pytest.approx(0.3)
    assert cur["steps_walked"] == 3 + 2 and cur["tiles_seen"] == 3
    s = tr.summary()
    assert s["progress"] == pytest.approx(0.3)
    assert s["gates_reached"] == 0 and s["total_gates"] == 3
    assert s["positions_recorded"] == 3
    assert s["graph"] == {"loaded": True, "source": "toy", "nodes": 15}


# --- leg window + D from the opening position ----------------------------------

def test_positions_before_previous_rung_stamps_do_not_count_and_D_is_from_open():
    tr = tracker()
    tr.record(1, 3, 0, 0, 0)
    tr.record(2, 4, 3, 2, 0)   # standing on the NPC tile BEFORE enter_room stamps
    tr.record(3, 4, 3, 0, 0)
    tr.observe_stamps({"enter_room": 3})

    legs = tr.summary()["legs"]
    assert [l["node_id"] for l in legs] == ["enter_room", "talk_npc"]
    leg0, leg1 = legs
    # Leg 0 legitimately closed on its target (the turn-3 tile IS the room entry).
    assert leg0["status"] == "closed" and leg0["closed_turn"] == 3 and leg0["d_min"] == 0
    # Leg 1 opened at the turn-3 position (room entry, node 10): D is 2 steps,
    # not the 12 from the corridor start — and the turn-2 visit to the target
    # tile did NOT pre-lower d_min.
    assert leg1["status"] == "open" and leg1["opened_turn"] == 3
    assert leg1["d_open"] == 2 and leg1["d_min"] == 2 and leg1["fraction"] == 0.0
    assert tr.summary()["progress"] == 1.0

    assert tr.record(4, 4, 3, 1, 0)["distance"] == 1
    assert current(tr)["fraction"] == 0.5
    assert tr.summary()["progress"] == 1.5


# --- off-graph -----------------------------------------------------------------

def test_off_graph_position_keeps_values_and_counts():
    tr = tracker()
    tr.record(1, 3, 0, 0, 0)
    tr.record(2, 3, 0, 3, 0)
    r = tr.record(3, 7, 7, 0, 0)        # a map the graph does not know
    assert r == {"on_graph": False, "distance": None, "next_gate": "enter_room"}
    r = tr.record(4, 3, 0, 3, 5)        # known map, impassable tile
    assert r["on_graph"] is False
    cur = current(tr)
    assert cur["off_graph"] == 2
    assert cur["d_min"] == 7 and cur["distance_now"] == 7
    assert cur["steps_walked"] == 3            # nothing added for off-graph hops
    assert cur["tiles_seen"] == 4              # coverage counts every distinct tile stood on


def test_unknown_opening_position_falls_back_to_first_on_graph():
    tr = tracker()
    tr.observe_stamps({"enter_room": 0})       # stamped before any position was recorded
    legs = tr.summary()["legs"]
    assert legs[0]["status"] == "closed" and legs[0]["closed_turn"] == 0
    assert legs[1]["opened_turn"] == 0 and legs[1]["d_open"] is None
    tr.record(1, 5, 5, 0, 0)                   # off-graph: still no D
    assert current(tr)["d_open"] is None and current(tr)["off_graph"] == 1
    tr.record(2, 3, 0, 0, 0)
    assert current(tr)["d_open"] == 12         # first on-graph position stands in


# --- cross-map distance through the warp, one-way ledge -------------------------

def test_cross_map_distance_through_warp():
    tr = tracker()
    tr.observe_stamps({"enter_room": 0})       # current leg is talk_npc (target node 12)
    # corridor x=0 → x=9 (9) → warp (1) → room x=0 → x=2 (2)
    assert tr.record(1, 3, 0, 0, 0)["distance"] == 12
    assert tr.record(2, 3, 0, 9, 0)["distance"] == 3
    assert tr.record(3, 4, 3, 0, 0)["distance"] == 2


def test_one_way_ledge_asymmetry():
    # Down the ledge counts toward the room …
    tr = tracker([ENTER_ROOM])
    assert tr.record(1, 3, 0, 5, 1)["distance"] == 6   # 14→5 (1) →9 (4) →10 (1)
    # … but the ledge top is unreachable from below: on-graph, distance None,
    # nothing lowered, not counted as off-graph.
    tr = tracker([LEDGE_TOP])
    r = tr.record(1, 3, 0, 5, 0)
    assert r == {"on_graph": True, "distance": None, "next_gate": "ledge_top"}
    cur = current(tr)
    assert cur["d_min"] is None and cur["fraction"] is None and cur["off_graph"] == 0
    assert cur["tiles_seen"] == 1 and cur["scored"] is True
    # Standing on it: D == 0 → fraction 0.0 by rule, never a division by zero.
    assert tr.record(2, 3, 0, 5, 1)["distance"] == 0
    assert current(tr)["d_open"] == 0 and current(tr)["fraction"] == 0.0


# --- efficiency + coverage on a closed leg -------------------------------------

def walk_to_room(tr):
    tr.record(1, 3, 0, 0, 0)
    tr.record(2, 3, 0, 3, 0)
    tr.record(3, 3, 0, 1, 0)   # backtrack: wasted steps
    tr.record(4, 3, 0, 9, 0)
    tr.record(5, 4, 3, 0, 0)
    tr.observe_stamps({"enter_room": 5})


def test_efficiency_on_closed_leg():
    tr = tracker()
    walk_to_room(tr)
    leg0, leg1 = tr.summary()["legs"]
    assert leg0["status"] == "closed" and leg0["closed_turn"] == 5
    assert leg0["d_open"] == 10 and leg0["d_min"] == 0
    assert leg0["steps_walked"] == 3 + 2 + 8 + 1
    assert leg0["efficiency"] == pytest.approx(10 / 14)
    assert leg0["tiles_seen"] == 5
    # The open leg has no efficiency yet and starts from the closing position.
    assert leg1["efficiency"] is None and leg1["steps_walked"] == 0 and leg1["tiles_seen"] == 1
    assert leg1["d_open"] == 2


def test_efficiency_capped_at_one_and_none_when_D_zero():
    tr = tracker()
    tr.record(1, 3, 0, 9, 0)
    tr.record(2, 4, 3, 0, 0)
    tr.observe_stamps({"enter_room": 2})
    assert tr.summary()["legs"][0]["efficiency"] == 1.0    # D 1, walked 1
    tr = tracker()
    tr.record(1, 4, 3, 0, 0)                               # opened ON the target
    tr.observe_stamps({"enter_room": 1})
    assert tr.summary()["legs"][0]["efficiency"] is None


# --- unscored leg, run complete --------------------------------------------------

def test_unscored_leg_when_locus_empty():
    tr = tracker()
    tr.record(1, 4, 3, 0, 0)
    tr.record(2, 4, 3, 2, 0)
    tr.observe_stamps({"enter_room": 1, "talk_npc": 2})
    r = tr.record(3, 4, 3, 1, 0)
    assert r == {"on_graph": True, "distance": None, "next_gate": "no_locus"}
    cur = current(tr)
    assert cur["scored"] is False and cur["fraction"] is None and cur["d_open"] is None
    assert cur["steps_walked"] == 1 and cur["tiles_seen"] == 2   # walking still measured
    s = tr.summary()
    assert s["gates_reached"] == 2 and s["progress"] == 2.0      # never wrong, never > gates


def test_complete_run_has_no_current_leg_and_progress_equals_total():
    tr = tracker()
    tr.record(1, 4, 3, 2, 0)
    tr.observe_stamps({"enter_room": 1, "talk_npc": 1, "no_locus": 1})
    s = tr.summary()
    assert s["current_leg"] is None and s["progress"] == 3.0 == s["total_gates"]
    assert [l["status"] for l in s["legs"]] == ["closed", "closed", "closed"]
    assert tr.record(2, 3, 0, 0, 0) == {"on_graph": True, "distance": None, "next_gate": None}


# --- skipped rung, multigate retarget ------------------------------------------

def test_skipped_rung_is_labelled_and_not_counted():
    tr = tracker()
    tr.record(1, 3, 0, 0, 0)
    tr.record(2, 4, 3, 2, 0)
    tr.observe_stamps({"talk_npc": 2})        # enter_room never stamped (past its deadline)
    s = tr.summary()
    statuses = {l["node_id"]: l["status"] for l in s["legs"]}
    assert statuses == {"enter_room": "skipped", "talk_npc": "closed", "no_locus": "open"}
    assert s["legs"][0]["efficiency"] is None and s["legs"][0]["d_min"] == 2  # stats kept
    assert s["legs"][1]["opened_turn"] == s["legs"][1]["closed_turn"] == 2
    assert s["gates_reached"] == 1 and s["progress"] == 1.0
    assert s["current_leg"]["node_id"] == "no_locus"


def test_multigate_retargets_to_unstamped_members():
    on_entry = Checkpoint("at_door", "At the door", "flag", {"flag_id": 9}, None,
                          locus={"map_group": 4, "map_num": 3, "tiles": [[0, 0]]})
    mg = MultiGate("pair", "Pair", [TALK_NPC, on_entry], [None, None])
    tr = tracker([mg])
    assert tr.record(1, 3, 0, 9, 0)["distance"] == 1          # union target {10, 12}: door is 1 away
    tr.record(2, 4, 3, 0, 0)
    tr.observe_stamps({"at_door": 2})                          # one member done: leg stays open
    cur = current(tr)
    assert cur["node_id"] == "pair" and cur["status"] == "open"
    assert cur["d_open"] == 2 and cur["d_min"] == 2            # re-seeded against the NPC tile
    assert cur["steps_walked"] == 1                            # walking totals carry over
    assert tr.record(3, 4, 3, 1, 0)["distance"] == 1
    assert current(tr)["steps_walked"] == 2
    assert tr.summary()["progress"] == 0.5
    tr.record(4, 4, 3, 2, 0)
    tr.observe_stamps({"at_door": 2, "talk_npc": 4})
    s = tr.summary()
    assert s["gates_reached"] == 1 and s["progress"] == 1.0 and s["current_leg"] is None


# --- no graph ------------------------------------------------------------------

def test_no_graph_records_positions_but_scores_nothing():
    tr = ProgressTracker(None, LADDER)
    assert tr.record(1, 3, 0, 0, 0) == {"on_graph": False, "distance": None, "next_gate": "enter_room"}
    tr.record(2, 3, 0, 1, 0)
    tr.observe_stamps({"enter_room": 2})
    s = tr.summary()
    assert s["progress"] is None and s["gates_reached"] == 1
    assert s["graph"] == {"loaded": False, "source": None, "nodes": 0}
    leg0 = s["legs"][0]
    assert leg0["status"] == "closed" and leg0["tiles_seen"] == 2 and leg0["off_graph"] == 2
    assert leg0["d_open"] is None and leg0["steps_walked"] is None and leg0["efficiency"] is None
    assert s["current_leg"]["scored"] is False
    assert tr.export_state() == {"positions": [[1, 3, 0, 0, 0], [2, 3, 0, 1, 0]]}


# --- export / load determinism ----------------------------------------------------

def test_export_load_round_trip_reproduces_summary():
    tr = tracker()
    walk_to_room(tr)
    tr.record(6, 4, 3, 1, 0)
    tr.record(7, 9, 9, 1, 1)                     # off-graph
    tr.record(8, 4, 3, 2, 0)
    tr.observe_stamps({"enter_room": 5, "talk_npc": 8})
    tr.record(9, 4, 3, 3, 0)

    state = json.loads(json.dumps(tr.export_state()))   # through JSON like the bundle
    assert state == {"positions": [[1, 3, 0, 0, 0], [2, 3, 0, 3, 0], [3, 3, 0, 1, 0], [4, 3, 0, 9, 0],
                                   [5, 4, 3, 0, 0], [6, 4, 3, 1, 0], [7, 9, 9, 1, 1], [8, 4, 3, 2, 0],
                                   [9, 4, 3, 3, 0]]}
    fresh = tracker()
    fresh.load_state(state)
    fresh.observe_stamps({"talk_npc": 8, "enter_room": 5})   # order of the dict must not matter
    assert fresh.summary() == tr.summary()
    # Stamps first, positions second — same answer.
    other = tracker()
    other.observe_stamps({"enter_room": 5, "talk_npc": 8})
    other.load_state(state)
    assert other.summary() == tr.summary()


def test_load_state_tolerates_junk():
    tr = tracker()
    tr.load_state({"positions": [[1, 3, 0, 0, 0], "junk", [2, 3, 0], None, [3, "x", 0, 0, 0], [4, 3, 0, 1, 0]]})
    assert tr.export_state() == {"positions": [[1, 3, 0, 0, 0], [4, 3, 0, 1, 0]]}
    tr.load_state("not a dict")
    assert tr.export_state() == {"positions": []} and tr.summary()["progress"] is not None


# --- ladder loci -------------------------------------------------------------------

def test_firstbadge_ladder_carries_the_five_loci():
    ladder = load_ladder(REPO / "configs" / "checkpoints-firered-firstbadge.yaml")
    loci = {cp.id: cp.locus for cp in ladder.checkpoints if cp.locus}
    assert set(loci) == {"starter_chosen", "rival1_done", "parcel_delivered", "pokedex_received", "brock_defeated"}
    assert loci["rival1_done"] == {"map_group": 4, "map_num": 3, "tiles": [[5, 8], [6, 8], [7, 8]]}
    assert loci["brock_defeated"] == {"map_group": 6, "map_num": 2, "near": [[6, 5]]}
    # Map gates need none — the graph derives their entry tiles.
    assert all(cp.locus is None for cp in ladder.checkpoints if cp.type == "map")


@pytest.mark.parametrize("bad", [
    {"map_group": 4, "map_num": 3},                              # no tiles at all
    {"map_group": 4, "map_num": 3, "tiles": []},                 # empty
    {"map_group": 4, "map_num": 3, "tile": [[1, 1]]},            # typo'd key
    {"map_group": "4", "map_num": 3, "tiles": [[1, 1]]},         # non-int
    {"map_group": True, "map_num": 3, "tiles": [[1, 1]]},        # bool is not an int
    {"map_group": 4, "map_num": 3, "near": [[1, 1, 1]]},         # not a pair
    {"map_group": 4, "map_num": 3, "near": [1, 1]},              # not pairs
    [4, 3],                                                      # not a dict
])
def test_locus_validation_rejects(tmp_path, bad):
    import yaml
    doc = {
        "benchmark_version": "t", "game": "g", "rom_sha1": {"v": "x"},
        "checkpoints": [{"id": "a", "name": "A", "type": "flag", "signature": {"flag_id": 1},
                         "deadline_turn": None, "locus": bad}],
    }
    path = tmp_path / "l.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match="locus"):
        load_ladder(path)


# --- runner restore caps positions ---------------------------------------------------

def test_restore_referee_state_caps_positions_to_savepoint_turn(tmp_path):
    sp = tmp_path / "savepoints" / "turn_20"
    sp.mkdir(parents=True)
    (sp / "referee_state.json").write_text(json.dumps({
        "stamps": {"a": 10, "b": 30}, "autofilled": [],
        "positions": [[10, 3, 0, 1, 0], [20, 3, 0, 2, 0], [21, 3, 0, 3, 0], [30, 4, 3, 0, 0], "junk", [1, 2]],
    }))
    new_run = tmp_path / "new"
    new_run.mkdir()
    _restore_referee_state(sp, new_run, up_to_turn=20)
    restored = json.loads((new_run / "referee_state.json").read_text())
    assert restored == {"stamps": {"a": 10}, "autofilled": [],
                        "positions": [[10, 3, 0, 1, 0], [20, 3, 0, 2, 0]]}


def test_restore_legacy_bundle_without_positions(tmp_path):
    sp = tmp_path / "savepoints" / "turn_20"
    sp.mkdir(parents=True)
    (sp / "referee_state.json").write_text(json.dumps({"stamps": {"a": 10}, "autofilled": []}))
    new_run = tmp_path / "new"
    new_run.mkdir()
    _restore_referee_state(sp, new_run, up_to_turn=20)
    assert json.loads((new_run / "referee_state.json").read_text())["positions"] == []


# --- referee integration -------------------------------------------------------------

DEFAULT_PTR = 0x02025734


def build_sb1(*, map_group=0, map_num=0, x=0, y=0, flags=None) -> bytes:
    block = bytearray(_SB1_READ_LEN)
    struct.pack_into("<h", block, SB1_PLAYER_X, x)
    struct.pack_into("<h", block, SB1_PLAYER_Y, y)
    block[SB1_MAP_GROUP] = map_group
    block[SB1_MAP_NUM] = map_num
    for flag_id, on in (flags or {}).items():
        if on:
            block[SB1_FLAGS + (flag_id >> 3)] |= 1 << (flag_id & 7)
    return bytes(block)


class FakeEmulator:
    def __init__(self, block: bytes, ptr: int = DEFAULT_PTR):
        self.block, self.ptr = block, ptr

    def at(self, **kw):
        self.block = build_sb1(**kw)
        self.ptr = DEFAULT_PTR

    def read_memory(self, addr: int, length: int) -> bytes:
        if addr == GSAVEBLOCK1_PTR and length == 4:
            return struct.pack("<I", self.ptr)
        if addr == PLAYER_PARTY_COUNT and length == 1:
            return b"\x00"
        if addr == self.ptr and length == _SB1_READ_LEN:
            return self.block
        raise AssertionError(f"unexpected read addr={addr:#x} len={length}")


class FakeLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log_event(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


def positions_logged(logger):
    return [d for t, d in logger.events if t == "referee_position"]


def test_referee_logs_position_every_poll_and_scores_progress(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(build_sb1(map_group=3, map_num=0, x=0, y=0))
    ref = Referee(LADDER, emu, logger, tmp_path, walkgraph=toy_graph())

    ref.poll(1)
    ev = positions_logged(logger)
    assert ev == [{"turn": 1, "map_group": 3, "map_num": 0, "x": 0, "y": 0,
                   "on_graph": True, "distance": 10, "next_gate": "enter_room"}]
    assert not ({"id", "type"} & set(ev[0]))  # RunLogger envelope keys untouched

    emu.at(map_group=4, map_num=3, x=0, y=0)
    ref.poll(2)                                      # enter_room stamps this poll
    assert ref.stamps == {"enter_room": 2}
    ev = positions_logged(logger)
    assert len(ev) == 2
    # The position is recorded BEFORE stamping, so it still reports against the
    # leg that closes this turn (distance 0 to the room entry).
    assert ev[1]["distance"] == 0 and ev[1]["next_gate"] == "enter_room"
    prog = ref.scorecard()["progress"]
    assert prog["progress"] == 1.0 and prog["gates_reached"] == 1
    assert prog["current_leg"]["node_id"] == "talk_npc" and prog["current_leg"]["d_open"] == 2
    assert prog["legs"][0]["status"] == "closed" and prog["legs"][0]["closed_turn"] == 2

    emu.at(map_group=4, map_num=3, x=1, y=0)
    ref.poll(3)
    assert positions_logged(logger)[2]["distance"] == 1
    assert ref.scorecard()["progress"]["progress"] == 1.5

    # Off-graph poll still logs; a not-in-game poll (pointer outside EWRAM) logs nothing.
    emu.at(map_group=9, map_num=9, x=1, y=1)
    ref.poll(4)
    assert positions_logged(logger)[3]["on_graph"] is False
    emu.ptr = 0
    ref.poll(5)
    assert len(positions_logged(logger)) == 4

    # Persisted state carries the positions; a fresh Referee over the same run
    # dir lands on the identical summary (continue path).
    state = json.loads((tmp_path / "referee_state.json").read_text())
    assert state["positions"] == [[1, 3, 0, 0, 0], [2, 4, 3, 0, 0], [3, 4, 3, 1, 0], [4, 9, 9, 1, 1]]
    assert ref.export_state() == state
    again = Referee(LADDER, emu, FakeLogger(), tmp_path, walkgraph=toy_graph())
    assert again.scorecard()["progress"] == ref.scorecard()["progress"]


def test_referee_without_graph_never_raises_and_reports_none(tmp_path):
    logger = FakeLogger()
    emu = FakeEmulator(build_sb1(map_group=3, map_num=0, x=2, y=0))
    ref = Referee(LADDER, emu, logger, tmp_path, graph_path=tmp_path / "missing.json")
    assert ref.walkgraph is None
    ref.poll(1)
    assert positions_logged(logger) == [{"turn": 1, "map_group": 3, "map_num": 0, "x": 2, "y": 0,
                                         "on_graph": False, "distance": None, "next_gate": "enter_room"}]
    prog = ref.scorecard()["progress"]
    assert prog["progress"] is None and prog["graph"]["loaded"] is False
    assert prog["positions_recorded"] == 1


def test_referee_position_serializes_with_real_logger(tmp_path):
    from src.core.logger import RunLogger

    logger = RunLogger({"runs_directory": str(tmp_path), "run_name": "postest"})
    emu = FakeEmulator(build_sb1(map_group=3, map_num=0, x=4, y=0))
    ref = Referee(LADDER, emu, logger, logger.run_dir, walkgraph=toy_graph())
    ref.poll(7)
    events = [json.loads(l) for l in (logger.run_dir / "events.jsonl").read_text().splitlines() if l.strip()]
    pos = [e for e in events if e.get("type") == "referee_position"]
    assert len(pos) == 1 and isinstance(pos[0]["id"], int)
    assert pos[0]["turn"] == 7 and pos[0]["x"] == 4 and pos[0]["distance"] == 6


@pytest.mark.skipif(not (REPO / DEFAULT_GRAPH_PATH).exists(), reason="walk graph not built")
def test_referee_default_loads_committed_graph(tmp_path):
    ladder = load_ladder(REPO / "configs" / "checkpoints-firered-firstbadge.yaml")
    emu = FakeEmulator(build_sb1(map_group=3, map_num=0, x=16, y=13))   # Pallet, outside the lab
    ref = Referee(ladder.nodes, emu, FakeLogger(), tmp_path)
    assert ref.walkgraph is not None and len(ref.walkgraph) > 1000
    prog = ref.scorecard()["progress"]
    assert prog["graph"]["loaded"] is True and prog["graph"]["source"].startswith("pret/")
