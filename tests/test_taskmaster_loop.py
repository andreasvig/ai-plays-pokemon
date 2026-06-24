"""Phase B4 — TaskMaster run-loop integration tests.

Drives the real handoff orchestration in ``TurnManager._run_loop_async`` with a
STUB Player (scripted ``GameAction``s — including a voluntary
``return_to_taskmaster`` and a budget-exhaustion case) and a STUB TaskMaster
(scripted ``TaskMasterOutput``s — NO OpenRouter call), writing a real
``events.jsonl`` to a temp run dir. Then it asserts the emitted event order +
shapes against the locked contract (local/plan-frontend-display.md "Event
contract") and the golden fixture, and round-trips the run dir through the
report to prove B4's own events render the task tree the frontend was built for.

No mGBA / network: the emulator, vision, state, and OCR are stubs, the Player
turn is overridden to return scripted actions, and the TaskMaster agent is
replaced by an in-memory ``StubTaskMasterRunner``.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# The Player agent (create_agent in TurnManager.__init__) constructs an
# OpenRouter-backed pydantic-ai Agent, which refuses an empty key. No real call
# is ever made (the stub Player overrides _run_turn and the stub TaskMaster
# replaces the agent), but construction needs a non-empty key present.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")

from src.agent.agent import GameAction, ReturnToTaskMaster
from src.agent.task_master import Rating, TaskMasterOutput, TaskSpec
from src.agent.turn import TaskMasterInvocation, TurnManager
from src.core import RunLogger


# --- Stubs -------------------------------------------------------------------


class _StubImage:
    """Stand-in for a PIL image — only needs to be saveable by RunLogger."""

    def save(self, path):
        # RunLogger.log_screenshot writes the file; create an empty placeholder.
        Path(path).write_bytes(b"")


class _StubEmulator:
    def __init__(self):
        self.facing = None
        self.presses = []

    def capture_screenshot(self, preprocess=True):
        return _StubImage()

    def press_button_list(self, buttons):
        self.presses.append(list(buttons))

    def wait_for_stable_screen(self):
        return 0.0


class _StubVision:
    def analyze_screenshot(self, screenshot):
        return {}

    def format_for_llm(self, analysis):
        return []

    def image_to_data_url(self, img):
        return "data:image/png;base64,"


class _StubState:
    def get_truncated_view(self):
        return {}

    def set_by_path(self, key, value):
        pass

    def delete_by_path(self, key):
        pass

    def save(self):
        pass


class StubTaskMasterRunner:
    """Scripted TaskMaster — pops a queued invocation each ``invoke_async`` call.

    Same one-method surface (``async invoke_async(TaskMasterInput, *,
    is_cold_start) -> TaskMasterInvocation``) as the real ``TaskMasterRunner``, so
    it drops into the seam without touching the loop. Records the inputs + the
    cold-start flag it was handed so the test can assert the rolling-window
    assembly and the cold-start vs boundary distinction.
    """

    def __init__(self, invocations):
        self._queue = list(invocations)
        self.inputs_seen = []
        self.cold_start_flags = []

    async def invoke_async(self, inp, *, is_cold_start=False):
        self.inputs_seen.append(inp)
        self.cold_start_flags.append(is_cold_start)
        assert self._queue, "StubTaskMasterRunner ran out of scripted invocations"
        return self._queue.pop(0)


class ScriptedPlayerTurnManager(TurnManager):
    """TurnManager whose Player turn is replaced by a scripted-action queue.

    Overriding ``_run_turn`` keeps the REAL ``_run_loop_async`` (cold start,
    handoff detection, ordering, backward-stamping, savepoints) under test while
    cutting out screenshot/vision/LLM. It still logs ``turn_start`` (with
    ``task_index``) and a screenshot the same way the real turn does, so the
    emitted player-turn shape is faithful.
    """

    def __init__(self, config, player_actions, task_master_runner):
        super().__init__(config, task_master_runner=task_master_runner)
        self._player_actions = list(player_actions)

    async def _run_turn(self):
        t = self.turn_number
        if self.task_master_enabled:
            self.current_task_turn += 1
        self.logger.log_turn_start(
            t,
            task_index=self.current_task_index if self.task_master_enabled else None,
        )
        ref = self.logger.log_screenshot(_StubImage(), label=f"turn_{t}")
        if self.task_master_enabled:
            if self._cur_task_first_image is None:
                self._cur_task_first_image = ref
            self._cur_task_last_image = ref
        if not self._player_actions:
            return None
        return self._player_actions.pop(0)


# --- Helpers -----------------------------------------------------------------


def _ga(reasoning="did a thing", inputs=None, handoff=None):
    return GameAction(
        inputs=inputs if inputs is not None else ["a"],
        reasoning=reasoning,
        last_turn_succeeded=True,
        memory_updates="none",
        return_to_taskmaster=handoff,
    )


def _inv(task_title, task_desc, criteria, rating=None, cost=0.01):
    """Build a scripted TaskMasterInvocation with a faithful trace shape."""
    out = TaskMasterOutput(
        reasoning="strategy reasoning",
        rating_of_previous_task=rating,
        task=TaskSpec(title=task_title, description=task_desc, success_criteria=criteria),
    )
    trace = [
        {"role": "system", "content": "You are the TaskMaster."},
        {"role": "user", "content": "Player progress..."},
        {"role": "thinking", "content": "Let me check the route."},
        {"role": "tool_call", "tool_name": "web_search", "args": {"query": "route"}},
        {"role": "tool_result", "tool_name": "web_search", "content": "results"},
        {"role": "tool_call", "tool_name": "final_result", "args": {"title": task_title}},
        {"role": "tool_result", "tool_name": "final_result", "content": "Final result processed."},
    ]
    return TaskMasterInvocation(
        output=out, trace=trace, cost_usd=cost, model_used="stub/tm-model"
    )


def _base_config(tmp_runs, enabled=True, max_turns_per_task=50):
    cfg = {
        "runs_directory": str(tmp_runs),
        "run_name": "tm_loop_test",
        "max_turns_per_task": max_turns_per_task,
        "max_steps_per_turn": 8,
        "historic_images_count": 0,
        "valid_inputs": ["a", "b"],
        "system_prompt": "play",
        "task": {"goal": "Beat Brock", "description": "starter then route"},
        "llm_model": "stub/player-model",
        "openrouter_api_key": "test-key-not-used",
    }
    if enabled:
        cfg["task_master"] = {"enabled": True, "history_window_n": 3}
    return cfg


def _make_mgr(cfg, player_actions, tm_runner):
    logger = RunLogger(cfg)
    mgr = ScriptedPlayerTurnManager(cfg, player_actions, tm_runner)
    mgr.setup(_StubEmulator(), _StubState(), _StubVision(), logger, None)
    return mgr, logger


def _read_events(run_dir):
    events = []
    with open(Path(run_dir) / "events.jsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


# --- Tests -------------------------------------------------------------------


def _run_three_task_scenario():
    """Run a cold start + two handoffs (budget then voluntary). Returns run_dir.

    Helper (not a test) so multiple tests can share the produced run dir without
    a test function returning non-None.
    """
    tmp = Path(tempfile.mkdtemp())

    # Budget = 2 per task. Both handoff paths are exercised:
    #   Task 1: turns 1,2 — neither action carries a handoff; the budget(2) hit
    #           on turn 2 forces the handoff (BUDGET-EXHAUSTION case).
    #   Task 2: turn 3 — a VOLUNTARY return_to_taskmaster before the budget.
    #   Task 3: turn 4 — one player move, run ends (no completion: in progress).
    player_actions = [
        _ga("t1 move 1"),
        _ga("t1 move 2"),  # turn 2 → budget(2) exhausted → forced handoff
        _ga(
            "t2 done",
            inputs=[],
            handoff=ReturnToTaskMaster(
                self_assessment="succeeded",
                task_summary="reached the lab door",
            ),
        ),
        _ga("t3 move 1"),
    ]

    tm_runner = StubTaskMasterRunner([
        # cold start → task 1 (no rating)
        _inv("Leave the bedroom", "Get downstairs.", "On ground floor.", rating=None),
        # handoff after task 1 (budget) → rate task 1 succeeded, set task 2
        _inv(
            "Reach Oak's lab",
            "Walk south to the lab.",
            "Inside the lab.",
            rating=Rating(status="succeeded", reasoning="last image shows outside"),
        ),
        # handoff after task 2 (voluntary) → rate task 2 partial, set task 3
        _inv(
            "Pick a starter",
            "Choose a starter Pokemon.",
            "A starter in party.",
            rating=Rating(status="partial", reasoning="still on the doormat"),
        ),
    ])

    cfg = _base_config(tmp, enabled=True, max_turns_per_task=2)
    mgr, logger = _make_mgr(cfg, player_actions, tm_runner)
    mgr.run_loop(max_turns=4)
    logger.close()
    return str(logger.run_dir)


def test_taskmaster_handoff_loop_order_and_shape():
    """Cold start + two handoffs: assert event ORDER, task_index, backward-stamp."""
    run_dir = _run_three_task_scenario()
    events = _read_events(run_dir)
    task_evts = [
        e for e in events
        if e["type"] in ("task_master_trace", "task_started", "task_completed")
    ]
    seq = [(e["type"], e["task_index"]) for e in task_evts]

    # Cold start: master_trace{1} → task_started{1}, NO completed.
    # Boundary 1: completed{1} → master_trace{2} → task_started{2}.
    # Boundary 2: completed{2} → master_trace{3} → task_started{3}.
    assert seq == [
        ("task_master_trace", 1),
        ("task_started", 1),
        ("task_completed", 1),
        ("task_master_trace", 2),
        ("task_started", 2),
        ("task_completed", 2),
        ("task_master_trace", 3),
        ("task_started", 3),
    ], seq

    # No task_completed emitted for the in-progress task 3.
    completed_indices = [e["task_index"] for e in events if e["type"] == "task_completed"]
    assert completed_indices == [1, 2], completed_indices

    # Every player turn_start carries the correct task_index.
    # Turns 1-2 under task 1, turn 3 under task 2, turn 4 under task 3.
    turn_starts = [e for e in events if e["type"] == "turn_start"]
    got = [(e["turn"], e["task_index"]) for e in turn_starts]
    assert got == [(1, 1), (2, 1), (3, 2), (4, 3)], got

    # Rating backward-stamps onto the RATED task (task_completed{N} carries the
    # rating produced by the master call that set task N+1).
    completed = {e["task_index"]: e["rating"] for e in events if e["type"] == "task_completed"}
    assert completed[1]["status"] == "succeeded"
    assert completed[2]["status"] == "partial"
    assert "still on the doormat" in completed[2]["reasoning"]


def test_event_shapes_match_fixture():
    """B4's emitted task events have the SAME keys as the golden fixture."""
    fixture = (
        Path(__file__).parent.parent
        / "local/fixtures/taskmaster_sample/events.jsonl"
    )
    fix_events = []
    with open(fixture) as f:
        for line in f:
            line = line.strip()
            if line:
                fix_events.append(json.loads(line))

    def keyset(events, etype):
        for e in events:
            if e["type"] == etype:
                return set(e.keys())
        return set()

    run_dir = _run_three_task_scenario()
    b4_events = _read_events(run_dir)

    # The logger adds id/timestamp/time to every event; compare the union the
    # contract cares about. Both sources must agree on the data keys.
    for etype in ("task_started", "task_completed", "task_master_trace"):
        fix_keys = keyset(fix_events, etype)
        b4_keys = keyset(b4_events, etype)
        assert fix_keys, f"fixture missing {etype}"
        assert b4_keys, f"B4 emitted no {etype}"
        # Ignore the logger envelope fields when comparing the contract payload.
        envelope = {"id", "timestamp", "time"}
        assert (fix_keys - envelope) == (b4_keys - envelope), (
            f"{etype} key mismatch: fixture={fix_keys - envelope} "
            f"b4={b4_keys - envelope}"
        )

    # turn_start gains task_index in both.
    fix_ts = keyset(fix_events, "turn_start")
    b4_ts = keyset(b4_events, "turn_start")
    assert "task_index" in fix_ts and "task_index" in b4_ts

    # rating sub-shape matches {status, reasoning}.
    fix_rating = next(e["rating"] for e in fix_events if e["type"] == "task_completed")
    b4_rating = next(e["rating"] for e in b4_events if e["type"] == "task_completed")
    assert set(fix_rating.keys()) == set(b4_rating.keys()) == {"status", "reasoning"}


