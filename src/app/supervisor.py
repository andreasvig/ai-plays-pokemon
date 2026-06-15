"""AppSupervisor — owns the emulator (mGBA + Lua connector) for the app's lifetime.

Plan §P2 + the "Wiring plan" missing-piece #2 (supervisor inversion). Today the
emulator is owned by each *run*: ``pokemon run``'s ``main()`` calls
``run_prepare_phase`` → ``run_connect_phase`` once, reuses the resulting handle
across ``run_single_loop`` calls, then ``cleanup_handle`` once at the end.

This class FORMALISES that already-reusable handle into a long-lived owner: the
``pokemon app`` entrypoint constructs one ``AppSupervisor``, ``start()``s it
once, and the (future, P3) ``RunExecutor`` borrows :attr:`handle` to dispatch
runs *into* the persistent emulator instead of each run spinning one up.

It deliberately reuses ``run_prepare_phase`` / ``run_connect_phase`` /
``cleanup_handle`` rather than reimplementing window positioning / caffeinate /
TCP — so ``pokemon run`` and the app share one code path. The class is
import-safe (no side effects at import; the emulator launches only on
``start()``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.cli.runner import (
    cleanup_handle as _cleanup_handle,
    run_connect_phase as _run_connect_phase,
    run_prepare_phase as _run_prepare_phase,
)


@dataclass
class SupervisorStatus:
    """A small health snapshot of the supervised emulator.

    process_up — mGBA process launched and still alive.
    connected  — the Lua client has connected to the TCP server.
    busy       — a run is currently executing against the handle (P3's executor
                 toggles this via :meth:`AppSupervisor.set_busy`).
    """

    process_up: bool
    connected: bool
    busy: bool


# Type of the injectable prepare/connect/cleanup seam (lets tests run headless).
PrepareFn = Callable[[dict, Path], dict]
ConnectFn = Callable[..., None]
CleanupFn = Callable[[dict], None]


class AppSupervisor:
    """Long-lived owner of the persistent emulator for the control center.

    Lifecycle: :meth:`start` (launch mGBA + Lua once) → :attr:`handle` borrowed
    by the executor for each run → :meth:`shutdown` (idempotent teardown).
    :meth:`restart` is shutdown + start. :meth:`status` reports health.

    The ``prepare_fn`` / ``connect_fn`` / ``cleanup_fn`` seams default to the
    shared ``runner`` helpers; tests inject fakes so no real mGBA launches.
    """

    def __init__(
        self,
        config: dict,
        saves_dir: str | Path,
        *,
        connect_timeout: float = 300.0,
        prepare_fn: PrepareFn | None = None,
        connect_fn: ConnectFn | None = None,
        cleanup_fn: CleanupFn | None = None,
    ) -> None:
        self._config = config
        self._saves_dir = Path(saves_dir)
        self._connect_timeout = connect_timeout

        self._prepare_fn: PrepareFn = prepare_fn or _run_prepare_phase
        self._connect_fn: ConnectFn = connect_fn or _run_connect_phase
        self._cleanup_fn: CleanupFn = cleanup_fn or _cleanup_handle

        self._handle: Optional[dict] = None
        self._connected: bool = False
        self._busy: bool = False

    # ───────────────────────────── lifecycle ─────────────────────────────

    def start(self) -> dict:
        """Launch mGBA + the Lua connector ONCE and block until connected.

        Reuses ``run_prepare_phase`` (TCP server, mGBA launch, window
        positioning, caffeinate) + ``run_connect_phase`` (wait for the Lua
        client). Stores the handle. Idempotent in spirit — if already started
        with a live handle, returns the existing one without relaunching.
        """
        if self._handle is not None and self._process_up():
            return self._handle

        self._saves_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self._prepare_fn(self._config, self._saves_dir)
        self._connect_fn(self._handle, timeout=self._connect_timeout)
        self._connected = True
        return self._handle

    @property
    def handle(self) -> dict:
        """The prepared handle (what ``run_single_loop`` consumes).

        Raises if accessed before :meth:`start` so callers can't run against a
        non-existent emulator.
        """
        if self._handle is None:
            raise RuntimeError("AppSupervisor.handle accessed before start()")
        return self._handle

    def set_busy(self, busy: bool) -> None:
        """Mark whether a run is currently executing against the handle.

        The future (P3) ``RunExecutor`` calls this around each ``run_single_loop``
        so :meth:`status` reflects idle vs busy. Defaults to idle.
        """
        self._busy = bool(busy)

    def status(self) -> SupervisorStatus:
        """Current health snapshot (process up? connected? busy?)."""
        return SupervisorStatus(
            process_up=self._process_up(),
            connected=self._connected and self._handle is not None,
            busy=self._busy,
        )

    def restart(self) -> dict:
        """Tear down and re-establish the emulator. Returns the new handle."""
        self.shutdown()
        return self.start()

    def shutdown(self) -> None:
        """Tear down the emulator. IDEMPOTENT — safe to call more than once.

        Disconnects the emu and terminates mGBA + caffeinate via the shared
        ``cleanup_handle``, then clears state so a second call is a no-op.
        """
        if self._handle is not None:
            self._cleanup_fn(self._handle)
        self._handle = None
        self._connected = False
        self._busy = False

    # ───────────────────────────── internals ─────────────────────────────

    def _process_up(self) -> bool:
        """True iff the handle holds an mGBA process that is still alive."""
        if self._handle is None:
            return False
        proc = self._handle.get("mgba_proc")
        if proc is None:
            # A fake handle without a process object: treat presence of the
            # handle as "up" (tests inject these; real prepare always sets it).
            return True
        try:
            return proc.poll() is None
        except Exception:
            return False
