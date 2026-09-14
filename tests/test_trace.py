"""Per-input trace (src/referee/trace.py) — decode, derive, and the wiring
through EmulatorClient.fetch_trace, ProgressTracker.traced_steps and
Referee.record_trace. Plan §3.2, artifacts/battle-and-movement-fidelity/plan.md.
"""

from __future__ import annotations

import struct

from src.emulator.emulator import EmulatorClient
from src.referee import trace
from src.referee.progress import ProgressTracker
from src.referee.referee import Referee
from tests.test_battles import BattleFakeEmulator, stats_block
from tests.test_progress import LADDER, tracker, walk_to_room
from tests.test_referee import FakeLogger, make_ladder

KEY = 0x9E3779B9


def pos(x, y, g=3, m=0):
    return struct.pack("<hhBB", x, y, g, m)


def row(name, x, y, in_battle=False, total=0, g=3, m=0):
    return (name, [pos(x, y, g, m), bytes([0x02 if in_battle else 0x00]), struct.pack("<I", total ^ KEY)])


def test_spec_names_the_three_ranges_the_bridge_samples():
    assert trace.TRACE_SPEC == ["*0x3005008+0:6", "0x3003529:1", "*0x3005008+0x121c:4"]


def test_decode_reads_tile_bit_and_counter_and_tolerates_missing_ranges():
    rows = [row("R", 5, 7, in_battle=False, total=3), ("A", [b"", b"", b""]), ("U", [pos(6, 7)])]
    out = trace.decode_samples(rows, KEY)
    assert out[0] == {"i": 0, "input": "R", "map_group": 3, "map_num": 0, "x": 5, "y": 7, "in_battle": False, "battles_total": 3}
    assert out[1]["x"] is None and out[1]["in_battle"] is None and out[1]["battles_total"] is None
    assert out[2]["x"] == 6 and out[2]["in_battle"] is None
    assert trace.decode_samples(rows, None)[0]["battles_total"] is None  # no key → no counter


def test_a_blind_trace_reports_no_steps_rather_than_zero():
    """Live 2026-09-14: the bridge returned rows with empty samples for every
    input; recording 0 traced steps would have replaced the bound with a lie."""
    rows = [("U", [b"", b"", b""]), ("R", [b"", b"", b""])]
    d = trace.derive(trace.decode_samples(rows, KEY), start_tile=(3, 0, 0, 0), start_in_battle=False)
    assert d["blind"] is True and d["overworld_steps"] is None and d["inputs"] == 2


def test_derive_counts_steps_only_outside_battle_and_names_lost_inputs():
    """Walk right twice, bump a wall, get jumped by a wild Pokémon on the fourth
    press, then press A twice in the battle: 2 steps, 1 lost input, battle from
    input 3, 3 inputs spent in battle."""
    rows = [row("R", 1, 0), row("R", 2, 0), row("R", 2, 0), row("R", 3, 0, in_battle=True, total=1),
            row("A", 3, 0, in_battle=True, total=1), row("A", 3, 0, in_battle=True, total=1)]
    d = trace.derive(trace.decode_samples(rows, KEY), start_tile=(3, 0, 0, 0), start_in_battle=False)
    assert d == {"inputs": 6, "blind": False, "overworld_steps": 2, "inputs_lost": 1, "battle_inputs": 3,
                 "battle_started_at": 3, "end_in_battle": True}


def test_derive_leaves_the_first_step_uncounted_when_the_start_is_unknown_and_a_warp_is_one_step():
    rows = [row("U", 4, 8), row("U", 4, 7), row("U", 2, 9, g=4, m=1)]  # the third press walks through a door
    d = trace.derive(trace.decode_samples(rows, KEY), start_tile=None, start_in_battle=None)
    assert d["overworld_steps"] == 2 and d["inputs_lost"] == 0  # first press: no start tile → not counted
    d2 = trace.derive(trace.decode_samples(rows, KEY), start_tile=(3, 0, 4, 9), start_in_battle=False)
    assert d2["overworld_steps"] == 3


def test_a_turn_that_starts_in_battle_charges_no_steps_until_it_is_over():
    rows = [row("A", 3, 0, in_battle=True), row("A", 3, 0, in_battle=False), row("L", 2, 0), row("L", 1, 0)]
    d = trace.derive(trace.decode_samples(rows, KEY), start_tile=(3, 0, 3, 0), start_in_battle=True)
    assert d["battle_inputs"] == 1 and d["battle_started_at"] is None and d["overworld_steps"] == 2


def test_fetch_trace_parses_rows_and_clears_nothing_on_its_own(monkeypatch):
    emu = EmulatorClient.__new__(EmulatorClient)
    sent = []
    monkeypatch.setattr(emu, "_request", lambda cmd, prefix, **kw: (sent.append(cmd), "TRACE:R|0500070003 00;02;zz/A||")[1])
    rows = emu.fetch_trace()
    assert sent == ["TRACE"]
    assert rows == [("R", [bytes.fromhex("0500070003 00".replace(" ", "")), b"\x02", b""]), ("A", [b""])]
    monkeypatch.setattr(emu, "_request", lambda cmd, prefix, **kw: "TRACE:")
    assert emu.fetch_trace() == []


def test_traced_steps_replace_the_between_poll_bound_for_that_turn_only():
    """The tracker's steps for a turn are the graph distance between its two
    polls unless the trace recorded the exact count. walk_to_room walks turns
    1-5 with a 3-step detour on turn 2 (bound 3); the trace says 7."""
    tr = tracker()
    tr.record_traced_steps(2, 7)
    walk_to_room(tr)
    bound = tracker(); walk_to_room(bound)
    leg_t, leg_b = tr.summary()["legs"][0], bound.summary()["legs"][0]
    assert leg_b["steps_walked"] + 7 - 3 == leg_t["steps_walked"]
    assert leg_t["steps_source"] == "mixed" and leg_t["traced_turns"] == 1 and leg_b["steps_source"] == "bound"
    state = tr.export_state()
    assert state["traced_steps"] == {"2": 7}
    fresh = tracker(); fresh.load_state(state); fresh.observe_stamps({"enter_room": 5})
    assert fresh.summary() == tr.summary()
    assert "traced_steps" not in bound.export_state()  # untraced runs keep the old shape


def test_referee_folds_the_trace_in_before_the_poll_and_logs_it(tmp_path):
    emu = BattleFakeEmulator(stats_block(total=0, wild=0, trainer=0, map_group=4, map_num=0))
    log = FakeLogger()
    ref = Referee(make_ladder(), emu, log, tmp_path)
    ref.poll(1)  # sets last_encryption_key and the first position
    rows = [row("R", 1, 0, g=4, m=0), row("R", 2, 0, g=4, m=0), row("R", 2, 0, g=4, m=0)]
    derived = ref.record_trace(2, rows)
    assert derived["inputs_lost"] == 1 and derived["overworld_steps"] >= 1
    ev = [d for t, d in log.events if t == "turn_input_trace"]
    assert len(ev) == 1 and ev[0]["turn"] == 2 and len(ev[0]["samples"]) == 3
    assert ev[0]["samples"][0]["battles_total"] == 0  # decoded with the poll's key
    ref.poll(2)
    assert ref.export_state()["traced_steps"] == {"2": derived["overworld_steps"]}
    assert ref.record_trace(3, []) is None
    blind = ref.record_trace(3, [("U", [b"", b"", b""])])
    assert blind["blind"] and "3" not in ref.export_state()["traced_steps"]  # the bound stands for a blind turn
