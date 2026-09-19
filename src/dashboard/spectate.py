"""Live spectate (and, for free, video) for a stepped backend — plan §4.

THE CONTRACT IS A PNG FILE THAT KEEPS CHANGING. That is the whole of it:

    lua/socketserver-1.lua:346  →  /tmp/mgba_stream_1.png  (mGBA, every frame)
    screen_stream.py            →  polls the mtime, validates the IEND marker
    server.py ws_screen         →  forwards each new PNG to every WS client

Nothing in that chain names an emulator, asks one a question, or holds a handle
to one. So "SkyEmu can be spectated" is not a feature of the dashboard — it is
one callable that writes that file. ``SkyEmuClient._step`` is the single place
every advance of the machine goes through and it already carries a ``sampler``
hook, so one attachment covers presses, waits and screen-settling alike.

The same file is also the recorder's game rectangle: ``recorder.maybe_start``
hands ``RunRecorder`` ``screen_source=session.streamer``, and the streamer is
reading this file. So the dashboard MP4 recorder needs nothing new either —
``v2-experiments/harness/record.py`` stays where it is, as the headless-only
path that has no dashboard to render around the game.

Two things are not free, and both live here:

1. **Sampling costs throughput.** Every capture is an HTTP round trip and
   SkyEmu answers one request at a time, so an attached sampler turns one long
   ``/step`` into ``frames/sample_every`` step+screen pairs. Measured ~54
   captures/s, hence ``sample_every = 2`` → 30 fps of a 60 fps console.
2. **Pacing** — ``realtime`` by default since 2026-09-19, reversing decision B.
   Andreas, watching the first Crystal run: *"the game is executing incredibly
   quick, could we do normal speed as a standard?"* A stepped backend advances
   as fast as the host allows, which on a GB title is many times the console's
   own clock — fine for a benchmark nobody watches, useless for the thing v2 is
   for. Opting IN to watchability was the wrong way round once every run is
   spectatable. ``--pace fast`` (or ``emulator.pace: fast``) is the opt-out.
   See :class:`RealtimePacer`.

3. **Free-running while the model thinks** — ``FreeRunner``, added 2026-09-19.
   A stepped emulator is a still photograph for the length of every LLM call.
   Andreas: *"i dont think the game should be paused, while we wait for inputs,
   it should just run."* This is the one of the three that DOES change a run's
   content, and it is the v1 behaviour being restored rather than a new risk —
   see the class docstring. ``emulator.free_run: false`` opts out.

The property worth protecting, and the reason pacing is allowed to exist at
all: **pacing changes a run's duration, not its content.** The emulator is
frozen between ``/step`` calls, so throttling only decides when the next call
is made, never what it computes. A spectator cannot change a score.

The flip side, and the reason the resolved pace is stamped onto the run config:
``duration_s`` and ``avg_s_per_turn`` on a run's index row DO move, because they
are wall clock. A realtime run and a fast run are not comparable on those two
figures, so a finished run has to record which it was rather than leaving a
reader to assume. Everything else — turns, cost, tokens, gates, the trace — is
untouched.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

#: The console's own clock. Every backend in this repo emulates a 60 Hz machine.
CONSOLE_FPS = 60.0

#: Where a stepped run's spectate feed is written, inside the run dir. Not
#: ``/tmp``: the mGBA path uses a fixed ``/tmp`` name because Lua's capture
#: target is baked into the script, and one file per run means two backends can
#: be up at once (which is the point of SkyEmu not taking slot 1's port).
STREAM_FILE = "stream.png"

PACES = ("fast", "realtime")
#: 2026-09-19: was "fast". A stepped backend with no pacer runs the game at
#: whatever rate the host can drive HTTP, which is far above 60 Hz — the first
#: Crystal run was unwatchable. Since every v2 run is spectatable and recordable,
#: the watchable rate is the sensible default and speed is the opt-in.
DEFAULT_PACE = "realtime"

#: 2026-09-19: the console keeps running while the model thinks. A stepped
#: backend is frozen between ``/step`` calls, so without this a spectator (and
#: the recorder) watch a still photograph for the ten-plus seconds of every LLM
#: call — Andreas: *"i dont think the game should be paused while we wait for
#: inputs, it should just run."* mGBA never had this property to lose: its
#: emulator runs at 60 Hz whoever is or is not asking it anything.
DEFAULT_FREE_RUN = True

#: Frames per free-run chunk. The lock is released between chunks, so this is
#: also the worst-case wait for a driver thread that wants the emulator back —
#: 2 frames is 33 ms of game time and ~19 ms of host time, the same chunk the
#: spectate sampler already uses during gameplay.
FREE_RUN_CHUNK = 2


# ───────────────────────────── pacing ──────────────────────────────


def resolve_pace(config: dict) -> str:
    """``emulator.pace``, defaulted to :data:`DEFAULT_PACE` (``realtime``).

    An unknown value RAISES rather than falling back. A typo silently meaning
    "fast" would make a spectated run quietly un-spectatable, and the run would
    look normal the whole way through.
    """
    raw = (config.get("emulator") or {}).get("pace", DEFAULT_PACE)
    if raw is None:
        return DEFAULT_PACE
    pace = str(raw).strip().lower()
    if pace not in PACES:
        raise ValueError(
            f"unknown emulator.pace {raw!r} (expected: {', '.join(PACES)})"
        )
    return pace


def resolve_free_run(config: dict) -> bool:
    """``emulator.free_run``, defaulted to :data:`DEFAULT_FREE_RUN` (on).

    Unlike :func:`resolve_pace` this one accepts anything truthy-looking,
    because it is a boolean and a config author writing ``no`` means no. The
    strings are spelled out rather than trusting ``bool("false")``, which is
    True.
    """
    raw = (config.get("emulator") or {}).get("free_run", DEFAULT_FREE_RUN)
    if raw is None:
        return DEFAULT_FREE_RUN
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in ("false", "no", "off", "0", ""):
            return False
        if text in ("true", "yes", "on", "1"):
            return True
        raise ValueError(
            f"unknown emulator.free_run {raw!r} (expected a boolean)"
        )
    return bool(raw)


class RealtimePacer:
    """Throttle a stepped backend to the console's wall clock.

    Called with the frame count that was just advanced. It keeps a deadline in
    *game* time and sleeps the difference, so the host's own stepping cost is
    inside the budget rather than on top of it.

    **Debt is never banked.** Any overshoot re-anchors the deadline to now, so
    the pacer throttles the next stretch of play and forgives everything before
    it. Carrying debt forward would let a turn run at full speed to "catch up"
    — the precise opposite of what a spectator asked for. This mattered most
    before :class:`FreeRunner`, when nothing stepped during an LLM call and the
    deadline fell arbitrarily far behind; a free-running console now keeps the
    deadline live across that gap, and the re-anchor is left in place for the
    case it was written for — a host that simply cannot keep up.

    ``clock`` / ``sleep`` are injectable so the behaviour can be tested without
    spending the wall clock it is about.
    """

    def __init__(
        self,
        fps: float = CONSOLE_FPS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if fps <= 0:
            raise ValueError(f"pacer fps must be positive, got {fps!r}")
        self._fps = float(fps)
        self._clock = clock
        self._sleep = sleep
        self._deadline: Optional[float] = None
        #: Wall-clock seconds this pacer has spent waiting. The cost of realtime.
        self.slept = 0.0
        #: Times the host could not keep up and the deadline was re-anchored.
        self.behind = 0

    def reset(self) -> None:
        """Forget the deadline. The next call starts a fresh budget."""
        self._deadline = None

    def __call__(self, frames: int) -> float:
        """Wait out ``frames`` of game time. Returns the seconds actually slept."""
        frames = int(frames)
        if frames <= 0:
            return 0.0
        now = self._clock()
        if self._deadline is None:
            self._deadline = now
        self._deadline += frames / self._fps
        delay = self._deadline - now
        if delay > 0:
            self._sleep(delay)
            self.slept += delay
            return delay
        self._deadline = now
        self.behind += 1
        return 0.0



#: How long :meth:`FreeRunner.stop` waits for the worker to finish its chunk.
#: Generous: with a pacer attached the chunk's wait happens under the emulator
#: lock, so a stop lands one chunk late by design.
FREE_RUN_JOIN_TIMEOUT = 5.0


class FreeRunner:
    """Keep the console running while nobody is driving it.

    A stepped backend is frozen between ``/step`` calls, which is the property
    that makes a v2 run reproducible — every wait is an exact frame count rather
    than a wall-clock sleep. It also means that for the ten-plus seconds of each
    LLM call the emulator is a **still photograph**: the spectate feed stops, the
    recording records a freeze frame, and the game visibly stutters one turn at a
    time. mGBA never had this to lose; its core runs at 60 Hz whoever is or is
    not asking it anything, and v1 ran that way for the whole benchmark.

    So this thread steps the machine while the turn loop waits for a model, and
    stops before the turn loop touches the emulator again.

    **What it gives back, and it is the whole tradeoff.** A frozen emulator makes
    a run's content independent of how long the model took; a free-running one
    does not. The screen the model was shown is the screen at the START of its
    call, and the presses land on the screen at the END of it — so machine state
    at each input becomes a function of model latency again, exactly as in v1.
    Every v1 run on the board was played that way, and Pokémon is almost entirely
    input-gated (nothing advances in an overworld or a battle menu while nobody
    presses anything), so in practice what changes is animations, the Gen 2+
    real-time clock and auto-advancing cutscenes. ``emulator.free_run: false``
    restores the frozen behaviour for a run that needs it — a determinism
    measurement, or a scored run whose protocol says so.

    **It never ends a run.** A chunk that raises is counted and the thread
    returns; an observer of the benchmark may not be a participant in it.

    Threading: one worker, started and stopped by the turn loop. It advances the
    emulator through the PUBLIC :meth:`step`, which takes the emulator's lock for
    each chunk and releases it in between — so a driver thread that wants the
    machine back waits at most one chunk, and never mid-press.
    """

    def __init__(
        self,
        emu: Any,
        *,
        chunk: int = FREE_RUN_CHUNK,
        fps: float = CONSOLE_FPS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if fps <= 0:
            raise ValueError(f"free-run fps must be positive, got {fps!r}")
        self.emu = emu
        self.chunk = max(1, int(chunk))
        self._fps = float(fps)
        self._clock = clock
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        #: Frames advanced while nobody was driving. The evidence it ran at all.
        self.frames = 0
        #: Chunks that raised, and the last reason. Never re-raised.
        self.errors = 0
        self.last_error: Optional[str] = None
        #: Times :meth:`stop` gave up waiting for the worker.
        self.stalled = 0

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        """Begin advancing. Idempotent — a second call while running is a no-op."""
        with self._guard:
            if self._thread is not None:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="free-run", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop advancing and wait for the worker. Idempotent.

        Must complete before the caller touches the emulator, which is why it
        joins rather than only setting the flag: two threads stepping the same
        machine would interleave a press with somebody else's frames.
        """
        with self._guard:
            thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=FREE_RUN_JOIN_TIMEOUT)
        if thread.is_alive():
            self.stalled += 1

    def __enter__(self) -> "FreeRunner":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.stop()
        return False

    def _loop(self) -> None:
        deadline: Optional[float] = None
        while not self._stop.is_set():
            try:
                self.emu.step(self.chunk)
            except Exception as exc:   # never kill the run over a spectator
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                return
            self.frames += self.chunk
            # Free-run is held to the CONSOLE's clock whatever the run's pace.
            # ``pace: fast`` is about not spending wall clock on gameplay, and
            # idle frames cost none either way — stepping them at host speed
            # would just burn minutes of in-game clock per model call.
            #
            # A deadline rather than a per-chunk sleep, and that is what keeps
            # this from fighting ``emu.pacer``: when a pacer is attached it has
            # ALREADY waited out the chunk inside ``step``, so the deadline here
            # is behind and re-anchors to now without waiting again. A plain
            # ``sleep(chunk/fps)`` would double every wait and run the feed this
            # exists for at half speed.
            now = self._clock()
            if deadline is None:
                deadline = now
            deadline += self.chunk / self._fps
            delay = deadline - now
            if delay > 0:
                self._stop.wait(delay)   # wait, not sleep: a stop lands at once
            else:
                deadline = now


