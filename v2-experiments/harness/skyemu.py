"""A SkyEmu HTTP client — the v2 backend, driven the way a turn loop needs it.

Scoped to v2-experiments on purpose. Nothing in src/ imports this; when the
backend is adopted the contract below is what gets ported into an
``EmulatorClient`` sibling, not this file.

Two facts shape the whole class (both cost time to find out — see
emulator-research.md "Using it"):

1. **Headless starts PAUSED.** Nothing advances until ``/step``. That is a
   feature for a benchmark — the emulator does not run while the model thinks,
   so a slow model and a fast one see byte-identical frames — but it means
   every input has to be sandwiched between explicit steps.
2. **An input is a level, not an edge.** ``/input?A=1`` holds A down until
   something sets it to 0. A press is therefore: set, step (the hold), clear,
   step (the gap). A set-and-clear inside one call registers nothing at all.
"""
from __future__ import annotations

import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_BINARY = Path.home() / "Applications/SkyEmu.app/Contents/MacOS/SkyEmu"

# The button names SkyEmu answers to, keyed by the lowercase names this harness
# (and the model) uses. `/status` lists ~35 inputs; these are the console's.
BUTTONS = {
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "l": "L", "r": "R", "start": "Start", "select": "Select",
}
TAP = "Tap Screen (NDS)"

# Frame geometry per system, used to identify what is running from the shape of
# a capture. `/status` does not name the system, and the capture has to be
# measured anyway (see frame.py) — so one probe answers both questions.
GEOMETRY = {(240, 160): "GBA", (160, 144): "GB", (256, 384): "NDS"}


class SkyEmuError(RuntimeError):
    pass


