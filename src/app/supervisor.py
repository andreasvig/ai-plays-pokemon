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

import time
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
    # Which ROM the emulator is holding (the path it was launched with), and
    # whether a switch to another one is in flight. During a switch the old
    # process is gone and the new one is up but not yet connected — the app is
    # waiting for the Lua script to be re-loaded, which is a state the UI has to
    # be able to explain rather than just showing "disconnected".
    rom_path: str = ""
    switching_to: Optional[str] = None


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
        # Set for the duration of a :meth:`switch_rom` — the ROM path being
        # switched TO, so the UI can say "loading Emerald, re-load the Lua
        # script" instead of just going grey. Cleared when the switch settles.
        self._switching_to: Optional[str] = None
        # Default muted: the emulator launches with `-C mute=1`, so audio never
        # plays to an empty room until something explicitly unmutes it.
        self.muted: bool = True

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

    def set_mute(self, mute: bool) -> bool:
        """Mute/unmute the emulator audio. Returns the resulting muted state.

        Drives mGBA's native ``Audio/Video → Mute`` toggle via Accessibility
        (the Lua socket has no audio control). When no emulator is live yet, just
        records the intent so a later launch / the status endpoint reflects it.
        Best-effort: an AppleScript failure leaves the previous flag and is
        swallowed by the caller — never let it derail a run or the drain.
        """
        proc = self._handle.get("mgba_proc") if self._handle else None
        if proc is None or not hasattr(proc, "pid"):
            self.muted = bool(mute)  # no live process to drive — record intent
            return self.muted
        from src.cli.runner import set_mgba_mute_for_pid

        if set_mgba_mute_for_pid(int(proc.pid), bool(mute)):
            self.muted = bool(mute)
        return self.muted

    # ───────────────────────────────── ROM ───────────────────────────────────

    @property
    def rom_path(self) -> str:
        """The ROM path the emulator was launched with (the currency mGBA takes).

        The registry id/name are resolved FROM this by callers that need them
        (``src.app.roms.rom_for_path``) — the supervisor deliberately doesn't
        depend on the registry, so a hand-rolled config with an off-registry ROM
        still supervises fine.
        """
        return str((self._config.get("emulator") or {}).get("rom_path", ""))

    def switch_rom(self, rom_path: str, *, force: bool = False) -> dict:
        """Point the emulator at a different ROM. Returns the (current) handle.

        Two mechanisms, and which one runs decides whether you have to touch mGBA:

        1. **In place** (preferred) — drive the running mGBA's ``File → Recent``
           menu. Its script context outlives the cartridge, so the Lua script and
           its socket survive: no re-load, no reconnect, ~2 seconds. Confirmed
           against the cartridge header before it is believed.
        2. **Relaunch** (fallback) — shutdown → start with the new path, for when
           the ROM has never been opened on this machine (so it isn't in Recent),
           when Accessibility is unavailable, or when the in-place swap fails to
           verify. This one DOES cost a manual Lua re-load, and blocks on the
           reconnect for up to ``connect_timeout``.

        A no-op when the requested ROM is already loaded and the process is alive
        — the common case, and the reason the executor can call this before every
        single run.

        Refuses while a run is executing: yanking the cartridge mid-run would
        kill it. ``force=True`` waives ONLY that check, and exists for exactly one
        caller — the executor's pre-dispatch reconcile, which already holds
        ``busy`` for a run whose turn loop has not started yet. Nothing driven by
        a user gets to pass it; the route checks ``status().busy`` and 409s.
        """
        rom_path = str(rom_path)
        if not rom_path:
            raise ValueError("switch_rom needs a rom path")
        if rom_path == self.rom_path and self._process_up():
            return self.handle
        if self._busy and not force:
            raise RuntimeError(
                "cannot switch ROM while a run is executing — stop it first"
            )
        self._switching_to = rom_path
        # `restart()` tears down through `shutdown()`, which clears `_busy` —
        # so without this the supervisor would report IDLE mid-switch while the
        # executor still holds the run, and a concurrent drain could dispatch a
        # second run into an emulator that is in the middle of relaunching.
        was_busy = self._busy
        try:
            emulator = self._config.get("emulator")
            if not isinstance(emulator, dict):
                emulator = {}
                self._config["emulator"] = emulator
            emulator["rom_path"] = rom_path
            if self._swap_rom_in_place(rom_path):
                return self.handle
            # Deliberately NOT `restart()`: `shutdown()` clears `_busy`, and the
            # gap that matters is the relaunch itself — `start()` blocks for as
            # long as it takes a human to re-load the Lua script. Reporting idle
            # for those minutes is what would let a second run dispatch.
            self.shutdown()
            self._busy = was_busy
            return self.start()
        finally:
            self._busy = was_busy
            self._switching_to = None

    def _swap_rom_in_place(self, rom_path: str) -> bool:
        """Try to change cartridge WITHOUT restarting mGBA. True iff verified.

        Requires a live Lua connection (that is the thing being preserved, and
        also the only way to check the result) and a registry entry for the ROM
        (for the expected cartridge code). Anything missing → False, and the
        caller relaunches instead.

        The verification is the whole point and is deliberately end-to-end: the
        game code is read over the SAME socket, by the SAME frame callback, out of
        the NEW cartridge's header. It can only answer correctly if the swap took
        AND the script survived AND its callback is still being driven by the
        re-attached core — which is exactly the claim being made.
        """
        if not self._connected or self._handle is None:
            return False
        proc = self._handle.get("mgba_proc")
        pid = getattr(proc, "pid", None)
        if pid is None or (hasattr(proc, "poll") and proc.poll() is not None):
            return False

        from src.app.roms import rom_for_path

        try:
            rom = rom_for_path(rom_path)
        except (FileNotFoundError, ValueError):
            rom = None
        if rom is None:
            return False

        from src.cli.runner import load_rom_in_mgba_for_pid

        if not load_rom_in_mgba_for_pid(int(pid), rom_path):
            return False

        # The core needs a moment to come up before the frame callback resumes.
        for _ in range(10):
            time.sleep(0.5)
            if self.verify_loaded_rom(rom.game_code):
                print(f"  ROM swapped in place — {rom.name} (Lua connection kept).")
                return True
        return False

    def verify_loaded_rom(self, expect_game_code: str) -> Optional[bool]:
        """Check what mGBA ACTUALLY has in the slot. True/False, or None if unknown.

        Reads the 4-byte game code from the cartridge header over the existing
        ``READMEM`` (``src.app.roms.GAME_CODE_ADDR``) — the one signal that
        survives the user loading a different ROM by hand in mGBA, which the
        launch path would never notice. ``None`` means "couldn't ask" (no
        connection, a fake handle in tests, a read error): an unanswerable check
        must never be reported as a failed one.
        """
        from src.app.roms import GAME_CODE_ADDR, GAME_CODE_LEN

        emu = self._handle.get("emu") if self._handle else None
        if emu is None or not hasattr(emu, "read_memory"):
            return None
        try:
            raw = emu.read_memory(GAME_CODE_ADDR, GAME_CODE_LEN)
            code = bytes(raw).decode("ascii", errors="replace")
        except Exception:
            return None
        return code == expect_game_code

    def status(self) -> SupervisorStatus:
        """Current health snapshot (process up? connected? busy? which ROM?)."""
        # `connected` is gated on the process being alive, not just on the flag:
        # the flag only records that a handshake once happened, so quitting mGBA
        # left the app reporting `process_up: false, connected: true` — a state
        # that reads as healthy to every consumer and would accept runs into an
        # emulator that no longer exists.
        up = self._process_up()
        return SupervisorStatus(
            process_up=up,
            connected=up and self._connected and self._handle is not None,
            busy=self._busy,
            rom_path=self.rom_path,
            switching_to=self._switching_to,
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
