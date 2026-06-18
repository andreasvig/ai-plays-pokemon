"""Instant-kill savepoint tests.

A UI "kill" used to wait for the whole in-flight turn (think → act → settle) to
finish before halting, because the stop flag was only polled at the loop
boundary. These tests pin the new behavior:

  1. A stop that lands MID-TURN (during the LLM call) cancels the in-flight turn
     within ~one poll interval, instead of blocking on it.
  2. The aborted turn never mutated the emulator (its buttons are pressed by the
     loop body only AFTER _run_turn returns), so `_last_settled_turn` stays on
     the PRIOR turn — the clean boundary a resume re-runs from.
  3. `save_savepoint(turn=...)` stamps the override turn, so the crash handler
     records the last settled turn, not the in-flight one.

No mGBA / network: emulator, vision, state are stubs; the Player turn is
overridden to block on the chosen turn.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")

from src.agent.agent import GameAction
from src.agent.turn import TurnManager
from src.core import RunLogger


class _StubImage:
    def save(self, path):
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


class _StubState:
    def get_truncated_view(self):
        return {}

    def set_by_path(self, key, value):
        pass

    def delete_by_path(self, key):
        pass

    def save(self):
        pass


def _ga(reasoning):
    return GameAction(
        inputs=["a"],
        reasoning=reasoning,
        last_turn_succeeded=True,
        memory_updates="none",
        return_to_taskmaster=None,
    )


class _BlockingPlayerManager(TurnManager):
    """Player turn returns instantly until ``block_at``, where it blocks.

    The block simulates a long LLM call. Once it's entered, ``_should_stop`` is
    armed (the test wires the predicate to ``entered_block``), so the race in
    ``_run_turn_or_stop`` cancels the in-flight turn.
    """

    def __init__(self, config, block_at):
        super().__init__(config)
        self.block_at = block_at
        self.entered_block = False

    async def _run_turn(self):
        t = self.turn_number
        self.logger.log_turn_start(t)
        self.logger.log_screenshot(_StubImage(), label=f"turn_{t}")
        if t == self.block_at:
            self.entered_block = True
            await asyncio.sleep(30)  # cancelled by the stop race; never elapses
        return _ga(f"move {t}")


def _config(tmp_runs):
    return {
        "runs_directory": str(tmp_runs),
        "run_name": "instant_kill_test",
        "max_turns_per_task": 50,
        "max_steps_per_turn": 8,
        "historic_images_count": 0,
        "valid_inputs": ["a", "b"],
        "system_prompt": "play",
        "task": {"goal": "Beat Brock"},
        "llm_model": "stub/player-model",
        "openrouter_api_key": "test-key-not-used",
    }


def _make(tmp, block_at):
    cfg = _config(tmp)
    logger = RunLogger(cfg)
    mgr = _BlockingPlayerManager(cfg, block_at=block_at)
    mgr.setup(_StubEmulator(), _StubState(), _StubVision(), logger, None)
    mgr._should_stop = lambda: mgr.entered_block
    return mgr


def test_midturn_stop_aborts_and_keeps_last_settled_on_prior_turn():
    tmp = Path(tempfile.mkdtemp())
    mgr = _make(tmp, block_at=3)

    with pytest.raises(KeyboardInterrupt):
        mgr.run_loop(max_turns=50)

    # Turns 1 and 2 completed; turn 3 was entered (counter advanced) but the
    # LLM call was cancelled before it could return / press a button.
    assert mgr.turn_number == 3
    assert mgr._last_settled_turn == 2
    # Only the two completed turns ever pressed a button — the aborted turn 3
    # never mutated the emulator, so resume from turn 2 is byte-exact.
    assert len(mgr.emulator.presses) == 2


def test_save_savepoint_honors_turn_override():
    """The crash handler stamps _last_settled_turn, not the in-flight number."""
    tmp = Path(tempfile.mkdtemp())
    mgr = _make(tmp, block_at=99)  # never reached
    mgr.turn_number = 7  # in-flight turn

    recorded = {}

    class _FakeSnap:
        def save_run_savepoint(self, *, run_dir, turn, kind, task_master_state, referee_state):
            recorded["turn"] = turn
            recorded["kind"] = kind
            return Path(run_dir) / "savepoints" / f"turn_{turn}"

    mgr._snapshot_mgr = _FakeSnap()

    # Default: stamps turn_number.
    mgr.save_savepoint("periodic")
    assert recorded["turn"] == 7

    # Override (the crash path): stamps the last settled turn.
    mgr.save_savepoint("crash", turn=5)
    assert recorded["turn"] == 5
    assert recorded["kind"] == "crash"


def test_clean_boundary_stop_stamps_completed_turn():
    """A stop with no blocking turn halts at the boundary; last settled == done."""
    tmp = Path(tempfile.mkdtemp())
    cfg = _config(tmp)
    logger = RunLogger(cfg)

    class _StopAfterTwo(TurnManager):
        async def _run_turn(self):
            t = self.turn_number
            self.logger.log_turn_start(t)
            self.logger.log_screenshot(_StubImage(), label=f"turn_{t}")
            return _ga(f"move {t}")

    mgr = _StopAfterTwo(cfg)
    mgr.setup(_StubEmulator(), _StubState(), _StubVision(), logger, None)
    # Stop requested once two turns have fully settled — caught at the top of the
    # loop before turn 3 starts.
    mgr._should_stop = lambda: mgr._last_settled_turn >= 2

    with pytest.raises(KeyboardInterrupt):
        mgr.run_loop(max_turns=50)

    assert mgr._last_settled_turn == 2
    # turn_number never advanced into an aborted turn — the top-of-loop check
    # fired before the increment.
    assert mgr.turn_number == 2
    assert len(mgr.emulator.presses) == 2
