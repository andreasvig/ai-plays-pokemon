"""Regression: a continued run's run_summary must be CUMULATIVE across segments.

The per-turn cost/time on the History row is `total ÷ turns`. If `total_turns` is
cumulative (the global counter is restored) but `duration_seconds`/`total_usd` are
only this segment, the per-turn figures drift badly (Andreas 2026-06-18, observed on
pre-fix runs: 58 turns but only 129s → 2.2 s/t vs a fresh run's 18.7 s/t).

This pins the contract end-to-end: restore_run_accounting(source_summary) followed by
finalize_run_summary() yields duration/cost/tokens that INCLUDE the source segment, so
numerator and denominator are both cumulative and the per-turn averages stay honest.
"""

import json
import time
from pathlib import Path

from src.agent.turn import TurnManager


class _Logger:
    def __init__(self, run_dir):
        self.run_dir = run_dir


def _manager(run_dir):
    m = TurnManager.__new__(TurnManager)
    # accounting + finalize fields
    m._run_start_time = time.time()      # this segment starts ~now (≈0s of its own)
    m._prior_duration_s = 0.0
    m.config = {"_llm_alias": None, "llm_model": "m", "thinking": None, "task": {"goal": "g"}}
    m.fallback_models = []
    m.tasks = None
    m.turn_number = 0
    m.task_master_turns = 0
    m.total_cost_usd = 0.0
    m.task_master_cost_usd = 0.0
    m.ocr = None
    m.total_input_tokens = 0
    m.total_output_tokens = 0
    m.turn_costs = []
    m.turn_explanations = []
    m._explanation_turns = []
    m.referee = None
    m.logger = _Logger(run_dir)
    return m


def test_finalize_is_cumulative_after_restore(tmp_path):
    m = _manager(tmp_path)

    # Source segment: reached turn 52, ran 500s, spent $0.40 LLM + $0.03 OCR +
    # $0.11 TaskMaster, 100k/8k tokens, 2 TM turns.
    m.restore_run_accounting({
        "session": {"duration_seconds": 500.0, "task_master_turns": 2},
        "cost": {"llm_usd": 0.40, "ocr_usd": 0.03, "task_master_usd": 0.11,
                 "total_input_tokens": 100_000, "total_output_tokens": 8_000},
    })

    # This segment then runs to a cumulative turn 58 and adds a little more spend
    # (the live loop accumulates onto the seeded baseline).
    m.turn_number = 58
    m.total_cost_usd += 0.05      # 0.43 seeded + 0.05 this segment
    m.task_master_cost_usd += 0.02
    m.total_input_tokens += 10_000
    m.task_master_turns += 1

    m._write_run_summary(status=None)   # the body finalize_run_summary guards
    s = json.loads((Path(tmp_path) / "run_summary.json").read_text())

    # Turns: cumulative global counter + TM turns (52→58 player, 2+1 TM).
    assert s["session"]["player_turns"] == 58
    assert s["session"]["total_turns"] == 58 + 3
    # Duration INCLUDES the source segment's 500s (plus this segment's ~0s).
    assert s["session"]["duration_seconds"] >= 500.0
    assert s["session"]["duration_seconds"] < 540.0   # not double-counted
    # Cost INCLUDES the seeded baseline: (0.43 + 0.05) player+ocr + (0.11+0.02) TM.
    assert round(s["cost"]["total_usd"], 6) == round(0.48 + 0.13, 6)
    assert s["cost"]["total_input_tokens"] == 110_000

    # The per-turn figure the History row shows is now cumulative/cumulative.
    s_per_turn = s["session"]["duration_seconds"] / s["session"]["total_turns"]
    assert 8.0 < s_per_turn < 9.0   # ~500s / 61 turns — honest, not the 2.2 drift


def test_segment_ledger_records_this_segment(tmp_path):
    # P6: a continued run is transparently multi-segment.
    m = _manager(tmp_path)
    m.config["_continued_from"] = "2026-06-18_src_run"
    m.config["_continued_from_turn"] = 52
    m.restore_run_accounting({"session": {"duration_seconds": 500.0}, "cost": {}})
    m.turn_number = 58

    m._write_run_summary(status=None)
    seg = json.loads((Path(tmp_path) / "run_summary.json").read_text())["session"]

    assert seg["resumed"] is True
    assert seg["segment"]["continued_from"] == "2026-06-18_src_run"
    assert seg["segment"]["resumed_at_turn"] == 52
    assert seg["segment"]["prior_duration_s"] == 500.0
    assert seg["segment"]["segment_player_turns"] == 6     # 58 - 52, this segment only
    assert seg["segment"]["segment_duration_s"] < 540.0    # this segment's own clock


def test_fresh_run_is_not_resumed(tmp_path):
    m = _manager(tmp_path)
    m.turn_number = 30
    m._write_run_summary(status=None)
    sess = json.loads((Path(tmp_path) / "run_summary.json").read_text())["session"]
    assert sess["resumed"] is False
    assert sess["segment"]["continued_from"] is None
    assert sess["segment"]["segment_player_turns"] == 30


def test_projection_surfaces_resumed(tmp_path):
    from src.app.projection import project_run_dir

    # Explicit flag from the writer.
    d1 = tmp_path / "2026-06-18_cfg__m"
    d1.mkdir()
    (d1 / "run_summary.json").write_text(json.dumps(
        {"session": {"resumed": True}, "cost": {}, "kind": "casual"}))
    assert project_run_dir(d1).resumed is True

    # Legacy run: no session.resumed, but a continued_from link → inferred True.
    d2 = tmp_path / "2026-06-18_cfg__m2"
    d2.mkdir()
    (d2 / "run_summary.json").write_text(json.dumps(
        {"session": {}, "cost": {}, "kind": "casual", "continued_from": "src"}))
    assert project_run_dir(d2).resumed is True

    # Fresh run → False.
    d3 = tmp_path / "2026-06-18_cfg__m3"
    d3.mkdir()
    (d3 / "run_summary.json").write_text(json.dumps(
        {"session": {}, "cost": {}, "kind": "casual"}))
    assert project_run_dir(d3).resumed is False
