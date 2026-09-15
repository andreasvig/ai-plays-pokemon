"""Board-side battle and movement figures (src/app/battle_stats.py)."""

from __future__ import annotations

import json

from src.app.battle_stats import battle_summary, cap_records, load_steps_backfill, movement, ocr_hints, reconcile_with_gates, synthesize_records
from src.referee.battles import BattleTracker


def _summary(records, states):
    t = BattleTracker()
    for r in synthesize_records(records, states):
        t.record(*r)
    return t.summary()


def test_synthesis_places_one_wild_battle_at_the_visible_segment_and_charges_its_turns():
    """Savepoints at 10 and 20: one wild battle in (10, 20]. Screenshots say
    turns 14, 15 and 16 started in battle → it began during 13, ran through 16
    (rule A: turns 14, 15, 16 are charged), and the counter moves at 13."""
    records = [[10, False, 0, 0, 0, []], [20, False, 1, 1, 0, []]]
    states = {t: t in (14, 15, 16) for t in range(1, 22)}
    recs = synthesize_records(records, states)
    by_turn = {r[0]: r for r in recs}
    assert by_turn[12][2] == 0 and by_turn[13] == (13, True, 1, 1, 0, (), None) and by_turn[16][1] is False
    s = _summary(records, states)
    assert s["wild"] == {"count": 1, "turns": 3, "segments": 1}
    assert s["turns_started_in_battle"] == 3


def test_synthesis_puts_contained_battles_at_the_window_end_and_clears_classifier_noise():
    """Two wild battles counted, no screenshot shows a battle: both contained (0
    turns). A third window shows a battle state but the counter is flat: noise."""
    records = [[10, False, 0, 0, 0, []], [20, False, 2, 2, 0, []], [30, False, 2, 2, 0, []]]
    states = {t: (t == 25) for t in range(1, 32)}
    s = _summary(records, states)
    assert s["wild"] == {"count": 2, "turns": 0, "segments": 2}
    assert s["turns_started_in_battle"] == 0


def test_synthesis_gives_the_trainer_increment_to_the_longest_segment_and_lands_the_flag_on_its_close():
    """Window (10, 20]: one wild + one trainer battle; segments 12-13 (short) and
    15-19 (long). The trainer is the long one, its flag lands when it closes."""
    records = [[10, False, 0, 0, 0, []], [20, False, 2, 1, 1, [102]]]
    states = {t: t in (13, 14, 16, 17, 18, 19, 20) for t in range(1, 22)}  # start-states: after 12,13 and after 15..19
    s = _summary(records, states)
    (wild,) = [x for x in s["segments"] if x["kind"] == "wild"]
    (trainer,) = [x for x in s["segments"] if x["kind"] == "trainer"]
    assert (wild["opened_turn"], wild["turns"]) == (12, 2)
    assert (trainer["opened_turn"], trainer["turns"], trainer["trainer_id"], trainer["won"]) == (15, 5, 102, True)


def test_synthesis_keeps_a_battle_running_across_a_savepoint_as_one_battle():
    records = [[10, True, 1, 1, 0, []], [20, False, 1, 1, 0, []]]
    states = {t: t in (10, 11, 12) for t in range(1, 22)}  # started during 9, ends during 12
    recs = synthesize_records(records, states)
    # recs[i] is turn i+1: turn 10 is the exact savepoint (still in battle), turn 11 still in, turn 12 ends it
    assert recs[9] == (10, True, 1, 1, 0, (), None) and recs[10][1] is True and recs[11][1] is False
    assert recs[8][2] == 1  # the counter moved during turn 9, when the battle began
    s = _summary(records, states)
    assert s["wild"]["count"] == 1 and s["wild"]["turns"] == 3  # turns 10, 11, 12 started inside it


def test_battle_summary_prefers_live_then_backfill_then_savepoint(tmp_path):
    live = {"available": True, "polls": 100, "wild": {"count": 3}}
    assert battle_summary(tmp_path, {"battles": live}, 100) == (live, "live")
    gappy = {"available": True, "polls": 10, "wild": {"count": 3}}
    assert battle_summary(tmp_path, {"battles": gappy}, 100) == (None, None)  # too gappy, nothing on disk
    (tmp_path / "battle_backfill.json").write_text(json.dumps({"records": [[10, False, 0, 0, 0, []], [20, False, 1, 1, 0, []]]}))
    s, fid = battle_summary(tmp_path, None, 20)
    assert fid == "savepoint" and s["wild"]["count"] == 1
    (tmp_path / "state_backfill.json").write_text(json.dumps({"states": {str(t): t in (14, 15) for t in range(1, 22)}}))
    s, fid = battle_summary(tmp_path, None, 20)
    assert fid == "backfill" and s["wild"] == {"count": 1, "turns": 2, "segments": 1}


