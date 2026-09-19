"""Two things reported the same afternoon about watching a v2 run.

The console keeps running while the model thinks, and a DS frame is laid out for
a human rather than stacked. Both are about the SPECTATOR's experience of a run
and neither may change what the model is shown — which is why they share a file:
the failure that matters for both is the transform or the thread leaking into
the benchmark.

Reported 2026-09-19, watching a v2 run: *"i dont think the game should be
paused, while we wait for inputs, it should just run."*

A stepped backend is frozen between ``/step`` calls — that is the property that
makes a v2 run reproducible, and it is also why the spectate feed and the MP4
recorder show a **still photograph** for the ten-plus seconds of every LLM call.
mGBA never had this to lose: its core runs at 60 Hz whoever is or is not asking
it anything, and the whole v1 board was played that way.

``spectate.FreeRunner`` is a worker thread that advances the machine while the
turn loop waits for a model, and stops before the turn loop touches it again.
The tests below are about the three things that can go wrong with that:

1. it does not run when it should (or does not stop when it must — a thread
   still stepping when ``press_button_list`` starts interleaves somebody else's
   frames into a press);
2. it double-paces, because both it and the spectate pacer think they own the
   clock — which would halve the frame rate of exactly the feed it exists for;
3. it takes the emulator hostage, so the turn loop cannot have it back.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dashboard.spectate import (
    CONSOLE_FPS,
    DEFAULT_FREE_RUN,
    FREE_RUN_CHUNK,
    FreeRunner,
    RealtimePacer,
    attach,
    resolve_free_run,
)
from tests.test_skyemu_backend import FakeSkyEmu, _config


# ── what the config says ────────────────────────────────────────────────────


def test_free_run_is_on_by_default():
    """The default IS the feature. A run that has to ask for its emulator to
    keep running is a run nobody will ask, and the frozen behaviour is what he
    reported as broken."""
    assert DEFAULT_FREE_RUN is True
    assert resolve_free_run({}) is True
    assert resolve_free_run({"emulator": {}}) is True
    assert resolve_free_run({"emulator": {"free_run": None}}) is True


def test_free_run_can_be_turned_off():
    """The opt-out has to work from a config file, where the value arrives as
    whatever YAML made of it — and YAML gives you ``False`` for ``false`` but a
    hand-edited quoted ``"false"`` is a string, which ``bool()`` calls True."""
    assert resolve_free_run({"emulator": {"free_run": False}}) is False
    assert resolve_free_run({"emulator": {"free_run": "false"}}) is False
    assert resolve_free_run({"emulator": {"free_run": "no"}}) is False
    assert resolve_free_run({"emulator": {"free_run": "off"}}) is False
    assert resolve_free_run({"emulator": {"free_run": True}}) is True
    assert resolve_free_run({"emulator": {"free_run": "yes"}}) is True


def test_an_unreadable_free_run_value_raises():
    """Same discipline as ``resolve_pace``: a typo that silently meant "on"
    would leave a determinism measurement quietly non-deterministic, and the run
    would look normal the whole way through."""
    with pytest.raises(ValueError, match="free_run"):
        resolve_free_run({"emulator": {"free_run": "sometimes"}})


# ── it runs, and it stops ───────────────────────────────────────────────────


class _Recorder:
    """Stands in for an emulator: counts frames, holds a lock like the real one."""

    def __init__(self, *, pacer=None, fail_after=None):
        self.lock = threading.RLock()
        self.frames = 0
        self.chunks = 0
        self.pacer = pacer
        self.fail_after = fail_after

    def step(self, frames):
        with self.lock:
            if self.fail_after is not None and self.chunks >= self.fail_after:
                raise RuntimeError("emulator went away")
            self.chunks += 1
            self.frames += frames
            if self.pacer is not None:
                self.pacer(frames)


def test_the_machine_advances_while_nobody_is_driving():
    """THE feature. Without a runner attached this count is zero for the whole
    length of a model call, which is what a spectator was watching."""
    emu = _Recorder()
    with FreeRunner(emu, chunk=2):
        time.sleep(0.25)
    assert emu.frames > 0, "the console did not advance while the model thought"


def test_it_stops_before_the_caller_gets_the_emulator_back():
    """Non-negotiable. The turn loop presses buttons the instant the context
    manager returns; a worker still stepping would interleave its frames into
    the middle of a press, and a press on SkyEmu is four requests."""
    emu = _Recorder()
    runner = FreeRunner(emu, chunk=2)
    with runner:
        time.sleep(0.1)
    assert not runner.running
    settled = emu.frames
    time.sleep(0.15)
    assert emu.frames == settled, "the free runner kept stepping after it stopped"


def test_start_and_stop_are_idempotent():
    """The turn loop calls stop() from a ``finally`` that can be reached twice
    on the cancellation path, and start() is reached again on the next turn."""
    emu = _Recorder()
    runner = FreeRunner(emu, chunk=2)
    runner.stop()                      # never started
    runner.start()
    runner.start()                     # second start must not spawn a twin
    time.sleep(0.08)
    runner.stop()
    runner.stop()
    settled = emu.frames
    time.sleep(0.1)
    assert emu.frames == settled


def test_a_free_running_step_that_raises_never_ends_the_run():
    """A spectator is an observer of the benchmark, never a participant. The
    thread records the failure and leaves; the run carries on frozen, which is
    the behaviour it had before this existed."""
    emu = _Recorder(fail_after=1)
    runner = FreeRunner(emu, chunk=2)
    with runner:
        time.sleep(0.2)
    assert runner.errors == 1
    assert "emulator went away" in (runner.last_error or "")
    assert not runner.running


# ── it does not double-pace ─────────────────────────────────────────────────


def _chunks_in(seconds, *, pacer):
    emu = _Recorder(pacer=pacer)
    with FreeRunner(emu, chunk=FREE_RUN_CHUNK):
        time.sleep(seconds)
    return emu.chunks


def test_attaching_a_pacer_does_not_change_the_free_run_rate():
    """Two things know the console's clock — ``emu.pacer`` and this runner — and
    a run has one or the other depending on ``--pace``. The feed must look the
    same either way.

    **What makes it safe is that both are DEADLINE-based, not sleep-based**, so
    they compose rather than add: whichever waits first leaves the other's
    deadline in the past, and a deadline in the past re-anchors without waiting.
    Verified by mutation — replacing the runner's deadline with a plain
    ``sleep(chunk/fps)`` does NOT halve the paced rate, because the pacer then
    stops sleeping instead. So this test is not a guard against double-pacing;
    it is the assertion that the two configurations produce one frame rate, with
    a bound on each side so a runner that stalled or ran away still fails.
    (``test_an_unpaced_emulator_is_still_held_to_the_console_clock`` is the one
    that bites when the pacing is removed altogether.)
    """
    window = 0.4
    expected = window * CONSOLE_FPS / FREE_RUN_CHUNK          # 12 chunks
    unpaced = _chunks_in(window, pacer=None)                   # runner's own clock
    paced = _chunks_in(window, pacer=RealtimePacer())          # step's clock

    for name, count in (("unpaced", unpaced), ("paced", paced)):
        assert expected * 0.5 <= count <= expected * 2.0, (
            f"{name} free-run advanced {count} chunks in {window}s where the "
            f"console's own clock gives {expected:.0f}"
        )


def test_an_unpaced_emulator_is_still_held_to_the_console_clock():
    """``pace: fast`` is about not spending wall clock on GAMEPLAY. Idle frames
    cost no wall clock either way, so free-running them at host speed would just
    burn game time — minutes of in-game clock per model call — for nothing."""
    emu = _Recorder()
    with FreeRunner(emu, chunk=FREE_RUN_CHUNK, fps=CONSOLE_FPS):
        time.sleep(0.3)
    ceiling = 0.3 * CONSOLE_FPS * 3
    assert emu.frames < ceiling, (
        f"free-running advanced {emu.frames} frames in 0.3s, far past the "
        f"console's own {0.3 * CONSOLE_FPS:.0f}"
    )


# ── it does not take the emulator hostage ───────────────────────────────────


def test_the_driver_can_take_the_machine_back_midflight():
    """The lock is released between chunks, not held across the whole call.

    Held, this would be a deadlock dressed as a feature: the turn loop's
    screenshot, the referee's memory poll and the dashboard's status read all go
    through the same lock, and none of them could ever run.
    """
    emu = _Recorder()
    got = threading.Event()

    def driver():
        with emu.lock:
            got.set()

    with FreeRunner(emu, chunk=FREE_RUN_CHUNK):
        time.sleep(0.05)
        t = threading.Thread(target=driver)
        t.start()
        assert got.wait(timeout=2.0), "a driver thread could not get the emulator back"
        t.join()


def test_a_real_backend_steps_whole_chunks_under_its_own_lock(tmp_path):
    """Against the real client, not a stand-in: ``FreeRunner`` drives the public
    ``step``, which is the only entry point that takes the emulator's lock per
    chunk. Asserting the chunk SIZE matters — the sampler and the recorder both
    ride on ``_step``'s chunk boundary, so a runner stepping 600 frames in one
    call would publish one frame per model call and nothing in between."""
    emu = FakeSkyEmu(_config())
    with FreeRunner(emu, chunk=FREE_RUN_CHUNK):
        time.sleep(0.2)
    steps = emu.steps()
    assert steps, "the real backend was never stepped"
    assert set(steps) == {FREE_RUN_CHUNK}
    assert emu.frames_stepped == len(steps) * FREE_RUN_CHUNK


def test_free_running_frames_reach_the_spectate_feed(tmp_path):
    """The point of the whole thing: the live feed keeps moving. The sampler
    hangs off ``_step``, so this holds only while the runner goes through
    ``step`` rather than talking to the wire itself."""
    emu = FakeSkyEmu(_config())
    config = {"emulator": {"type": "skyemu"}}
    feed = attach(emu, config, tmp_path)
    primed = feed.frames
    try:
        assert feed.free_runner is not None
        with feed.free_runner:
            time.sleep(0.25)
        assert feed.frames > primed, "no new frames were published while idle"
    finally:
        feed.detach()


# ── how it reaches the emulator, and how it leaves ──────────────────────────


def test_attach_hangs_a_runner_on_the_emulator(tmp_path):
    """Same pattern as ``sampler`` and ``pacer``, and for the same reason: the
    turn loop holds an emulator, not a spectate feed."""
    emu = FakeSkyEmu(_config())
    config = {"emulator": {"type": "skyemu"}}
    feed = attach(emu, config, tmp_path)
    try:
        assert isinstance(emu.free_runner, FreeRunner)
        assert feed.free_runner is emu.free_runner
        assert config["emulator"]["free_run"] is True   # the RESOLVED value, recorded
    finally:
        feed.detach()


def test_attach_hangs_nothing_when_free_run_is_off(tmp_path):
    """Off means the attribute is None, not a runner that declines to run — the
    turn loop's check is ``is None``, and a present-but-inert runner would make
    "frozen" a property of the runner instead of a property of the run."""
    emu = FakeSkyEmu(_config())
    config = {"emulator": {"type": "skyemu", "free_run": False}}
    feed = attach(emu, config, tmp_path)
    try:
        assert emu.free_runner is None
        assert feed.free_runner is None
        assert config["emulator"]["free_run"] is False
    finally:
        feed.detach()


def test_detach_stops_the_runner(tmp_path):
    """Sequential runs share one backend process. A runner left alive after its
    run ends advances the NEXT run's start state before it has pressed
    anything — the same class of bug as a feed left writing into a finished
    run's directory, one layer worse because it moves the game."""
    emu = FakeSkyEmu(_config())
    feed = attach(emu, {"emulator": {"type": "skyemu"}}, tmp_path)
    runner = feed.free_runner
    runner.start()
    time.sleep(0.05)
    feed.detach()
    assert not runner.running
    assert emu.free_runner is None
    settled = emu.frames_stepped
    time.sleep(0.15)
    assert emu.frames_stepped == settled