def test_rolling_window_and_evidence_fed_to_taskmaster():
    """The boundary TaskMaster input carries prev reasons, self-assessment, refs."""
    tmp = Path(tempfile.mkdtemp())
    player_actions = [
        _ga("t1 reason A"),
        _ga(
            "t1 done",
            inputs=[],
            handoff=ReturnToTaskMaster(
                self_assessment="failed",
                task_summary="got stuck at the door",
            ),
        ),
        _ga("t2 reason A"),
    ]
    tm_runner = StubTaskMasterRunner([
        _inv("Task one", "d1", "c1", rating=None),
        _inv("Task two", "d2", "c2",
             rating=Rating(status="failed", reasoning="evidence")),
    ])
    cfg = _base_config(tmp, enabled=True, max_turns_per_task=50)
    mgr, logger = _make_mgr(cfg, player_actions, tm_runner)
    mgr.run_loop(max_turns=3)
    logger.close()

    # The first invocation is flagged cold-start, the boundary is not — this is
    # what drives the rating-required output validator (Decision 11).
    assert tm_runner.cold_start_flags == [True, False], tm_runner.cold_start_flags

    # Cold-start input: empty rolling window.
    cold = tm_runner.inputs_seen[0]
    assert cold.prior_task_outputs == []
    assert cold.prev_player_self_assessment is None
    assert cold.meta_goal == "Beat Brock"

    # Boundary input: carries the just-finished task's evidence.
    boundary = tm_runner.inputs_seen[1]
    assert "t1 reason A" in boundary.prev_player_reasons
    assert boundary.prev_player_self_assessment is not None
    assert "got stuck at the door" in boundary.prev_player_self_assessment
    assert "failed" in boundary.prev_player_self_assessment
    assert boundary.prev_first_image is not None
    assert boundary.prev_last_image is not None
    # task_master_cost accumulated separately from player cost.
    assert mgr.task_master_cost_usd > 0
    assert mgr.total_cost_usd == 0.0  # stub player has no LLM cost

    # run_summary surfaces the separate TaskMaster cost.
    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["cost"]["task_master_usd"] > 0


