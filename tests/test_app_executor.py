"""Headless tests for RunExecutor (Plan §P3).

No real mGBA / emulator: a FAKE supervisor exposes a busy flag + a dummy handle,
and a FAKE ``run_fn`` writes a minimal ``run_summary.json`` instead of running
the agent. These assert STRUCTURE/invariants — serial drain order, single-active
never violated, status mapping, stop→cancelled(+void), continue reuses model +
savepoint, official forces frozen config — NEVER a tuned gate/deadline number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app.executor import OFFICIAL_BENCHMARK_VERSION, RunExecutor
from src.app.models import RunKind, RunStatus
from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex


# ───────────────────────────── fakes ─────────────────────────────


class FakeSupervisor:
    """Minimal supervisor: a busy flag + a sentinel handle. No process."""

    def __init__(self) -> None:
        self._busy = False
        self.handle = {"emu": object(), "slot_cfg": {}}
        self.max_concurrent_observed = 0
        self._active = 0

    class _Status:
        def __init__(self, busy: bool) -> None:
            self.busy = busy
            self.process_up = True
            self.connected = True

    def status(self):
        return self._Status(self._busy)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._active += 1 if busy else -1
        self.max_concurrent_observed = max(self.max_concurrent_observed, self._active)


def make_run_fn(runs_root: Path, *, recorder: list[str] | None = None):
    """A fake run_single_loop: writes a minimal run_summary.json + returns run_dir.

    The summary mirrors the nested shape the projection reads (session/cost), so
    ``project_run_dir`` produces a valid flat RunSummary.
    """
    counter = {"n": 0}

    def run_fn(handle, config, *, turns, snapshot, open_browser=False, on_run_dir=None, should_stop=None):
        counter["n"] += 1
        # Derive a run-dir name from the run_name so order is observable.
        run_name = config.get("run_name", f"run{counter['n']}")
        run_dir = runs_root / f"2026-06-15_00-00-0{counter['n']}_{run_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        # Mirror real run_single_loop: publish the run dir before "running".
        if on_run_dir is not None:
            on_run_dir(run_dir)
        summary = {
            "session": {
                "llm_alias": config.get("_llm_alias") or config.get("llm_model"),
                "llm_model": config.get("llm_model", "fake/model"),
                "total_turns": min(turns, 3),
                "duration_seconds": 12.0,
                "started_at": "2026-06-15T00:00:00",
            },
            "cost": {"total_usd": 1.0, "per_turn": []},
            "turns": [],
        }
        # Echo the referee block presence so tests can assert official wiring.
        if config.get("referee"):
            summary["_referee_config"] = config["referee"]
        with open(run_dir / "run_summary.json", "w") as f:
            json.dump(summary, f)
        # Record the config the run_fn actually saw (frozen-config assertions).
        with open(run_dir / "config.json", "w") as f:
            json.dump(
                {
                    "referee": config.get("referee"),
                    "_config_path": config.get("_config_path"),
                    "run_name": run_name,
                },
                f,
            )
        if recorder is not None:
            recorder.append(run_dir.name)
        return run_dir

    return run_fn, counter


def fake_prepare_config(path, model, tm_model_alias=None):
    """Stand-in for runner.prepare_config — no models.yaml / disk reads.

    Mirrors ONE real property of the loaded YAML that the executor branches on:
    whether the config carries a ``task_master`` block. Every 3.x config does
    (with ``enabled: true``); config-4.0+ is the self-directed single-agent line
    and has none, which is what tells the executor to skip TaskMaster wiring
    entirely. A stub that always omitted the block would hide that branch and
    make the TaskMaster-on assertions below vacuous.
    """
    stem = Path(path).stem if path else "latest"
    cfg = {
        "_config_path": path,
        "_llm_alias": model,
        "llm_model": f"resolved/{model}",
        "run_name": f"{stem}__{model}",
    }
    if not stem.startswith("config-4"):
        cfg["task_master"] = {"enabled": True, "history_window_n": 20}
    return cfg


@pytest.fixture
def harness(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    queue = QueueManager(app_dir / "queue.json")
    index = RunIndex(app_dir / "runs_index.json", runs_root)
    index.load()
    supervisor = FakeSupervisor()
    recorder: list[str] = []
    run_fn, counter = make_run_fn(runs_root, recorder=recorder)

    executor = RunExecutor(
        supervisor=supervisor,
        queue_manager=queue,
        run_index=index,
        runs_root=runs_root,
        saves_dir=tmp_path / "saves",
        run_fn=run_fn,
        prepare_config_fn=fake_prepare_config,
    )
    return {
        "executor": executor,
        "queue": queue,
        "index": index,
        "supervisor": supervisor,
        "runs_root": runs_root,
        "recorder": recorder,
        "counter": counter,
    }


# ───────────────────────────── tests ─────────────────────────────


def test_two_casual_runs_execute_in_order_single_active(harness):
    queue = harness["queue"]
    executor = harness["executor"]
    index = harness["index"]
    supervisor = harness["supervisor"]
    recorder = harness["recorder"]

    a = queue.enqueue(kind=RunKind.casual, model="model-a", config="cfg", max_turns=3)
    b = queue.enqueue(kind=RunKind.casual, model="model-b", config="cfg", max_turns=3)

    first = executor.drain_once()
    second = executor.drain_once()
    third = executor.drain_once()  # queue now empty → None

    assert first is not None and second is not None
    assert third is None
    # Executed in enqueue order (model-a before model-b).
    assert "model-a" in recorder[0]
    assert "model-b" in recorder[1]
    # Single-active invariant: never more than one busy at a time.
    assert supervisor.max_concurrent_observed <= 1
    # Both produced index entries + run_summary.json.
    entries = index.all()
    assert len(entries) == 2
    for e in entries:
        assert e.kind == RunKind.casual
        assert e.status == RunStatus.completed  # casual natural end → completed
        assert (harness["runs_root"] / e.run_id / "run_summary.json").exists()
    # Queue drained empty + no active left.
    assert queue.items == []
    assert queue.active is None
    # Both items consumed (no leftover queue ids).
    assert a.queue_id not in {it.queue_id for it in queue.items}
    assert b.queue_id not in {it.queue_id for it in queue.items}


def test_drain_once_noop_when_busy(harness):
    queue = harness["queue"]
    executor = harness["executor"]
    supervisor = harness["supervisor"]

    queue.enqueue(kind=RunKind.casual, model="m", config="c", max_turns=3)
    supervisor.set_busy(True)  # pretend a run is already executing
    assert executor.drain_once() is None  # single-active: refuse to start


def test_stop_casual_marks_cancelled(harness):
    queue = harness["queue"]
    executor = harness["executor"]
    index = harness["index"]
    runs_root = harness["runs_root"]

    queue.enqueue(kind=RunKind.casual, model="m", config="c", max_turns=3)

    # Pre-compute the run-dir name the fake will mint, and request a stop on it.
    expected = "2026-06-15_00-00-01_c__m"
    executor._stop_requested_run_id = expected

    run_id = executor.drain_once()
    assert run_id == expected
    entry = index.get(run_id)
    assert entry.status == RunStatus.cancelled
    assert entry.leaderboard_eligible is False  # casual never eligible anyway


def test_stop_official_voids_run_excluded_from_leaderboard(harness):
    from src.app.derivations import leaderboard

    queue = harness["queue"]
    executor = harness["executor"]
    index = harness["index"]

    queue.enqueue(kind=RunKind.official, model="m")
    expected = "2026-06-15_00-00-01_config-3.13__m"
    executor._stop_requested_run_id = expected

    run_id = executor.drain_once()
    assert run_id == expected
    entry = index.get(run_id)
    assert entry.kind == RunKind.official
    assert entry.status == RunStatus.cancelled
    # Voided: benchmark_version nulled → not leaderboard-eligible (locked #9).
    assert entry.benchmark_version is None
    assert entry.leaderboard_eligible is False
    assert leaderboard(index.all()) == []


def test_official_enqueue_forces_frozen_config_enforce_no_maxturns(harness):
    """Official run uses config-3.13 + enforced v1 ladder + sentinel turns.

    Structural: the run_fn must have seen a referee block with enforce=True
    pointing at the official ladder path, and the config built from the official
    config path — regardless of any config/max_turns the item carried.
    """
    queue = harness["queue"]
    executor = harness["executor"]

    # Item tries to smuggle a casual config + tiny max_turns — must be ignored.
    item = queue.enqueue(
        kind=RunKind.official, model="m", config="configs/sneaky.yaml", max_turns=5
    )
    config, snapshot, turns = executor.build_run_config(item)

    from src.app.benchmarks import default_benchmark

    assert config["_config_path"] == executor.official_config_path  # frozen config
    assert config["referee"]["enforce"] is True
    # No benchmark chosen → registry default's ladder.
    assert config["referee"]["checkpoints"] == default_benchmark().ladder
    assert turns == executor._OFFICIAL_TURN_SENTINEL  # "no max-turns" sentinel
    assert turns != 5  # the smuggled max_turns was ignored
    assert snapshot == executor.canonical_save  # canonical start save
    assert config["task_master"]["mode"] == "benchmark"  # official = benchmark


def test_official_default_benchmark_is_easy_ladder(harness):
    """An official item with NO benchmark falls back to the registry default
    (pokebench-easy) — the easy ladder + the easy goal text."""
    from src.app.benchmarks import get_benchmark

    queue = harness["queue"]
    executor = harness["executor"]

    item = queue.enqueue(kind=RunKind.official, model="m")  # no benchmark
    config, _snapshot, _turns = executor.build_run_config(item)

    easy = get_benchmark("pokebench-easy")
    assert easy.is_default  # registry default is now easy
    assert config["referee"]["checkpoints"] == easy.ladder
    assert config["referee"]["enforce"] is True
    # Goal override: the frozen config's task.goal is replaced by the benchmark's.
    assert config["task"]["goal"] == easy.goal


def test_official_benchmark_selects_ladder_and_overrides_goal(harness):
    """A chosen benchmark drives BOTH the gate ladder and the goal override —
    same frozen config, different objective + gates per benchmark."""
    from src.app.benchmarks import get_benchmark

    queue = harness["queue"]
    executor = harness["executor"]

    for bid in ("pokebench-easy", "pokebench-first-badge", "pokebench-full"):
        item = queue.enqueue(kind=RunKind.official, model="m", benchmark=bid)
        config, _snapshot, _turns = executor.build_run_config(item)
        bench = get_benchmark(bid)
        # Each benchmark injects its OWN ladder file...
        assert config["referee"]["checkpoints"] == bench.ladder
        assert config["referee"]["enforce"] is True
        # ...and overrides the goal with its own objective text.
        assert config["task"]["goal"] == bench.goal
        # Frozen config is the SAME across benchmarks (comparability).
        assert config["_config_path"] == executor.official_config_path


def test_official_run_stamps_benchmark_id(harness):
    """A completed official run records WHICH benchmark it played, so the
    per-benchmark leaderboard filter can group it."""
    queue = harness["queue"]
    executor = harness["executor"]
    index = harness["index"]

    queue.enqueue(kind=RunKind.official, model="m", benchmark="pokebench-easy")
    run_id = executor.drain_once()
    entry = index.get(run_id)
    assert entry.kind == RunKind.official
    assert entry.benchmark == "pokebench-easy"


def test_casual_uses_chosen_config_and_maxturns(harness):
    queue = harness["queue"]
    executor = harness["executor"]

    item = queue.enqueue(
        kind=RunKind.casual, model="m", config="configs/config-3.13.yaml", max_turns=42
    )
    config, snapshot, turns = executor.build_run_config(item)
    assert config["_config_path"] == "configs/config-3.13.yaml"
    assert turns == 42
    assert "referee" not in config  # casual = no gates
    assert snapshot == executor.canonical_save
    assert config["task_master"]["mode"] == "freeplay"  # casual = custom/freeplay
    assert config.get("_task_master_alias") == "gemini-3.5-flash(medium)"
    assert config.get("task_master_model") == "google/gemini-3.5-flash"


def test_continue_spec_reuses_model_and_savepoint_ignores_request_model(harness):
    """Continue (locked #10): casual, reuses source model, resolves savepoint."""
    queue = harness["queue"]
    executor = harness["executor"]
    index = harness["index"]
    runs_root = harness["runs_root"]

    # Build a source run on disk with a savepoint + a known model.
    source_id = "2026-06-15_src_config-3.12__claude"
    source_dir = runs_root / source_id
    (source_dir / "savepoints" / "turn_120").mkdir(parents=True)
    with open(source_dir / "run_summary.json", "w") as f:
        json.dump(
            {"session": {"llm_alias": "claude", "llm_model": "anthropic/claude"}}, f
        )
    # Index it so _source_model finds the alias via the index too.
    index.rebuild_from_scan()

    spec = executor.build_continue_spec(source_id)
    assert spec["kind"] == RunKind.casual
    assert spec["continue_from"] == source_id
    assert spec["model"] == "claude"  # reused from source
    assert spec.get("config") is None

    # A bare positional `model=` is still not a parameter (the Player model rides
    # on the dedicated `player_model` keyword), proving a stray request model is moot.
    with pytest.raises(TypeError):
        executor.build_continue_spec(source_id, model="other")  # type: ignore[call-arg]


def test_continue_spec_casual_accepts_model_overrides(harness):
    """Casual continue may swap Player + TaskMaster models (UI pickers)."""
    executor = harness["executor"]
    runs_root = harness["runs_root"]
    index = harness["index"]

    source_id = "2026-06-15_src_cfg__claude"
    source_dir = runs_root / source_id
    (source_dir / "savepoints" / "turn_50").mkdir(parents=True)
    with open(source_dir / "run_summary.json", "w") as f:
        json.dump({"session": {"llm_alias": "claude", "llm_model": "anthropic/claude"}}, f)
    index.rebuild_from_scan()

    spec = executor.build_continue_spec(
        source_id,
        player_model="gpt-5.5(medium)",
        task_master_model="gemini-3.5-flash(low)",
    )
    assert spec["kind"] == RunKind.casual
    assert spec["model"] == "gpt-5.5(medium)"  # Player override
    assert spec["task_master_model"] == "gemini-3.5-flash(low)"  # TM override


def test_continue_spec_official_ignores_model_overrides(harness):
    """Official continue is model-locked: overrides dropped, source models reused."""
    executor = harness["executor"]
    runs_root = harness["runs_root"]
    index = harness["index"]

    source_id = "2026-06-15_off_cfg__claude"
    source_dir = runs_root / source_id
    (source_dir / "savepoints" / "turn_50").mkdir(parents=True)
    with open(source_dir / "run_summary.json", "w") as f:
        json.dump(
            {
                "kind": "official",
                "benchmark": "pokebench-easy",
                "session": {"llm_alias": "claude", "llm_model": "anthropic/claude"},
            },
            f,
        )
    index.rebuild_from_scan()

    spec = executor.build_continue_spec(
        source_id,
        player_model="gpt-5.5(medium)",
        task_master_model="gemini-3.5-flash(low)",
    )
    assert spec["kind"] == RunKind.official
    assert spec["model"] == "claude"  # override ignored — model-locked
    assert spec["task_master_model"] is None  # override ignored
    assert spec["benchmark"] == "pokebench-easy"


def test_continue_dispatch_applies_player_override(harness):
    """A casual continue with a NEW valid Player model re-resolves llm_model."""
    executor = harness["executor"]
    runs_root = harness["runs_root"]
    queue = harness["queue"]

    source_id = "2026-06-16_src__gpt"

    def fake_continue_fn(arg):
        # Source resolved to gpt-5.5; the continue picks a DIFFERENT model.
        return (
            {
                "run_name": "x",
                "_config_path": "configs/x.yaml",
                "_llm_alias": "gpt-5.5(medium)",
                "llm_model": "openai/gpt-5.5",
                "task_master": {"mode": "freeplay"},
            },
            runs_root / source_id / "savepoints" / "turn_50",
        )

    executor._continue_fn = fake_continue_fn
    item = queue.enqueue(kind=RunKind.casual, model="gemini-3.5-flash(low)", max_turns=30)
    item.continue_from = source_id

    cfg, _snapshot, turns = executor.build_run_config(item)
    assert cfg["_llm_alias"] == "gemini-3.5-flash(low)"  # re-resolved to the override
    assert cfg["llm_model"] == "google/gemini-3.5-flash"
    assert turns == 30


def test_continue_dispatch_reuse_keeps_source_settings(harness):
    """A reuse continue (item.model == source alias) does NOT re-resolve the model,
    preserving the source run's exact resolved settings (no registry round-trip)."""
    executor = harness["executor"]
    runs_root = harness["runs_root"]
    queue = harness["queue"]

    source_id = "2026-06-16_src__gpt2"

    def fake_continue_fn(arg):
        return (
            {
                "run_name": "x",
                "_config_path": "configs/x.yaml",
                "_llm_alias": "gpt-5.5(medium)",
                "llm_model": "openai/gpt-5.5-PINNED",  # deliberately odd → proves no re-resolve
                "task_master": {"mode": "freeplay"},
            },
            runs_root / source_id / "savepoints" / "turn_50",
        )

    executor._continue_fn = fake_continue_fn
    item = queue.enqueue(kind=RunKind.casual, model="gpt-5.5(medium)", max_turns=30)
    item.continue_from = source_id

    cfg, _snapshot, _turns = executor.build_run_config(item)
    assert cfg["llm_model"] == "openai/gpt-5.5-PINNED"  # source settings kept verbatim


def test_continue_dispatch_resolves_full_runs_root_path(harness):
    """Dispatching a continue item must hand the resolver the FULL run dir under
    runs_root — not the bare run_id. continue_from_run does Path(arg).resolve(),
    so a bare id resolves against CWD and dies "not a directory" (live bug
    2026-06-16). The build_continue_spec test above never caught this because it
    only checks the ENQUEUE side, not the DISPATCH (build_run_config) — a
    stub-at-the-seam miss. Here we inject a fake resolver and assert the path.
    """
    queue = harness["queue"]
    executor = harness["executor"]
    runs_root = harness["runs_root"]

    source_id = "2026-06-16_09-30-27_config-3.13__gemini"
    got = {}

    def fake_continue_fn(arg):
        got["arg"] = arg
        return ({"run_name": "x", "_config_path": "configs/x.yaml"}, runs_root / source_id / "savepoints" / "turn_50")

    executor._continue_fn = fake_continue_fn  # inject the seam
    item = queue.enqueue(kind=RunKind.casual, model="gemini", config=None, max_turns=30)
    item.continue_from = source_id

    cfg, snapshot, turns = executor.build_run_config(item)

    # The resolver got the absolute source dir under runs_root, NOT the bare id.
    assert got["arg"] == str(runs_root / source_id)
    assert got["arg"] != source_id  # the bug: bare id (resolves against CWD)
    assert turns == 30


def test_continue_spec_raises_without_savepoint(harness):
    executor = harness["executor"]
    runs_root = harness["runs_root"]
    source_id = "2026-06-15_nosave_cfg__m"
    (runs_root / source_id).mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        executor.build_continue_spec(source_id)


# ─────────────── referee success-exit (locked decision #8) ───────────────


class _NullLogger:
    def log_event(self, *a, **k):
        pass


def _make_referee(tmp_path, *, stamp_final: bool):
    """Build a Referee over a tiny 2-gate ladder; optionally stamp the LAST gate.

    Structural — we don't assert a specific gate count; we assert the WIN signal
    fires iff the FINAL ladder rung is complete.
    """
    from src.referee.checkpoints import Checkpoint
    from src.referee.referee import Referee

    nodes = [
        Checkpoint(id="g1", name="One", type="party", signature={"min_count": 1}, deadline_turn=None),
        Checkpoint(id="g2", name="Final", type="party", signature={"min_count": 2}, deadline_turn=None),
    ]

    class _Emu:
        def read_memory(self, addr, length):
            return b"\x00" * length

    ref = Referee(nodes, emulator=_Emu(), logger=_NullLogger(), run_dir=tmp_path)
    if stamp_final:
        ref.stamps["g2"] = 50  # final rung complete → WIN
    return ref


def test_referee_should_complete_only_when_final_gate_stamped(tmp_path):
    not_done = _make_referee(tmp_path / "a", stamp_final=False)
    assert not_done.should_complete_run() is False
    assert not_done.completion_reason is None

    done = _make_referee(tmp_path / "b", stamp_final=True)
    assert done.should_complete_run() is True
    assert done.completion_reason == "final_gate:g2"


def test_referee_success_does_not_disturb_missed_deadline_path(tmp_path):
    """The success exit is parallel — it never sets terminated_reason."""
    done = _make_referee(tmp_path, stamp_final=True)
    assert done.should_complete_run() is True
    assert done.should_terminate() is False
    assert done.termination_reason is None


def test_turn_loop_win_stamps_completed_status(tmp_path):
    """The loop-stop path records `completed` (WIN) via _write_run_summary.

    We drive _write_run_summary on a TurnManager stub with _referee_completed
    True (what the loop sets on the success-exit break) and assert the on-disk
    summary status is `completed` — proving the WIN status propagates without a
    real emulator or a tuned gate count.
    """
    import time as _time

    from src.agent.turn import TurnManager

    # Build a bare TurnManager without running __init__ (avoids agent/model
    # construction); set only the attributes _write_run_summary touches.
    tm = TurnManager.__new__(TurnManager)
    run_dir = tmp_path / "win_run"
    run_dir.mkdir()

    class _Logger:
        def __init__(self, rd):
            self.run_dir = rd

    tm.logger = _Logger(run_dir)
    tm.config = {"_llm_alias": "m", "llm_model": "resolved/m", "thinking": None, "task": {"goal": "x"}}
    tm.fallback_models = []
    tm.tasks = None
    tm.turn_number = 7
    tm._run_start_time = _time.time()
    tm.total_cost_usd = 1.0
    tm.task_master_cost_usd = 0.0
    tm.task_master_turns = 0
    tm.ocr = None
    tm.total_input_tokens = 0
    tm.total_output_tokens = 0
    tm.turn_costs = []
    tm.turn_explanations = []
    tm._explanation_turns = []        # parallel real-turn list (handoff-gap fix)
    tm._prior_duration_s = 0.0        # cumulative-resume accounting baseline
    tm.referee = None
    tm._referee_completed = True
    tm._aborted_no_output = False   # no-valid-output abort latch (writer reads it)
    tm._abort_error = None

    # Mirror the loop's call on a referee success-exit.
    tm._write_run_summary(status="completed" if tm._referee_completed else None)

    with open(run_dir / "run_summary.json") as f:
        summary = json.load(f)
    assert summary["status"] == "completed"


# ─────────── a dropped item leaves a trace, and a dead model isn't a score ───────────


def test_dispatch_failure_is_recorded_and_the_item_still_leaves_the_queue(harness):
    """A failure between dequeue and run start must be visible somewhere.

    ``drain_loop`` catches everything so one poisoned item can't freeze the
    serial queue — which also means the item disappears and the queue looks
    idle. ``last_error`` is the trace that makes those two states
    distinguishable. Both halves are asserted: the queue still drains (the
    original invariant) AND the reason survives (the new one).
    """
    queue = harness["queue"]
    executor = harness["executor"]

    def boom(*a, **k):
        raise RuntimeError("mGBA would not load the rom")

    executor._run_fn = boom
    item = queue.enqueue(kind=RunKind.casual, model="model-x", config="cfg", max_turns=3)

    with pytest.raises(RuntimeError):
        executor.drain_once()   # re-raised for drain_loop's traceback

    assert executor.last_error is not None
    assert executor.last_error["queue_id"] == item.queue_id
    assert executor.last_error["model"] == "model-x"
    assert "mGBA would not load the rom" in executor.last_error["error"]
    # The queue must not be wedged by the failure — the finally still fires.
    assert queue.items == []
    assert queue.active is None


def test_last_error_clears_once_a_run_actually_starts(harness):
    queue = harness["queue"]
    executor = harness["executor"]
    executor.last_error = {"queue_id": "q_old", "error": "stale"}

    queue.enqueue(kind=RunKind.casual, model="model-a", config="cfg", max_turns=3)
    assert executor.drain_once() is not None
    assert executor.last_error is None


def test_no_valid_output_abort_stamps_crashed_not_completed(tmp_path):
    """A run whose model never answered must not read as a played-and-lost run.

    Anchored on a real one: `2026-06-19_17-11-32_..._gemma-4-26b-a4b-fast-...`
    ended after a provider 400 ("not a valid model ID") on turn 1, was stamped
    `completed`, and posted a 0% / 2-turn row to the leaderboard. `crashed` is
    reused rather than a new status because it already has the two properties
    wanted here — excluded by `leaderboard_eligible`, and `_finalize_run`
    withholds `benchmark_version` from it.
    """
    import json as _json
    import time as _time

    from src.agent.turn import TurnManager

    tm = TurnManager.__new__(TurnManager)
    run_dir = tmp_path / "dead_run"
    run_dir.mkdir()

    class _Logger:
        def __init__(self, rd):
            self.run_dir = rd

    tm.logger = _Logger(run_dir)
    tm.config = {"_llm_alias": "m", "llm_model": "resolved/m", "thinking": None, "task": {"goal": "x"}}
    tm.fallback_models = []
    tm.tasks = None
    tm.turn_number = 1
    tm._run_start_time = _time.time()
    tm.total_cost_usd = 0.0
    tm.task_master_cost_usd = 0.0
    tm.task_master_turns = 0
    tm.ocr = None
    tm.total_input_tokens = 0
    tm.total_output_tokens = 0
    tm.turn_costs = []
    tm.turn_explanations = []
    tm._explanation_turns = []
    tm._prior_duration_s = 0.0
    tm.referee = None
    tm._referee_completed = False
    tm._aborted_no_output = True
    tm._abort_error = "status_code: 400 ... is not a valid model ID"

    # Mirror the loop's exit-status decision.
    if tm._referee_completed:
        status = "completed"
    elif tm._aborted_no_output:
        status = "crashed"
    else:
        status = None
    tm._write_run_summary(status=status, kind="official", run_id="r1")

    summary = _json.loads((run_dir / "run_summary.json").read_text())
    assert summary["status"] == "crashed"
    assert "not a valid model ID" in summary["error"]

    # ...and the projection of that summary can never post a row.
    from src.app.models import RunKind as _Kind, RunStatus as _Status, RunSummary

    row = RunSummary(
        run_id="r1", kind=_Kind.official, model="m", status=_Status.crashed
    )
    assert row.leaderboard_eligible is False
    # Control: the same row as `completed` WOULD have posted — which is exactly
    # what the gemma-4-26b row on the published board is.
    assert RunSummary(
        run_id="r1", kind=_Kind.official, model="m", status=_Status.completed
    ).leaderboard_eligible is True


def test_a_clean_run_is_unaffected_by_the_abort_latch(tmp_path):
    """Control: with the latch False the summary keeps its old shape exactly.

    Without this the previous test passes for a version that stamps `crashed`
    on every run.
    """
    import json as _json
    import time as _time

    from src.agent.turn import TurnManager

    tm = TurnManager.__new__(TurnManager)
    run_dir = tmp_path / "ok_run"
    run_dir.mkdir()

    class _Logger:
        def __init__(self, rd):
            self.run_dir = rd

    tm.logger = _Logger(run_dir)
    tm.config = {"_llm_alias": "m", "llm_model": "resolved/m", "thinking": None, "task": {"goal": "x"}}
    tm.fallback_models = []
    tm.tasks = None
    tm.turn_number = 20
    tm._run_start_time = _time.time()
    tm.total_cost_usd = 1.0
    tm.task_master_cost_usd = 0.0
    tm.task_master_turns = 0
    tm.ocr = None
    tm.total_input_tokens = 0
    tm.total_output_tokens = 0
    tm.turn_costs = []
    tm.turn_explanations = []
    tm._explanation_turns = []
    tm._prior_duration_s = 0.0
    tm.referee = None
    tm._referee_completed = False
    tm._aborted_no_output = False
    tm._abort_error = None

    tm._write_run_summary(status=None)
    summary = _json.loads((run_dir / "run_summary.json").read_text())
    assert "status" not in summary          # left for the executor to infer
    assert "error" not in summary           # key only exists on the abort path