# ── where the turn loop uses it ─────────────────────────────────────────────


def test_the_turn_loop_free_runs_around_every_model_call():
    """Three waits, not one. The player turn is the obvious one; TaskMaster's
    cold start and its handoff are the same kind of wait, and a run that froze
    for those would stutter at exactly the moments a watcher is most likely to
    be looking (the opening, and every task boundary)."""
    import inspect
    from src.agent import turn as turn_mod

    loop = inspect.getsource(turn_mod.TurnManager._run_loop_async)
    assert loop.count("with self._free_running():") == 3, (
        "a model call in the run loop is not wrapped in _free_running"
    )
    for call in (
        "await self._cold_start()",
        "result = await self._run_turn_or_stop()",
        "await self._handle_handoff(result, handoff)",
    ):
        assert call in loop


def test_a_backend_with_no_runner_is_a_plain_passthrough():
    """mGBA. Its core is already running at 60 Hz, so there is nothing to start
    — and ``_free_running`` must not fabricate one, must not raise, and must not
    change the value the block produces."""
    from src.agent.turn import TurnManager

    class _Emu:
        pass

    mgr = TurnManager.__new__(TurnManager)
    mgr.emulator = _Emu()
    with mgr._free_running() as runner:
        assert runner is None


def test_the_runner_is_stopped_even_when_the_turn_blows_up():
    """The cancellation path. ``_run_turn_or_stop`` raises KeyboardInterrupt to
    abort a wedged turn, and the run then takes a crash savepoint — with a
    worker still stepping, that savepoint is of a machine nobody is holding
    still."""
    from src.agent.turn import TurnManager

    emu = _Recorder()
    runner = FreeRunner(emu, chunk=2)

    class _Emu:
        free_runner = runner

    mgr = TurnManager.__new__(TurnManager)
    mgr.emulator = _Emu()
    with pytest.raises(KeyboardInterrupt):
        with mgr._free_running():
            time.sleep(0.05)
            raise KeyboardInterrupt
    assert not runner.running