def test_backfill_past_the_counted_turns_is_dropped_and_the_first_later_record_clipped(tmp_path):
    """An adjudicated run ends at session.total_turns; savepoints after it are
    play the run is not scored on. gpt-6-astra(low): badge at 145, played to 224."""
    records = [[140, False, 4, 3, 1, []], [150, False, 5, 3, 2, []], [160, False, 9, 7, 2, []], [224, True, 16, 10, 6, []]]
    assert cap_records(records, 145) == [[140, False, 4, 3, 1, []], [145, False, 5, 3, 2, []]]
    assert cap_records(records, 150) == [[140, False, 4, 3, 1, []], [150, False, 5, 3, 2, []]]
    assert cap_records(records, 0) == records
    (tmp_path / "battle_backfill.json").write_text(json.dumps({"records": records}))
    (tmp_path / "state_backfill.json").write_text(json.dumps({"states": {str(t): 141 <= t <= 143 or t >= 155 for t in range(130, 225)}}))
    s, fid = battle_summary(tmp_path, None, 145)
    assert fid == "backfill" and s["battles_total"] == 5 and s["wild"]["count"] == 3
    assert s["turns_started_in_battle"] == 2  # turns 142-143: the exact bit at 140 overrides the classifier for 141; nothing after 145


def test_a_stamped_brock_gate_names_the_last_unknown_trainer_attempt():
    """gpt-6-astra(low): the badge was adjudicated at turn 145 but the game never
    set Brock's flag, so the tracker saw an unidentified lost attempt."""
    unknown = lambda: {"group": "unknown", "id": None, "name": "Unknown trainer", "attempts": 1, "turns": 5, "won": False, "mandatory": False}
    summary = {"available": True, "trainers": [
        {"group": "rival_oaks_lab", "id": 327, "name": "Rival (Oak's Lab)", "attempts": 1, "turns": 2, "won": True, "mandatory": True}, unknown()]}
    ref = {"gates": [{"id": "brock_defeated", "turn": 145}]}
    out = reconcile_with_gates(summary, ref)
    g = out["trainers"][1]
    assert (g["group"], g["id"], g["name"], g["won"], g["mandatory"], g["turns"], g["reconciled"]) == ("414", 414, "Leader Brock", True, True, 5, "brock_defeated gate")
    # Not stamped → untouched; Brock already identified → untouched.
    assert reconcile_with_gates({"available": True, "trainers": [unknown()]}, {"gates": [{"id": "brock_defeated", "turn": None}]})["trainers"][0]["group"] == "unknown"
    both = {"available": True, "trainers": [{"group": "414", "attempts": 1}, unknown()]}
    assert reconcile_with_gates(both, ref)["trainers"][1]["group"] == "unknown"


