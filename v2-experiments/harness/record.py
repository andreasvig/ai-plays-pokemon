"""MP4 of a run, sampled straight out of the emulator.

``src/dashboard/recorder.py`` records a board run by launching its OWN headless
Chrome against the dashboard and compositing the emulator's frames into the
game rectangle — because the requirement there (Andreas, 2026-08-01) is a file
that is "independent of where on the interface the viewer is", and because the
presentation card around the game is part of what is being recorded.

Headless has no browser and no dashboard, so this is only that pipeline's second
half: emulator frames in, H.264 out, same encoder settings. What it therefore
does NOT produce is the card, the typography, or the detailed instrument-panel
view. It produces the game.

Two things the dashboard recorder needs machinery for are free here:

- **Cut-thinking needs no gate.** There, the emulator runs continuously and the
  recorder has to open and close a gate on ``llm_output``/``screen_settled`` to
  keep the model's thinking time out of the file. Here the emulator is frozen
  unless something calls ``/step``, so the model's thinking time is not in the
  frame stream to begin with — a recording of game time is what you get by
  doing nothing.
- **Constant frame rate needs no sampler thread.** Frames arrive because the
  turn loop asked for them, ``sample_every`` at a time, so N frames written IS
  N/fps seconds of game time. There is no clock to drift against.

Realtime is the mode that costs something here, and it is ``hold()``: while the
model thinks, the machine is genuinely stopped, so the faithful rendering of
those seconds is the frozen frame repeated. That is a construction, not a
capture, and it is why ``game`` is the default.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image

FPS = 30


class Recorder:
    """Raw RGB into ffmpeg. Degrades to "no recording", never to a failed run."""

    def __init__(self, out_path: Path, size: tuple[int, int],
                 fps: int = FPS, upscale: int = 3):
        self.out_path = Path(out_path)
        self.width, self.height = size
        self.fps = fps
        self.upscale = upscale
        self.frames = 0
        self.error: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._stderr = None
        self._last: Optional[bytes] = None

    # --- lifecycle -------------------------------------------------------

    def start(self) -> bool:
        if shutil.which("ffmpeg") is None:
            self.error = "ffmpeg not on PATH (brew install ffmpeg)"
            return False
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        # ffmpeg's stderr goes to a file, not DEVNULL: when the MP4 does not
        # materialise, the reason is the only thing that explains it.
        self._stderr = tempfile.NamedTemporaryFile(suffix=".ffmpeg.log", delete=False)
        try:
            self._proc = subprocess.Popen(
                self._args(), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=self._stderr,
            )
        except OSError as exc:
            self.error = f"could not start ffmpeg: {exc}"
            return False
        return True

    def stop(self) -> Optional[Path]:
        """Close the pipe, wait, and return the file — or None with `error` set."""
        if self._proc is None:
            return None
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.kill()
        self._proc = None
        if self._stderr:
            self._stderr.close()
        if self.out_path.is_file() and self.out_path.stat().st_size > 0:
            self._drop_log()
            return self.out_path
        self.error = self._ffmpeg_error() or "ffmpeg produced no output"
        return None

    # --- frames ----------------------------------------------------------

    def add(self, png: bytes) -> None:
        """One sampled frame. Anything unwritable stops recording, not the run."""
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            rgb = Image.open(io.BytesIO(png)).convert("RGB")
            if rgb.size != (self.width, self.height):
                # A geometry change mid-run means the raw stream and ffmpeg's
                # declared -video_size no longer agree, which silently produces
                # a sheared picture rather than an error.
                self.error = f"frame is {rgb.size}, recorder was opened for {(self.width, self.height)}"
                self._proc.kill()
                self._proc = None
                return
            self._last = rgb.tobytes()
            self._proc.stdin.write(self._last)
            self.frames += 1
        except (BrokenPipeError, OSError) as exc:
            self.error = f"ffmpeg pipe closed: {exc}"
            self._proc = None

    def hold(self, seconds: float) -> None:
        """Repeat the last frame — the realtime rendering of a stopped machine."""
        if self._last is None or self._proc is None:
            return
        for _ in range(int(round(seconds * self.fps))):
            if self._proc is None or self._proc.stdin is None:
                return
            try:
                self._proc.stdin.write(self._last)
                self.frames += 1
            except (BrokenPipeError, OSError) as exc:
                self.error = f"ffmpeg pipe closed: {exc}"
                self._proc = None
                return

    @property
    def seconds(self) -> float:
        return self.frames / self.fps

    # --- internals -------------------------------------------------------

    def _args(self) -> list[str]:
        # Encoder settings match src/dashboard/recorder.py so the two files look
        # like each other in a player. The scale filter is the one addition:
        # native frames go in and the upscale happens once, in ffmpeg, with
        # NEIGHBOR — smoothing a 1px game font is worse than not scaling at all,
        # and doing it here keeps ~2.6 MB/frame of upscaled RGB off the pipe.
        w, h = self.width * self.upscale, self.height * self.upscale
        return [
            "ffmpeg", "-y", "-loglevel", "error",
            "-thread_queue_size", str(self.fps * 10),
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.fps),
            "-i", "-",
            "-an",
            # trunc(/2)*2: libx264 with yuv420p cannot encode an odd dimension
            # and fails by writing an empty file rather than warning.
            "-vf", f"scale={w}:{h}:flags=neighbor,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "medium", "-tune", "animation",
            "-crf", "14", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(self.out_path),
        ]

    def _ffmpeg_error(self) -> Optional[str]:
        if not self._stderr:
            return None
        try:
            text = Path(self._stderr.name).read_text(errors="replace").strip()
        except OSError:
            return None
        return "ffmpeg: " + " | ".join(text.splitlines()[-3:]) if text else None

    def _drop_log(self) -> None:
        if self._stderr:
            Path(self._stderr.name).unlink(missing_ok=True)
