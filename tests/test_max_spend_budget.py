"""Tests for the casual spend ceiling — the third stop condition.

A casual run can be bounded three ways, and whichever lands first ends it: the
turn cap (``max_turns``), the story event (``stop_at``), and now an all-in USD
budget (``max_spend_usd``). Four layers, one file, mirroring
``test_stop_at_event.py``:

* the predicate — what counts toward the bill, and the segment baseline that
  keeps a continue from inheriting spend it did not make;
* config validation — a budget you asked for is never silently dropped;
* the wiring — executor (queue) and the enqueue/continue API;
* the turn loop — the run actually stops, reports ``completed``, and says why.

Neither mGBA nor the network is touched: the loop tests reuse the scripted stub
harness from test_taskmaster_loop and charge a fixed cost per turn the same way
the real ``_run_turn`` does (``self.total_cost_usd += turn_cost``).
"""

from __future__ import annotations

import json

import pytest

from src.agent.turn import TurnManager
from src.config import _validate_config as validate_config

# --- the predicate ------------------------------------------------------------


def _mgr_shell(**cfg):
    """A TurnManager constructed for its counters only — no setup(), no emulator.

    ``__init__`` reads the budget off the config and zeroes the cost counters,
    which is everything ``_budget_exhausted`` touches.
    """
    return TurnManager({
        "max_steps_per_turn": 4,
        "llm_model": "stub/player-model",
        "openrouter_api_key": "test-key-not-used",
        **cfg,
    })


def test_no_ceiling_is_unbounded():
    """The default. A run with no budget must never stop on spend, however
    much it has cost — that is every casual run that existed before this."""
    m = _mgr_shell()
    assert m.max_spend_usd is None
    m.total_cost_usd = 9_999.0
    assert m._budget_exhausted() is False


def test_ceiling_is_read_off_the_config():
    assert _mgr_shell(max_spend_usd=2.5).max_spend_usd == 2.5


def test_under_the_ceiling_keeps_going():
    m = _mgr_shell(max_spend_usd=1.0)
    m.total_cost_usd = 0.99
    assert m._budget_exhausted() is False


def test_exactly_at_the_ceiling_stops():
    """Inclusive: at the cap the budget is gone. The alternative (`>`) lets a
    run that has spent its last cent start one more paid turn."""
    m = _mgr_shell(max_spend_usd=1.0)
    m.total_cost_usd = 1.0
    assert m._budget_exhausted() is True


def test_the_taskmaster_share_counts_toward_the_bill():
    """The Player's cost lands in ``total_cost_usd``; the TaskMaster's is
    accumulated separately (Decision 10). A budget that read only the first
    would under-report a 3.x run by the whole strategy layer."""
    m = _mgr_shell(max_spend_usd=1.0)
    m.total_cost_usd = 0.6
    m.task_master_cost_usd = 0.5
    assert m._all_in_spend_usd() == pytest.approx(1.1)
    assert m._budget_exhausted() is True


def test_the_taskmaster_share_alone_can_exhaust_it():
    """Mutation control for the test above: if `_all_in_spend_usd` dropped the
    TaskMaster term, this is the case that would survive silently — the Player
    has spent nothing at all."""
    m = _mgr_shell(max_spend_usd=1.0)
    m.task_master_cost_usd = 1.5
    assert m._budget_exhausted() is True


def test_the_budget_is_segment_relative():
    """A continue seeds ``total_cost_usd`` from the source run so the reported
    cost is cumulative. The budget bounds the segment you launched — otherwise
    a $2 continue of a run that already spent $5 would end before turn one."""
    m = _mgr_shell(max_spend_usd=2.0)
    m.total_cost_usd = 5.0            # inherited from the source run
    m._spend_baseline_usd = 5.0       # what _run_loop_async captures
    assert m._budget_exhausted() is False
    m.total_cost_usd = 7.0            # this segment has now spent its $2
    assert m._budget_exhausted() is True


# --- config validation --------------------------------------------------------


def _cfg(**kw):
    return {
        "task": {"goal": "g"},
        "emulator": {"host": "h", "port": 1, "rom_path": "r"},
        "valid_inputs": ["a"],
        "state_file": "s",
        **kw,
    }


@pytest.mark.parametrize("good", [0.01, 0.5, 2, 100.0])
def test_validate_accepts_a_positive_budget(good):
    validate_config(_cfg(max_spend_usd=good), require_llm_model=False)