def test_legacy_tm_disabled_emits_no_task_events():
    """TM disabled: legacy single-task path, NO task_* events, no task_index."""
    tmp = Path(tempfile.mkdtemp())
    player_actions = [_ga("legacy 1"), _ga("legacy 2")]
    # A runner that would explode if ever called — proves the TM path is dead.
    tm_runner = StubTaskMasterRunner([])
    cfg = _base_config(tmp, enabled=False)
    mgr, logger = _make_mgr(cfg, player_actions, tm_runner)
    mgr.run_loop(max_turns=2)
    logger.close()

    events = _read_events(logger.run_dir)
    task_types = {"task_started", "task_completed", "task_master_trace"}
    assert not any(e["type"] in task_types for e in events), \
        "legacy path must emit NO task_* events"

    # turn_start must NOT carry task_index on the legacy path.
    for e in events:
        if e["type"] == "turn_start":
            assert "task_index" not in e, "legacy turn_start must omit task_index"

    # The stub runner was never invoked.
    assert tm_runner.inputs_seen == []
    # Two player turns ran and pressed buttons (no handoff bypass).
    assert mgr.emulator.presses == [["a"], ["a"]]


def test_real_runner_async_path_no_nested_asyncio():
    """Regression: drive the loop through the REAL TaskMasterRunner.

    The other tests inject a stub runner, so they never exercise
    ``TaskMasterRunner.invoke_async`` itself. This builds the real runner (only
    its pydantic-ai agent's ``run`` is monkeypatched to avoid a network call) and
    drives the real run loop, so ``invoke_async`` is awaited under the live event
    loop started by ``run_loop`` → ``asyncio.run``. If anyone reintroduces a
    nested ``asyncio.run`` inside the invocation path, this raises
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``.
    """
    from src.agent.turn import TaskMasterRunner

    tmp = Path(tempfile.mkdtemp())
    cfg = _base_config(tmp, enabled=True, max_turns_per_task=2)

    runner = TaskMasterRunner(cfg)  # constructs the real agent (no API call yet)

    class _FakeResult:
        def __init__(self, output):
            self.output = output

    calls = {"n": 0, "is_cold_start": []}

    async def _fake_run(user_message, **kwargs):
        calls["n"] += 1
        n = calls["n"]
        rating = None if n == 1 else Rating(status="succeeded", reasoning="ok")
        out = TaskMasterOutput(
            reasoning="strategy",
            rating_of_previous_task=rating,
            task=TaskSpec(title=f"task {n}", description="d", success_criteria="c"),
        )
        return _FakeResult(out)

    runner._agent.run = _fake_run  # bypass the network; keep the real async path

    player_actions = [_ga("m1"), _ga("m2")]  # budget 2 → one boundary handoff
    mgr, logger = _make_mgr(cfg, player_actions, runner)
    mgr.run_loop(max_turns=2)  # would raise RuntimeError if invoke nested asyncio.run
    logger.close()

    events = _read_events(logger.run_dir)
    types = [e["type"] for e in events]
    # Cold start (trace+started) advanced via the real awaited path, plus a boundary.
    assert "task_started" in types and "task_master_trace" in types
    assert mgr.current_task_index >= 2, mgr.current_task_index
    assert calls["n"] >= 2  # real invoke_async actually ran twice


