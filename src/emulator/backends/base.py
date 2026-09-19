"""The backend protocol — what an emulator backend must implement to be usable
by the rest of this harness.

This file is the *contract*, not an implementation. It exists so that a second
backend (SkyEmu, P1) can be written against a written-down method set rather
than against whatever ``EmulatorClient`` happened to expose.

Scope of the contract
---------------------
``EmulatorClient`` (the mGBA backend) has 18 public methods. Only **11** of them
are reached from outside ``src/emulator/`` — 10 required plus ``fetch_trace``,
which is optional — along with **2** attributes the app writes. That smaller set
is what a backend actually owes. The counts below were measured on this tree,
not copied from the plan::

    grep -rn "\\.<method>(" --include="*.py" src/ | grep -v src/emulator/

============================  =====  ==================================================
member                        sites  where
============================  =====  ==================================================
read_memory                       8  referee.py:287,311,318,327,331,339,340; supervisor.py:290
disconnect                        4  runner.py:990; launch.py:155; snapshot.py:91,117
start_server                      3  runner.py:600; launch.py:97; snapshot.py:36
wait_for_connection               3  runner.py:661; launch.py:129; snapshot.py:57
load_state                        3  snapshots.py:240; runner.py:786; launch.py:136
capture_screenshot                3  turn.py:1208,2075; snapshot.py:87
save_state                        2  snapshots.py:59,120
ping                              2  launch.py:139,147
press_button_list                 1  turn.py:1803
wait_for_stable_screen            1  turn.py:1807
fetch_trace                       1  turn.py:1897 — via getattr(), OPTIONAL (see below)
trace_spec (attribute, write)     2  runner.py:599; launch.py:96
facing (attribute, write)         1  turn.py:1818
============================  =====  ==================================================

Public methods of ``EmulatorClient`` with **zero** call sites in ``src/``, and
therefore *not* part of this contract: ``connect``, ``resync``, ``pause``,
``unpause``, ``press_button``, ``press_sequence``, ``normalize_button_list``.
``connect``/``press_button``/``press_sequence``/``pause``/``unpause`` are
exercised only by ``tests/test_emulator.py``, which pytest collects zero tests
from — it is a hand-run script with a ``main()``, not a suite member.
``press_sequence`` is additionally used by ``tests/test_emulator_protocol.py``,
which is an mGBA *wire protocol* test and rightly stays mGBA-specific.

A backend may implement any of those; nothing in the app will call them.

Notes for the SkyEmu backend (P1)
---------------------------------
Three members are awkward to honour on a stepped emulator, and they are named
here so P1 does not rediscover them:

* ``wait_for_stable_screen() -> float`` returns *seconds spent settling*. On
  mGBA that is a real wall-clock duration, because settling means polling a
  free-running emulator. On a stepped backend settling is "step N, compare,
  repeat" and the honest unit is frames. The return value is logged as a
  duration (``turn.py:1807``), so a stepped backend has to either report its
  own wall clock (fast, therefore near-zero and not comparable across arms) or
  report ``frames / 60`` (comparable, but not a measurement of anything that
  happened). Pick deliberately; do not let it fall out of the implementation.

* ``press_button_list(buttons)`` is fire-and-forget on mGBA: it sends, sleeps
  ``total_frames/60 + 0.5`` s against a wall clock, and returns. On a stepped
  backend the equivalent is exact (``step(total_frames)``) and strictly better
  — but it also means the method now *blocks for the whole input*, where mGBA
  returned while the game was still catching up. Anything that assumed the
  game was still moving after the call returns changes meaning.

* ``capture_screenshot`` / ``read_memory`` / input are three independent
  round trips on mGBA, serialised by an ``RLock`` and safely callable from the
  OCR poller thread. SkyEmu answers one HTTP request at a time and only
  advances on ``/step``, so a second thread cannot observe the game "while" it
  runs — there is no while. Any concurrent consumer becomes a sampler inside
  the step loop (plan §6, risk 3).

``fetch_trace`` is already optional at the call site::

    turn.py:1897   fetch = getattr(self.emulator, "fetch_trace", None)

so a backend without per-input tracing simply omits it; the referee's trace
folding is then skipped. It is declared on :class:`TracingEmulatorBackend`
rather than on the base protocol for exactly that reason.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from PIL import Image


@runtime_checkable
class EmulatorBackend(Protocol):
    """The method set every emulator backend must provide.

    Constructed from the whole config dict (``config["emulator"]`` holds the
    backend's own block); see :func:`src.emulator.backends.make_emulator`.
    """

    # --- attributes the app writes ---------------------------------------

    #: Raw memory ranges to sample after every input, or None for no tracing.
    #: Set by the owner *before* the connection is established
    #: (``runner.py:599``, ``launch.py:96``).
    trace_spec: Optional[list[str]]

    #: Last known player facing ("up"/"down"/"left"/"right"), or None when
    #: unknown. ``turn.py:1818`` resets it to None after a failed input.
    facing: Optional[str]

    # --- lifecycle --------------------------------------------------------

    def start_server(self) -> None:
        """Make the backend reachable. For mGBA this binds the TCP socket the
        Lua bridge dials into; for a self-hosted backend it may launch the
        process. Called before the emulator itself is started."""

    def wait_for_connection(self, timeout: float = 60.0) -> None:
        """Block until the backend is ready to take commands, or raise."""

    def disconnect(self) -> None:
        """Release everything. Must be safe to call twice and after a failed
        connect (``snapshot.py`` calls it on both the success and error path)."""

    def ping(self) -> bool:
        """True if the backend is alive. Must not raise — ``launch.py:139``
        uses it as a liveness poll and treats an exception as a crash."""

    # --- observation ------------------------------------------------------

    def capture_screenshot(self, preprocess: bool = True) -> Image.Image:
        """The current frame. ``preprocess=True`` applies the config's upscale
        and grid overlay; ``False`` returns the native-resolution frame."""

    def read_memory(self, addr: int, length: int) -> bytes:
        """``length`` raw bytes at bus address ``addr``.

        The referee's entire backend contract (``referee.py:108``). Must raise
        rather than return short — every caller indexes the result directly.
        """

    def wait_for_stable_screen(self) -> float:
        """Block until the frame stops changing; return seconds spent.

        See the module docstring: the return value's unit is the awkward part
        on a stepped backend.
        """

    # --- input ------------------------------------------------------------

    def press_button_list(self, buttons: list[str]) -> None:
        """Play a list of full button names (``["left", "left", "up", "a"]``).

        ``"wait"`` is a pseudo-input: it presses nothing and lets the game run
        for the configured ``wait_input_seconds``. Invalid names raise
        ``ValueError`` — ``turn.py`` relies on that to reject a model's bad
        action rather than silently dropping it. Updates :attr:`facing` from
        the last directional press.
        """

    # --- savestates -------------------------------------------------------

    def save_state(self, filepath: str) -> None:
        """Write a savestate. Format is backend-private — see plan §3.4: mGBA
        and SkyEmu states are NOT interchangeable, which is why a savepoint
        needs a backend marker."""

    def load_state(self, filepath: str) -> None:
        """Restore a savestate. Must raise ``FileNotFoundError`` for a missing
        path and a ``RuntimeError`` for a state the backend refuses."""


@runtime_checkable
class TracingEmulatorBackend(EmulatorBackend, Protocol):
    """A backend that can also sample memory after every individual input.

    Optional: ``turn.py:1897`` reaches for ``fetch_trace`` with ``getattr`` and
    skips the referee's per-input folding when it is absent.
    """

    def fetch_trace(self) -> list[tuple[str, list[bytes]]]:
        """Collect and clear the samples taken since the last call:
        ``[(input name, [bytes per trace_spec entry]), ...]``. A range the
        backend could not read comes back as ``b""``."""


__all__ = ["EmulatorBackend", "TracingEmulatorBackend"]
