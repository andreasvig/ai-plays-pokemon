"""projection.py reads the ProgressTracker summary out of run_summary.json["referee"]["progress"]."""

from __future__ import annotations

import json
from pathlib import Path

from src.app.projection import project_run_dir


def _run_dir(tmp_path: Path, referee: dict) -> Path:
    run = tmp_path / "2026-09-09_10-00-00_config-5.0__test-model-high"
    run.mkdir()
    (run / "run_summary.json").write_text(json.dumps({
        "run_id": run.name, "kind": "official", "status": "terminated",
        "session": {"llm_alias": "test-model(high)", "total_turns": 100, "duration_seconds": 10.0, "started_at": "2026-09-09T10:00:00"},
        "cost": {"total_usd": 0.1},
        "referee": referee,
    }))
    return run


_GATES = [
    {"kind": "single", "id": "left_bedroom", "name": "Left the bedroom", "type": "map", "deadline_turn": 20, "turn": 1, "status": "done"},
    {"kind": "single", "id": "left_house", "name": "Stepped outside", "type": "map", "deadline_turn": 30, "turn": 4, "status": "done"},
    {"kind": "single", "id": "oaks_lab_entered", "name": "Entered Oak's Lab", "type": "map", "deadline_turn": 40, "turn": None, "status": "pending"},
]


def test_projection_reads_progress_and_current_leg(tmp_path):
    run = _run_dir(tmp_path, {
        "gates": _GATES, "furthest": "left_house", "first_unmet": "oaks_lab_entered", "termination_reason": "missed_gate:oaks_lab_entered",
        "progress": {"progress": 2.625, "current_leg": {"node_id": "oaks_lab_entered", "name": "Entered Oak's Lab",
                                                        "d_min": 6, "d_open": 16, "fraction": 0.625, "steps_walked": 30,
                                                        "tiles_seen": 22, "off_graph": 0, "distance_now": 9},
                     "legs": [], "graph": {"loaded": True, "source": "pret"}, "positions_recorded": 40},
    })
    s = project_run_dir(run)
    assert s.gates_reached == 2 and s.progress == 2.625 and s.rank_score == 2.625
    assert s.leg_gate == "oaks_lab_entered" and s.leg_fraction == 0.625
    assert s.leg_distance_min == 6 and s.leg_distance_open == 16


def test_projection_without_progress_block_ranks_on_gates(tmp_path):
    s = project_run_dir(_run_dir(tmp_path, {"gates": _GATES, "furthest": "left_house", "termination_reason": None}))
    assert s.progress is None and s.leg_gate is None and s.rank_score == 2.0


def test_projection_tolerates_a_malformed_progress_block(tmp_path):
    s = project_run_dir(_run_dir(tmp_path, {"gates": _GATES, "furthest": "left_house",
                                             "progress": {"progress": "lots", "current_leg": {"node_id": 7, "fraction": None, "d_min": True}}}))
    assert s.progress is None and s.leg_gate is None and s.leg_distance_min is None and s.rank_score == 2.0


def test_projection_carries_the_crash_error_and_record(tmp_path):
    run = tmp_path / "2026-09-09_14-30-46_config-5.0__gemini-3-8-flash-minimal"
    run.mkdir()
    crash = {"turn": 44, "last_settled_turn": 43, "phase": "emulator", "error_type": "RuntimeError",
             "message": "Action outcome uncertain; resume from the last complete savepoint",
             "cause": "RuntimeError: Unexpected screenshot response: SEQUENCE_DONE", "where": []}
    (run / "run_summary.json").write_text(json.dumps({
        "run_id": run.name, "kind": "official", "status": "crashed",
        "error": "RuntimeError: Action outcome uncertain ← RuntimeError: Unexpected screenshot response: SEQUENCE_DONE",
        "crash": crash,
        "session": {"llm_alias": "gemini-3.8-flash(minimal)", "total_turns": 43, "duration_seconds": 10.0, "started_at": "2026-09-09T14:30:46"},
        "cost": {"total_usd": 0.1}, "referee": {"gates": _GATES, "furthest": "left_house"},
    }))
    s = project_run_dir(run)
    assert s.status.value == "crashed"
    assert s.error.startswith("RuntimeError: Action outcome uncertain")
    assert s.crash == crash
    # a clean run has neither
    clean = project_run_dir(_run_dir(tmp_path, {"gates": _GATES, "furthest": "left_house", "termination_reason": None}))
    assert clean.error is None and clean.crash is None
