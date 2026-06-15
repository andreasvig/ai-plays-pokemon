"""Tests for the pure leaderboard + history derivations (Plan §P1).

Assert ORDERING / membership invariants, never tuned numbers: leaderboard is
sorted gates-desc then turns-asc, one row per model = its best, casual + continue
+ cancelled-official excluded.
"""

from __future__ import annotations

import pytest

from src.app.derivations import history, leaderboard
from src.app.models import RunKind, RunStatus, RunSummary


def _run(run_id, model, *, kind=RunKind.official, status=RunStatus.completed,
         gates=0, turns=100, cost=1.0, duration=100.0, started="2026-06-01",
         continued_from=None) -> RunSummary:
    return RunSummary(
        run_id=run_id, model=model, kind=kind, status=status,
        gates_reached=gates, total_gates=20, turns=turns, total_cost_usd=cost,
        duration_s=duration, started_at=started, continued_from=continued_from,
    )


# --- leaderboard --------------------------------------------------------------

def test_leaderboard_excludes_casual_continue_cancelled():
    rows = [
        _run("r1", "model-a", gates=8),                                  # eligible
        _run("r2", "model-b", kind=RunKind.casual,
             status=RunStatus.completed, gates=20),                      # casual → out
        _run("r3", "model-c", kind=RunKind.casual,
             status=RunStatus.completed, gates=15,
             continued_from="r1"),                                       # continue → out
        _run("r4", "model-d", kind=RunKind.official,
             status=RunStatus.cancelled, gates=19),                      # voided → out
    ]
    lb = leaderboard(rows)
    assert [r.run_id for r in lb] == ["r1"]
    assert all(r.leaderboard_eligible for r in lb)


def test_leaderboard_terminated_official_is_eligible():
    rows = [_run("r1", "m", status=RunStatus.terminated, gates=11)]
    assert [r.run_id for r in leaderboard(rows)] == ["r1"]


def test_leaderboard_best_per_model_then_ordered():
    rows = [
        _run("a-weak", "model-a", gates=5, turns=100),
        _run("a-strong", "model-a", gates=8, turns=300),   # best for A (more gates)
        _run("b-only", "model-b", gates=11, turns=900),    # best for B
    ]
    lb = leaderboard(rows)
    # one row per model
    assert {r.model for r in lb} == {"model-a", "model-b"}
    # best-per-model picked
    by_model = {r.model: r for r in lb}
    assert by_model["model-a"].run_id == "a-strong"
    # ordered gates desc: B (11) before A (8)
    assert [r.run_id for r in lb] == ["b-only", "a-strong"]
    # monotonic non-increasing gates
    g = [r.gates_reached for r in lb]
    assert g == sorted(g, reverse=True)


def test_leaderboard_tiebreak_fewest_turns():
    rows = [
        _run("slow", "model-a", gates=10, turns=500),
        _run("fast", "model-a", gates=10, turns=200),   # same gates, fewer turns → best
    ]
    lb = leaderboard(rows)
    assert len(lb) == 1
    assert lb[0].run_id == "fast"


def test_leaderboard_full_clear_ordering_by_turns():
    """Two full-clears (same gates) compare head-to-head by fewest turns."""
    rows = [
        _run("clear-slow", "model-a", gates=20, turns=900),
        _run("clear-fast", "model-b", gates=20, turns=600),
    ]
    lb = leaderboard(rows)
    assert [r.run_id for r in lb] == ["clear-fast", "clear-slow"]


def test_leaderboard_empty():
    assert leaderboard([]) == []
    assert leaderboard([_run("c", "m", kind=RunKind.casual)]) == []


# --- history ------------------------------------------------------------------

def _mixed():
    return [
        _run("r1", "claude-a", kind=RunKind.official, status=RunStatus.completed,
             gates=8, cost=5.0, duration=300.0, started="2026-06-01"),
        _run("r2", "gemini-b", kind=RunKind.casual, status=RunStatus.completed,
             gates=3, cost=1.0, duration=100.0, started="2026-06-03"),
        _run("r3", "claude-a", kind=RunKind.official, status=RunStatus.terminated,
             gates=11, cost=9.0, duration=500.0, started="2026-06-02"),
    ]


def test_history_filter_by_kind():
    rows = history(_mixed(), kind=RunKind.casual)
    assert {r.run_id for r in rows} == {"r2"}


def test_history_filter_by_status():
    rows = history(_mixed(), status=RunStatus.terminated)
    assert {r.run_id for r in rows} == {"r3"}


def test_history_search_matches_model_and_run_id():
    assert {r.run_id for r in history(_mixed(), q="claude")} == {"r1", "r3"}
    assert {r.run_id for r in history(_mixed(), q="R2")} == {"r2"}  # case-insensitive


def test_history_sort_recent_desc_default():
    rows = history(_mixed())
    assert [r.started_at for r in rows] == sorted(
        [r.started_at for r in rows], reverse=True
    )
    assert rows[0].run_id == "r2"  # latest started_at


def test_history_sort_cost_asc():
    rows = history(_mixed(), sort="cost", order="asc")
    costs = [r.total_cost_usd for r in rows]
    assert costs == sorted(costs)


def test_history_sort_completion_and_duration():
    by_completion = history(_mixed(), sort="completion", order="desc")
    assert by_completion[0].run_id == "r3"  # most gates
    by_duration = history(_mixed(), sort="duration", order="asc")
    durs = [r.duration_s for r in by_duration]
    assert durs == sorted(durs)


def test_history_no_filters_returns_all():
    assert len(history(_mixed())) == 3