def test_validate_accepts_an_absent_budget():
    validate_config(_cfg(), require_llm_model=False)


@pytest.mark.parametrize("bad", [0, -1, -0.5, "2.00", True, [2]])
def test_validate_rejects_a_nonsense_budget(bad):
    """Rejected, never coerced. A budget that was silently dropped is only
    discovered by the bill — and ``True`` is an int in Python, so a stray
    boolean would otherwise read as a $1 cap."""
    with pytest.raises(ValueError, match="max_spend_usd"):
        validate_config(_cfg(max_spend_usd=bad), require_llm_model=False)


# --- executor wiring ----------------------------------------------------------


def _item(**kw):
    from src.app.models import QueuedRun, RunKind

    return QueuedRun(
        queue_id="q_test", kind=kw.pop("kind", RunKind.casual),
        model="claude-haiku-4.5(medium)", enqueued_at="2026-08-01T00:00:00Z", **kw,
    )


def _apply(cfg, max_spend_usd):
    from src.app.executor import RunExecutor

    RunExecutor._apply_max_spend(cfg, max_spend_usd)
    return cfg


def test_executor_stamps_the_ceiling_on_a_casual_run():
    assert _apply({}, 1.25)["max_spend_usd"] == 1.25


def test_executor_leaves_an_uncapped_run_without_the_key():
    """Absent, not None — the same reasoning as the `referee` key: a present
    None is falsy today but is a trap for any reader that does arithmetic on
    it. TurnManager's `config.get("max_spend_usd")` reads absent as unbounded."""
    cfg = _apply({"llm_model": "x"}, None)
    assert "max_spend_usd" not in cfg


def test_queued_run_defaults_to_no_ceiling():
    assert _item().max_spend_usd is None


def test_queue_manager_round_trips_the_ceiling(tmp_path):
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager

    q = QueueManager(tmp_path / "queue.json")
    q.enqueue(RunKind.casual, "claude-haiku-4.5(medium)", max_spend_usd=3.5)
    # Reloaded from disk, not the in-memory item — the drain reads the file.
    assert QueueManager(tmp_path / "queue.json").items[0].max_spend_usd == 3.5


# --- the API ------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    import types

    from fastapi.testclient import TestClient

    from src.app.queue_manager import QueueManager
    from src.dashboard import server

    server.configure_control_plane(
        queue_manager=QueueManager(tmp_path / "queue.json"),
        executor=types.SimpleNamespace(runs_root=tmp_path / "runs"),
        run_index=types.SimpleNamespace(all=lambda: [], get=lambda rid: None),
    )
    yield TestClient(server.app)
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None


def test_enqueue_accepts_a_budget(api):
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)",
        "config": "config-3.13", "max_turns": 100, "max_spend_usd": 2.5,
    })
    assert r.status_code == 201
    assert r.json()["max_spend_usd"] == 2.5


@pytest.mark.parametrize("bad", [0, -1, "2.00", True])
def test_enqueue_rejects_a_nonsense_budget(api, bad):
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)", "max_spend_usd": bad,
    })
    assert r.status_code == 400
    assert "max_spend_usd" in r.json()["detail"]


def test_enqueue_without_a_budget_is_unchanged(api):
    r = api.post("/api/queue", json={"kind": "casual", "model": "claude-haiku-4.5(medium)"})
    assert r.status_code == 201
    assert r.json()["max_spend_usd"] is None


def test_official_ignores_a_budget(api):
    """Pace is an official run's only bound (locked #8). Honouring a budget
    would produce a short benchmark run that still looked comparable."""
    r = api.post("/api/queue", json={
        "kind": "official", "model": "claude-haiku-4.5(medium)", "max_spend_usd": 0.05,
    })
    assert r.status_code == 201
    assert r.json()["max_spend_usd"] is None


def test_a_bad_budget_rejects_the_whole_batch(api):
    r = api.post("/api/queue/batch", json={"items": [
        {"kind": "casual", "model": "claude-haiku-4.5(medium)", "max_spend_usd": 1.0},
        {"kind": "casual", "model": "claude-haiku-4.5(medium)", "max_spend_usd": -3},
    ]})
    assert r.status_code == 400
    assert api.get("/api/queue").json()["items"] == []   # nothing half-enqueued


# --- the turn loop ------------------------------------------------------------


