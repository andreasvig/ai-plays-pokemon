"""The SkyEmu backend (P1) — a stepped emulator behind the same method set.

Ported from ``v2-experiments/harness/skyemu.py``, which is the client this was
proved with (``v2-experiments/referee_proof.py`` latched a real checkpoint
through it). This file is that client reshaped to
:class:`src.emulator.backends.base.EmulatorBackend`; the experiment file stays
where it is.

The one inversion everything follows from
--------------------------------------------
**mGBA runs free and Python watches it. SkyEmu is frozen and Python advances
it.** A headless SkyEmu does nothing at all until ``/step`` and answers one HTTP
request at a time. Two consequences shape this class:

1. **Every wall-clock sleep in the mGBA backend becomes a frame count here.**
   ``press_button_list`` steps ``hold + gap`` frames per button instead of
   sleeping ``total_frames/60 + 0.5`` s; ``WAIT`` steps ``wait_input_seconds *
   60`` frames instead of sleeping; ``wait_for_stable_screen`` steps between
   captures instead of polling a clock. A frame count is the same on every
   machine and a sleep is not.
2. **An input is a level, not an edge.** ``/input?A=1`` holds A down until
   something sets it to 0, so a press is set → step (the hold) → clear → step
   (the gap). A set-and-clear inside one call registers nothing at all.

Traps that cost real time, both recorded in
``v2-experiments/emulator-research.md`` and both handled below:

* Text replies carry a trailing NUL — ``/ping`` answers ``b"pong\\x00"`` — and
  ``bytes.strip()`` removes ASCII whitespace but NOT NUL. Every text reply goes
  through :meth:`_text`.
* ``/read_byte``'s ``map`` parameter must come BEFORE its ``addr`` parameters in
  the query string, which is why :meth:`read_memory` builds a list of pairs
  rather than a dict.
* ``/save`` and ``/load`` answer HTTP **200** with the body ``failed``. The
  status line says nothing; :meth:`_check` reads the body.

Where this backend's meaning differs from mGBA's
------------------------------------------------
* ``wait_for_stable_screen`` returns **emulated** seconds (frames / 60), not
  host wall clock. See its docstring.
* ``press_button_list`` blocks for the whole input. On mGBA it returned while
  the game was still catching up.
* ``capture_screenshot(preprocess=True)`` draws **no grid overlay** (see
  :mod:`src.emulator.backends.frame`) and stacks the two NDS screens with a
  seam. The v2 frame is not v1's frame.
* There is no ``pause``/``unpause``: a stepped emulator is paused whenever
  nothing is stepping it, which is what ``pause_during_thinking`` wanted.
* **There is a stylus** (P3b). ``press_button_list`` accepts a
  ``tap:<x>,<y>`` element alongside the button names, and only when the config
  put ``TAP`` in ``valid_inputs`` AND the loaded ROM measures as NDS. The mGBA
  backend refuses ``TAP`` in its constructor; this one refuses a non-NDS ROM in
  :meth:`_probe_system`, which is the first moment the console is knowable.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from src.emulator.backends import frame as frame_mod
from src.emulator.inputs import TAP_INPUT, format_tap, is_tap, parse_tap

DEFAULT_BINARY = "~/Applications/SkyEmu.app/Contents/MacOS/SkyEmu"

# The SkyEmu input this project's patch added for the stylus, and the two
# coordinate parameters that ride with it. They do NOT exist upstream.
# ``touch_x``/``touch_y`` are normalised over the TOUCH SCREEN, not over the
# 256x384 capture — see :mod:`src.emulator.inputs`.
SKYEMU_TAP = "Tap Screen (NDS)"
SKYEMU_TOUCH_X = "touch_x"
SKYEMU_TOUCH_Y = "touch_y"

#: The only system with a touch screen. ``frame.geometry`` measures it from the
#: capture's shape; ``/status`` does not name the console.
TOUCH_SYSTEM = "NDS"

# This harness's short codes -> the names SkyEmu's /input answers to. The short
# codes are the ones ``normalize_button_list`` produces, so this table is the
# whole translation layer between a model's action and the wire.
#
# LB/RB are the shoulder buttons. They are in no shipped ``valid_inputs`` list,
# so nothing reaches them today; they are here so a config that adds them works.
SKYEMU_BUTTON = {
    "A": "A", "B": "B",
    "U": "Up", "D": "Down", "L": "Left", "R": "Right",
    "START": "Start", "SELECT": "Select",
    "LB": "L", "RB": "R",
}

DIRECTION_BUTTONS = {"U": "up", "D": "down", "L": "left", "R": "right"}
AB_BUTTONS = {"A", "B"}

FPS = 60.0

# The window a dereferenced trace pointer has to land in to be believed — GBA
# EWRAM. Same bound as the Lua bridge's ``sample_range``, so a spec entry means
# the same thing on both backends.
EWRAM_LO, EWRAM_HI = 0x02000000, 0x02040000


class SkyEmuError(RuntimeError):
    """SkyEmu answered, and the answer was a refusal."""


class SkyEmuClient:
    """One headless SkyEmu process, driven the way a turn loop needs it."""

    def __init__(self, config: dict[str, Any]):
        emu_config = config["emulator"]
        self.host = emu_config.get("host", "127.0.0.1")
        self.port = emu_config["port"]
        self.rom_path = Path(emu_config["rom_path"])
        self.binary = Path(emu_config.get("binary_path", DEFAULT_BINARY)).expanduser()

        self.hold_frames = emu_config.get("button_hold_frames", 6)
        self.gap_frames = emu_config.get("frames_between_inputs", 30)
        self.ab_hold_frames = emu_config.get("ab_hold_frames", 50)
        self.ab_gap_frames = emu_config.get("ab_gap_frames", 30)
        # A stylus tap is held longer than a button press: the harness probe
        # settled on 20/24 against Platinum and SoulSilver, and a shorter hold
        # was the difference between a menu registering the touch and not.
        self.tap_hold_frames = emu_config.get("tap_hold_frames", 20)
        self.tap_gap_frames = emu_config.get("tap_gap_frames", 24)
        # Duration of the "WAIT" pseudo-input, in seconds of GAME time — it is
        # stepped, not slept, so it costs whatever the host needs and the game
        # sees exactly this many seconds.
        self.wait_seconds = emu_config.get("wait_input_seconds", 5.0)

        # Frames stepped once the process answers /ping, before anything reads
        # memory or a state is loaded. A freshly launched core has not run a
        # single instruction; ``v2-experiments/referee_proof.py`` steps 60 for
        # the same reason. 0 disables it.
        self.boot_frames = emu_config.get("boot_frames", 60)
        self.launch_timeout = emu_config.get("launch_timeout", 120.0)

        # SkyEmu writes the battery save (.sav) NEXT TO THE ROM — measured, a
        # Platinum .sav appeared in v2-experiments/roms/ during a run. mGBA is
        # told ``savegamePath=<run saves dir>``; SkyEmu has no such switch, so
        # the equivalent is to launch from a copy. It also makes "cold boot"
        # mean cold boot: a .sav beside the ROM puts CONTINUE on FireRed's title
        # screen and derails every press after it (make_start_state.py).
        # ``rom_stage_dir`` says where the copy goes; unset means a temp dir
        # removed on disconnect. ``copy_rom: false`` launches the ROM in place.
        self.copy_rom = emu_config.get("copy_rom", True)
        self.rom_stage_dir = emu_config.get("rom_stage_dir")

        # NDS only: which CPU's address space /read_byte should use (9 = ARM9,
        # 7 = ARM7). None on GBA/GB, where the parameter is not sent at all.
        self.memory_map = emu_config.get("memory_map")

        screenshot_config = config.get("screenshot", {})
        # None = per-system default (frame.UPSCALE): 6x for GB/GBA, 3x for NDS,
        # which already has 2.5x the pixels. An int forces one factor.
        self.upscale_factor = screenshot_config.get("upscale_factor")
        if screenshot_config.get("grid_overlay"):
            raise ValueError(
                "screenshot.grid_overlay is true, and the skyemu backend draws no grid. "
                "v1's lattice is 16 native px on an 8 px offset — both GBA viewport "
                "facts that describe nothing on a DS frame (see "
                "src/emulator/backends/frame.py). Set screenshot.grid_overlay: false; "
                "silently dropping the overlay would leave the config claiming one."
            )
        self.screen_divider = screenshot_config.get("nds_screen_divider", True)

        self.valid_inputs = set(config.get("valid_inputs", []))
        #: True when the config asked for the stylus (``TAP`` in valid_inputs).
        #: Unlike every other entry this one is a CAPABILITY claim as well as a
        #: permission: it asserts the ROM about to be loaded has a touch screen,
        #: and :meth:`wait_for_connection` checks that against the frame the
        #: console actually renders before turn 1.
        self.touch_enabled = TAP_INPUT in {
            str(v).strip().upper() for v in self.valid_inputs
        }
        #: "NDS" / "GBA" / "GB" once a frame has been measured, else None.
        self.system: Optional[str] = None

        stability = config.get("screen_stability", {})
        # Kept for parity with the mGBA backend, which also reads and never uses
        # it. Named here so a config author does not think it does something.
        self.stability_min_wait = stability.get("min_wait", 0.3)
        self.stability_max_wait = stability.get("max_wait", 10.0)
        self.stability_poll_interval = stability.get("poll_interval", 0.3)
        self.stability_threshold_start = stability.get("threshold_start", 0.99)
        self.stability_threshold_end = stability.get("threshold_end", 0.90)
        self.stability_num_frames = stability.get("num_frames", 3)

        # --- attributes the app writes (base.py) -------------------------
        self.facing: Optional[str] = None
        self.trace_spec: Optional[list[str]] = None

        # --- process + wire ----------------------------------------------
        self.proc: Optional[subprocess.Popen] = None
        self.log_path: Optional[str] = None
        self._log_file = None
        self._staged_dir: Optional[Path] = None
        self._launched_rom: Optional[Path] = None
        self._connected = False
        # SkyEmu answers one request at a time; the lock makes a MULTI-request
        # operation (a press is four requests) atomic too, so a referee poll or
        # a liveness ping from another thread cannot land mid-press.
        self._lock = threading.RLock()

        # --- observability -------------------------------------------------
        #: What ``frame.prepare`` said about the last preprocessed capture —
        #: system, native size, final size, and on NDS the touch screen's first
        #: row. None until the first ``capture_screenshot(preprocess=True)``.
        self.last_frame_meta: Optional[dict[str, Any]] = None
        #: Frames the last ``wait_for_stable_screen`` spent settling. The return
        #: value is that number over 60; this is the raw count, for a consumer
        #: that wants frames rather than emulated seconds.
        self.last_settle_frames: int = 0
        #: Total frames this backend has stepped since it was constructed.
        self.frames_stepped: int = 0
        #: Attach a callable to record. Every advance goes through ``_step``, so
        #: one hook catches presses, waits and settles alike. ``sample_every``
        #: frames is the interval: 2 gives 30 fps of a 60 fps console.
        self.sampler: Optional[Any] = None
        self.sample_every = 2
        #: Attach a callable to throttle. Called with the frames just advanced,
        #: on the same chunk boundary as the sampler — a stepped emulator runs
        #: as fast as the host allows, and a spectator wants the console's clock
        #: (``dashboard/spectate.RealtimePacer``). It can only decide WHEN the
        #: next ``/step`` is sent, never what it computes, so a paced run and an
        #: unpaced one differ in duration and in nothing else.
        self.pacer: Optional[Any] = None

        self._trace_rows: list[tuple[str, list[bytes]]] = []

    # --- lifecycle --------------------------------------------------------

    def start_server(self) -> None:
        """Launch the headless SkyEmu process.

        The mGBA backend binds a TCP socket here and the emulator dials in
        later. SkyEmu is the server, so "make the backend reachable" is the
        launch itself — and ``wait_for_connection`` is then a ping loop rather
        than an ``accept``.
        """
        if self.proc is not None and self.proc.poll() is None:
            raise RuntimeError("SkyEmu already launched; call disconnect() first")
        if not self.binary.is_file():
            raise FileNotFoundError(
                f"no SkyEmu binary at {self.binary} — build it from the patched source "
                "(v2-experiments/skyemu-headless-arm64.patch), or set "
                "emulator.binary_path in the config"
            )
        if not self.rom_path.is_file():
            raise FileNotFoundError(f"ROM not found at {self.rom_path}")

        rom = self._stage_rom()
        self._log_file = tempfile.NamedTemporaryFile(
            prefix="skyemu_", suffix=".log", delete=False, mode="w"
        )
        self.log_path = self._log_file.name
        self.proc = subprocess.Popen(
            [str(self.binary), "http_server", str(self.port), str(rom)],
            stdout=self._log_file, stderr=subprocess.STDOUT,
        )
        self._launched_rom = rom
        print(f"SkyEmu launched (PID {self.proc.pid}) on port {self.port} — log: {self.log_path}")

    def wait_for_connection(self, timeout: float = 60.0) -> None:
        """Poll ``/ping`` until the process answers, then step ``boot_frames``.

        The timeout is generous by default because the wait is dominated by
        reading the ROM: a 128 MB NDS card off a cold page cache took over 30 s,
        where the same ROM warm answered in 4 s. A short timeout turns "the disk
        was slow" into "the backend is broken".

        Every exit path that fails kills the child. A headless SkyEmu does NOT
        exit when its client goes away — one from a crashed session was still
        holding a ROM hours later (2026-09-18) — and an orphan on the port makes
        the NEXT launch answer against a half-loaded ROM, which reads as an
        intermittent backend bug rather than as a leak.
        """
        if self.proc is None:
            raise RuntimeError("SkyEmu not launched. Call start_server() first.")
        budget = max(timeout, self.launch_timeout)
        deadline = time.time() + budget
        try:
            while time.time() < deadline:
                rc = self.proc.poll()
                if rc is not None:
                    raise ConnectionError(
                        f"SkyEmu exited with {rc} before binding port {self.port}"
                        + (f" — see {self.log_path}" if self.log_path else "")
                    )
                try:
                    if self._text(self._get("/ping", timeout=5.0)) == "pong":
                        self._connected = True
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                raise ConnectionError(
                    f"SkyEmu did not answer /ping on port {self.port} within {budget:.0f}s"
                )
        except BaseException:
            self.disconnect()
            raise

        try:
            if self.boot_frames:
                self._step(self.boot_frames)
            self._probe_system()
        except BaseException:
            self.disconnect()
            raise
        print(f"SkyEmu ready on port {self.port} — {self.system}, "
              f"booted {self.boot_frames} frames"
              + (", stylus enabled" if self.touch_enabled else ""))

    def _probe_system(self) -> None:
        """Measure which console is loaded, and refuse a stylus it cannot hold.

        This is the second of the two "no touch screen here" checks and the one
        that needs a running machine. The first — ``TAP`` on the mGBA backend —
        is answerable from the config alone and lives in that backend's
        constructor. This one is not: ``emulator.type: skyemu`` is compatible
        with a stylus, and whether THIS RUN has one depends on the ROM. SkyEmu's
        ``/status`` does not name the console, so the only way to know is to
        measure a frame (``frame.geometry``) — which means after the process
        answers and has stepped at least one frame.

        It still runs before turn 1, before a savestate is loaded and before a
        single token is billed, which is the whole point: a config that asks a
        GBA cartridge for a stylus dies here with the system named, instead of
        forty turns later with a rejected action the model cannot interpret.

        The probe runs unconditionally, not only when ``touch_enabled`` — one
        ``/screen`` is cheap and :attr:`system` is worth having on every run.
        """
        try:
            self.system = frame_mod.geometry(self._get("/screen"))[0]
        except Exception as exc:
            if not self.touch_enabled:
                return       # diagnostics only; do not fail a run over them
            raise RuntimeError(
                f"valid_inputs asks for {TAP_INPUT!r} but the console could not be "
                f"identified from a capture, so whether it has a touch screen is "
                f"unknown: {exc}"
            ) from exc
        if self.touch_enabled and self.system != TOUCH_SYSTEM:
            raise ValueError(
                f"valid_inputs lists {TAP_INPUT!r} but {self.rom_path.name} is a "
                f"{self.system} ROM, and only {TOUCH_SYSTEM} has a touch screen. "
                f"Remove {TAP_INPUT!r} from valid_inputs, or point emulator.rom_path "
                "at an NDS ROM."
            )

    def disconnect(self) -> None:
        """Kill the process and remove the staged ROM. Safe to call twice."""
        self._connected = False
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        staged, self._staged_dir = self._staged_dir, None
        if staged is not None and staged.is_dir():
            shutil.rmtree(staged, ignore_errors=True)
        self._launched_rom = None

    def ping(self) -> bool:
        """True if SkyEmu is alive. Never raises — ``launch.py`` uses it as a
        liveness poll and treats an exception as a crash."""
        try:
            if self.proc is None or self.proc.poll() is not None:
                return False
            return self._text(self._get("/ping", timeout=10.0)) == "pong"
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        """``/status`` as a dict. Not part of the backend protocol; useful for
        diagnostics and for the NDS work (P3b)."""
        return json.loads(self._get("/status"))

    # --- observation ------------------------------------------------------

    def capture_screenshot(self, preprocess: bool = True) -> Image.Image:
        """The current frame.

        ``preprocess=True`` runs :func:`src.emulator.backends.frame.prepare`:
        upscale with NEAREST, and on NDS re-stack the two screens with a seam.
        **No grid overlay** — the v2 frame is not v1's frame; see that module.
        ``False`` returns the native-resolution capture untouched.
        """
        png = self._get("/screen")
        if not preprocess:
            img = Image.open(io.BytesIO(png))
            img.load()
            return img.convert("RGB")
        img, meta = frame_mod.prepare(
            png, upscale=self.upscale_factor, divider=self.screen_divider
        )
        self.last_frame_meta = meta
        return img

    def screen_png(self) -> bytes:
        """The current frame as SkyEmu's own PNG bytes, unmodified.

        The same bytes :attr:`sampler` is handed. Public so a spectate feed can
        publish a frame WITHOUT a step — a stepped emulator shows nothing at all
        until something advances it (``dashboard/spectate.SpectateFeed.prime``).
        """
        return self._get("/screen")

    def read_memory(self, addr: int, length: int) -> bytes:
        """``length`` raw bytes at bus address ``addr`` — the referee's entire
        backend contract (``referee.py:108``).

        ``/read_byte`` takes REPEATED ``addr`` parameters and answers with the
        bytes concatenated as bare hex, so a range is one round trip rather than
        ``length`` of them. Chunked at 128 because the query string, not the
        emulator, is the limit. Raises rather than returning short — every
        caller indexes the result directly.

        The ``map`` parameter must PRECEDE the ``addr`` parameters, which is why
        the params are a list of pairs and not a dict.
        """
        if length <= 0:
            return b""
        out = bytearray()
        for start in range(0, length, 128):
            span = range(addr + start, addr + min(start + 128, length))
            params: list[tuple[str, Any]] = (
                [("map", self.memory_map)] if self.memory_map is not None else []
            )
            params += [("addr", hex(a)) for a in span]
            hexed = self._text(self._get("/read_byte", params))
            if len(hexed) != 2 * len(span):
                raise RuntimeError(
                    f"read_memory asked for {len(span)} bytes at {addr + start:#x}, "
                    f"got {len(hexed) // 2}"
                )
            try:
                out += bytes.fromhex(hexed)
            except ValueError as exc:
                raise RuntimeError(
                    f"Malformed hex in read_memory response at {addr + start:#x}: {hexed[:64]!r}"
                ) from exc
        return bytes(out)

    def write_memory(self, addr: int, data: bytes) -> None:
        """Into the RUNNING machine. Not part of the backend protocol; the
        start-state tooling and the selftest use it."""
        for i, value in enumerate(data):
            params: dict[str, Any] = {hex(addr + i): hex(value)}
            if self.memory_map is not None:
                params["map"] = self.memory_map
            self._get("/write_byte", params)

    def wait_for_stable_screen(self) -> float:
        """Step until the frame stops changing; return **emulated** seconds.

        The algorithm is the mGBA backend's, unchanged in meaning: a rolling
        window of ``num_frames`` slots, each new capture checked against every
        unique frame seen so far. A match (similarity >= ``threshold_start``) is
        a known animation frame and goes in as an empty slot; a new frame goes
        in for real. With 0 or 1 real frames in a full window the screen is
        stable; otherwise the pairwise similarity product must meet a threshold
        that relaxes linearly toward ``threshold_end`` as the budget is spent.
        That is what lets idle animations — character sway, water — settle.

        **The unit is the deliberate part.** mGBA returns host wall clock, which
        it can, because settling there means polling a free-running emulator.
        Here the emulator only moves when this method steps it, so host wall
        clock would measure the CPU that happened to run the benchmark — exactly
        the class of v1 artefact this backend exists to remove. So the return
        value is ``frames / 60``: emulated seconds, identical on every machine,
        and directly comparable with the mGBA number because that one is also
        (approximately) the game time that elapsed. ``turn.py`` logs it as a
        duration and nothing else consumes it.

        The raw count is not thrown away: it is on :attr:`last_settle_frames`
        and in the printed settle line, for a consumer that wants frames.

        mGBA's ``poll_interval`` and ``max_wait`` are seconds of wall clock;
        both are converted to frames here and the relaxation runs over frames.
        """
        n = self.stability_num_frames
        dedup_threshold = self.stability_threshold_start
        poll_frames = max(1, int(round(self.stability_poll_interval * FPS)))
        max_frames = max(poll_frames, int(round(self.stability_max_wait * FPS)))

        elapsed = 0
        seen_frames: list[np.ndarray] = []   # every unique frame seen so far
        window: list = []                    # rolling window: real frame or None

        with self._lock:
            while True:
                if elapsed >= max_frames:
                    real = [f for f in window if f is not None]
                    n_dup = len(window) - len(real)
                    print(f"    settle: {elapsed}f ({elapsed / FPS:.1f}s emulated) | "
                          f"window=[{len(real)} new, {n_dup} dup]/{n} MAX")
                    break

                self._step(poll_frames)
                elapsed += poll_frames
                frame = self._capture_raw_frame()

                is_dup = False
                best_sim = 0.0
                for sf in seen_frames:
                    sim = self._frame_similarity(frame, sf)
                    best_sim = max(best_sim, sim)
                    if sim >= dedup_threshold:
                        is_dup = True
                        break

                if is_dup:
                    window.append(None)
                else:
                    window.append(frame)
                    seen_frames.append(frame)

                if len(window) > n:
                    window = window[-n:]

                real = [f for f in window if f is not None]
                n_dup = len(window) - len(real)
                tag = "dup" if is_dup else "NEW"
                base = (f"    settle: {elapsed}f ({elapsed / FPS:.1f}s emulated) | "
                        f"{tag} best={best_sim:.4f} | window=[{len(real)} new, {n_dup} dup]/{n}")

                if len(window) < n:
                    print(f"{base} filling...")
                    continue

                if len(real) <= 1:
                    print(f"{base} ✓")
                    break

                product = 1.0
                for i in range(len(real)):
                    for j in range(i + 1, len(real)):
                        product *= self._frame_similarity(real[i], real[j])

                progress = min(1.0, max(0.0, elapsed / max_frames))
                threshold = (self.stability_threshold_start
                             + progress * (self.stability_threshold_end
                                           - self.stability_threshold_start))
                stable = product >= threshold
                print(f"{base} sim={product:.4f} thresh={threshold:.4f} "
                      f"{'✓' if stable else '✗'}")
                if stable:
                    break

        self.last_settle_frames = elapsed
        return elapsed / FPS

    # --- input ------------------------------------------------------------

    def normalize_button_list(self, buttons: list[str]) -> list[str]:
        """Full names (``["left", "up", "a"]``) -> short codes (``["L","U","A"]``).

        Byte-identical in behaviour to the mGBA backend's method for buttons:
        the model's action vocabulary must not depend on which emulator is
        behind it.

        A tap is the exception, and it stays IN PLACE in the returned list — a
        tap between two presses has to run between them, so it cannot be split
        off into a separate pass. It normalises to its canonical token
        (``tap:0.500,0.400``) rather than to a short code, because there is no
        short code that carries two coordinates.

        Three ways a tap is refused here, all ``ValueError`` so that
        ``turn.py`` rejects the model's action rather than half-running it:
        the run never asked for the stylus (``TAP`` not in ``valid_inputs``),
        the console has none (:attr:`system` is not NDS), or the coordinates are
        malformed / off the screen (:func:`src.emulator.inputs.parse_tap`).
        """
        ALIASES = {"UP": "U", "DOWN": "D", "LEFT": "L", "RIGHT": "R"}
        result = []
        for btn in buttons:
            if is_tap(btn):
                if not self.touch_enabled:
                    raise ValueError(
                        f"Invalid button: {btn!r} — this run did not enable the stylus. "
                        f"Add {TAP_INPUT!r} to valid_inputs to allow taps."
                    )
                if self.system is not None and self.system != TOUCH_SYSTEM:
                    raise ValueError(
                        f"Invalid button: {btn!r} — the loaded ROM is {self.system}, "
                        f"which has no touch screen."
                    )
                result.append(format_tap(*parse_tap(btn)))
                continue
            normalized = btn.strip().upper()
            normalized = ALIASES.get(normalized, normalized)
            if normalized == TAP_INPUT:
                raise ValueError(
                    f"Invalid button: {btn!r} — {TAP_INPUT} is a verb, not a button. "
                    "A tap carries its target: 'tap:<x>,<y>', e.g. 'tap:0.5,0.4'."
                )
            if normalized not in self.valid_inputs:
                raise ValueError(f"Invalid button: {btn!r} (normalized to {normalized!r})")
            result.append(normalized)
        return result

    def press_button_list(self, buttons: list[str]) -> None:
        """Play a list of full button names, exactly.

        mGBA sends the whole sequence to the Lua bridge and sleeps
        ``total_frames / 60 + 0.5`` s against a wall clock; here each button is
        ``step(hold)`` held and ``step(gap)`` released, which costs the game
        precisely ``hold + gap`` frames and costs the host whatever it costs.
        The per-button rules are mGBA's: A and B get ``ab_hold_frames`` /
        ``ab_gap_frames``, every other input gets ``button_hold_frames`` /
        ``frames_between_inputs``.

        ``"wait"`` presses nothing and steps ``wait_input_seconds * 60`` frames.
        A ``tap:<x>,<y>`` element is a stylus touch at that point of the touch
        screen (:meth:`tap`), run in its position in the list — a tap between
        two presses happens between them. Invalid names raise ``ValueError``
        before anything is pressed, so a model's bad action is rejected rather
        than half-executed.

        **This blocks for the whole input**, where mGBA returned while the game
        was still catching up (base.py names this). Nothing downstream depended
        on the game still moving after the call: ``turn.py`` goes straight into
        ``wait_for_stable_screen``.

        Like the Lua bridge, the LAST input waits its gap too — the walk
        animation (~16 frames) has to finish before the trace samples the tile.

        **mGBA's extra half second is gone, deliberately.** Its sleep is
        ``total_frames / 60 + 0.5``, so the free-running emulator got roughly 30
        frames MORE than the inputs cost. Stepping exactly ``hold + gap`` drops
        that slack, and a slow multi-frame transition — the stairs warp out of
        the bedroom is the one that showed it (2026-09-19) — is therefore still
        resolving when this returns. In the turn loop that is invisible, because
        ``wait_for_stable_screen`` runs next and steps until the frame stops
        moving. A caller that presses WITHOUT settling and then reads memory is
        the case that changes meaning: it has to step the transition itself.
        """
        normalized = self.normalize_button_list(buttons)
        wait_frames = int(round(self.wait_seconds * FPS))

        with self._lock:
            for btn in normalized:
                if is_tap(btn):
                    x, y = parse_tap(btn)
                    self._tap(x, y)
                    self._trace_sample(btn)
                    continue
                if btn == "WAIT":
                    # No trace row: the Lua bridge samples per QUEUED input and
                    # WAIT never enters its queue. A WAIT row would land in
                    # trace.derive's ``idle_ab`` bucket and change what that
                    # number means.
                    self._step(wait_frames)
                    continue
                key = SKYEMU_BUTTON.get(btn)
                if key is None:
                    raise ValueError(
                        f"{btn!r} is in valid_inputs but has no SkyEmu key "
                        f"(known: {sorted(SKYEMU_BUTTON)})"
                    )
                is_ab = btn in AB_BUTTONS
                hold = self.ab_hold_frames if is_ab else self.hold_frames
                gap = self.ab_gap_frames if is_ab else self.gap_frames
                self._set_inputs({key: 1})
                self._step(hold)
                self._set_inputs({key: 0})
                self._step(gap)
                self._trace_sample(btn)

        for btn in reversed(normalized):
            if btn in DIRECTION_BUTTONS:
                self.facing = DIRECTION_BUTTONS[btn]
                break

    def tap(self, x: float, y: float) -> None:
        """Touch the bottom screen at (x, y), each normalised 0..1.

        The coordinates are normalised over the **touch screen**, not over the
        256x384 capture — the touch screen is the bottom half of that image, so
        an image row *r* maps to ``(r - 192) / 192``
        (:func:`src.emulator.backends.frame.image_row_to_touch_y` does it, and
        is the only thing that should).

        Shaped like a press: set the input level, step the hold, clear it, step
        the gap. An input on SkyEmu is a LEVEL, not an edge — a set-and-clear
        inside one call registers nothing at all.

        Public for the same reason :meth:`step` is: the scripted probes drive it
        directly. The turn loop reaches it through ``press_button_list`` with a
        ``tap:<x>,<y>`` token, which is what keeps a tap ordered among presses.
        """
        with self._lock:
            self._tap(x, y)

    def _tap(self, x: float, y: float) -> None:
        if not self.touch_enabled:
            raise ValueError(
                f"This run did not enable the stylus: add {TAP_INPUT!r} to valid_inputs."
            )
        if self.system is not None and self.system != TOUCH_SYSTEM:
            raise ValueError(
                f"The loaded ROM is {self.system}, which has no touch screen."
            )
        # Round through the canonical token so the coordinate on the wire is the
        # coordinate in the log, to the digit. Also the range check.
        x, y = parse_tap(format_tap(x, y))
        self._set_inputs({SKYEMU_TAP: 1, SKYEMU_TOUCH_X: x, SKYEMU_TOUCH_Y: y})
        self._step(self.tap_hold_frames)
        # Only the tap level is cleared. touch_x/touch_y are coordinates, not a
        # held input; SkyEmu reads them while the tap level is 1 and ignores
        # them otherwise, so re-sending them as 0 would claim a touch at the
        # top-left corner rather than no touch.
        self._set_inputs({SKYEMU_TAP: 0})
        self._step(self.tap_gap_frames)

    # --- savestates -------------------------------------------------------

    def save_state(self, filepath: str) -> None:
        """Write a SkyEmu savestate (a PNG with the state embedded).

        NOT interchangeable with mGBA's: ``/load`` on
        ``configs/saves/pokebench-v1/emulator.state`` answers ``failed``
        (plan §3.4). And two saves of an identical machine differ across most of
        their bytes, because the PNG container is not reproducible — compare
        emulated memory or the framebuffer, never the ``.state`` file.
        """
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._check("/save", {"path": str(filepath)})
        except SkyEmuError as exc:
            raise RuntimeError(f"Save state failed: {exc}") from exc

    def load_state(self, filepath: str) -> None:
        """Restore a SkyEmu savestate."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"State file not found: {filepath}")
        try:
            self._check("/load", {"path": str(filepath)})
        except SkyEmuError as exc:
            raise RuntimeError(
                f"Load state failed: {exc}. A state SkyEmu refuses is usually an "
                "mGBA state — the two formats are not interchangeable (plan §3.4)."
            ) from exc

    # --- tracing ----------------------------------------------------------

    def fetch_trace(self) -> list[tuple[str, list[bytes]]]:
        """Collect (and clear) the per-input samples taken since the last call.

        ``[(input name, [bytes per trace_spec entry]), ...]`` — the shape
        ``src/referee/trace.py`` consumes, with the same short-code input names
        the Lua bridge reports (``"U"``, ``"A"``, …). A range that could not be
        read comes back as ``b""``.

        v1 samples once per INPUT, not per frame (``lua/socketserver-1.lua:266``
        samples at the end of each input's gap), and so does this: the sample is
        taken by ``press_button_list`` after the gap step. Since this backend
        owns the step loop there is no bridge-side buffer to drain and no cap to
        reach — the rows live here.
        """
        rows, self._trace_rows = self._trace_rows, []
        return rows

    def _trace_sample(self, name: str) -> None:
        """One trace row for the input just played, per :attr:`trace_spec`."""
        if not self.trace_spec:
            return
        parts: list[bytes] = []
        for entry in self.trace_spec:
            try:
                parts.append(self._read_spec_entry(entry))
            except Exception:
                parts.append(b"")
        self._trace_rows.append((name, parts))

    def _read_spec_entry(self, entry: str) -> bytes:
        """One ``trace_spec`` range.

        Grammar (``src/referee/trace.py``), identical to the Lua bridge's::

            <addr>:<len>           absolute bus address
            *<ptr>+<off>:<len>     dereference the u32 at ptr, then read at +off

        A pointer that does not land in EWRAM reads as ``b""`` — the same bound
        the bridge applies, so a spec entry means the same thing on both
        backends. Numbers are decimal or ``0x``-prefixed.
        """
        text = entry.strip()
        if text.startswith("*"):
            ptr_s, _, rest = text[1:].partition("+")
            off_s, _, len_s = rest.partition(":")
            ptr, off, length = int(ptr_s, 0), int(off_s, 0), int(len_s, 0)
            base = int.from_bytes(self.read_memory(ptr, 4), "little")
            if base < EWRAM_LO or base >= EWRAM_HI:
                return b""
            return self.read_memory(base + off, length)
        addr_s, _, len_s = text.partition(":")
        return self.read_memory(int(addr_s, 0), int(len_s, 0))

    # --- stepping ---------------------------------------------------------

    def step(self, frames: int) -> None:
        """Advance exactly ``frames``. This is the whole clock.

        Public because the start-state tooling and the smoke tests drive it
        directly; the turn loop never calls it — it presses buttons and settles.
        """
        with self._lock:
            self._step(frames)

    def _step(self, frames: int) -> None:
        """Advance ``frames``, sampling into :attr:`sampler` if one is attached.

        Stepping runs as fast as the host can, not in real time, so a large N
        simply blocks the HTTP response until it finishes — hence a long request
        timeout rather than a chunking loop. With a sampler the step IS chunked,
        because SkyEmu handles one request at a time: a ``/screen`` sent from
        another thread during a long ``/step`` does not answer until the step
        finishes, so sampling has to be interleaved, never concurrent.

        A :attr:`pacer` chunks it for the same reason from the other end: a
        throttle applied once to a 300-frame ``wait`` would stall five seconds
        in one lump, which is a freeze, not real time.
        """
        frames = int(frames)
        if frames <= 0:
            return
        self.frames_stepped += frames
        if self.sampler is None and self.pacer is None:
            self._get("/step", {"frames": frames})
            return
        left = frames
        while left > 0:
            n = min(self.sample_every, left)
            self._get("/step", {"frames": n})
            left -= n
            # Neither hook may end a run: a spectator and a recorder are
            # observers of the benchmark, never participants in it.
            if self.sampler is not None:
                try:
                    self.sampler(self._get("/screen"))
                except Exception:
                    pass
            if self.pacer is not None:
                try:
                    self.pacer(n)
                except Exception:
                    pass

    def _set_inputs(self, levels: dict[str, Any]) -> None:
        """Hold or release inputs. Keys are SkyEmu's own names."""
        self._get("/input", levels)

    # --- frame comparison -------------------------------------------------

    def _capture_raw_frame(self) -> np.ndarray:
        """A frame for stability comparison — 120x80 grayscale, as on mGBA."""
        img = Image.open(io.BytesIO(self._get("/screen")))
        img.load()
        small = img.resize((120, 80)).convert("L")
        return np.array(small, dtype=np.float32)

    @staticmethod
    def _frame_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """0-1 similarity; 1.0 identical. The mGBA backend's metric, unchanged."""
        diff = np.abs(a - b)
        mean_diff = diff.mean() / 255.0
        return 1.0 - mean_diff

    # --- transport --------------------------------------------------------

    @staticmethod
    def _text(raw: bytes) -> str:
        """SkyEmu's text replies carry a trailing NUL — ``/ping`` answers
        ``b"pong\\x00"``.

        ``bytes.strip()`` removes ASCII whitespace and NOT NUL, so a naive
        ``== b"pong"`` is false forever: the client sits in its connect loop
        until the timeout and reports the backend as dead while it is running
        and answering (2026-09-18). Every text reply goes through here.
        """
        return raw.decode("utf-8", "replace").strip("\x00 \t\r\n")

    def _get(self, path: str, params: Optional[Any] = None,
             timeout: float = 600.0) -> bytes:
        """One HTTP GET.

        ``params`` is a dict, or a LIST OF PAIRS when a key repeats — which
        ``/read_byte`` relies on: it answers one ``addr`` parameter with one
        byte, so a range is a list of pairs sharing the key ``addr``.
        """
        url = f"http://{self.host}:{self.port}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with self._lock:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()

    def _check(self, path: str, params: dict) -> None:
        """For the endpoints that answer ok/failed in the BODY, not the status.

        ``/save`` with an unwritable path returns HTTP 200 and the word
        ``failed``, so a silent no-op is the default failure mode without this.
        """
        reply = self._text(self._get(path, params)).lower()
        if reply != "ok":
            raise SkyEmuError(f"{path} {params} -> {reply!r}")

    # --- ROM staging ------------------------------------------------------

    def _stage_rom(self) -> Path:
        """The path SkyEmu is actually launched with. See ``copy_rom``."""
        if not self.copy_rom:
            return self.rom_path
        if self.rom_stage_dir:
            target_dir = Path(self.rom_stage_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
        else:
            target_dir = Path(tempfile.mkdtemp(prefix="skyemu-rom-"))
            self._staged_dir = target_dir
        target = target_dir / self.rom_path.name
        if not target.exists() or target.stat().st_size != self.rom_path.stat().st_size:
            shutil.copy2(self.rom_path, target)
        return target


__all__ = ["DEFAULT_BINARY", "SKYEMU_BUTTON", "SkyEmuClient", "SkyEmuError"]