def test_task_master_runner_request_limit_from_config():
    """task_master.request_limit in config overrides the module default."""
    from src.agent.task_master import DEFAULT_REQUEST_LIMIT
    from src.agent.turn import TaskMasterRunner

    tmp = Path(tempfile.mkdtemp())
    cfg = _base_config(tmp, enabled=True)
    runner = TaskMasterRunner(cfg)
    assert runner._request_limit == DEFAULT_REQUEST_LIMIT

    cfg["task_master"]["request_limit"] = 30
    assert TaskMasterRunner(cfg)._request_limit == 30


def test_task_master_runner_retry_settings_from_config():
    from src.agent.task_master import DEFAULT_INVOKE_RETRIES, DEFAULT_OUTPUT_RETRIES
    from src.agent.turn import TaskMasterRunner

    tmp = Path(tempfile.mkdtemp())
    cfg = _base_config(tmp, enabled=True)
    runner = TaskMasterRunner(cfg)
    assert runner._invoke_retries == DEFAULT_INVOKE_RETRIES

    cfg["task_master"]["invoke_retries"] = 4
    assert TaskMasterRunner(cfg)._invoke_retries == 4

    from src.agent.task_master import create_task_master_agent

    agent, _ = create_task_master_agent(cfg)
    assert agent._max_result_retries == DEFAULT_OUTPUT_RETRIES

    cfg["task_master"]["output_retries"] = 10
    agent, _ = create_task_master_agent(cfg)
    assert agent._max_result_retries == 10


