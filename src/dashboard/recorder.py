"""Server-side MP4 recorder for a live run.

THE INDEPENDENCE REQUIREMENT (Andreas, 2026-08-01): a recording must be produced
"independent of where on the interface the viewer is, and independently of if I
had focus or have the web app minimised."

That rules out every in-page capture route (MediaRecorder, canvas grab, a
screen-recorder pointed at the user's window): all of them are hostage to the
viewer's tab, its route, and Chrome's background throttling. So the recorder does
NOT capture the user's browser at all. It launches its OWN headless Chrome
against the same dashboard, on a URL that pins the run and the presentation, and
streams frames out of it over the DevTools protocol into ffmpeg. The user can be
on Home, on History, or have the whole app closed — the recording is unaffected.

Pipeline:

    headless Chrome ──Page.startScreencast──> latest JPEG (kept, not queued)
                                                   │
                          sampler thread @ fps ────┤ (skipped while gated shut)
                                                   ▼
                                    ffmpeg -f image2pipe → H.264 MP4

The sampler is what makes both speed modes one mechanism. It ticks at a fixed
rate and writes whatever the newest frame is, so the output is constant-frame-
rate by construction and wall-clock-faithful:

  - ``realtime``      — the gate is open for the whole run, so every pause the
                        model takes is in the file at its true length.
  - ``cut-thinking``  — the gate opens at ``llm_output`` (the model has answered;
                        the turn starts executing) and shuts a beat after
                        ``screen_settled`` (the emulator has stopped moving).
                        The model's response time is simply never sampled, so it
                        doesn't appear in the video — no post-hoc editing, no
                        timestamp arithmetic.

Those three event names are the same ones ``SimpleView.svelte`` keys its phase
machine on. ``button_sequence`` is deliberately NOT used: it is logged AFTER
``press_button_list()`` returns, so it marks the END of pressing.

Everything here degrades to "no recording" rather than failing a run: a missing
ffmpeg, a missing Chrome, or a CDP hiccup logs a line and leaves the run alone.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ── geometry ────────────────────────────────────────────────────────────────
# The simple view's stage is `min(100vw, 100vh)` square, so a square viewport
# makes the stage exactly fill the frame — the recording IS the 1:1 view, with
# no cropping step and no letterbox bars. It also sidesteps the box-geometry
# drift noted in the simple-view follow-ups: `.stage`'s percentage padding
# resolves against viewport WIDTH, which only diverges from the stage's own
# width on a non-square window.
#
# The detailed view is the whole wide instrument panel, so it gets 1080p.
VIEWPORTS = {
    "simple": (1080, 1080),
    "detailed": (1920, 1080),
}

# Seconds of recording kept after `screen_settled` in cut-thinking mode. The
# settled screen is the payoff frame of the turn; cutting on the event itself
# lands the video on the last frame of motion instead.
SETTLE_TAIL_S = 0.9

# How long a cut-thinking gate may stay open with no `screen_settled` before it
# shuts itself. Guards against a turn that errors out between the two events and
# would otherwise record the whole of the NEXT think.
GATE_MAX_OPEN_S = 90.0

JPEG_QUALITY = 82

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def find_chrome() -> Optional[str]:
    """First usable Chrome/Chromium binary, or None."""
    for cand in CHROME_CANDIDATES:
        if "/" in cand:
            if Path(cand).is_file():
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


def recorder_preflight() -> Optional[str]:
    """``None`` if a recording can be made, else a human-readable reason."""
    if find_chrome() is None:
        return "no Chrome/Chromium binary found (needed to render the view headlessly)"
    if shutil.which("ffmpeg") is None:
        return "ffmpeg not on PATH (brew install ffmpeg)"
    try:
        import websockets.sync.client  # noqa: F401
    except Exception:
        return "the `websockets` package is missing (needed to speak CDP)"
    return None


def normalize_spec(raw: Any) -> Optional[dict]:
    """Coerce a record spec from any of its wire shapes → a plain dict or None.

    Accepts the pydantic model, a dict, a bare view string (``"simple"``), or
    None/False. Raises ``ValueError`` on an unknown view or speed so a bad CLI
    flag or API body fails loudly at the edge rather than silently recording the
    wrong thing.
    """
    if raw is None or raw is False:
        return None
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if isinstance(raw, str):
        raw = {"view": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"record spec must be a dict or a view name, got {type(raw).__name__}")

    # `is None` rather than truthiness on every field: `or` would read fps=0 as
    # "absent" and silently substitute 30, turning an invalid request into a
    # valid-looking one. Absent means absent; 0 means 0 and is rejected below.
    view = str(raw["view"]) if raw.get("view") is not None else "simple"
    speed = str(raw["speed"]) if raw.get("speed") is not None else "realtime"
    if view not in VIEWPORTS:
        raise ValueError(f"unknown record view {view!r} (expected: {', '.join(VIEWPORTS)})")
    if speed not in ("realtime", "cut-thinking"):
        raise ValueError(f"unknown record speed {speed!r} (expected: realtime, cut-thinking)")
    try:
        fps = 30 if raw.get("fps") is None else int(raw["fps"])
    except (TypeError, ValueError):
        raise ValueError(f"record fps must be a whole number, got {raw.get('fps')!r}")
    if not 1 <= fps <= 60:
        raise ValueError(f"record fps must be 1..60, got {fps}")
    return {"view": view, "speed": speed, "fps": fps}


def record_url(port: int, run_id: str, view: str) -> str:
    """The pinned, chrome-free URL the recorder's browser loads.

    ``run`` pins the run id rather than reading it from ``/api/emulator/status``:
    that field goes null the moment the run ends, which would drop the recorder
    out of the view for the final seconds. ``record=1`` suppresses the exit
    affordance and the localStorage round-trip.
    """
    return f"http://127.0.0.1:{port}/spectate?record=1&view={view}&run={run_id}"


# ───────────────────────────── the gate ─────────────────────────────


class RecordGate:
    """Decides, at any instant, whether the sampler should be writing frames.

    Split out from the recorder with no I/O in it so the cut-thinking state
    machine is testable without a browser, an encoder, or a run.
    """

    def __init__(self, speed: str, *, tail_s: float = SETTLE_TAIL_S,
                 max_open_s: float = GATE_MAX_OPEN_S) -> None:
        self.speed = speed
        self.tail_s = tail_s
        self.max_open_s = max_open_s
        # realtime records everything from start(); cut-thinking waits for the
        # first llm_output.
        self._open = speed == "realtime"
        self._opened_at: Optional[float] = None
        self._closes_at: Optional[float] = None

    def on_event(self, etype: str, now: float) -> None:
        """Fold one run event into the gate state."""
        if self.speed != "cut-thinking":
            return
        if etype == "llm_output":
            # The model has answered — the turn starts executing here.
            self._open = True
            self._opened_at = now
            self._closes_at = None
        elif etype == "screen_settled" and self._open:
            # Screen has stopped moving. Hold a beat, then stop sampling; the
            # gap until the next llm_output is the think we're cutting.
            self._closes_at = now + self.tail_s
        elif etype == "turn_start":
            # A new turn began thinking. Only meaningful if a prior turn never
            # settled — close immediately rather than record the new think.
            if self._open and self._closes_at is None and self._opened_at is not None:
                self._closes_at = now

    def is_open(self, now: float) -> bool:
        """Whether frames should be written right now."""
        if self.speed == "realtime":
            return True
        if not self._open:
            return False
        if self._closes_at is not None and now >= self._closes_at:
            self._open = False
            self._opened_at = None
            self._closes_at = None
            return False
        if self._opened_at is not None and now - self._opened_at > self.max_open_s:
            self._open = False
            self._opened_at = None
            self._closes_at = None
            return False
        return True


# ───────────────────────────── CDP plumbing ─────────────────────────────


class _CDPSession:
    """A minimal DevTools client: launch Chrome, subscribe to the screencast.

    Deliberately hand-rolled over ``websockets`` rather than adding Playwright
    or Puppeteer — the whole surface used here is four CDP methods, and the repo
    has no node/browser-automation dependency to hang a heavier one off.
    """

    def __init__(self, url: str, width: int, height: int, chrome: str) -> None:
        self.url = url
        self.width = width
        self.height = height
        self.chrome = chrome
        self.proc: Optional[subprocess.Popen] = None
        self.profile_dir: Optional[str] = None
        self._ws = None
        self._send_lock = threading.Lock()
        self._next_id = 0
        self._reader: Optional[threading.Thread] = None
        self._running = False
        self._frame_lock = threading.Lock()
        self._frame: Optional[bytes] = None
        self._frames_seen = 0

    # --- lifecycle ---

    def start(self, boot_timeout: float = 25.0) -> None:
        self.profile_dir = tempfile.mkdtemp(prefix="pokebench-rec-")
        args = [
            self.chrome,
            "--headless=new",
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-port=0",
            f"--window-size={self.width},{self.height}",
            "--force-device-scale-factor=1",
            "--hide-scrollbars",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            # The whole point of this module: frames must keep coming when the
            # renderer is not "visible" to anybody.
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--autoplay-policy=no-user-gesture-required",
            self.url,
        ]
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        ws_url = self._await_target(boot_timeout)
        from websockets.sync.client import connect

        self._ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=10)
        self._running = True
        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name="cdp-reader"
        )
        self._reader.start()
        self._send("Page.enable")
        # `--window-size` is NOT the viewport. Measured 2026-08-01: asking for
        # 1080x1080 produced a 1080x993 content area — Chrome takes its cut even
        # headless. That broke two things at once: the simple view's stage is
        # `min(100vw, 100vh)`, so a non-square viewport letterboxes the 1:1 frame
        # instead of filling it, and the odd 993 made libx264 exit -22 ("Invalid
        # argument") before writing a single packet — a zero-byte mp4 at the end
        # of the run. setDeviceMetricsOverride sets the viewport exactly.
        self._send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self.width,
                "height": self.height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        self._send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": JPEG_QUALITY,
                "maxWidth": self.width,
                "maxHeight": self.height,
                "everyNthFrame": 1,
            },
        )

    def _await_target(self, timeout: float) -> str:
        """Poll DevToolsActivePort + /json/list until the page target exists."""
        port_file = Path(self.profile_dir or "") / "DevToolsActivePort"
        deadline = time.time() + timeout
        port: Optional[int] = None
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"chrome exited early (code {self.proc.returncode})")
            try:
                first = port_file.read_text().splitlines()[0].strip()
                port = int(first)
                break
            except Exception:
                time.sleep(0.1)
        if port is None:
            raise RuntimeError("chrome never published a DevTools port")
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2
                ) as r:
                    targets = json.loads(r.read().decode())
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.15)
        raise RuntimeError("chrome never exposed a page target")

    def stop(self) -> None:
        self._running = False
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        if self.profile_dir:
            shutil.rmtree(self.profile_dir, ignore_errors=True)

    # --- protocol ---

    def _send(self, method: str, params: Optional[dict] = None) -> None:
        """Fire a CDP command. We never need a reply, so nothing is awaited."""
        with self._send_lock:
            self._next_id += 1
            payload = {"id": self._next_id, "method": method}
            if params:
                payload["params"] = params
            if self._ws is not None:
                self._ws.send(json.dumps(payload))

    def _read_loop(self) -> None:
        while self._running:
            try:
                raw = self._ws.recv(timeout=1.0)  # type: ignore[union-attr]
            except TimeoutError:
                continue
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("method") != "Page.screencastFrame":
                continue
            params = msg.get("params") or {}
            try:
                data = base64.b64decode(params.get("data", ""))
            except Exception:
                data = b""
            if data:
                with self._frame_lock:
                    self._frame = data
                    self._frames_seen += 1
            # Chrome stops sending frames until each one is acked.
            sid = params.get("sessionId")
            if sid is not None:
                self._send("Page.screencastFrameAck", {"sessionId": sid})

    def latest_frame(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._frame

    @property
    def frames_seen(self) -> int:
        with self._frame_lock:
            return self._frames_seen

    def wait_for_first_frame(self, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.latest_frame() is not None:
                return True
            time.sleep(0.1)
        return False


# ───────────────────────────── the recorder ─────────────────────────────


class RunRecorder:
    """Records one run to ``<run_dir>/recording.mp4``.

    Start it the instant the run dir is known, stop it when the run returns.
    Both are best-effort: a recorder that fails to boot reports the reason and
    leaves the run untouched.
    """

    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        port: int,
        spec: dict,
        out_path: Optional[Path] = None,
        chrome: Optional[str] = None,
    ) -> None:
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.port = port
        self.spec = spec
        self.view = spec["view"]
        self.speed = spec["speed"]
        self.fps = spec["fps"]
        self.width, self.height = VIEWPORTS[self.view]
        self.out_path = Path(out_path) if out_path else self.run_dir / "recording.mp4"
        self.chrome = chrome or find_chrome()

        self.gate = RecordGate(self.speed)
        self.error: Optional[str] = None
        self.frames_written = 0

        self._cdp: Optional[_CDPSession] = None
        self._ff: Optional[subprocess.Popen] = None
        self._ff_err: Any = None
        self._stop = threading.Event()
        self._sampler: Optional[threading.Thread] = None
        self._events: Optional[threading.Thread] = None
        self._started = False

    # --- lifecycle ---

    def start(self) -> bool:
        """Boot Chrome + ffmpeg and begin sampling. False if it couldn't."""
        reason = recorder_preflight()
        if reason:
            self.error = reason
            return False
        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            self._cdp = _CDPSession(
                record_url(self.port, self.run_id, self.view),
                self.width,
                self.height,
                self.chrome or "",
            )
            self._cdp.start()
            if not self._cdp.wait_for_first_frame():
                raise RuntimeError("no screencast frame arrived within 20s")
            # ffmpeg's stderr goes to a temp file, not /dev/null: when a
            # recording comes out empty the encoder's own last words are the
            # only thing that says why, and a silent zero-byte mp4 at the end of
            # a real run is not a diagnosis anyone can act on.
            self._ff_err = tempfile.NamedTemporaryFile(
                prefix="pokebench-rec-", suffix=".log", delete=False
            )
            self._ff = subprocess.Popen(
                self._ffmpeg_args(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._ff_err,
            )
        except Exception as e:  # noqa: BLE001 — never take the run down with us
            self.error = f"{type(e).__name__}: {e}"
            self._teardown()
            return False

        self._started = True
        self._sampler = threading.Thread(
            target=self._sample_loop, daemon=True, name="rec-sampler"
        )
        self._sampler.start()
        if self.speed == "cut-thinking":
            self._events = threading.Thread(
                target=self._event_loop, daemon=True, name="rec-gate"
            )
            self._events.start()
        return True

    def stop(self, timeout: float = 60.0) -> Optional[Path]:
        """Finish the file. Returns the mp4 path, or None if nothing was made."""
        if not self._started:
            self._teardown()
            return None
        # A short grace period so the final settled screen makes the cut — the
        # run has just ended and the page may still be painting its last frame.
        time.sleep(min(SETTLE_TAIL_S, 1.0))
        self._stop.set()
        if self._sampler is not None:
            self._sampler.join(timeout=5)
        if self._events is not None:
            self._events.join(timeout=2)
        if self._ff is not None:
            try:
                if self._ff.stdin:
                    self._ff.stdin.close()
            except Exception:
                pass
            try:
                self._ff.wait(timeout=timeout)
            except Exception:
                self._ff.kill()
        self._teardown()
        if self.frames_written == 0:
            self.error = self.error or "no frames were sampled (gate never opened?)"
            return None
        # An mp4 that exists but is zero bytes is the signature of an encoder
        # that died on its first frame — treat it as a failure, not a result.
        if self.out_path.exists() and self.out_path.stat().st_size > 0:
            return self.out_path
        self.error = self._ffmpeg_error() or "ffmpeg produced no output"
        return None

    def _ffmpeg_error(self) -> Optional[str]:
        """Tail of ffmpeg's stderr, for when the file didn't materialise."""
        try:
            if self._ff_err is None:
                return None
            self._ff_err.flush()
            text = Path(self._ff_err.name).read_text(errors="replace").strip()
        except Exception:
            return None
        if not text:
            return None
        return "ffmpeg: " + " | ".join(text.splitlines()[-3:])

    def _teardown(self) -> None:
        if self._cdp is not None:
            try:
                self._cdp.stop()
            except Exception:
                pass
            self._cdp = None

    # --- workers ---

    def _ffmpeg_args(self) -> list[str]:
        return [
            "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-framerate", str(self.fps),
            "-i", "-",
            "-an",
            # Safety net for the odd-dimension trap above: libx264 with yuv420p
            # cannot encode an odd width or height, and it fails by producing an
            # empty file rather than a warning. Rounding down here means a
            # surprise viewport costs one pixel, not the whole recording.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(self.out_path),
        ]

    def _sample_loop(self) -> None:
        """Write the newest frame every 1/fps, whenever the gate is open.

        Sampling (rather than forwarding every screencast frame) is what makes
        the output constant-frame-rate and both speed modes the same code: a
        shut gate simply produces no samples, so the time it covers does not
        exist in the file.
        """
        period = 1.0 / self.fps
        next_tick = time.monotonic()
        while not self._stop.is_set():
            next_tick += period
            sleep = next_tick - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                # Fell behind (a slow encode, a busy machine). Resync rather
                # than spin trying to catch up — a dropped sample costs 33ms of
                # video, a catch-up burst distorts the whole timeline.
                next_tick = time.monotonic()
            if not self.gate.is_open(time.monotonic()):
                continue
            frame = self._cdp.latest_frame() if self._cdp else None
            if frame is None:
                continue
            try:
                self._ff.stdin.write(frame)  # type: ignore[union-attr]
                self.frames_written += 1
            except (BrokenPipeError, ValueError, AttributeError):
                break

    def _event_loop(self) -> None:
        """Drive the cut-thinking gate off the run's own event stream.

        The recorder subscribes independently rather than reading the rendered
        page, so the gate is view-agnostic — it works identically for the
        detailed view, which publishes no phase attribute.
        """
        url = f"ws://127.0.0.1:{self.port}/runs/{self.run_id}/ws/events"
        from websockets.sync.client import connect

        while not self._stop.is_set():
            try:
                with connect(url, open_timeout=5, max_size=32 * 1024 * 1024) as ws:
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=1.0)
                        except TimeoutError:
                            continue
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if msg.get("type") != "event":
                            continue
                        etype = (msg.get("data") or {}).get("type", "")
                        self.gate.on_event(etype, time.monotonic())
            except Exception:
                # The socket 1008s until the run registers, and drops when it
                # unregisters. Neither is an error worth surfacing.
                if self._stop.wait(1.0):
                    return