# ── and the second thing he asked for the same day: the DS layout ───────────
#
# *"for ds games the watching experience is not that good ... change the
# 'scale'/ratio between upper and lower screen, such that the upper screen is at
# least twice as wide?"*
#
# These live here rather than in test_spectate.py because the risk they guard is
# not about the feed: it is that a re-laid-out frame reaches the MODEL, whose
# prompt describes a vertical stack and whose taps are mapped against one.


def _nds_png(top=(200, 30, 30), bottom=(30, 30, 200)) -> bytes:
    from PIL import Image
    import io as _io

    img = Image.new("RGB", (256, 384), bottom)
    img.paste(Image.new("RGB", (256, 192), top), (0, 0))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _size(png: bytes):
    from PIL import Image
    import io as _io

    return Image.open(_io.BytesIO(png)).size


def test_the_spectate_frame_puts_the_top_screen_beside_the_touch_screen():
    """The ask, measured: the upper screen is twice the lower one's width."""
    from src.emulator.backends.frame import NDS_SCREEN, spectate_frame

    out = spectate_frame(_nds_png())
    assert out is not None
    w, h = _size(out)
    assert (w, h) == (NDS_SCREEN[0] * 3, NDS_SCREEN[1] * 2)   # 768x384, 2:1
    # top occupies [0, 512), touch [512, 768) — 2x the width, and not stacked.
    assert w - NDS_SCREEN[0] == 2 * NDS_SCREEN[0]


