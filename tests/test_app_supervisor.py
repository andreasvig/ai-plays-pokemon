"""Headless tests for AppSupervisor (Plan §P2).

No real mGBA / emulator launches: the prepare/connect/cleanup seam is injected
with fakes. These assert STRUCTURE/behaviour — process_up flips on start,
status reflects busy, shutdown is idempotent, restart re-establishes a handle,
and the turn loop runs against an INJECTED handle without launching a process.
"""

from __future__ import annotations

import pytest

from src.app.supervisor import AppSupervisor


# ───────────────────────────── fakes ─────────────────────────────


class FakeProc:
    """Stand-in for subprocess.Popen — alive until terminate()."""

    def __init__(self) -> None:
        self._alive = True
        self.pid = 4242

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0


class FakeEmu:
    def __init__(self) -> None:
        self.disconnected = False

    def disconnect(self) -> None:
        self.disconnected = True


def make_fake_handle() -> dict:
    return {
        "emu": FakeEmu(),
        "mgba_proc": FakeProc(),
        "caffeinate_proc": None,
        "slot_cfg": {
            "port": 9999,
            "stream_path": "/tmp/fake_stream.png",
            "screenshot_path": "/tmp/fake_shot.png",
            "lua_path": "/tmp/fake.lua",
        },
    }


class FakeSeam:
    """Records prepare/connect/cleanup calls and hands out fake handles.

    Drop-in replacements for run_prepare_phase / run_connect_phase /
    cleanup_handle so AppSupervisor never touches a real process.
    """

    def __init__(self) -> None:
        self.prepared = 0
        self.connected = 0
        self.cleaned = 0
        self.last_handle: dict | None = None

    def prepare(self, config: dict, saves_dir) -> dict:
        self.prepared += 1
        self.last_handle = make_fake_handle()
        return self.last_handle

    def connect(self, handle: dict, timeout: float = 300.0) -> None:
        self.connected += 1

    def cleanup(self, handle: dict) -> None:
        self.cleaned += 1
        # Mirror real cleanup_handle: terminate the process so process_up flips.
        proc = handle.get("mgba_proc")
        if proc is not None:
            proc.terminate()
        emu = handle.get("emu")
        if emu is not None:
            emu.disconnect()


@pytest.fixture
def supervisor(tmp_path):
    seam = FakeSeam()
    sup = AppSupervisor(
        config={"emulator": {"rom_path": "/dev/null"}},
        saves_dir=tmp_path / "saves",
        prepare_fn=seam.prepare,
        connect_fn=seam.connect,
        cleanup_fn=seam.cleanup,
    )
    return sup, seam


# ───────────────────────────── tests ─────────────────────────────


def test_start_brings_process_up_and_connected(supervisor):
    sup, seam = supervisor
    pre = sup.status()
    assert not pre.process_up and not pre.connected

    sup.start()

    st = sup.status()
    assert st.process_up is True
    assert st.connected is True
    assert st.busy is False
    assert seam.prepared == 1 and seam.connected == 1


def test_handle_raises_before_start_and_returns_after(supervisor):
    sup, _ = supervisor
    with pytest.raises(RuntimeError):
        _ = sup.handle

    sup.start()
    assert sup.handle is not None
    assert "emu" in sup.handle


def test_set_busy_reflected_in_status(supervisor):
    sup, _ = supervisor
    sup.start()
    assert sup.status().busy is False

    sup.set_busy(True)
    assert sup.status().busy is True

    sup.set_busy(False)
    assert sup.status().busy is False


def test_shutdown_is_idempotent(supervisor):
    sup, seam = supervisor
    sup.start()

    sup.shutdown()
    st = sup.status()
    assert st.process_up is False
    assert st.connected is False

    # Second call must NOT raise.
    sup.shutdown()
    assert sup.status().process_up is False
    # cleanup not re-invoked once the handle is cleared (no double-terminate).
    assert seam.cleaned == 1


def test_start_is_noop_when_already_up(supervisor):
    sup, seam = supervisor
    sup.start()
    sup.start()  # already up → no relaunch
    assert seam.prepared == 1


def test_restart_reestablishes_handle(supervisor):
    sup, seam = supervisor
    sup.start()
    first = sup.handle

    sup.restart()
    second = sup.handle

    assert sup.status().process_up is True
    assert sup.status().connected is True
    assert second is not first  # a fresh handle
    assert seam.prepared == 2 and seam.cleaned == 1


def test_busy_resets_on_shutdown(supervisor):
    sup, _ = supervisor
    sup.start()
    sup.set_busy(True)
    sup.shutdown()
    assert sup.status().busy is False


# ─────────── injected-handle smoke: no process launch on the loop ───────────


def test_run_single_loop_runs_against_injected_handle_without_launching(
    monkeypatch, tmp_path,
):
    """run_single_loop consumes an externally-owned handle and never launches
    a process. We stub the heavy collaborators and assert subprocess.Popen is
    never called (which is what would spawn mGBA / caffeinate).
    """
    import src.cli.runner as runner

    # Any process launch attempt is a failure for this smoke.
    def _no_popen(*a, **k):  # pragma: no cover - should never run
        raise AssertionError("run_single_loop must not launch a process")

    monkeypatch.setattr(runner.subprocess, "Popen", _no_popen)

    # Stub EmulatorClient so even an accidental construction can't open sockets.
    monkeypatch.setattr(
        runner, "EmulatorClient", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("run_single_loop must not construct an emulator")
        ),
    )

    # Fake RunLogger / StateManager / Vision / TurnManager / dashboard so the
    # loop body runs entirely in-memory.
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class FakeLogger:
        def __init__(self, config):
            self.run_dir = str(run_dir)

        def add_listener(self, fn):
            pass

        def seed_screenshot_id(self):
            pass

        def close(self):
            pass

    class FakeState:
        def __init__(self, path):
            pass

    class FakeVision:
        def __init__(self, config):
            pass

    loop_calls = {"n": 0}

    class FakeTurnMgr:
        task_master_enabled = False
        savepoint_on_crash = False

        def __init__(self, config):
            pass

        def setup(self, *a, **k):
            pass

        def run_loop(self, max_turns):
            loop_calls["n"] += 1

    def fake_start_dashboard(**kwargs):
        class S:
            run_id = "fake"
        return S()

    monkeypatch.setattr(runner, "RunLogger", FakeLogger)
    monkeypatch.setattr(runner, "StateManager", FakeState)
    monkeypatch.setattr(runner, "VisionPipeline", FakeVision)
    monkeypatch.setattr(runner, "TurnManager", FakeTurnMgr)
    monkeypatch.setattr(
        "src.dashboard.start_dashboard", fake_start_dashboard,
    )
    monkeypatch.setattr("src.dashboard.unregister_run", lambda rid: None)
    # Avoid report generation / `open` subprocess on darwin.
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setattr(
        "src.cli.report.load_events", lambda d: [],
    )
    monkeypatch.setattr(
        "src.cli.report.group_events_by_turn", lambda e: {},
    )
    monkeypatch.setattr(
        "src.cli.report.generate_html", lambda *a, **k: "<html></html>",
    )

    handle = make_fake_handle()
    config = {"emulator": {}, "task": {"goal": "x"}, "llm_model": "fake", "ocr": {}}

    result = runner.run_single_loop(
        handle, config, turns=1, snapshot=None, open_browser=False,
    )

    assert loop_calls["n"] == 1          # the loop body ran
    assert result == run_dir             # against the injected handle's run dir
