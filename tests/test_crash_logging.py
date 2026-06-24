"""Crash-logging surfaces: the crash banner, mGBA-death diagnostics, heartbeat.

These make a livestreamed run's failure legible in the terminal — what broke,
where, and whether the game (mGBA) died vs. the agent — instead of a buried
traceback or dead silence on a hang.
"""
import asyncio
import tempfile
import types
from pathlib import Path

from src.agent.turn import _emit_heartbeat
from src.cli.runner import _print_crash_banner


def _turn_mgr():
    return types.SimpleNamespace(turn_number=42, _last_settled_turn=41)


def _config():
    return {
        "_llm_alias": "gemma-4-31b(thinking)",
        "llm_model": "google/gemma-4-31b-it",
        "task_master_model": "google/gemini-3.5-flash",
        "_task_master_alias": "gemini-3.5-flash(medium)",
    }


class _Proc:
    def __init__(self, rc):
        self._rc = rc

    def poll(self):
        return self._rc


def test_crash_banner_shows_turn_models_and_paths(capsys):
    _print_crash_banner(
        RuntimeError("boom"), _turn_mgr(), _config(), "/tmp/runs/xyz",
        {"mgba_proc": _Proc(None)},  # still alive
    )
    out = capsys.readouterr().out
    assert "RUN CRASHED" in out
    assert "Turn:        42" in out
    assert "last settled: 41" in out
    assert "RuntimeError: boom" in out
    assert "gemma-4-31b(thinking)" in out  # Player
    assert "gemini-3.5-flash(medium)" in out  # TaskMaster
    assert "/tmp/runs/xyz/events.jsonl" in out
    # mGBA alive → the fault is attributed to the agent side, no log tail.
    assert "still running" in out


def test_crash_banner_surfaces_mgba_death_and_log_tail(capsys):
    log = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
    log.write("mGBA: info: booting\nmGBA: fatal: ROM load failed\nsegfault\n")
    log.close()
    try:
        _print_crash_banner(
            RuntimeError("socket dropped"), _turn_mgr(), _config(), "/tmp/runs/xyz",
            {"mgba_proc": _Proc(-11), "mgba_log_path": log.name},  # died
        )
    finally:
        Path(log.name).unlink(missing_ok=True)
    out = capsys.readouterr().out
    assert "DIED (exit code -11)" in out
    assert "emulator/game crashed" in out
    assert "ROM load failed" in out  # the captured mGBA output is surfaced


def test_crash_banner_without_taskmaster_omits_tm_line(capsys):
    cfg = {"_llm_alias": "gpt-5.5(medium)", "llm_model": "openai/gpt-5.5"}
    _print_crash_banner(ValueError("x"), _turn_mgr(), cfg, "/tmp/r", {})
    out = capsys.readouterr().out
    assert "Player:" in out
    assert "TaskMaster:" not in out


def test_heartbeat_emits_then_stops_on_cancel(capsys):
    async def scenario():
        hb = asyncio.ensure_future(_emit_heartbeat("[Turn 1] LLM in flight", interval_s=0.02))
        await asyncio.sleep(0.07)  # ~3 ticks
        hb.cancel()
        await asyncio.sleep(0.05)  # prove no further output after cancel

    asyncio.run(scenario())
    out = capsys.readouterr().out
    ticks = out.count("LLM in flight")
    assert ticks >= 2  # heartbeat fired while in flight
    # Cancellation is clean: re-running with an immediate cancel emits nothing.

    async def immediate():
        hb = asyncio.ensure_future(_emit_heartbeat("x", interval_s=10))
        hb.cancel()
        await asyncio.sleep(0)

    asyncio.run(immediate())
    assert "x …" not in capsys.readouterr().out