def test_taskmaster_invoke_retries_on_validation_failure(monkeypatch):
    import asyncio

    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from src.agent.task_master import TaskMasterInput, TaskMasterOutput, TaskSpec
    from src.agent.turn import TaskMasterInvocation, TaskMasterRunner

    tmp = Path(tempfile.mkdtemp())
    cfg = _base_config(tmp, enabled=True)
    cfg["task_master"]["invoke_retries"] = 4
    runner = TaskMasterRunner(cfg)

    class _FakeResult:
        def __init__(self, output):
            self.output = output

    calls = {"n": 0}

    async def _fake_run(user_message, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise UnexpectedModelBehavior(
                "Exceeded maximum retries (3/5) for output validation"
            )
        return _FakeResult(
            TaskMasterOutput(
                reasoning="ok",
                rating_of_previous_task=None,
                task=TaskSpec(title="t", description="d", success_criteria="c"),
            )
        )

    runner._agent.run = _fake_run
    monkeypatch.setattr(
        "src.agent.turn.create_task_master_agent",
        lambda config: (runner._agent, runner._model_settings),
    )
    monkeypatch.setattr("src.agent.turn._retry_backoff_s", lambda _idx: 0.0)

    inp = TaskMasterInput(meta_goal="goal", player_memory="", prior_outputs=[])
    inv = asyncio.run(runner.invoke_async(inp, is_cold_start=True))

    assert isinstance(inv, TaskMasterInvocation)
    assert calls["n"] == 3
    assert inv.output.task.title == "t"


def test_handoff_takes_a_savepoint(monkeypatch):
    """P4: each TaskMaster handoff checkpoints, bounding hard-kill replay.

    The scenario runs a cold start + two handoffs; save_savepoint must fire with
    kind='handoff' once per handoff (independent of the periodic cadence)."""
    kinds: list[str] = []

    def _spy(self, kind):
        kinds.append(kind)
        return None

    monkeypatch.setattr(TurnManager, "save_savepoint", _spy)
    _run_three_task_scenario()  # two handoffs (budget-exhaustion + voluntary)

    assert kinds.count("handoff") == 2, kinds


def test_official_config_savepoint_cadence_is_tight():
    """P4: the frozen official config checkpoints every 10 turns (hard-kill bound)."""
    from src.config import load_config

    cfg = load_config("configs/config-3.13.yaml")
    sp = cfg["savepoints"]
    assert sp["every_n_turns"] == 10
    assert sp["on_crash"] is True and sp["at_end"] is True


if __name__ == "__main__":
    test_taskmaster_handoff_loop_order_and_shape()
    test_event_shapes_match_fixture()
    test_rolling_window_and_evidence_fed_to_taskmaster()
    test_legacy_tm_disabled_emits_no_task_events()
    test_real_runner_async_path_no_nested_asyncio()
    print("TaskMaster loop: ALL TESTS PASSED")
