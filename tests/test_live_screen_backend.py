"""The live spectate screen, and the two ways it showed the wrong game.

Reported 2026-09-19: a Crystal run started from the SkyEmu control center played
correctly — the trace in History is real Crystal data — but Spectate showed a
Gen 3 screen. It was a FOUR-HOUR-OLD FireRed frame at
``/tmp/mgba_stream_1.png``, left there by an unrelated v1 mGBA run.

Two independent defects, and either alone reproduces it:

1. ``run_single_loop`` decided the backend from the RUN's config. Under the
   control center the emulator belongs to the supervisor and is built from ITS
   config, so a casual run on config-5.1 (``type: mgba``) dispatched into a
   SkyEmu supervisor executes on SkyEmu while its own config says mGBA. The mGBA
   branch stamps ``paths.stream`` to the file Lua writes and skips attaching the
   stepped backend's spectate sampler — so nothing wrote a frame, and the
   streamer watched a path some other run owns.

2. ``ScreenStreamer`` served whatever valid PNG it found there. A stream file
   outlives the run that wrote it, and ``/tmp/mgba_stream_1.png`` is a fixed path
   every mGBA run on the machine reuses.

Fixing only the first would leave a live feed that shows another game whenever a
path is wrong for any other reason. A stale picture is worse than no picture: it
is not obviously broken.
"""

from __future__ import annotations

import os
import time

import pytest

from src.cli.runner import _backend_type, _handle_backend
from src.dashboard.screen_stream import ScreenStreamer


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40 + b"IEND\xaeB`\x82")


# --- which backend is actually running ------------------------------------


def test_the_handle_beats_the_config():
    """THE regression. This exact pair — a mGBA config executing against a
    SkyEmu handle — is what the control center produces for every casual run on
    a numbered config, and it is what pointed the feed at the stale file."""
    config = {"emulator": {"type": "mgba"}}
    handle = {"backend": "skyemu"}
    assert _backend_type(config) == "mgba"          # what the run's file says
    assert _handle_backend(handle, config) == "skyemu"   # what is actually running


def test_the_config_is_the_fallback_when_the_handle_is_silent():
    """A hand-built handle (tests) or one prepared before either branch stamped
    a backend still has to resolve to something, and the config is the only
    other evidence. Blank and non-string both count as silent."""
    config = {"emulator": {"type": "skyemu"}}
    assert _handle_backend({}, config) == "skyemu"
    assert _handle_backend({"backend": ""}, config) == "skyemu"
    assert _handle_backend({"backend": None}, config) == "skyemu"


def test_both_prepare_phases_stamp_what_they_are():
    """The fix depends on the handle carrying a backend at all. SkyEmu's branch
    always did; mGBA's did not, so 'mgba' was the ABSENCE of a key — which is
    indistinguishable from a handle that simply never stamped one."""
    import inspect
    from src.cli import runner

    mgba = inspect.getsource(runner.run_prepare_phase)
    sky = inspect.getsource(runner._run_prepare_phase_skyemu)
    assert '"backend": "mgba"' in mgba
    assert '"backend": "skyemu"' in sky


def test_run_single_loop_branches_on_the_handle_not_the_config():
    """A source-level guard, and deliberately so — say what it can and cannot do.

    ``_handle_backend`` being correct is worthless if ``run_single_loop`` does
    not CALL it, and that is precisely the regression: the function was always
    available, the call site read the config. Exercising the real call site means
    launching an emulator and running a turn, which no unit test can do, so this
    asserts the decision is taken from ``_handle_backend`` and that the old
    ``_backend_type(config)`` spelling is gone from the two branches that hurt.

    It will not catch a rewrite that keeps the name and changes the meaning. It
    will catch the exact revert, which is what a regression test is for."""
    import inspect
    from src.cli import runner

    src = inspect.getsource(runner.run_single_loop)
    assert "_handle_backend(handle, config)" in src, (
        "run_single_loop must take its backend from the handle"
    )
    assert "_backend_type(config)" not in src, (
        "a branch in run_single_loop still reads the run config's backend; "
        "under the control center that is the supervisor's emulator's config, "
        "not this run's"
    )


def test_the_run_config_records_the_backend_that_actually_ran():
    """config.json is read by reports, replays and by a person asking why a run
    looked wrong. Leaving it naming the emulator the run did NOT use makes every
    one of those answer confidently and wrongly."""
    import inspect
    from src.cli import runner

    src = inspect.getsource(runner.run_single_loop)
    assert '["type"] = backend' in src


# --- and the streamer refuses somebody else's frame -----------------------


def _write(path, when=None):
    path.write_bytes(PNG)
    if when is not None:
        os.utime(path, ns=(when, when))


