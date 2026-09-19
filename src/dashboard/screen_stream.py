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

    ``require_fresh`` decides whether a frame already on disk may be served, and
    the answer depends entirely on whether the PATH is shared.

    A SHARED path outlives the run that wrote it. /tmp/mgba_stream_1.png is a
    fixed name every mGBA run on the machine reuses, so a streamer pointed at it
    by mistake finds a perfectly valid PNG of a DIFFERENT GAME and serves it
    forever. That happened (2026-09-19): a Crystal run on the SkyEmu control
    center showed a four-hour-old FireRed frame. There, the first frame must be
    newer than this object — an obviously broken feed beats a convincing wrong
    one, and mGBA's Lua writes at 30fps so a real frame arrives in ~33ms.

    A PER-RUN path (``<run_dir>/stream.png``, the stepped backend's feed) cannot
    hold anyone else's frame: the directory is this run's. Requiring freshness
    there rejects the run's own PRIMED frame, and that is not hypothetical — it
    is what the first version of this guard did, hours later the same day. The
    spectate feed primes deliberately, because a stepped emulator does not
    advance until the model has answered: ``attach()`` writes a frame, and only
    then does ``start_dashboard`` construct this object. So the primed frame is
    ALWAYS older than the streamer, and always legitimate.

    The cost of getting that wrong was two bugs at once, neither obviously about
    a timestamp. The live feed stayed blank until the first turn completed
    (Andreas: "we only get to see the live feed once the first turn has actually
    begun"), and every recording failed with ``simple view never exposed its
    game-screen rectangle`` — the recorder measures the page's game <img>, an
    <img> with no frame has no natural size, and no size means no crop rectangle.

    Hence the rule is about the PATH, not the clock: a file only this run can
    have written is trusted; a shared one has to prove it is current.
    """

    def __init__(
        self,
        stream_path: str = "/tmp/mgba_stream.png",
        *,
        require_fresh: bool = True,
    ):
        self._path = stream_path
        self._require_fresh = require_fresh
        # Frames at or before this are somebody else's run — consulted only when
        # require_fresh. Nanoseconds, the same clock st_mtime_ns reports.
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
                # for the whole run. Re-checked every poll rather than latched,
                # because on a shared path the writer for THIS run is usually
                # about to overwrite the leftover.
                fresh = not self._require_fresh or mtime > self._started_ns
                if fresh and mtime != self._last_mtime:
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
