"""Live spectate and pacing for the stepped backend (plan §4).

The claim this file exists to hold up is the one the plan makes: **the spectate
feed's contract is a PNG file that keeps changing, and nothing in the chain
knows which emulator wrote it.** So the tests below never mention SkyEmu where
the dashboard is concerned — a plain :class:`StreamFileWriter` stands in for the
sampler, and the real ``ScreenStreamer`` and the real ``/ws/screen`` route read
it. If the contract were narrower than "a file whose mtime moves", these would
be the tests that failed.

The second claim is the one that protects the benchmark: **pacing changes a
run's duration and not its content.** ``test_pacing_changes_duration_not_content``
is that claim written down.
"""

import io
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dashboard.event_bridge import EventBridge
from src.dashboard.screen_stream import ScreenStreamer
from src.dashboard.server import RunSession, app, get_registry
from src.dashboard.spectate import (
    RealtimePacer,
    StreamFileWriter,
    attach,
    resolve_pace,
)
from tests.test_skyemu_backend import FakeSkyEmu, _config, _png


def _frame(color=(0, 0, 0), size=(240, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _wait_for(predicate, timeout=5.0, interval=0.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


# ── the contract: a PNG file that keeps changing ────────────────────────────


def test_the_streamer_reads_whatever_wrote_the_file(tmp_path):
    """No part of this asks which emulator produced the bytes, because no part
    of the shipped chain does either."""
    path = tmp_path / "stream.png"
    writer = StreamFileWriter(path)
    streamer = ScreenStreamer(stream_path=str(path))
    streamer.start()
    try:
        first = _frame((10, 20, 30))
        writer(first)
        assert _wait_for(lambda: streamer.get_frame() == first)

        second = _frame((200, 100, 50))
        writer(second)
        assert _wait_for(lambda: streamer.get_frame() == second)
    finally:
        streamer.stop()
    assert writer.frames == 2
    assert writer.errors == 0


def test_the_screen_socket_forwards_the_sampled_frames(tmp_path):
    """The whole chain, end to end, with a file writer standing in for the
    sampler: writer → ScreenStreamer → ``/runs/{id}/ws/screen`` → a client."""
    path = tmp_path / "stream.png"
    writer = StreamFileWriter(path)
    streamer = ScreenStreamer(stream_path=str(path))
    streamer.start()
    get_registry().register(
        RunSession(
            run_id="spectate-contract",
            label="spectate-contract",
            config={"task": {"goal": "x"}},
            bridge=EventBridge(),
            streamer=streamer,
            state_manager=None,
            run_dir=tmp_path,
        )
    )
    try:
        sent = [_frame((i * 20, 0, 0)) for i in range(1, 6)]
        with TestClient(app).websocket_connect(
            "/runs/spectate-contract/ws/screen"
        ) as ws:
            received = []
            for png in sent:
                writer(png)
                # Each frame is a distinct colour, so a dropped one is visible
                # as a missing entry rather than as a duplicate that passes.
                received.append(ws.receive_bytes())
        assert received == sent
    finally:
        get_registry().unregister("spectate-contract")
        streamer.stop()


def test_a_write_never_leaves_a_partial_file_behind(tmp_path):
    """The writer swaps a complete file in; it does not truncate and refill.

    ``ScreenStreamer`` does guard against a torn read by checking for IEND — but
    that guard DROPS the frame it catches, and a reader polling at 125 Hz against
    a 30 Hz writer overlaps constantly. A reader thread watches the file size
    here while frames of two very different sizes alternate: with a truncating
    write it observes intermediate sizes, with an atomic swap it cannot.
    """
    path = tmp_path / "stream.png"
    writer = StreamFileWriter(path)
    small = _frame((0, 0, 0), size=(240, 160))
    big = _frame(size=(1200, 800)) + b"\x00" * 400_000
    complete = {len(small), len(big)}

    observed: set[int] = set()
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            try:
                observed.add(os.stat(path).st_size)
            except OSError:
                pass

    writer(small)
    reader = threading.Thread(target=watch, daemon=True)
    reader.start()
    try:
        for _ in range(40):
            writer(big)
            writer(small)
    finally:
        stop.set()
        reader.join(timeout=5)

    assert observed <= complete, f"saw partial sizes: {sorted(observed - complete)}"
    assert writer.errors == 0
    assert not (tmp_path / "stream.png.part").exists()


# ── pacing ──────────────────────────────────────────────────────────────────


class FakeClock:
    """A clock that only moves when the test or a sleep moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_realtime_pays_only_the_difference_between_game_time_and_host_time():
    """Two frames are 1/30 s of game time. The host's own stepping cost comes
    out of that budget, not on top of it — otherwise a paced run would drift
    slower than the console it is imitating."""
    clock = FakeClock()
    pacer = RealtimePacer(clock=clock.time, sleep=clock.sleep)

    for _ in range(3):
        clock.advance(0.005)   # what the /step + /screen round trip cost
        pacer(2)

    # The FIRST chunk pays a full interval: the deadline is anchored when the
    # pacer is first called, so the round trip that preceded it is before the
    # budget exists. Every chunk after it has its host cost deducted.
    interval = 2 / 60.0
    assert clock.slept == pytest.approx([interval] + [interval - 0.005] * 2)
    assert pacer.slept == pytest.approx(interval + 2 * (interval - 0.005))
    assert pacer.behind == 0


def test_thinking_time_is_forgiven_rather_than_banked():
    """While the model thinks, nothing steps, so the deadline falls arbitrarily
    far behind. Carrying that debt would let the next turn run flat out to catch
    up — the exact opposite of what a spectator asked for."""
    clock = FakeClock()
    pacer = RealtimePacer(clock=clock.time, sleep=clock.sleep)

    clock.advance(0.005)
    pacer(2)
    clock.slept.clear()

    clock.advance(20.0)        # the model thinking
    assert pacer(2) == 0.0     # the overdue chunk itself is not throttled
    assert pacer.behind == 1

    # …and the chunk after it is paced normally again, at full price.
    clock.advance(0.005)
    assert pacer(2) == pytest.approx(2 / 60.0 - 0.005)
    assert clock.slept == pytest.approx([2 / 60.0 - 0.005])


def test_a_pacer_never_sleeps_for_a_step_that_did_not_happen():
    clock = FakeClock()
    pacer = RealtimePacer(clock=clock.time, sleep=clock.sleep)
    assert pacer(0) == 0.0
    assert pacer(-5) == 0.0
    assert clock.slept == []


def test_pace_defaults_to_realtime_and_refuses_a_typo():
    """Reversed 2026-09-19 (was `fast`, decision B). A stepped backend with no
    pacer advances at whatever rate the host can drive HTTP — far above 60 Hz.
    Andreas, on the first Crystal run: "the game is executing incredibly quick,
    could we do normal speed as a standard?" Once every run is spectatable,
    opting IN to watchability is the wrong way round.

    A typo still RAISES rather than falling back: silently meaning something
    would leave a run looking entirely normal while running at the wrong speed,
    and that is now true in both directions."""
    assert resolve_pace({"emulator": {}}) == "realtime"
    assert resolve_pace({}) == "realtime"
    assert resolve_pace({"emulator": {"pace": None}}) == "realtime"
    # The opt-out still works, and is the half that would break silently if the
    # flip had been done by deleting the branch rather than moving the default.
    assert resolve_pace({"emulator": {"pace": "fast"}}) == "fast"
    assert resolve_pace({"emulator": {"pace": "FAST"}}) == "fast"
    assert resolve_pace({"emulator": {"pace": "REALTIME"}}) == "realtime"
    with pytest.raises(ValueError, match="real-time"):
        resolve_pace({"emulator": {"pace": "real-time"}})


def test_attach_records_the_pace_it_resolved(tmp_path):
    """`duration_s` and `avg_s_per_turn` are wall clock, so they mean different
    things under the two paces. A run that leaves the key absent forces a reader
    to guess which default was in force on the day it ran — and that default has
    already changed once."""
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)
    config = _config()
    config["emulator"].pop("pace", None)

    feed = attach(emu, config, tmp_path / "run")
    try:
        assert config["emulator"]["pace"] == "realtime"
        assert feed.pace == "realtime"
    finally:
        feed.detach()

    # And an explicit choice is recorded as itself, not overwritten by the
    # default — otherwise the stamp would say the same thing for every run.
    config2 = _config()
    config2["emulator"]["pace"] = "fast"
    feed2 = attach(emu, config2, tmp_path / "run-2")
    try:
        assert config2["emulator"]["pace"] == "fast"
        assert feed2.pacer is None, "fast must attach no pacer"
    finally:
        feed2.detach()


def test_the_default_gives_a_run_a_pacer_without_being_asked(tmp_path):
    """The behavioural half of the flip. resolve_pace returning 'realtime' is
    worth nothing if attach does not then hook a RealtimePacer onto the
    emulator — that is the object that actually slows the game down."""
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)
    config = _config()
    config["emulator"].pop("pace", None)

    feed = attach(emu, config, tmp_path / "run")
    try:
        assert isinstance(emu.pacer, RealtimePacer)
    finally:
        feed.detach()


# ── the step loop ───────────────────────────────────────────────────────────


def test_a_pacer_alone_chunks_the_step():
    """A throttle applied once to a 300-frame `wait` would stall five seconds in
    one lump, which is a freeze and not real time. So the pacer chunks the step
    even with no sampler attached — and nothing is captured, because nobody
    asked for frames."""
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)
    paced: list[int] = []
    emu.pacer = paced.append
    emu.sample_every = 2
    emu.step(10)
    assert emu.steps() == [2, 2, 2, 2, 2]
    assert paced == [2, 2, 2, 2, 2]
    assert [path for path, _ in emu.calls if path == "/screen"] == []


def test_pacing_changes_duration_not_content():
    """The property that lets a spectator exist at all: the emulator is frozen
    between calls, so a pacer can only decide WHEN the next /step is sent. The
    frames the game sees and the inputs it sees are identical either way."""
    unpaced = FakeSkyEmu(_config(), screens=[_png()] * 400)
    paced = FakeSkyEmu(_config(), screens=[_png()] * 400)
    paced.pacer = lambda frames: None

    for emu in (unpaced, paced):
        emu.press_button_list(["U", "A", "WAIT"])

    assert sum(paced.steps()) == sum(unpaced.steps())
    assert paced.inputs() == unpaced.inputs()
    # The only difference is the granularity of the requests that carried it.
    assert len(paced.steps()) > len(unpaced.steps())


def test_a_hook_that_raises_cannot_end_a_run():
    """A spectator and a recorder observe the benchmark; they never participate
    in it."""
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)

    def explode(*_args):
        raise RuntimeError("the dashboard went away")

    emu.sampler = explode
    emu.pacer = explode
    emu.step(6)
    assert emu.steps() == [2, 2, 2]


# ── attach / detach ─────────────────────────────────────────────────────────


def test_attach_points_the_dashboard_at_this_runs_file(tmp_path):
    """`start_dashboard` builds the run's ScreenStreamer from
    `config['paths']['stream']`, and sequential runs share one config dict — so
    the path is re-stamped per run. Run 2 inheriting run 1's file would be a
    live feed of a finished game."""
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)
    config = _config()
    # Pinned, not inherited. This test is about the stream PATH; it used to read
    # `assert emu.pacer is None  # fast is the default` as an aside, which made
    # it fail the day the default moved (2026-09-19) for a reason that has
    # nothing to do with what it checks. It also keeps the steps below off the
    # wall clock.
    config["emulator"]["pace"] = "fast"

    first = attach(emu, config, tmp_path / "run-1")
    assert config["paths"]["stream"] == str(tmp_path / "run-1" / "stream.png")
    assert emu.sampler is first.writer
    assert emu.pacer is None
    emu.step(2)
    assert first.frames == 2       # the primed frame, plus one step
    assert (tmp_path / "run-1" / "stream.png").is_file()
    first.detach()

    second = attach(emu, config, tmp_path / "run-2")
    assert config["paths"]["stream"] == str(tmp_path / "run-2" / "stream.png")
    emu.step(2)
    assert second.frames == 2
    # run 1's feed is closed, not merely replaced: its file is the final frame.
    assert first.frames == 2
    second.detach()


def test_the_feed_shows_a_frame_before_anything_has_stepped(tmp_path):
    """The frozen emulator's own gap: a run does not step until the model has
    answered, so without a primed frame the feed's file does not exist for the
    first ten-odd seconds. The recorder needs one to measure the page's game
    rectangle, and a spectator who opens the tab first would see nothing."""
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)
    config = _config()

    feed = attach(emu, config, tmp_path / "run")
    assert feed.frames == 1
    assert (tmp_path / "run" / "stream.png").is_file()
    # One /screen, and not a single /step: priming must not advance the game.
    assert [path for path, _ in emu.calls] == ["/screen"]
    assert emu.frames_stepped == 0
    feed.detach()


def test_detach_stops_the_feed_and_unhooks_the_emulator(tmp_path):
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)
    config = _config()
    config["emulator"]["pace"] = "realtime"

    feed = attach(emu, config, tmp_path / "run")
    assert isinstance(emu.pacer, RealtimePacer)
    emu.step(2)
    assert feed.frames == 2

    feed.detach()
    assert emu.sampler is None
    assert emu.pacer is None
    feed.writer(_frame((1, 2, 3)))
    assert feed.frames == 2