def test_a_frame_older_than_the_streamer_is_never_served(tmp_path):
    """The reported symptom, reproduced: a valid PNG of another game already
    sitting at the watched path. The feed must stay empty rather than show it."""
    stale = tmp_path / "mgba_stream_1.png"
    _write(stale, when=time.time_ns() - 4 * 3600 * 10**9)   # four hours ago

    s = ScreenStreamer(stream_path=str(stale), require_fresh=True)
    s.start()
    time.sleep(0.2)
    try:
        assert s.get_frame() is None, "served a frame from before the run started"
    finally:
        s.stop()


def test_a_frame_written_after_the_streamer_starts_is_served(tmp_path):
    """THE control. Without it the test above passes for a streamer that serves
    nothing at all, which would be a worse bug than the one being fixed."""
    live = tmp_path / "stream.png"
    s = ScreenStreamer(stream_path=str(live))
    s.start()
    time.sleep(0.05)
    try:
        _write(live)
        for _ in range(100):
            if s.get_frame() is not None:
                break
            time.sleep(0.01)
        assert s.get_frame() == PNG
    finally:
        s.stop()


def test_a_stale_file_that_is_then_overwritten_starts_serving(tmp_path):
    """The staleness check must be re-evaluated per poll, not latched. mGBA's
    stream path is FIXED and reused, so the normal mGBA case is exactly this:
    a leftover file from the previous run that this run then writes over."""
    path = tmp_path / "mgba_stream_1.png"
    _write(path, when=time.time_ns() - 10**10)

    s = ScreenStreamer(stream_path=str(path), require_fresh=True)
    s.start()
    time.sleep(0.05)
    try:
        assert s.get_frame() is None
        _write(path)   # the live run draws
        for _ in range(100):
            if s.get_frame() is not None:
                break
            time.sleep(0.01)
        assert s.get_frame() == PNG
    finally:
        s.stop()


def test_a_stale_file_does_not_spin_the_cpu(tmp_path):
    """The staleness skip must not bypass the loop's sleep. An early `continue`
    there would busy-spin a core for the whole run — a fix that costs more than
    the bug. Measured as process CPU time over a window many polls long."""
    stale = tmp_path / "mgba_stream_1.png"
    _write(stale, when=time.time_ns() - 10**10)

    s = ScreenStreamer(stream_path=str(stale), require_fresh=True)
    before = time.process_time()
    s.start()
    time.sleep(0.5)
    s.stop()
    used = time.process_time() - before
    assert used < 0.2, f"streamer burned {used:.2f}s of CPU in 0.5s of wall clock"


# --- and the case the first version of that guard broke -------------------


def test_a_per_run_path_serves_the_frame_that_is_already_there(tmp_path):
    """THE second regression, caused by fixing the first.

    The stepped backend's feed PRIMES a frame on attach, deliberately: a frozen
    emulator does not advance until the model has answered, so without it there
    is nothing to show for the whole first turn. ``attach()`` writes that frame
    and only THEN does ``start_dashboard`` construct the streamer — so the
    primed frame is always older than the streamer, and always legitimate.

    Requiring freshness on a per-run path rejected it, and cost two symptoms at
    once that look nothing like a timestamp bug: the live feed stayed blank
    until the first turn completed, and every recording died with "simple view
    never exposed its game-screen rectangle" (the recorder measures the page's
    game <img>; an <img> with no frame has no natural size, and no size means no
    crop rectangle)."""
    primed = tmp_path / "stream.png"
    primed.write_bytes(PNG)
    time.sleep(0.02)

    s = ScreenStreamer(stream_path=str(primed), require_fresh=False)
    s.start()
    try:
        for _ in range(100):
            if s.get_frame() is not None:
                break
            time.sleep(0.01)
        assert s.get_frame() == PNG, (
            "a frame primed by this run's own feed was rejected; the live view "
            "and the recorder both have nothing to show for the first turn"
        )
    finally:
        s.stop()


def test_the_two_regimes_disagree_about_the_same_file(tmp_path):
    """The two tests above in one place, on ONE file, so the flag is shown to be
    what decides — not the fixture. A guard that behaved identically either way
    would pass both of them separately."""
    f = tmp_path / "stream.png"
    f.write_bytes(PNG)
    time.sleep(0.02)

    lenient = ScreenStreamer(stream_path=str(f), require_fresh=False)
    strict = ScreenStreamer(stream_path=str(f), require_fresh=True)
    lenient.start()
    strict.start()
    time.sleep(0.2)
    try:
        assert lenient.get_frame() == PNG
        assert strict.get_frame() is None
    finally:
        lenient.stop()
        strict.stop()


def test_start_dashboard_trusts_a_run_dir_path_and_doubts_a_shared_one():
    """Which regime each backend lands in, asserted at the decision itself.

    mGBA's stream path is a fixed /tmp name every run on the machine reuses —
    that is the shared case the guard exists for. The stepped backend writes
    <run_dir>/stream.png, which only this run can have written."""
    import inspect
    from src.dashboard import server

    src = inspect.getsource(server.start_dashboard)
    assert "is_relative_to(run_dir" in src
    assert "require_fresh=shared" in src
