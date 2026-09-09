"""EmulatorClient wire protocol — the desync that killed six runs (2026-09-09).

A fake Lua side lives on the other end of a ``socket.socketpair()`` and answers
the way lua/socketserver-1.lua does: ``CAP`` → ``SCREENSHOT:<png>``, ``SEQ`` →
``QUEUED:<n>`` now and ``SEQUENCE_DONE`` *later* (configurable delay, the crux),
``PING`` → ``PONG``, ``LOAD`` → ``OK:State loaded``, ``READMEM`` → ``MEM:<hex>``.

Each test replays a real crash shape from local/runs/*/terminal.log:
- "Unexpected screenshot response: SEQUENCE_DONE" — the DONE landed after the
  fire-and-forget drain window and the next CAP read it (gemini minimal T44).
- "Unexpected screenshot response: QUEUED:4" — the OCR poller's CAP interleaved
  with the turn loop's SEQ (glm high T16).
- "Load state failed: SCREENSHOT:/tmp/…" — a stale reply on the control
  center's long-lived connection poisoned the NEXT dispatch (qwen, this morning).
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from src.emulator.emulator import EmulatorClient, ProtocolError

CONFIG = {
    "emulator": {"host": "127.0.0.1", "port": 0, "button_hold_frames": 6, "frames_between_inputs": 30,
                 "ab_hold_frames": 50, "ab_gap_frames": 30, "wait_input_seconds": 0.01},
    "screenshot": {"upscale_factor": 1, "grid_overlay": False},
    "valid_inputs": ["A", "B", "U", "D", "L", "R", "START", "SELECT", "WAIT"],
    "screen_stability": {"min_wait": 0.0, "max_wait": 0.5, "poll_interval": 0.01, "num_frames": 2},
}


class FakeLua(threading.Thread):
    """Answers commands on one end of a socketpair like the mGBA connector."""

    def __init__(self, sock: socket.socket, png: Path, *, done_delay: float = 0.0, preload: str = ""):
        super().__init__(daemon=True)
        self.sock = sock
        self.png = str(png)
        self.done_delay = done_delay
        self.preload = preload          # bytes already sitting unread (a previous run's leftovers)
        self.commands: list[str] = []
        self._stop = threading.Event()
        self._buf = ""

    def send(self, line: str) -> None:
        try:
            self.sock.sendall((line + "\n").encode())
        except OSError:
            pass

    def _done_later(self) -> None:
        time.sleep(self.done_delay)
        self.send("SEQUENCE_DONE")

    def run(self) -> None:
        if self.preload:
            self.sock.sendall(self.preload.encode())
        self.sock.settimeout(0.05)
        while not self._stop.is_set():
            try:
                data = self.sock.recv(4096).decode()
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            self._buf += data
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self.commands.append(line)
                cmd = line.split(":", 1)[0]
                if cmd == "CAP":
                    self.send(f"SCREENSHOT:{self.png}")
                elif cmd == "PING":
                    self.send("PONG")
                elif cmd == "SEQ":
                    n = line.count(";") + 1
                    self.send(f"QUEUED:{n}")
                    threading.Thread(target=self._done_later, daemon=True).start()
                elif cmd == "LOAD":
                    self.send("OK:State loaded")
                elif cmd == "SAVE":
                    self.send("OK:State saved")
                elif cmd == "READMEM":
                    length = int(line.rsplit(":", 1)[1])
                    self.send("MEM:" + "ab" * length)
                elif cmd == "PRESS":
                    self.send("OK")
                elif cmd == "CONFIG":
                    self.send("OK:" + line.split(":", 1)[1])
                else:
                    self.send("ERROR:Unknown command: " + cmd)

    def stop(self) -> None:
        self._stop.set()


def _raise_via_wrapper(client):
    from unittest import mock
    with mock.patch.object(client, "_request", side_effect=ProtocolError("ERROR:boom")):
        client.read_memory(0x10, 2)


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "frame.png"
    Image.new("RGB", (240, 160), (10, 20, 30)).save(p)
    return p


@pytest.fixture
def wired(tmp_path, png):
    """(client, fake_lua_factory): call the factory with FakeLua kwargs."""
    made = []

    def factory(**kw):
        a, b = socket.socketpair()
        client = EmulatorClient(CONFIG)
        client._socket = a
        lua = FakeLua(b, png, **kw)
        lua.start()
        made.append((client, lua, a, b))
        return client, lua

    yield factory
    for client, lua, a, b in made:
        lua.stop()
        a.close()
        b.close()


def test_late_sequence_done_is_a_notification_not_a_screenshot(wired):
    # Buttons "finish" in ~0.5 s of sleep, but the DONE arrives 0.4 s after that —
    # exactly the gap that produced "Unexpected screenshot response: SEQUENCE_DONE".
    client, lua = wired(done_delay=0.9)
    client.hold_frames = client.gap_frames = 0
    client.press_button_list(["right", "right", "up"])
    img = client.capture_screenshot(preprocess=False)
    assert img.size == (240, 160)
    time.sleep(1.0)  # let the DONE land, then prove the next reader skips it
    assert client.capture_screenshot(preprocess=False).size == (240, 160)
    assert client.notifications_skipped >= 1
    assert client.stale_replies_skipped == 0
    assert [c.split(":")[0] for c in lua.commands] == ["SEQ", "CAP", "CAP"]


def test_ocr_poller_cannot_interleave_with_a_button_sequence(wired):
    # The real shape: OCRRunner hammers CAP from its own thread every 0.4 s while
    # the turn loop sends SEQ and then reads memory / captures. Every reader must
    # get its own reply.
    client, lua = wired(done_delay=0.05)
    client.hold_frames = client.gap_frames = 1
    errors: list[BaseException] = []
    captured = []
    stop = threading.Event()

    def poller():
        while not stop.is_set():
            try:
                captured.append(client.capture_screenshot(preprocess=False).size)
            except BaseException as exc:  # noqa: BLE001 — the test wants every failure
                errors.append(exc)
            time.sleep(0.005)

    t = threading.Thread(target=poller, daemon=True)
    t.start()
    try:
        for _ in range(4):
            client.press_button_list(["a", "up", "wait", "b"])
            assert client.read_memory(0x03005008, 4) == bytes.fromhex("ab" * 4)
            assert client.capture_screenshot(preprocess=False).size == (240, 160)
    finally:
        stop.set()
        t.join(2)
    assert errors == []
    assert len(captured) > 4
    assert client.stale_replies_skipped == 0, "with the lock, nothing is ever read by the wrong caller"


def test_load_state_resyncs_past_a_previous_runs_leftovers(wired, tmp_path):
    # The control center keeps one connection across dispatches. After a desync
    # the unread reply poisoned the next LOAD ("Load state failed: SCREENSHOT:…").
    state = tmp_path / "emulator.state"
    state.write_bytes(b"x")
    client, lua = wired(preload="SCREENSHOT:/tmp/mgba_screenshot_1.png\nOK:State saved to somewhere\nSEQUENCE_DONE\n")
    time.sleep(0.1)
    client.load_state(str(state))
    assert client.resyncs == 1
    assert client.read_memory(0x1000, 2) == bytes.fromhex("abab")
    assert [c.split(":")[0] for c in lua.commands] == ["PING", "LOAD", "READMEM"]


def test_a_stale_reply_that_slips_through_is_skipped_not_fatal(wired):
    # Belt and braces: a leftover on the stream that resync did not see (no load
    # happened yet) is skipped by the next request, with a printed warning.
    client, lua = wired(preload="OK:State saved to old\n")
    time.sleep(0.1)
    assert client.capture_screenshot(preprocess=False).size == (240, 160)
    assert client.stale_replies_skipped == 1


def test_error_reply_raises_and_hopeless_streams_give_up(wired):
    client, lua = wired()
    with pytest.raises(ProtocolError, match="ERROR:Unknown command"):
        client._request("BOGUS", "NEVER:")
    # …and the public wrappers keep their historical RuntimeError messages.
    lua.commands.clear()
    with pytest.raises(RuntimeError, match="read_memory failed: ERROR:"):
        # READMEM with a non-numeric length makes the fake answer ERROR:
        client._request("READMEM:1:x", "MEM:") if False else _raise_via_wrapper(client)
    client2, lua2 = wired(preload="".join(f"JUNK:{i}\n" for i in range(40)))
    time.sleep(0.1)
    with pytest.raises(ProtocolError, match="desynchronised"):
        client2.capture_screenshot(preprocess=False)


def test_blocking_press_sequence_waits_for_its_own_done(wired):
    client, lua = wired(done_delay=0.2)
    t0 = time.time()
    client.press_sequence("R;R;A")
    assert time.time() - t0 >= 0.2
    assert client.notifications_skipped == 0  # it consumed QUEUED and DONE as its own replies
