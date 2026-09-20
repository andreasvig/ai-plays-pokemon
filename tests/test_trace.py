"""Per-input trace (src/referee/trace.py) — decode, derive, and the wiring
through EmulatorClient.fetch_trace, ProgressTracker.traced_steps and
Referee.record_trace. Plan §3.2, artifacts/battle-and-movement-fidelity/plan.md.
"""

from __future__ import annotations

import struct

from src.emulator.emulator import EmulatorClient
from src.referee import trace
from src.referee.contracts import FIRERED
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
    out = trace.decode_samples(rows, KEY, FIRERED)
    assert out[0] == {"i": 0, "input": "R", "map_group": 3, "map_num": 0, "map_id": None, "x": 5, "y": 7, "in_battle": False, "battles_total": 3, "foe_species": None}
    assert out[1]["x"] is None and out[1]["in_battle"] is None and out[1]["battles_total"] is None
    assert out[2]["x"] == 6 and out[2]["in_battle"] is None
    assert trace.decode_samples(rows, None, FIRERED)[0]["battles_total"] is None  # no key → no counter
    torn = [("D", [pos(10, 3), b"\x00", struct.pack("<I", 1379579746 ^ KEY)])]  # mid-warp torn counter (live 2026-09-14)
    assert trace.decode_samples(torn, KEY, FIRERED)[0]["battles_total"] is None and trace.decode_samples(torn, KEY, FIRERED)[0]["x"] == 10


def test_a_blind_trace_reports_no_steps_rather_than_zero():
    """Live 2026-09-14: the bridge returned rows with empty samples for every
    input; recording 0 traced steps would have replaced the bound with a lie."""
    rows = [("U", [b"", b"", b""]), ("R", [b"", b"", b""])]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 0, 0), start_in_battle=False)
    assert d["blind"] is True and d["overworld_steps"] is None and d["inputs"] == 2


def test_derive_counts_steps_only_outside_battle_and_names_lost_inputs():
    """Walk right twice, bump a wall, get jumped by a wild Pokémon on the fourth
    press, then press A twice in the battle: 2 steps, 1 lost input, battle from
    input 3, 3 inputs spent in battle."""
    rows = [row("R", 1, 0), row("R", 2, 0), row("R", 2, 0), row("R", 3, 0, in_battle=True, total=1),
            row("A", 3, 0, in_battle=True, total=1), row("A", 3, 0, in_battle=True, total=1)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 0, 0), start_in_battle=False)
    assert d == {"inputs": 6, "blind": False, "overworld_steps": 2, "inputs_lost": 1, "battle_inputs": 3,
                 "battle_started_at": 3, "end_in_battle": True, "scripted_tiles": 0, "relocations": 0,
                 "moved_inputs": 2, "battle_edge": 0, "unclassified": 0,
                 # no passable() here, so the lost press cannot be attributed
                 "walls_hit": 0, "turns_to_face": 0, "blocked_by_actor": 0, "blocked_unknown": 1,
                 "idle_ab": 0, "end_tile": (3, 0, 3, 0)}


def test_a_multi_tile_displacement_counts_its_graph_distance_when_one_is_known():
    """Live 2026-09-14 (gemini-3.8-flash low, turn 24): Oak walks the player to
    the lab under five B presses — samples 5, 8, 5, 3 and 7 tiles apart. One
    step per sample undercounted the walk the video and the bound both count."""
    rows = [row("B", 11, 5), row("B", 11, 13), row("B", 11, 13)]
    dist = {((3, 0, 12, 1), (3, 0, 11, 5)): 5, ((3, 0, 11, 5), (3, 0, 11, 13)): 8}
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 12, 1), start_in_battle=False,
                     distance=lambda a, b: dist.get((a, b)))
    assert (d["overworld_steps"], d["scripted_tiles"], d["end_tile"]) == (13, 11, (3, 0, 11, 13))
    plain = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 12, 1), start_in_battle=False)
    assert (plain["overworld_steps"], plain["scripted_tiles"]) == (2, 0)  # no graph: one step per displacement
    unknown = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 12, 1), start_in_battle=False,
                           distance=lambda a, b: None)
    assert unknown["overworld_steps"] == 2  # no path known: one step, never zero


def test_derive_leaves_the_first_step_uncounted_when_the_start_is_unknown_and_a_warp_is_one_step():
    rows = [row("U", 4, 8), row("U", 4, 7), row("U", 2, 9, g=4, m=1)]  # the third press walks through a door
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=None, start_in_battle=None)
    assert d["overworld_steps"] == 2 and d["inputs_lost"] == 0  # first press: no start tile → not counted
    d2 = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 4, 9), start_in_battle=False)
    assert d2["overworld_steps"] == 3


