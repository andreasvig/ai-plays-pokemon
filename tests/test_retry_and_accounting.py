"""Regression tests for the 2026-06-17 reliability + accounting rework.

Three independent fixes, one file:

* Phase 1 — retry/backoff/provider-routing helpers (pure functions):
  per-attempt provider sort escalates throughput → latency → OpenRouter
  default, the slow-model timeout doubling, and jittered exponential backoff.
* Phase 5 — gate off-by-one: a deadline gate that falls due on a TaskMaster
  HANDOFF turn must terminate AT that turn, not one turn later. Before the fix
  the handoff path `continue`d past the referee poll, so the deadline was
  evaluated a turn late. NB (Andreas 2026-06-17): deadlines are now measured
  against TOTAL turns (player + TaskMaster), so the poll values below are the
  running player+master sum, not the bare player-turn index.
* Phase 6 — TaskMaster turns + cost: each TaskMaster invocation counts as +1
  reported turn and its cost is attached to a per-turn entry (it was previously
  invisible in any per-turn view).

The loop tests reuse the stub Player / stub TaskMaster harness from
test_taskmaster_loop (no mGBA / no network).
"""

import json
from pathlib import Path

# Importing the sibling test module sets OPENROUTER_API_KEY + sys.path and gives
# us the stub harness (ScriptedPlayerTurnManager, StubTaskMasterRunner, etc.).
from tests.test_taskmaster_loop import (
    StubTaskMasterRunner,
    _base_config,
    _ga,
    _inv,
    _make_mgr,
)

from src.agent.agent import ReturnToTaskMaster
from src.agent.task_master import Rating
import asyncio

from src.agent.turn import (
    _LLM_CALL_TIMEOUTS_S,
    _RETRY_BACKOFF_BASE_S,
    _RETRY_BACKOFF_CAP_S,
    _RETRY_BACKOFF_FACTOR,
    _SLOW_MODEL_TIMEOUT_MULT,
    _is_taskmaster_retryable,
    _is_transient_llm_error,
    _provider_routing_for_attempt,
    _retry_backoff_s,
    _settings_for_attempt,
)


# --- Phase 1: retry / backoff / provider-routing helpers ---------------------


def test_provider_routing_escalates_throughput_latency_then_default():
    # No base provider block: attempt 1 throughput, attempt 2 latency, 3+ none.
    assert _provider_routing_for_attempt(0, None) == {"sort": "throughput"}
    assert _provider_routing_for_attempt(1, None) == {"sort": "latency"}
    assert _provider_routing_for_attempt(2, None) is None
    assert _provider_routing_for_attempt(5, None) is None


def test_provider_routing_preserves_registry_base_block():
    # A model's own provider block (e.g. an allowlist) is kept as the base; only
    # `sort` is overridden on the early attempts. Attempt 3+ falls back to the
    # registry block as-is (here {sort: throughput}).
    base = {"order": ["groq"], "sort": "throughput"}
    assert _provider_routing_for_attempt(0, base) == {"order": ["groq"], "sort": "throughput"}
    assert _provider_routing_for_attempt(1, base) == {"order": ["groq"], "sort": "latency"}
    assert _provider_routing_for_attempt(2, base) == {"order": ["groq"], "sort": "throughput"}
    # The base dict is not mutated by the helper.
    assert base == {"order": ["groq"], "sort": "throughput"}


def test_settings_for_attempt_swaps_provider_without_touching_reasoning():
    base = {"temperature": 0.3, "extra_body": {"reasoning": {"effort": "high"}}}
    out = _settings_for_attempt(base, {"sort": "throughput"})
    assert out["temperature"] == 0.3
    assert out["extra_body"]["reasoning"] == {"effort": "high"}
    assert out["extra_body"]["provider"] == {"sort": "throughput"}
    # Original is untouched (per-attempt cloning, not mutation).
    assert "provider" not in base["extra_body"]


def test_settings_for_attempt_drops_provider_when_none():
    base = {"extra_body": {"reasoning": {"effort": "low"}, "provider": {"sort": "latency"}}}
    out = _settings_for_attempt(base, None)
    assert "provider" not in out["extra_body"]
    assert out["extra_body"]["reasoning"] == {"effort": "low"}


def test_settings_for_attempt_none_base_returns_none_when_empty():
    assert _settings_for_attempt(None, None) is None
    assert _settings_for_attempt(None, {"sort": "throughput"}) == {
        "extra_body": {"provider": {"sort": "throughput"}}
    }