# ───────────────────────── the spectate feed ───────────────────────


class StreamFileWriter:
    """The sampler: PNG bytes in, a file whose mtime keeps moving out.

    The write is a temp file plus ``os.replace``, not a truncate-and-write.
    ``ScreenStreamer`` already guards against a torn read by checking for the
    IEND marker — but that guard DROPS the frame it catches, and at 30 fps the
    reader and the writer overlap often. An atomic rename means the poller only
    ever stats a complete PNG, so the guard never has to fire.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # Same directory, so the rename stays on one filesystem (os.replace is
        # only atomic within one).
        self._tmp = self.path.with_name(self.path.name + ".part")
        #: Frames written. The evidence that a feed ran at all.
        self.frames = 0
        #: Writes that failed. A recorder must never stop a run, so failures are
        #: counted rather than raised; a nonzero count is what a report needs.
        self.errors = 0
        self.last_error: Optional[str] = None
        self.closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, png: bytes) -> None:
        if self.closed or not png:
            return
        try:
            with open(self._tmp, "wb") as fh:
                fh.write(png)
            os.replace(self._tmp, self.path)
            self.frames += 1
        except OSError as exc:
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        """Stop writing and drop the temp file. The last frame stays."""
        self.closed = True
        try:
            self._tmp.unlink(missing_ok=True)
        except OSError:
            pass


class SpectateFeed:
    """What :func:`attach` left on the emulator, and how to take it back off."""

    def __init__(
        self,
        emu: Any,
        writer: StreamFileWriter,
        pacer: Optional[RealtimePacer],
        pace: str,
        free_runner: Optional[FreeRunner] = None,
    ) -> None:
        self.emu = emu
        self.writer = writer
        self.pacer = pacer
        self.pace = pace
        self.free_runner = free_runner

    @property
    def stream_path(self) -> Path:
        return self.writer.path

    @property
    def frames(self) -> int:
        return self.writer.frames

    def prime(self) -> bool:
        """Publish one frame NOW, before anything has stepped.

        A stepped emulator produces no frames while nothing steps, and a run
        does not step until the model has answered its first turn — ten or more
        seconds after the run registers. mGBA has no equivalent gap: Lua writes
        the stream file from the moment the emulator is running, whoever is or
        is not watching.

        That gap is not cosmetic. It is what broke the MP4 recorder twice on
        2026-09-19: ``RunRecorder.start`` gives the page 8 s to report the game
        image's natural size, the image has no size until a frame has arrived,
        and the recorder disabled itself with "simple view never exposed its
        game-screen rectangle" before turn 1 had pressed anything.
        """
        getter = getattr(self.emu, "screen_png", None)
        if getter is None:
            return False
        before = self.writer.frames
        try:
            self.writer(getter())
        except Exception:
            return False
        return self.writer.frames > before

    def detach(self) -> None:
        """Unhook from the emulator. Sequential runs share one backend process,
        so a feed left attached would keep writing into a finished run's dir."""
        # The free runner first, and unconditionally: it is a live THREAD, and
        # one left stepping after its run ended advances the next run's start
        # state before that run has pressed anything.
        if self.free_runner is not None:
            self.free_runner.stop()
            if getattr(self.emu, "free_runner", None) is self.free_runner:
                self.emu.free_runner = None
        self.writer.close()
        if getattr(self.emu, "sampler", None) is self.writer:
            self.emu.sampler = None
        if self.pacer is not None and getattr(self.emu, "pacer", None) is self.pacer:
            self.emu.pacer = None