def test_a_counter_step_marks_the_battle_start_even_before_the_bit_rises():
    """Live 2026-09-14, rival fight: the last input's sample read battles_total
    1 with in_battle still 0; the referee's poll a second later read the bit.
    The counter move is the start signal."""
    rows = [row("L", 7, 7, total=0), row("D", 6, 8, total=0), row("B", 6, 8, total=0), row("B", 6, 8, in_battle=False, total=1)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 8, 7), start_in_battle=False)
    assert (d["battle_started_at"], d["battle_inputs"], d["end_in_battle"], d["overworld_steps"]) == (3, 1, True, 2)


def test_a_turn_that_starts_in_battle_charges_no_steps_until_it_is_over():
    rows = [row("A", 3, 0, in_battle=True), row("A", 3, 0, in_battle=False), row("L", 2, 0), row("L", 1, 0)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(3, 0, 3, 0), start_in_battle=True)
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


def test_a_blackout_warp_is_one_step_not_the_shortest_path_home():
    """Live 2026-09-15 (gemini-3.8-flash high): every Pokemon fainted in Viridian
    Forest and the game warped the player to the player's house — 224 tiles on
    one B press, and 374 tiles from Pewter Gym later. Crediting the shortest
    path charged the run 598 steps it never walked. A displacement past
    MAX_TILES_PER_INPUT is the GAME moving the player: one step, like any warp."""
    rows = [row("B", 43, 5, g=1, m=0), row("B", 8, 5, g=4, m=0)]
    far = {((1, 0, 43, 5), (4, 0, 8, 5)): 224}
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), start_tile=(1, 0, 43, 5), start_in_battle=False,
                     distance=lambda a, b: far.get((a, b)))
    assert (d["overworld_steps"], d["relocations"], d["scripted_tiles"]) == (1, 1, 0)
    # the escort stays a walk: 8 tiles is inside one input's reach
    near = {((3, 0, 11, 5), (3, 0, 11, 13)): 8}
    esc = trace.derive(trace.decode_samples([row("B", 11, 13)], KEY, FIRERED), start_tile=(3, 0, 11, 5),
                       start_in_battle=False, distance=lambda a, b: near.get((a, b)))
    assert (esc["overworld_steps"], esc["relocations"], esc["scripted_tiles"]) == (8, 0, 7)
    # the boundary itself is still a walk
    edge = trace.derive(trace.decode_samples([row("B", 11, 13)], KEY, FIRERED), start_tile=(3, 0, 11, 5),
                        start_in_battle=False, distance=lambda a, b: trace.MAX_TILES_PER_INPUT)
    assert edge["overworld_steps"] == trace.MAX_TILES_PER_INPUT and edge["relocations"] == 0


def test_movement_the_poll_sees_after_the_last_sample_is_added_to_the_turn():
    """Live 2026-09-14: 7 of 140 turns ended on the pre-warp tile (the door,
    the stairs) — the warp fade outlasts the input's gap — and the poll a
    second later stood on the other side. That step belongs to the turn."""
    lagging = tracker()
    lagging.record_traced_steps(5, 3, end_tile=(3, 0, 9, 0))   # sampled ON the door; the poll is in the room
    walk_to_room(lagging)
    settled = tracker()
    settled.record_traced_steps(5, 3, end_tile=(4, 3, 0, 0))   # sample and poll agree
    walk_to_room(settled)
    assert lagging.summary()["legs"][0]["steps_walked"] == settled.summary()["legs"][0]["steps_walked"] + 1
    state = lagging.export_state()
    assert state["traced_end"] == {"5": [3, 0, 9, 0]}
    fresh = tracker(); fresh.load_state(state); fresh.observe_stamps({"enter_room": 5})
    assert fresh.summary() == lagging.summary()
    lagging.record_traced_steps(5, 3)  # no end tile → the key is dropped, not left stale
    assert "traced_end" not in lagging.export_state()


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
    assert ref.export_state()["traced_end"] == {"2": [4, 0, 2, 0]}
    assert ref.record_trace(3, []) is None
    blind = ref.record_trace(3, [("U", [b"", b"", b""])])
    assert blind["blind"] and "3" not in ref.export_state()["traced_steps"]  # the bound stands for a blind turn


# --- wasted-input buckets (artifacts/wasted-inputs/plan.md) -------------------

def _wall_at(tile, direction):
    """passable() stub: (3,0,5,5) is walled to the north, open everywhere else."""
    return False if (tuple(tile) == (3, 0, 5, 5) and direction == "U") else True