def test_transient_classifier_covers_timeout_jsondecode_and_wrapped_5xx():
    # Timeouts re-roll (verified live on the same run that surfaced the bug).
    assert _is_transient_llm_error(asyncio.TimeoutError()) is True
    # A malformed/truncated provider body raises JSONDecodeError mid-call. This
    # is the EXACT failure that killed a gpt-5.5 run at T31 (it was non-transient
    # and skipped the re-roll). Both the real shape and the name-match path.
    real = None
    try:
        json.loads("not json\nline two")
    except json.JSONDecodeError as exc:
        real = exc
    assert real is not None
    assert _is_transient_llm_error(real) is True

    class _ForeignJSONDecodeError(ValueError):
        pass
    _ForeignJSONDecodeError.__name__ = "JSONDecodeError"
    assert _is_transient_llm_error(_ForeignJSONDecodeError("boom")) is True

    # The OpenRouter wrapped-5xx NoneType subscript stays transient.
    assert _is_transient_llm_error(
        TypeError("'NoneType' object is not subscriptable")
    ) is True
    # A genuine deterministic fault must NOT be retried (would burn the budget).
    assert _is_transient_llm_error(ValueError("schema mismatch: field x required")) is False


def test_taskmaster_retry_classifier_covers_validation_and_usage_limit():
    from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded

    assert _is_taskmaster_retryable(UnexpectedModelBehavior("bad json")) is True
    assert _is_taskmaster_retryable(
        UnexpectedModelBehavior("Exceeded maximum retries (3/5) for output validation")
    ) is True
    assert _is_taskmaster_retryable(UsageLimitExceeded("request_limit")) is True
    assert _is_taskmaster_retryable(ValueError("schema mismatch: field x required")) is False


def test_retry_schedule_is_six_attempts_and_escalates():
    assert len(_LLM_CALL_TIMEOUTS_S) == 6
    assert list(_LLM_CALL_TIMEOUTS_S) == sorted(_LLM_CALL_TIMEOUTS_S)  # non-decreasing
    assert _LLM_CALL_TIMEOUTS_S[0] >= 120.0  # ~2x the old 60s first attempt
    assert _SLOW_MODEL_TIMEOUT_MULT == 2.0


def test_retry_backoff_grows_exponentially_and_clears_two_minutes():
    # Equal jitter → each draw sits in [ceiling/2, ceiling] where ceiling is
    # base*factor**idx, capped. Schedule of ceilings ≈ 2, 6, 18, 54, 162, 300s.
    expected_ceilings = {0: 2.0, 1: 6.0, 2: 18.0, 3: 54.0, 4: 162.0, 10: 300.0}
    for idx, ceil in expected_ceilings.items():
        draws = [_retry_backoff_s(idx) for _ in range(400)]
        assert all(ceil / 2.0 - 1e-9 <= d <= ceil + 1e-9 for d in draws)
    # Monotonic growth (compare the guaranteed floors across attempts).
    floors = [
        _RETRY_BACKOFF_BASE_S * (_RETRY_BACKOFF_FACTOR ** i) / 2.0 for i in range(5)
    ]
    assert floors == sorted(floors) and floors[0] < floors[-1]
    # The safety ask (Andreas 2026-06-17): the longer waits clear two minutes.
    # By attempt idx 4 the floor alone (81s) is large and the ceiling (162s)
    # routinely exceeds 120s.
    assert max(_retry_backoff_s(4) for _ in range(400)) > 120.0
    # Still capped — never runs away.
    assert all(_retry_backoff_s(20) <= _RETRY_BACKOFF_CAP_S + 1e-9 for _ in range(200))


# --- Phase 5 + 6: drive the real loop with stubs -----------------------------


class _StubReferee:
    """Minimal referee: latches a missed-gate termination once turn >= deadline.

    Records every turn it was polled on so a test can prove the deadline fires
    on the exact turn (including a handoff turn) rather than one turn late.
    """

    def __init__(self, deadline: int):
        self.deadline = deadline
        self._reason = None
        self.polled_turns: list[int] = []

    def poll(self, turn: int) -> bool:
        self.polled_turns.append(turn)
        if self._reason is None and turn >= self.deadline:
            self._reason = "missed_gate:test_gate"
        return self._reason is not None

    @property
    def termination_reason(self):
        return self._reason

    def should_complete_run(self) -> bool:
        return False

    def should_stop_at(self) -> bool:
        return False

    def scorecard(self) -> dict:
        return {"termination_reason": self._reason, "gates": [], "furthest": None}


def _read_summary(run_dir) -> dict:
    with open(Path(run_dir) / "run_summary.json") as f:
        return json.load(f)