def test_both_screens_survive_the_relayout():
    """A layout that dropped or duplicated a screen would still be 768x384 and
    would still be "twice as wide". Sample a pixel from each region instead."""
    from PIL import Image
    import io as _io
    from src.emulator.backends.frame import spectate_frame

    img = Image.open(_io.BytesIO(spectate_frame(_nds_png()))).convert("RGB")
    assert img.getpixel((256, 192)) == (200, 30, 30)    # middle of the big top
    assert img.getpixel((640, 192)) == (30, 30, 200)    # middle of the touch screen


def test_the_model_still_sees_a_vertical_stack():
    """THE regression this pair of functions exists to prevent.

    The prompt tells the model the bottom half of its image is the touch screen,
    and ``image_row_to_touch_y`` converts a row of THAT image into the normalised
    y SkyEmu wants. Hand it a side-by-side frame and every tap lands on the wrong
    screen — silently, because a tap always "works"."""
    from src.emulator.backends.frame import image_row_to_touch_y, prepare

    img, meta = prepare(_nds_png())
    assert img.height > img.width, "the model's frame stopped being a stack"
    assert meta["touch_top_row"] > 0
    assert image_row_to_touch_y(meta["touch_top_row"], meta) == 0.0
    assert image_row_to_touch_y(
        meta["touch_top_row"] + meta["touch_height"] - 1, meta
    ) == pytest.approx(1.0, abs=0.01)


