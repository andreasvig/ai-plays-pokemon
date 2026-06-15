"""Regression: a STOPPED run must not kill the serial drain thread.

The real ``run_single_loop`` RAISES ``KeyboardInterrupt`` when the turn loop is
interrupted (a stop request — locked #9 — or a Ctrl-C reaching the run) instead
of returning the run_dir. The original executor only ``try/finally``'d the
run_fn call and ``drain_loop`` only caught ``Exception`` — so a single stop
raised a ``KeyboardInterrupt`` that escaped ``drain_once`` and killed the daemon
drain thread, freezing the queue forever (no run, official or casual, would ever
start again — observed live 2026-06-15).

The existing ``test_stop_casual_marks_cancelled`` could not catch this: its fake
run_fn RETURNS the run_dir, so it never exercised the raising path — a textbook
stub-at-the-seam miss (the fake skips the real adapter's own control flow).

These tests drive a run_fn that raises ``KeyboardInterrupt`` like the real loop.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from src.app.executor import RunExecutor
from src.app.models import RunKind, RunStatus
from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex

# Reuse the fakes from the executor test module.
from tests.test_app_executor import FakeSupervisor, fake_prepare_config


def make_run_fn(runs_root: Path, *, raise_on=frozenset(), recorder=None):
    """Fake run_single_loop. Publishes the run dir + writes a minimal summary
    (like the real loop's finally), then RAISES KeyboardInterrupt for any run
    whose 1-based index is in ``raise_on`` — mirroring a stopped/interrupted run.
    """
    counter = {"n": 0}

    def run_fn(handle, config, *, turns, snapshot, open_browser=False, open_report=False, on_run_dir=None):
        counter["n"] += 1
        n = counter["n"]
        run_name = config.get("run_name", f"run{n}")
        run_dir = runs_root / f"2026-06-15_00-00-0{n}_{run_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        # Publish BEFORE the (would-be) blocking turn loop, exactly like the real
        # run_single_loop — this is what lets the executor recover the run dir to
        # finalise even when the run is then interrupted.
        if on_run_dir is not None:
            on_run_dir(run_dir)
        summary = {
            "session": {
                "llm_alias": config.get("_llm_alias"),
                "llm_model": config.get("llm_model", "fake/model"),
                "total_turns": 1,
                "duration_seconds": 4.0,
                "started_at": "2026-06-15T00:00:00",
            },
            "cost": {"total_usd": 0.5, "per_turn": []},
            "turns": [],
        }
        with open(run_dir / "run_summary.json", "w") as f:
            json.dump(summary, f)
        with open(run_dir / "config.json", "w") as f:
            json.dump({"referee": config.get("referee"), "run_name": run_name}, f)
        if recorder is not None:
            recorder.append(run_dir.name)
        if n in raise_on:
            # The real loop's "user interrupted" signal.
            raise KeyboardInterrupt
        return run_dir

    return run_fn, counter


def _make_executor(tmp_path, *, raise_on=frozenset(), recorder=None):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    queue = QueueManager(app_dir / "queue.json")
    index = RunIndex(app_dir / "runs_index.json", runs_root)
    index.load()
    supervisor = FakeSupervisor()
    run_fn, counter = make_run_fn(runs_root, raise_on=raise_on, recorder=recorder)
    executor = RunExecutor(
        supervisor=supervisor,
        queue_manager=queue,
        run_index=index,
        runs_root=runs_root,
        saves_dir=tmp_path / "saves",
        run_fn=run_fn,
        prepare_config_fn=fake_prepare_config,
    )
    return executor, queue, index, supervisor


def test_drain_once_does_not_propagate_keyboardinterrupt(tmp_path):
    """A stopped run (run_fn raises KeyboardInterrupt) is handled inside
    drain_once — it does NOT escape (which would kill the drain thread)."""
    executor, queue, index, supervisor = _make_executor(tmp_path, raise_on={1})
    queue.enqueue(kind=RunKind.casual, model="m", config="c", max_turns=3)

    # Must NOT raise. (Before the fix this propagated KeyboardInterrupt.)
    run_id = executor.drain_once()

    assert run_id is not None  # finalised from the captured run dir
    # busy + active cleared, item removed — the queue advanced cleanly.
    assert supervisor.status().busy is False
    assert queue.active is None
    assert queue.items == []


def test_stopped_run_still_finalised_as_cancelled(tmp_path):
    """Even though run_fn RAISED instead of returning, the stop verdict
    (cancelled) is still applied — finalise runs off the captured run dir."""
    executor, queue, index, _ = _make_executor(tmp_path, raise_on={1})
    queue.enqueue(kind=RunKind.casual, model="m", config="c", max_turns=3)
    expected = "2026-06-15_00-00-01_c__m"
    executor._stop_requested_run_id = expected  # user requested a stop

    run_id = executor.drain_once()
    assert run_id == expected
    entry = index.get(run_id)
    assert entry is not None
    assert entry.status == RunStatus.cancelled


def test_next_run_starts_after_a_stopped_run(tmp_path):
    """The core regression: stopping one run must not block the NEXT.
    First item is interrupted; the second must still execute (drain survives)."""
    recorder: list[str] = []
    executor, queue, index, supervisor = _make_executor(
        tmp_path, raise_on={1}, recorder=recorder
    )
    queue.enqueue(kind=RunKind.casual, model="model-a", config="c", max_turns=3)
    queue.enqueue(kind=RunKind.casual, model="model-b", config="c", max_turns=3)

    first = executor.drain_once()   # interrupted — must not raise
    second = executor.drain_once()  # MUST still run
    third = executor.drain_once()   # queue empty

    assert first is not None and second is not None
    assert third is None
    assert "model-a" in recorder[0]
    assert "model-b" in recorder[1]  # the second run actually executed
    assert supervisor.max_concurrent_observed <= 1
    assert queue.items == []


def test_drain_loop_thread_survives_a_stop(tmp_path):
    """End-to-end: run the real drain_loop in a daemon thread (as `pokemon app`
    does). An interrupting item then a normal item — both must be processed and
    the thread must stay alive. This reproduces the live freeze and proves the
    fix at the loop level (not just drain_once)."""
    recorder: list[str] = []
    executor, queue, index, supervisor = _make_executor(
        tmp_path, raise_on={1}, recorder=recorder
    )
    t = threading.Thread(target=executor.drain_loop, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    try:
        queue.enqueue(kind=RunKind.casual, model="model-a", config="c", max_turns=3)
        queue.enqueue(kind=RunKind.casual, model="model-b", config="c", max_turns=3)
        # Wait until both have been processed (or time out).
        deadline = time.time() + 5.0
        while time.time() < deadline and len(recorder) < 2:
            time.sleep(0.02)
    finally:
        executor.stop()
        t.join(timeout=2.0)

    assert "model-a" in recorder[0]
    assert "model-b" in recorder[1], "second run never ran — drain thread died on the stop"
    assert not t.is_alive()  # stop() cleanly ended the loop
