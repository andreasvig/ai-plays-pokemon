"""Real-path tests for the `pokemon app` stale-process reclaim (Round 8.1).

We don't stub the reclaim logic — we spawn a REAL child process that holds a
real LISTEN socket on a real free port, and assert the helper detects it, the
--no-reclaim path refuses, and the default path actually SIGKILLs it and frees
the port. Only the *kind* of victim (a tiny python listener) stands in for a
leftover uvicorn; the detection (lsof) + kill (os.kill) path is the real one.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from src.cli import app as app_mod


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn_listener(port: int) -> subprocess.Popen:
    """A child that binds + listens on `port` and sleeps, like a stale server."""
    code = (
        "import socket,time;"
        "s=socket.socket();"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('127.0.0.1',{port}));"
        "s.listen();"
        "time.sleep(30)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    # wait until it's actually listening
    for _ in range(50):
        if app_mod._pids_listening_on(port):
            break
        time.sleep(0.05)
    return proc


@pytest.fixture
def no_process_scan(monkeypatch):
    """Neutralize the global mGBA/caffeinate pgrep scan so tests only exercise the
    port logic on ports WE control — deterministic, and never kills a real mGBA
    that happens to be running on the dev machine."""
    monkeypatch.setattr(app_mod, "_pids_matching", lambda *a, **k: [])


def test_no_stale_is_a_noop(no_process_scan):
    port = _free_port()
    assert app_mod._pids_listening_on(port) == []
    assert app_mod._find_stale_processes(port, _free_port()) == {}
    # nothing to do → proceeds, regardless of do_kill
    assert app_mod._reclaim_stale_processes(port, _free_port(), do_kill=True) is True
    assert app_mod._reclaim_stale_processes(port, _free_port(), do_kill=False) is True


def test_detects_stale_listener(no_process_scan):
    port = _free_port()
    proc = _spawn_listener(port)
    try:
        pids = app_mod._pids_listening_on(port)
        assert proc.pid in pids
        stale = app_mod._find_stale_processes(port, _free_port())
        assert proc.pid in stale
        assert f":{port}" in stale[proc.pid]
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_no_reclaim_refuses_without_killing(no_process_scan, capsys):
    port = _free_port()
    proc = _spawn_listener(port)
    try:
        # do_kill=False → returns False (caller aborts) and the victim survives
        ok = app_mod._reclaim_stale_processes(port, _free_port(), do_kill=False)
        assert ok is False
        assert proc.poll() is None  # still alive
        out = capsys.readouterr().out
        assert "stale processes" in out.lower()
        assert "pkill" in out  # prints the manual fix
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_reclaim_kills_stale_and_frees_port(no_process_scan):
    port = _free_port()
    proc = _spawn_listener(port)
    try:
        assert app_mod._pids_listening_on(port)  # confirm it's held first
        ok = app_mod._reclaim_stale_processes(port, _free_port(), do_kill=True)
        assert ok is True
        # the victim is dead and the port is free
        assert proc.wait(timeout=5) is not None
        assert app_mod._pids_listening_on(port) == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_self_pid_never_in_stale_match():
    # pgrep -f matching our own interpreter must exclude this process
    me = app_mod.os.getpid()
    assert me not in app_mod._pids_matching("python", ignore_case=True)


def test_cli_parses_no_reclaim_flag():
    # the flag exists and defaults to off (reclaim on)
    r = subprocess.run(
        [sys.executable, "-m", "src.cli.app", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert "--no-reclaim" in r.stdout