def attach(emu: Any, config: dict, run_dir: str | Path) -> SpectateFeed:
    """Point ``emu``'s sampler at this run's stream file, and pace it.

    Also stamps ``config['paths']['stream']``, which is what ``start_dashboard``
    reads to build the run's ``ScreenStreamer`` — so this must run BEFORE the
    dashboard session is created. The path is re-stamped every run rather than
    reused: sequential runs share one config dict, and run 2 pointing the
    dashboard at run 1's file is a live feed of a finished game.
    """
    pace = resolve_pace(config)
    stream_path = Path(run_dir) / STREAM_FILE
    config.setdefault("paths", {})["stream"] = str(stream_path)
    # Record the RESOLVED pace, not just the authored one. duration_s and
    # avg_s_per_turn are wall clock, so they mean different things under the two
    # paces; a run that leaves the key absent forces a reader to guess which
    # default was in force on the day it ran — and that default has already
    # changed once.
    config.setdefault("emulator", {})["pace"] = pace

    writer = StreamFileWriter(stream_path)
    emu.sampler = writer
    pacer = RealtimePacer() if pace == "realtime" else None
    emu.pacer = pacer

    # Hung on the emulator beside ``sampler`` and ``pacer``, and for the same
    # reason: the turn loop holds an emulator, not a spectate feed, and the two
    # backends must differ only in what is attached to them. mGBA never gets one
    # (this function is not called for it) and does not need one — its core is
    # already running.
    free_run = resolve_free_run(config)
    config.setdefault("emulator", {})["free_run"] = free_run
    runner = FreeRunner(emu) if free_run else None
    emu.free_runner = runner

    feed = SpectateFeed(emu, writer, pacer, pace, runner)
    feed.prime()
    return feed


__all__ = [
    "CONSOLE_FPS",
    "DEFAULT_FREE_RUN",
    "DEFAULT_PACE",
    "FREE_RUN_CHUNK",
    "PACES",
    "STREAM_FILE",
    "FreeRunner",
    "RealtimePacer",
    "SpectateFeed",
    "StreamFileWriter",
    "attach",
    "resolve_free_run",
    "resolve_pace",
]