def test_ocr_hints_read_the_battle_intro_of_the_previous_turn(tmp_path):
    events = [
        {"type": "ocr_flush", "turn": 184, "cleaned": "Come on! Let's battle 'em!\nBUG CATCHER RICK would like to battle!\nGo! Squirtle!"},
        {"type": "ocr_flush", "turn": 197, "cleaned": "Wild CATERPIE appeared!"},
        {"type": "ocr_flush", "turn": 201, "cleaned": "BUG CATCHER RICK would like to battle! Wild PIDGEY appeared!"},
        {"type": "ocr_flush", "turn": 260, "cleaned": "LEADER BROCK would like to battle!"},
        {"type": "ocr_flush", "turn": 300, "cleaned": "nothing here"},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    h = ocr_hints(tmp_path)
    assert h == {183: {"kind": "trainer", "trainer_id": 102}, 196: {"kind": "wild", "trainer_id": None},
                 200: {"kind": "trainer", "trainer_id": 102}, 259: {"kind": "trainer", "trainer_id": 414}}
    assert ocr_hints(tmp_path, upto=200) == {k: v for k, v in h.items() if k <= 200}


def test_hints_decide_the_kind_when_segments_tie_and_a_long_fight_runs_past_the_window():
    """Window (190, 200]: a one-turn wild Caterpie at 196 and Rick's fight opening
    at 200 that runs to 204. Without hints both segments are one turn long in the
    window; the look-ahead and the OCR both say the 200 one is the trainer."""
    records = [[180, False, 0, 0, 0, []], [190, False, 0, 0, 0, []], [200, True, 2, 1, 1, []], [210, False, 2, 1, 1, [102]]]
    states = {t: t in (197, 201, 202, 203, 204) for t in range(180, 212)}
    for hints in (None, {196: {"kind": "wild", "trainer_id": None}, 200: {"kind": "trainer", "trainer_id": 102}}):
        recs = synthesize_records(records, states, hints)
        by = {r[0]: r for r in recs}
        assert by[196] == (196, True, 1, 1, 0, (), None), hints         # wild opened at 196
        assert by[200][4] == 1 and by[200][1] is True, hints        # trainer opened at 200
        t = BattleTracker()
        if hints:
            t.identity_hints = {200: 102}
        for r in recs:
            t.record(*r)
        seg, = [s for s in t.segments() if s["kind"] == "trainer"]
        assert (seg["opened_turn"], seg["turns"], seg["trainer_id"], seg["won"]) == (200, 4, 102, True), hints


REFEREE = {
    "gates": [{"id": "left_bedroom", "type": "map"}, {"id": "starter_chosen", "type": "flag"}, {"id": "route1_reached", "type": "map"},
              {"id": "viridian_reached", "type": "map"}],
    "progress": {"legs": [
        {"node_id": "left_bedroom", "status": "closed", "opened_turn": 0, "closed_turn": 3, "d_open": 9, "steps_walked": 9, "steps_source": "bound"},
        {"node_id": "starter_chosen", "status": "closed", "opened_turn": 3, "closed_turn": 8, "d_open": 1, "steps_walked": 6, "steps_source": "bound"},
        {"node_id": "route1_reached", "status": "closed", "opened_turn": 8, "closed_turn": 12, "d_open": 20, "steps_walked": 30, "steps_source": "trace"},
        {"node_id": "viridian_reached", "status": "open", "opened_turn": 12, "closed_turn": None, "d_open": 52, "distance_now": 40, "steps_walked": 30},
    ]},
}


def test_movement_uses_every_leg_with_a_path_and_credits_the_open_leg_with_ground_gained():
    """Every leg with a shortest path counts, the flag gate's included (option A,
    2026-09-14 night: until then only map legs did). The open leg opened 52 tiles
    out and ended 40 out after 30 steps: 12 gained ÷ 30 steps."""
    m = movement(REFEREE, None)
    assert m["shortest"] == 9 + 1 + 20 + 12 and m["steps"] == 9 + 6 + 30 + 30 and round(m["efficiency"], 3) == round(42 / 75, 3)
    assert [l["node_id"] for l in m["legs"]] == ["left_bedroom", "starter_chosen", "route1_reached", "viridian_reached"]
    assert m["legs"][-1] == {"node_id": "viridian_reached", "d_open": 12, "steps": 30, "walls": 0,
                             "source": "bound", "status": "open"}
    # No leg carried a walls_hit key: this run has no per-input trace, so the
    # charge is UNMEASURED, not zero (plan W7).
    assert m["walls"] is None and m["charged"] is None
    assert m["fidelity"] == "mixed"
    # Mutation control: the flag leg is the one that used to be dropped — without it the figure was 41/69.
    assert round(m["efficiency"], 3) != round(41 / 69, 3)
    # No net progress on the open leg → 0 credit, the steps still count.
    lost = {**REFEREE, "progress": {"legs": REFEREE["progress"]["legs"][:3] + [{**REFEREE["progress"]["legs"][3], "distance_now": 60}]}}
    m2 = movement(lost, None)
    assert m2["shortest"] == 30 and m2["steps"] == 45 + 30
    # An open leg whose distance is unknown is left out; so is any leg without a recorded path.
    blind = {**REFEREE, "progress": {"legs": REFEREE["progress"]["legs"][:3] + [{**REFEREE["progress"]["legs"][3], "distance_now": None}]}}
    assert [l["node_id"] for l in movement(blind, None)["legs"]] == ["left_bedroom", "starter_chosen", "route1_reached"]
    nopath = {**REFEREE, "progress": {"legs": [{**REFEREE["progress"]["legs"][1], "d_open": None}] + REFEREE["progress"]["legs"][2:3]}}
    assert [l["node_id"] for l in movement(nopath, None)["legs"]] == ["route1_reached"]


def test_movement_prefers_video_steps_when_the_leg_is_fully_covered():
    video = {t: 3 for t in range(1, 4)}  # covers turns 1-3 of the first leg only
    m = movement(REFEREE, video)
    first = m["legs"][0]
    assert (first["steps"], first["source"]) == (9, "video")  # 3 turns × 3 steps
    assert m["legs"][2]["source"] == "trace" and m["fidelity"] == "mixed"   # legs[1] is the starter (flag) leg, bound
    assert m["legs"][3]["source"] == "bound"                       # open leg: last_turn unknown → bound
    full = {t: 2 for t in range(1, 21)}                           # video covers turns 1-20; the run ended at 20
    m3 = movement(REFEREE, full, last_turn=20)
    assert m3["legs"][3] == {"node_id": "viridian_reached", "d_open": 12, "steps": 16, "walls": 0,
                             "source": "video", "status": "open"}  # turns 13-20 × 2
    assert movement({"gates": [], "progress": {"legs": []}}, None) is None


def test_load_steps_backfill_accepts_both_shapes(tmp_path):
    assert load_steps_backfill(tmp_path) is None
    (tmp_path / "steps_backfill.json").write_text(json.dumps({"turns": [{"turn": 1, "overworld_steps": 4}, {"turn": "x"}], "steps": {"2": 5}}))
    assert load_steps_backfill(tmp_path) == {1: 4, 2: 5}


def test_synthesis_puts_the_savepoint_opponent_id_on_the_turn_the_trainer_fight_opened():
    """Savepoint 20 reads opponent 102 (Rick) with the trainer counter up by one;
    the screenshots show the fight from turn 15 to 17. Turns before 14 keep the
    previous value (None), the opening turn 14 and everything after carry 102,
    so the tracker names the fight at its counter step — and the summary names
    Rick even though no flag was set (a LOST fight)."""
    records = [[10, False, 0, 0, 0, [], None], [20, False, 1, 0, 1, [], 102]]
    states = {t: t in (15, 16, 17) for t in range(1, 22)}
    recs = synthesize_records(records, states)
    by = {r[0]: r for r in recs}
    assert by[13][6] is None and by[14] == (14, True, 1, 0, 1, (), 102) and by[20][6] == 102
    t = BattleTracker()
    for r in recs:
        t.record(*r)
    g, = t.summary()["trainers"]
    assert (g["id"], g["attempts"], g["turns"], g["won"]) == (102, 1, 3, False)
    # Six-field records (pre-opponent backfills) still synthesize, with None.
    assert synthesize_records([[10, False, 0, 0, 0, []]], {})[-1][6] is None


def test_synthesis_leaves_the_first_of_two_fights_in_a_window_unnamed_rather_than_stale():
    """Window 20→30: two trainer fights (counter +2), the savepoint reads opponent 414
    (Brock, the last one). The earlier fight's opponent was never read, so its turns
    carry None — not 104 from the previous window — and a flag names it instead."""
    records = [[10, False, 0, 0, 0, [], None], [20, False, 1, 0, 1, [104], 104], [30, True, 3, 0, 3, [104, 142], 414]]
    states = {t: t in (15, 16, 23, 24, 27, 28, 29, 30, 31) for t in range(1, 33)}
    recs = synthesize_records(records, states)
    by = {r[0]: r for r in recs}
    assert by[14][6] == 104 and by[22][6] is None and by[26][6] == 414 and by[30][6] == 414
    t = BattleTracker()
    for r in recs:
        t.record(*r)
    segs = [x for x in t.segments() if x["kind"] == "trainer"]
    # Sammy named by the savepoint at 20; the first Pewter fight named by Liam's flag; Brock still open at the end.
    assert [(x["opened_turn"], x["trainer_id"], x["won"]) for x in segs] == [(14, 104, True), (22, 142, True), (26, 414, False)]


def test_a_wall_press_lowers_efficiency_by_exactly_one_step():
    """Mutation control for plan W4: the SAME leg, with and without the walls,
    and nothing else changed. Before this rule a bump cost the run nothing."""
    def leg(walls):
        return {"node_id": "route1_reached", "status": "closed", "scored": True,
                "d_open": 20, "d_min": 0, "opened_turn": 1, "closed_turn": 9,
                "steps_walked": 30, "walls_hit": walls, "steps_source": "trace"}
    free = movement({"progress": {"legs": [leg(0)]}}, None)
    charged = movement({"progress": {"legs": [leg(10)]}}, None)
    assert free["steps"] == charged["steps"] == 30        # same ground walked
    assert free["charged"] == 30 and charged["charged"] == 40
    assert free["efficiency"] == 20 / 30
    assert charged["efficiency"] == 20 / 40
    assert charged["legs"][0]["walls"] == 10