class SkyEmu:
    """One headless SkyEmu process, owned for the life of the `with` block."""

    def __init__(self, rom: Path, port: int = 8099,
                 binary: Path = DEFAULT_BINARY, log: Optional[Path] = None):
        self.rom = Path(rom)
        self.port = port
        self.binary = Path(binary)
        self.log = log
        self._proc: Optional[subprocess.Popen] = None
        # Attach a callable to record. Every advance of the machine goes through
        # `step`, so one hook here catches presses, taps and waits alike — there
        # is no second path by which the game can move. `sample_every` frames is
        # the sampling interval: 2 gives 30 fps of a 60 fps console.
        self.sampler: Optional[Any] = None
        self.sample_every = 2

    # --- lifecycle -------------------------------------------------------

    def __enter__(self) -> "SkyEmu":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self, timeout: float = 120.0) -> None:
        """Launch and wait for /ping.

        The timeout is generous because the wait is dominated by reading the
        ROM: a 128 MB NDS card off a cold page cache took over 30 s here, where
        the same ROM warm answered in 4 s. A short timeout turns "the disk was
        slow" into "the backend is broken".
        """
        if not self.binary.is_file():
            raise SkyEmuError(
                f"no SkyEmu at {self.binary} — build it from the patched source "
                "(see v2-experiments/skyemu-headless-arm64.patch)")
        if not self.rom.is_file():
            raise SkyEmuError(f"no ROM at {self.rom}")
        out = open(self.log, "w") if self.log else subprocess.DEVNULL
        self._proc = subprocess.Popen(
            [str(self.binary), "http_server", str(self.port), str(self.rom)],
            stdout=out, stderr=subprocess.STDOUT,
        )
        # Every exit from here kills the child. A headless instance outlives
        # its client, so a start() that raises without this leaves a process
        # holding the port — and the NEXT start() then fails against the
        # orphan's half-loaded ROM, which reads as an intermittent backend bug.
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    raise SkyEmuError(
                        f"SkyEmu exited with {self._proc.returncode} before binding "
                        f"port {self.port}" + (f" — see {self.log}" if self.log else ""))
                try:
                    if self._text(self._get("/ping", timeout=5.0)) == "pong":
                        return
                except OSError:
                    time.sleep(0.2)
            raise SkyEmuError(f"SkyEmu did not answer /ping within {timeout}s")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        """Kill it explicitly. A headless instance does not exit when its client
        goes away — one from a crashed session was still holding a ROM hours
        later (2026-09-18)."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    # --- transport -------------------------------------------------------

    @staticmethod
    def _text(raw: bytes) -> str:
        """SkyEmu's text replies carry a trailing NUL — `/ping` answers b"pong\x00".

        `bytes.strip()` removes ASCII whitespace and NOT NUL, so a naive
        `== b"pong"` is false forever: the client sits in its connect loop until
        the timeout and reports the backend as dead while it is running and
        answering. Every text reply goes through here (2026-09-18).
        """
        return raw.decode("utf-8", "replace").strip("\x00 \t\r\n")

    def _get(self, path: str, params: Optional[Any] = None, timeout: float = 600.0) -> bytes:
        """`params` is a dict, or a list of pairs when a key repeats — which
        `/read_byte` relies on: it answers one `addr` parameter with one byte,
        so a range is a list of pairs sharing the key `addr`."""
        url = f"http://127.0.0.1:{self.port}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()

    # --- the contract ----------------------------------------------------

    def status(self) -> dict[str, Any]:
        import json
        return json.loads(self._get("/status"))

    def step(self, frames: int) -> None:
        """Advance exactly `frames`. This is the whole clock.

        Stepping runs as fast as the host can, not in real time, so a large N
        simply blocks the HTTP response until it finishes — hence the long
        default timeout rather than a chunking loop here.

        With a `sampler` attached the step is broken into `sample_every`-frame
        chunks with a capture after each, which is what makes a recording
        possible at all: SkyEmu's HTTP server handles one request at a time, so
        a /screen sent from another thread during a long /step does not answer
        until the step finishes. Sampling has to be interleaved, not concurrent.
        Measured cost: 54 captures/s on Platinum, so a 30 fps recording renders
        at ~1.8x real time.
        """
        frames = int(frames)
        if self.sampler is None:
            self._get("/step", {"frames": frames})
            return
        left = frames
        while left > 0:
            n = min(self.sample_every, left)
            self._get("/step", {"frames": n})
            left -= n
            self.sampler(self.screen())

    def set_inputs(self, **levels: float) -> None:
        """Hold or release inputs. Keys are SkyEmu's own names."""
        self._get("/input", {k: v for k, v in levels.items()})

    def press(self, button: str, hold: int = 12, gap: int = 24) -> None:
        name = BUTTONS[button.lower()]
        self.set_inputs(**{name: 1})
        self.step(hold)
        self.set_inputs(**{name: 0})
        self.step(gap)

    def tap(self, x: float, y: float, hold: int = 20, gap: int = 24) -> None:
        """Touch the bottom screen at (x, y), each normalised 0..1.

        `touch_x`/`touch_y` are this project's patch (they do not exist
        upstream) and they are normalised over the TOUCH SCREEN, not over the
        256x384 capture — the touch screen is the bottom half of that image, so
        image row r maps to touch_y = (r - 192) / 192.
        """
        x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        self.set_inputs(**{TAP: 1, "touch_x": x, "touch_y": y})
        self.step(hold)
        self.set_inputs(**{TAP: 0})
        self.step(gap)

    def screen(self) -> bytes:
        """PNG of the whole console.

        On NDS this is 256x384: the top screen's framebuffer followed by the
        bottom screen's, stacked vertically. That is `se_screenshot` in
        SkyEmu's main.c, a straight memcpy of framebuffer_top then
        framebuffer_bottom — not a GUI layout setting, so no configuration can
        turn it into a side-by-side image. frame.py asserts it anyway.
        """
        return self._get("/screen")

    def system(self) -> str:
        """'NDS' / 'GBA' / 'GB', measured from a capture."""
        from frame import geometry
        return geometry(self.screen())[0]

    def save_state(self, path: Path) -> None:
        self._check("/save", {"path": str(path)})

    def load_state(self, path: Path) -> None:
        self._check("/load", {"path": str(path)})

    def read_byte(self, addr: int, map_: Optional[int] = None) -> int:
        """One byte. `map_` selects the NDS CPU's address space: 9 = ARM9, 7 = ARM7.

        The reply is bare hex digits with no prefix and no `0x`, so it is parsed
        base-16 explicitly — `int(x, 0)` reads "10" as ten.
        """
        params: dict[str, Any] = {"addr": hex(addr)}
        if map_ is not None:
            params["map"] = map_
        return int(self._text(self._get("/read_byte", params)), 16)

    def read_memory(self, addr: int, length: int, map_: Optional[int] = None) -> bytes:
        """`length` bytes from `addr` — the referee's whole emulator contract.

        ``src/referee/referee.py`` asks for exactly one method from a backend:
        ``read_memory(addr, length) -> bytes``. This is it, so a v2 referee is a
        swap of the object, not a rewrite of the referee.

        `/read_byte` takes REPEATED `addr` parameters and answers with the bytes
        concatenated as bare hex, so a range is one round trip rather than
        `length` of them. Measured on Platinum: ~4,800 requests/s at 64 bytes a
        request, and a 1-frame step plus a 64-byte read sustains 156 frames/s —
        2.6x real time. Large reads are chunked because the query string, not
        the emulator, is the limit.
        """
        out = bytearray()
        for start in range(0, length, 128):
            span = range(addr + start, addr + min(start + 128, length))
            params = [("map", map_)] if map_ is not None else []
            params += [("addr", hex(a)) for a in span]
            hexed = self._text(self._get("/read_byte", params))
            if len(hexed) != 2 * len(span):
                raise SkyEmuError(
                    f"asked for {len(span)} bytes at {addr + start:#x}, got {len(hexed)//2}")
            out += bytes.fromhex(hexed)
        return bytes(out)

    def write_byte(self, addr: int, value: int, map_: Optional[int] = None) -> None:
        """Into the RUNNING machine. `/save` afterwards is what makes it durable."""
        params: dict[str, Any] = {hex(addr): hex(value)}
        if map_ is not None:
            params["map"] = map_
        self._get("/write_byte", params)

    def _check(self, path: str, params: dict) -> None:
        """For the endpoints that answer ok/failed in the body instead of a status.

        `/save` with an unwritable path returns HTTP 200 and the word "failed",
        so a silent no-op is the default failure mode without this.
        """
        reply = self._text(self._get(path, params)).lower()
        if reply != "ok":
            raise SkyEmuError(f"{path} {params} -> {reply!r}")
