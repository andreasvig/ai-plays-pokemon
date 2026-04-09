"""Background thread that captures the live emulator screen for dashboard streaming."""

import os
import threading
import time
from typing import Optional


class ScreenStreamer:
    """Polls a PNG file written by Lua and serves it via WebSocket.

    The Lua script in mGBA auto-captures to /tmp/mgba_stream.png every few frames.
    This thread watches the file for changes and reads the raw bytes directly —
    no decode/re-encode needed since Lua already writes PNG.
    """

    def __init__(self, stream_path: str = "/tmp/mgba_stream.png"):
        self._path = stream_path
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
                if mtime != self._last_mtime:
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
            time.sleep(0.033)  # ~30Hz poll rate (captures at 15fps from Lua)