def test_a_frame_the_layout_does_not_handle_is_published_unchanged():
    """GB and GBA captures, and a torn or unexpected one. A frame is worth less
    than the run: the feed publishes what it was given rather than stalling."""
    from PIL import Image
    import io as _io
    from src.emulator.backends.frame import spectate_frame

    for size in ((240, 160), (160, 144), (256, 192)):
        buf = _io.BytesIO()
        Image.new("RGB", size, (1, 2, 3)).save(buf, format="PNG")
        assert spectate_frame(buf.getvalue()) is None, size
    assert spectate_frame(b"not a png at all") is None
    assert spectate_frame(b"") is None


def test_the_writer_publishes_the_relaid_out_frame(tmp_path):
    """End to end through the sampler, which is where the transform actually
    sits — the emulator hands bytes to the writer and nothing else touches them."""
    from src.dashboard.spectate import StreamFileWriter
    from src.emulator.backends.frame import spectate_frame

    path = tmp_path / "stream.png"
    writer = StreamFileWriter(path, transform=spectate_frame)
    writer(_nds_png())
    assert _size(path.read_bytes()) == (768, 384)
    assert writer.frames == 1
    assert writer.untransformed == 0


def test_the_writer_publishes_a_gba_frame_untouched(tmp_path):
    """The control. Three of the seven games in the registry are not DS, and the
    transform must be invisible to them — including in the counters, so
    ``untransformed`` means "declined", not "failed"."""
    from PIL import Image
    import io as _io
    from src.dashboard.spectate import StreamFileWriter
    from src.emulator.backends.frame import spectate_frame

    buf = _io.BytesIO()
    Image.new("RGB", (240, 160), (9, 9, 9)).save(buf, format="PNG")
    raw = buf.getvalue()

    path = tmp_path / "stream.png"
    writer = StreamFileWriter(path, transform=spectate_frame)
    writer(raw)
    assert path.read_bytes() == raw
    assert writer.untransformed == 1


def test_attach_lays_ds_frames_out_by_default_and_can_be_told_not_to(tmp_path):
    from src.dashboard.spectate import DEFAULT_DS_LAYOUT, attach, resolve_ds_layout
    from src.emulator.backends.frame import spectate_frame

    assert DEFAULT_DS_LAYOUT == "wide"
    assert resolve_ds_layout({}) == "wide"
    with pytest.raises(ValueError, match="ds_layout"):
        resolve_ds_layout({"emulator": {"ds_layout": "sideways"}})

    emu = FakeSkyEmu(_config())
    config = {"emulator": {"type": "skyemu"}}
    feed = attach(emu, config, tmp_path)
    try:
        assert feed.writer._transform is spectate_frame
        assert config["emulator"]["ds_layout"] == "wide"
    finally:
        feed.detach()

    emu2 = FakeSkyEmu(_config())
    config2 = {"emulator": {"type": "skyemu", "ds_layout": "stacked"}}
    feed2 = attach(emu2, config2, tmp_path / "b")
    try:
        assert feed2.writer._transform is None
    finally:
        feed2.detach()