def test_first_press_in_a_direction_is_a_turn_to_face_and_the_repeats_are_walls():
    # Facing is inferred from the previous direction press: the first U only
    # turns the player (a legal input — that is how you face a sign), every U
    # after it is a bump. Three turns of walking into a wall must therefore
    # read as the presses they were, not as three turns.
    rows = [row("R", 5, 5)] + [row("U", 5, 5) for _ in range(4)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), (3, 0, 4, 5), False, passable=_wall_at)
    assert d["inputs_lost"] == 4
    assert d["turns_to_face"] == 1 and d["walls_hit"] == 3
    assert d["blocked_by_actor"] == 0 and d["blocked_unknown"] == 0


def test_a_press_on_an_open_tile_that_moved_nothing_is_blocked_not_a_wall():
    # Same shape, but the graph says the tile ahead is reachable — a textbox,
    # an NPC or a script ate the press. Measured, never charged (plan W5).
    rows = [row("L", 5, 5), row("L", 5, 5), row("L", 5, 5)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), (3, 0, 6, 5), False, passable=_wall_at)
    # The first L moved (6,5)->(5,5), which also leaves the player facing L, so
    # both presses after it are attributable and neither is a turn-to-face.
    assert d["overworld_steps"] == 1
    assert d["walls_hit"] == 0 and d["blocked_by_actor"] == 2 and d["turns_to_face"] == 0


def test_ab_presses_are_counted_separately_and_never_as_lost_movement():
    rows = [row("A", 5, 5), row("B", 5, 5), row("U", 5, 5), row("U", 5, 5)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), (3, 0, 5, 5), False, passable=_wall_at)
    assert d["idle_ab"] == 2
    assert d["inputs_lost"] == 2          # direction presses only
    # The B reset the facing inference, so the first U is a turn-to-face again.
    assert d["turns_to_face"] == 1 and d["walls_hit"] == 1


def test_an_unknown_facing_under_charges_rather_than_guessing():
    # Without a preceding direction press the facing is unknown, so a press
    # into a wall is booked as a turn-to-face and costs the run nothing.
    d = trace.derive(trace.decode_samples([row("U", 5, 5)], KEY, FIRERED), (3, 0, 5, 5), False, passable=_wall_at)
    assert d["walls_hit"] == 0 and d["turns_to_face"] == 1


def test_without_a_passable_callable_no_press_is_charged():
    rows = [row("U", 5, 5), row("U", 5, 5), row("U", 5, 5)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), (3, 0, 5, 5), False)
    assert d["inputs_lost"] == 3 and d["walls_hit"] == 0 and d["blocked_unknown"] == 2


def test_a_direction_press_inside_a_battle_drops_the_facing_inference():
    # A direction in battle moves a menu cursor, not the player: it must not
    # seed the facing and make the next overworld press look like a bump.
    rows = [row("U", 5, 5, in_battle=True), row("U", 5, 5), row("U", 5, 5), row("U", 5, 5)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), (3, 0, 5, 5), True, passable=_wall_at)
    assert d["battle_inputs"] == 1
    # Press 2 ends the battle and is classified as neither; press 3 re-seeds the
    # facing as a turn-to-face, and only press 4 is charged.
    assert d["turns_to_face"] == 1 and d["walls_hit"] == 1


def test_the_buckets_partition_every_input():
    """The report lists the buckets and states a total. If they did not sum to
    `inputs` the total would be a lie — and the first draft was one: it showed
    overworld_steps (TILES) in the row labelled "moved" (PRESSES), and lost the
    press a battle ends on entirely."""
    rows = [
        row("U", 5, 5),                       # moved onto the walled tile
        row("U", 5, 5),                       # wall (facing north already)
        row("A", 5, 5),                       # idle A/B
        row("D", 5, 5, in_battle=True),       # in battle
        row("D", 5, 5),                       # the press the battle ended on
        row("L", 4, 5),                       # moved
    ]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), (3, 0, 5, 6), False, passable=_wall_at)
    assert sum(d[k] for k in trace.INPUT_BUCKETS) == d["inputs"] == 6
    assert d["moved_inputs"] == 2 and d["battle_edge"] == 1 and d["battle_inputs"] == 1
    assert d["walls_hit"] == 1 and d["idle_ab"] == 1


def test_moved_inputs_counts_presses_while_overworld_steps_counts_tiles():
    """One scripted B press can move the player eight tiles (Oak's escort)."""
    rows = [row("B", 4, 5), row("B", 4, 1)]
    d = trace.derive(trace.decode_samples(rows, KEY, FIRERED), (3, 0, 4, 6), False,
                     distance=lambda a, b: abs(a[3] - b[3]))
    assert d["moved_inputs"] == 2 and d["overworld_steps"] == 5
    assert sum(d[k] for k in trace.INPUT_BUCKETS) == d["inputs"] == 2
