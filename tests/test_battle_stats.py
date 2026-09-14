"""Board-side battle and movement figures (src/app/battle_stats.py)."""

from __future__ import annotations

import json

from src.app.battle_stats import battle_summary, load_steps_backfill, movement, synthesize_records
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
    assert by_turn[12][2] == 0 and by_turn[13] == (13, True, 1, 1, 0, ()) and by_turn[16][1] is False
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
    assert recs[9] == (10, True, 1, 1, 0, ()) and recs[10][1] is True and recs[11][1] is False
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


REFEREE = {
    "gates": [{"id": "left_bedroom", "type": "map"}, {"id": "starter_chosen", "type": "flag"}, {"id": "route1_reached", "type": "map"}],
    "progress": {"legs": [
        {"node_id": "left_bedroom", "status": "closed", "opened_turn": 0, "closed_turn": 3, "d_open": 9, "steps_walked": 9, "steps_source": "bound"},
        {"node_id": "starter_chosen", "status": "closed", "opened_turn": 3, "closed_turn": 8, "d_open": 1, "steps_walked": 6, "steps_source": "bound"},
        {"node_id": "route1_reached", "status": "closed", "opened_turn": 8, "closed_turn": 12, "d_open": 20, "steps_walked": 30, "steps_source": "trace"},
        {"node_id": "viridian_reached", "status": "open", "opened_turn": 12, "closed_turn": None, "d_open": 52, "steps_walked": 5},
    ]},
}


def test_movement_uses_closed_map_legs_only_and_reports_the_source_mix():
    m = movement(REFEREE, None)
    assert m["shortest"] == 29 and m["steps"] == 39 and round(m["efficiency"], 3) == round(29 / 39, 3)
    assert [l["node_id"] for l in m["legs"]] == ["left_bedroom", "route1_reached"]  # flag gate and open leg excluded
    assert m["fidelity"] == "mixed"


def test_movement_prefers_video_steps_when_the_leg_is_fully_covered():
    video = {t: 3 for t in range(1, 4)}  # covers turns 1-3 of the first leg only
    m = movement(REFEREE, video)
    first = m["legs"][0]
    assert (first["steps"], first["source"]) == (9, "video")  # 3 turns × 3 steps
    assert m["legs"][1]["source"] == "trace" and m["fidelity"] == "mixed"
    assert movement({"gates": [], "progress": {"legs": []}}, None) is None


def test_load_steps_backfill_accepts_both_shapes(tmp_path):
    assert load_steps_backfill(tmp_path) is None
    (tmp_path / "steps_backfill.json").write_text(json.dumps({"turns": [{"turn": 1, "overworld_steps": 4}, {"turn": "x"}], "steps": {"2": 5}}))
    assert load_steps_backfill(tmp_path) == {1: 4, 2: 5}
