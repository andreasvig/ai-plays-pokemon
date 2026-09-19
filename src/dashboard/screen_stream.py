"""Background thread that captures the live emulator screen for dashboard streaming."""

import os
import threading
import time
from typing import Optional


class ScreenStreamer:
    """Polls a PNG file the emulator writes and serves it via WebSocket.

    Two writers produce that file and this class knows about neither: mGBA's Lua
    script auto-captures to /tmp/mgba_stream_1.png at 30fps, and the stepped
    (SkyEmu) backend's spectate sampler writes one per run into the run dir
    (``src/dashboard/spectate.py``). This thread watches for changes and reads
    the raw bytes — no decode/re-encode, both writers already produce PNG.

    **It will not serve a frame written before it started.** A stream file
    outlives the run that wrote it: /tmp/mgba_stream_1.png is a fixed path reused
    by every mGBA run on the machine, so a streamer pointed at it by mistake
    finds a perfectly valid PNG of a DIFFERENT GAME and serves it forever. That
    happened (2026-09-19): a Crystal run on the SkyEmu control center showed a
    four-hour-old FireRed frame, because its config named mGBA and nothing was
    writing the file it therefore watched. The run was fine; only the picture
    lied, which is the hard kind of wrong to notice.

    So the first frame has to be NEWER than this object. A live feed then shows
    nothing until the emulator actually draws — which is the honest answer, and
    an obviously broken feed beats a convincingly wrong one.
    """

    def __init__(self, stream_path: str = "/tmp/mgba_stream.png"):
        self._path = stream_path
        # Frames at or before this are somebody else's run. Nanosecond mtimes,
        # compared against the same clock st_mtime_ns reports.
        self._started_ns: int = time.time_ns()
        self._last_mtime: int = 0
        self._frame: Optional[bytes] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background capture thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="screen-streamer")
        self._thread.start()

    def stop(self) -> None:
        """Stop the background capture thread."""
        self._running = False

    def get_frame(self) -> Optional[bytes]:
        """Get the latest PNG frame bytes, or None if no frame captured yet."""
        with self._lock:
            return self._frame

    def _loop(self) -> None:
        """Poll the stream file and read raw PNG bytes when it changes."""
        while self._running:
            try:
                st = os.stat(self._path)
                mtime = st.st_mtime_ns
                # `and` rather than an early `continue`: the sleep at the
                # bottom of this loop is the only thing keeping it off a core,
                # and skipping it while a stale file sits there would busy-spin
                # for the whole run. Written before this streamer existed means
                # a leftover from an earlier run, not this one's screen — and it
                # is re-checked every poll rather than latched, because the
                # writer for THIS run may still be about to touch the same path.
                if mtime > self._started_ns and mtime != self._last_mtime:
                    self._last_mtime = mtime
                    with open(self._path, "rb") as f:
                        data = f.read()
                    # Validate complete PNG: header + IEND end marker
                    # A truncated file (read mid-write) won't have IEND
                    if (len(data) > 12
                            and data[:4] == b'\x89PNG'
                            and b'IEND' in data[-16:]):
                        with self._lock:
                            self._frame = data
            except (FileNotFoundError, OSError):
                pass  # File not yet created or being written
            except Exception:
                pass  # Skip corrupt reads
            # Poll substantially faster than the 30fps writer. Polling at the
            # exact same 33ms cadence can phase-lock badly and miss every other
            # short-lived file version, recreating a 15fps feed by accident.
            time.sleep(0.008)