# ───────────────────── integration helper (one call site) ─────────────────


def maybe_start(config: dict, run_dir: Path, run_id: str) -> Optional[RunRecorder]:
    """Start a recorder if ``config['_record']`` asks for one.

    The spec rides on the config under a private ``_record`` key — the same
    convention ``_llm_alias`` / ``_config_path`` already use — so it reaches
    ``run_single_loop`` from BOTH entry points (the CLI's ``--record`` and the
    executor's queued ``record`` spec) without either having to thread a new
    argument through the run function's signature.
    """
    try:
        spec = normalize_spec(config.get("_record"))
    except ValueError as e:
        print(f"  ⚠ recording disabled: {e}")
        return None
    if spec is None:
        return None

    from src.dashboard.server import get_server_port

    port = get_server_port()
    if port is None:
        print("  ⚠ recording disabled: the dashboard server is not bound")
        return None

    rec = RunRecorder(run_id=run_id, run_dir=Path(run_dir), port=port, spec=spec)
    if rec.start():
        print(
            f"  ⏺ recording {spec['view']} view ({spec['speed']}, {spec['fps']}fps) "
            f"→ {rec.out_path}"
        )
        return rec
    print(f"  ⚠ recording disabled: {rec.error}")
    return None


def finish(rec: Optional[RunRecorder]) -> Optional[Path]:
    """Stop a recorder started by :func:`maybe_start` and report the result."""
    if rec is None:
        return None
    try:
        out = rec.stop()
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ recording failed to finalise: {type(e).__name__}: {e}")
        return None
    if out is None:
        print(f"  ⚠ no recording written: {rec.error}")
        return None
    size_mb = out.stat().st_size / 1e6
    secs = rec.frames_written / max(rec.fps, 1)
    print(f"  ⏹ recording: {out}  ({secs:.0f}s, {size_mb:.1f} MB)")
    return out