def _spending_mgr(cfg, n_turns, per_turn_usd):
    """The scripted harness, charging ``per_turn_usd`` per completed turn.

    Stands in for the real ``_run_turn``, which adds that turn's OpenRouter cost
    to ``total_cost_usd`` after the model answers.
    """
    from tests.test_taskmaster_loop import ScriptedPlayerTurnManager, _ga
    from src.core.logger import RunLogger
    from tests.test_taskmaster_loop import _StubEmulator, _StubState, _StubVision

    class _Spending(ScriptedPlayerTurnManager):
        async def _run_turn(self):
            out = await super()._run_turn()
            if out is not None:
                self.total_cost_usd += per_turn_usd
            return out

    logger = RunLogger(cfg)
    mgr = _Spending(cfg, [_ga(f"t{i}") for i in range(1, n_turns + 1)], None)
    mgr.setup(_StubEmulator(), _StubState(), _StubVision(), logger, None)
    return mgr, logger


def test_loop_stops_when_the_budget_runs_out(tmp_path):
    """The composition: the ceiling ends the run BEFORE its turn budget, the
    summary says `completed` (the run did what it was asked to), and it records
    which of the three conditions fired.

    $0.10/turn against a $0.25 cap: turns 1-3 start under the cap, and turn 4
    never starts because $0.30 >= $0.25. The overshoot is by design — a turn's
    cost is only known once it has been paid.
    """
    from tests.test_taskmaster_loop import _base_config

    cfg = _base_config(tmp_path, enabled=False)
    cfg["max_spend_usd"] = 0.25
    mgr, logger = _spending_mgr(cfg, 4, 0.10)

    mgr.run_loop(max_turns=4)

    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["session"]["player_turns"] == 3        # not 4
    # No explicit status — exactly like a casual run that reaches its turn cap.
    # The writer/projection infers `completed` from the absence of an error and
    # of a referee termination, which is the truth: the run finished as asked.
    assert "status" not in summary
    assert "error" not in summary
    assert summary["stop_reason"] == "max_spend"
    assert summary["max_spend_usd"] == 0.25
    assert summary["cost"]["total_usd"] == pytest.approx(0.30)


def test_loop_without_a_budget_uses_the_whole_turn_cap(tmp_path):
    """The control. Same harness and the same per-turn spend, no ceiling — the
    run must play all four turns, or the test above proves only that the loop
    breaks somewhere."""
    from tests.test_taskmaster_loop import _base_config

    cfg = _base_config(tmp_path, enabled=False)
    mgr, logger = _spending_mgr(cfg, 4, 0.10)

    mgr.run_loop(max_turns=4)

    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["session"]["player_turns"] == 4
    assert "stop_reason" not in summary


def test_a_ceiling_it_never_reaches_changes_nothing(tmp_path):
    """The second control: the budget is set but generous. Distinguishes "the
    cap fired" from "the presence of a cap shortens the run"."""
    from tests.test_taskmaster_loop import _base_config

    cfg = _base_config(tmp_path, enabled=False)
    cfg["max_spend_usd"] = 100.0
    mgr, logger = _spending_mgr(cfg, 4, 0.10)

    mgr.run_loop(max_turns=4)

    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["session"]["player_turns"] == 4
    assert "stop_reason" not in summary


def test_the_budget_stop_is_logged_as_an_event(tmp_path):
    """The run report reads events.jsonl, not the console — an ending nobody
    can explain from the log is the thing this whole field is for."""
    from tests.test_taskmaster_loop import _base_config, _read_events

    cfg = _base_config(tmp_path, enabled=False)
    cfg["max_spend_usd"] = 0.25
    mgr, logger = _spending_mgr(cfg, 4, 0.10)

    mgr.run_loop(max_turns=4)

    ev = [e for e in _read_events(logger.run_dir) if e.get("type") == "budget_exhausted"]
    assert len(ev) == 1
    assert ev[0]["max_spend_usd"] == 0.25
    assert ev[0]["spent_usd"] == pytest.approx(0.30)


def test_a_continued_run_spends_its_own_budget(tmp_path):
    """A continue whose source run already blew past the ceiling still gets its
    full segment. Without the baseline this run would stop at turn 1."""
    from tests.test_taskmaster_loop import _base_config

    cfg = _base_config(tmp_path, enabled=False)
    cfg["max_spend_usd"] = 0.25
    mgr, logger = _spending_mgr(cfg, 4, 0.10)
    mgr.total_cost_usd = 12.0          # inherited from the source run

    mgr.run_loop(max_turns=4)

    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["session"]["player_turns"] == 3
    assert summary["cost"]["total_usd"] == pytest.approx(12.30)