def test_deadline_on_handoff_turn_terminates_that_turn_not_next(tmp_path):
    """Off-by-one fix: a deadline gate falling due on a handoff turn terminates
    at that turn. The referee must be polled on the handoff turn and NOT proceed
    to a third player turn — pre-fix the handoff `continue` skipped the poll, so
    the deadline was evaluated a turn late.

    Poll values are TOTAL turns (player + TaskMaster) (Andreas 2026-06-17):
      - cold start invokes TaskMaster → task_master_turns = 1 before turn 1;
      - player turn 1 polls at total 1+1 = 2;
      - player turn 2 hands back → _handle_handoff invokes TaskMaster
        (task_master_turns = 2) → its poll sees total 2+2 = 4.
    With deadline 4 the run survives turn 1 (2 < 4) and terminates on the handoff
    turn (4 >= 4); a third player turn would have polled at total 5."""
    cfg = _base_config(tmp_path, enabled=True, max_turns_per_task=50)
    # turn 1 normal; turn 2 hands back voluntarily (a handoff turn); turn 3 would
    # be played only if the run didn't stop at the deadline.
    player_actions = [
        _ga("t1 move"),
        _ga("t2 hand back", inputs=[],
            handoff=ReturnToTaskMaster(self_assessment="succeeded", task_summary="done")),
        _ga("t3 should-not-run"),
    ]
    invocations = [
        _inv("Task 1", "desc", "crit"),  # cold start
        _inv("Task 2", "desc", "crit", rating=Rating(status="succeeded", reasoning="ok")),
    ]
    runner = StubTaskMasterRunner(invocations)
    mgr, logger = _make_mgr(cfg, player_actions, runner)
    ref = _StubReferee(deadline=4)
    mgr.referee = ref

    mgr.run_loop()

    assert ref.termination_reason == "missed_gate:test_gate"
    # Polled in total-turn units: turn 1 at 2, the handoff turn at 4 — and
    # stopped there (never the third player turn, which would poll at 5).
    assert ref.polled_turns == [2, 4], ref.polled_turns
    summary = _read_summary(logger.run_dir)
    assert summary["session"]["player_turns"] == 2


def test_taskmaster_invocations_count_as_turns_and_attach_cost(tmp_path):
    """Phase 6: cold-start + each handoff = +1 reported turn, with its cost in a
    per-turn entry tagged task_master, and folded into total_turns."""
    cfg = _base_config(tmp_path, enabled=True, max_turns_per_task=50)
    player_actions = [
        _ga("t1 move"),
        _ga("t2 hand back", inputs=[],
            handoff=ReturnToTaskMaster(self_assessment="succeeded", task_summary="done")),
        _ga("t3 move"),
        # 4th turn pops nothing → _run_turn returns None → loop stops.
    ]
    invocations = [
        _inv("Task 1", "d", "c", cost=0.02),  # cold start
        _inv("Task 2", "d", "c", rating=Rating(status="succeeded", reasoning="ok"), cost=0.03),
    ]
    runner = StubTaskMasterRunner(invocations)
    mgr, logger = _make_mgr(cfg, player_actions, runner)

    mgr.run_loop()

    summary = _read_summary(logger.run_dir)
    session = summary["session"]
    cost = summary["cost"]

    # Two TaskMaster invocations were made (cold start + one handoff).
    assert session["task_master_turns"] == 2
    assert session["total_turns"] == session["player_turns"] + 2

    # Each invocation's cost is now a per-turn entry tagged task_master.
    tm_entries = [e for e in cost["per_turn"] if e.get("kind") == "task_master"]
    assert len(tm_entries) == 2
    assert abs(sum(e["cost_usd"] for e in tm_entries) - 0.05) < 1e-9
    # And still rolled into the separate strategy bucket + grand total.
    assert abs(cost["task_master_usd"] - 0.05) < 1e-9
    assert cost["total_usd"] >= 0.05


def test_crashed_run_writes_readable_summary_and_is_idempotent(tmp_path):
    """A mid-run fault still leaves a FULL, readable run_summary.json stamped
    `crashed` (Andreas 2026-06-17) — so a failed run lands in History as
    INCOMPLETE with its report intact instead of vanishing or masquerading as
    `completed`. finalize_run_summary is idempotent: the loop's clean-exit
    finalize and run_single_loop's crash handler can both call it; the first
    wins, the second is a no-op (no double-write, no status clobber)."""
    cfg = _base_config(tmp_path, enabled=True)
    mgr, logger = _make_mgr(
        cfg, [_ga("noop")], StubTaskMasterRunner([_inv("T", "d", "c")])
    )

    # Stand in for run_single_loop's `except Exception` handler (no loop run).
    mgr.finalize_run_summary(status="crashed")

    summary = _read_summary(logger.run_dir)
    assert summary["status"] == "crashed"
    # Full report payload present — not an empty stub the executor had to backfill.
    assert "session" in summary and "cost" in summary and "turns" in summary
    assert summary["session"]["player_turns"] == 0  # faulted before any turn ran

    # A second finalize (e.g. the loop's own clean-exit call) is a no-op.
    mgr.finalize_run_summary(status="completed")
    assert _read_summary(logger.run_dir)["status"] == "crashed"
