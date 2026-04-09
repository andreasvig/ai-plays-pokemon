"""Python server that mGBA's Lua script connects to."""

import hashlib
import socket
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image


class EmulatorClient:
    """Listens for a connection from the mGBA Lua script and provides a high-level API.

    Flow:
    1. Python starts a TCP server and waits
    2. User loads the Lua script in mGBA
    3. Lua script connects to Python
    4. Python sends commands, Lua responds
    """

    def __init__(self, config: dict[str, Any]):
        emu_config = config["emulator"]
        self.host = emu_config["host"]
        self.port = emu_config["port"]
        self.hold_frames = emu_config.get("button_hold_frames", 6)
        self.gap_frames = emu_config.get("frames_between_inputs", 30)

        screenshot_config = config.get("screenshot", {})
        self.upscale_factor = screenshot_config.get("upscale_factor", 3)

        self.valid_inputs = set(config.get("valid_inputs", []))

        # Screen stability detection config
        stability = config.get("screen_stability", {})
        self.stability_min_wait = stability.get("min_wait", 0.3)
        self.stability_max_wait = stability.get("max_wait", 10.0)
        self.stability_poll_interval = stability.get("poll_interval", 0.3)
        self.stability_threshold_start = stability.get("threshold_start", 0.99)
        self.stability_threshold_end = stability.get("threshold_end", 0.90)

        # Track player facing direction (updated after each directional input)
        self.facing: Optional[str] = None  # "up", "down", "left", "right" or None (unknown)

        self._server: Optional[socket.socket] = None
        self._socket: Optional[socket.socket] = None
        self._buffer = ""

    def connect(self, timeout: float = 60.0) -> None:
        """Start TCP server and wait for mGBA Lua script to connect."""
        self.start_server()
        self.wait_for_connection(timeout)

    def start_server(self) -> None:
        """Start the TCP server and begin listening."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(1)
        print(f"TCP server listening on {self.host}:{self.port}")

    def wait_for_connection(self, timeout: float = 60.0) -> None:
        """Wait for the mGBA Lua script to connect to the server."""
        if not self._server:
            raise RuntimeError("Server not started. Call start_server() first.")

        self._server.settimeout(timeout)
        print(f"Waiting for mGBA to connect...")

        try:
            self._socket, addr = self._server.accept()
        except socket.timeout:
            self._server.close()
            self._server = None
            raise ConnectionError(
                f"No connection from mGBA within {timeout}s. "
                "Load lua/socketserver.lua in mGBA via Tools > Scripting."
            )

        # Wait for HELLO from Lua
        response = self._recv_line()
        if response != "HELLO":
            raise ConnectionError(f"Unexpected greeting from mGBA: {response}")

        # Configure timing
        self._send(f"CONFIG:hold_frames={self.hold_frames}")
        self._recv_line()
        self._send(f"CONFIG:gap_frames={self.gap_frames}")
        self._recv_line()

        print(f"mGBA connected from {addr[0]}:{addr[1]}")

    def disconnect(self) -> None:
        """Close the connection and server."""
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._server:
            self._server.close()
            self._server = None

    def ping(self) -> bool:
        """Check if the connection is alive."""
        try:
            self._send("PING")
            response = self._recv_line()
            return response == "PONG"
        except Exception:
            return False

    def capture_screenshot(self, preprocess: bool = True) -> Image.Image:
        """Capture a screenshot from the emulator."""
        self._send("CAP")
        response = self._recv_line()

        if not response.startswith("SCREENSHOT:"):
            raise RuntimeError(f"Unexpected screenshot response: {response}")

        filepath = response[len("SCREENSHOT:"):]
        time.sleep(0.05)
        img = Image.open(filepath)
        # Load into memory so the temp file can be reused
        img.load()

        if preprocess:
            img = self._preprocess_screenshot(img)

        return img

    def press_button(self, button: str) -> None:
        """Press a single button."""
        button = button.upper()
        if button not in self.valid_inputs:
            raise ValueError(
                f"Invalid button: {button}. Valid: {self.valid_inputs}"
            )
        self._send(f"PRESS:{button}")
        response = self._recv_line()
        if response != "OK":
            raise RuntimeError(f"Button press failed: {response}")

    def press_sequence(self, buttons: str) -> None:
        """Press a sequence of buttons (e.g., 'RRRRAAA' or 'R;R;R;A;A').

        Accepts either semicolon-separated or concatenated single-char buttons.
        Multi-char buttons (START, SELECT, LB, RB) must use semicolons.
        """
        parsed = self._parse_sequence(buttons)
        seq_str = ";".join(parsed)
        self._send(f"SEQ:{seq_str}")
        n = len(parsed)
        frames_per_button = self.hold_frames + self.gap_frames
        expected_seconds = (n * frames_per_button) / 60.0
        seq_timeout = expected_seconds * 3 + 15.0
        self._recv_expected("QUEUED:", timeout=seq_timeout)
        self._recv_expected("SEQUENCE_DONE", timeout=seq_timeout)

    def save_state(self, filepath: str) -> None:
        """Save emulator state to a file."""
        self._send(f"SAVE:{filepath}")
        response = self._recv_line()
        if not response.startswith("OK:"):
            raise RuntimeError(f"Save state failed: {response}")

    def load_state(self, filepath: str) -> None:
        """Load emulator state from a file."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"State file not found: {filepath}")
        self._send(f"LOAD:{filepath}")
        response = self._recv_line()
        if not response.startswith("OK:"):
            raise RuntimeError(f"Load state failed: {response}")

    def pause(self) -> None:
        """Pause emulation."""
        self._send("PAUSE")
        response = self._recv_line()
        if not response.startswith("OK:"):
            raise RuntimeError(f"Pause failed: {response}")

    def unpause(self) -> None:
        """Unpause emulation."""
        self._send("UNPAUSE")
        response = self._recv_line()
        if not response.startswith("OK:"):
            raise RuntimeError(f"Unpause failed: {response}")

    # --- Internal methods ---

    def _send(self, msg: str) -> None:
        """Send a newline-terminated message."""
        if not self._socket:
            raise ConnectionError("Not connected to mGBA")
        self._socket.sendall((msg + "\n").encode("utf-8"))

    def _recv_line(self, timeout: float = 10.0) -> str:
        """Receive a single newline-terminated response."""
        if not self._socket:
            raise ConnectionError("Not connected to mGBA")

        self._socket.settimeout(timeout)
        while "\n" not in self._buffer:
            try:
                data = self._socket.recv(4096).decode("utf-8")
            except socket.timeout:
                raise TimeoutError(
                    f"No response from mGBA within {timeout}s. Buffer: {self._buffer!r}"
                )
            if not data:
                raise ConnectionError("mGBA connection closed")
            self._buffer += data

        line, self._buffer = self._buffer.split("\n", 1)
        return line.strip()

    def _recv_expected(self, expected_prefix: str, timeout: float = 60.0) -> str:
        """Receive lines until one matches the expected prefix, skipping others.

        This handles the socket timing issue where SCREENSHOT responses from
        stability checks can arrive during sequence waits.
        """
        deadline = time.time() + timeout
        skipped = 0
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for {expected_prefix} "
                    f"(timeout={timeout:.1f}s, skipped {skipped} lines)"
                )
            line = self._recv_line(timeout=remaining)
            if line.startswith(expected_prefix) or line == expected_prefix:
                return line
            # Skip unexpected responses (e.g., SCREENSHOT: from stability checks)
            skipped += 1

    def _drain_buffer(self) -> None:
        """Read and discard any pending data in the socket buffer.

        Called after fire-and-forget sequences to clear QUEUED/SEQUENCE_DONE
        responses so they don't interfere with subsequent recv calls.
        """
        if not self._socket:
            return
        # Read any data already in our string buffer
        self._buffer = ""
        # Non-blocking read of any pending socket data
        self._socket.settimeout(0.1)
        try:
            while True:
                data = self._socket.recv(4096)
                if not data:
                    break
        except (socket.timeout, BlockingIOError, OSError):
            pass  # No more data available

    _DIRECTION_CODES = {"U", "D", "L", "R"}
    _CODE_TO_FACING = {"U": "up", "D": "down", "L": "left", "R": "right"}
    _FACING_TO_CODE = {"up": "U", "down": "D", "left": "L", "right": "R"}

    def _insert_turning_frames(self, buttons: list[str]) -> list[str]:
        """Insert extra direction presses to compensate for turning frames.

        In Pokemon, if you face up and press right, the first press just turns
        you — no movement. This method doubles the first press in a new direction
        so the model's intended movement actually happens.
        """
        if not buttons:
            return buttons

        result = []
        current_facing = self._FACING_TO_CODE.get(self.facing) if self.facing else None

        for btn in buttons:
            if btn in self._DIRECTION_CODES:
                if current_facing is not None and btn != current_facing:
                    # Need to turn first — insert an extra press
                    result.append(btn)
                current_facing = btn
            result.append(btn)

        return result

    def normalize_button_list(self, buttons: list[str]) -> list[str]:
        """Normalize a list of button names to emulator short codes.

        Accepts full names like ["left", "left", "up", "a"] and returns ["L", "L", "U", "A"].
        """
        ALIASES = {
            "UP": "U", "DOWN": "D", "LEFT": "L", "RIGHT": "R",
        }
        result = []
        for btn in buttons:
            normalized = btn.strip().upper()
            normalized = ALIASES.get(normalized, normalized)
            if normalized not in self.valid_inputs:
                raise ValueError(f"Invalid button: {btn!r} (normalized to {normalized!r})")
            result.append(normalized)
        return result

    def press_button_list(self, buttons: list[str]) -> None:
        """Press a sequence of buttons from a list of full names.

        Fire-and-forget: sends the SEQ command and sleeps for the
        calculated duration. No waiting for TCP responses — the screen
        stability check after this confirms execution completed.

        Args:
            buttons: e.g. ["left", "left", "up", "a"]
        """
        normalized = self.normalize_button_list(buttons)

        seq_str = ";".join(normalized)
        self._send(f"SEQ:{seq_str}")

        # Sleep for the expected execution time + small buffer
        n = len(normalized)
        frames_per_button = self.hold_frames + self.gap_frames
        total_frames = n * frames_per_button
        expected_seconds = total_frames / 60.0
        sleep_time = expected_seconds + 0.5  # 0.5s buffer
        time.sleep(sleep_time)

        # Drain any pending responses (QUEUED, SEQUENCE_DONE) from the buffer
        # so they don't interfere with the next recv call (e.g. screenshot)
        self._drain_buffer()

        # Update facing based on last directional input in the sequence
        DIRECTION_BUTTONS = {"U": "up", "D": "down", "L": "left", "R": "right"}
        for btn in reversed(normalized):
            if btn in DIRECTION_BUTTONS:
                self.facing = DIRECTION_BUTTONS[btn]
                break

    def _parse_sequence(self, buttons: str) -> list[str]:
        """Parse a button sequence string into a list of button names."""
        # Normalize common full names to short codes
        ALIASES = {
            "UP": "U", "DOWN": "D", "LEFT": "L", "RIGHT": "R",
        }

        if ";" in buttons:
            parts = [b.strip().upper() for b in buttons.split(";") if b.strip()]
            parts = [ALIASES.get(p, p) for p in parts]
        else:
            # Check if it's a full word like "DOWN" without semicolons
            upper = buttons.upper().strip()
            if upper in ALIASES:
                parts = [ALIASES[upper]]
            elif upper in self.valid_inputs:
                parts = [upper]
            else:
                char_map = {"U", "D", "L", "R", "A", "B"}
                parts = []
                for ch in upper:
                    if ch in char_map:
                        parts.append(ch)
                    else:
                        raise ValueError(
                            f"Cannot parse '{ch}' in concatenated sequence '{buttons}'. "
                            "Use U/D/L/R/A/B for single chars or semicolons: 'R;R;START;A'"
                        )

        for btn in parts:
            if btn not in self.valid_inputs:
                raise ValueError(f"Invalid button in sequence: {btn}")

        return parts

    def wait_for_stable_screen(self) -> None:
        """Wait until the screen stabilizes after a sequence.

        Takes screenshots at regular intervals and compares them. The screen
        is considered stable when the average similarity of the last 3 frames
        exceeds a threshold. The threshold gradually relaxes from
        threshold_start to threshold_end over the wait period, so long
        animations (battle intros, cutscenes) eventually pass.
        """
        start = time.time()

        # Always wait the minimum first
        time.sleep(self.stability_min_wait)

        # Collect recent frame fingerprints for comparison
        recent_frames: list[np.ndarray] = []

        while True:
            elapsed = time.time() - start

            # Hard cap
            if elapsed >= self.stability_max_wait:
                break

            # Capture a low-res frame for comparison (raw, no upscale)
            frame = self._capture_raw_frame()
            recent_frames.append(frame)

            # Need at least 3 frames to compare
            if len(recent_frames) >= 3:
                # Compute similarity between consecutive pairs of last 3 frames
                last3 = recent_frames[-3:]
                sim1 = self._frame_similarity(last3[0], last3[1])
                sim2 = self._frame_similarity(last3[1], last3[2])
                avg_similarity = (sim1 + sim2) / 2.0

                # Interpolate threshold: starts strict, relaxes over time
                progress = (elapsed - self.stability_min_wait) / (self.stability_max_wait - self.stability_min_wait)
                progress = min(1.0, max(0.0, progress))
                threshold = self.stability_threshold_start + progress * (self.stability_threshold_end - self.stability_threshold_start)

                if avg_similarity >= threshold:
                    break

            time.sleep(self.stability_poll_interval)

    def _capture_raw_frame(self) -> np.ndarray:
        """Capture a small grayscale frame for fast comparison."""
        self._send("CAP")
        response = self._recv_line()
        if not response.startswith("SCREENSHOT:"):
            raise RuntimeError(f"Unexpected screenshot response: {response}")
        filepath = response[len("SCREENSHOT:"):]
        time.sleep(0.02)
        img = Image.open(filepath)
        img.load()
        # Downscale to 48x32 grayscale for fast comparison
        small = img.resize((48, 32)).convert("L")
        return np.array(small, dtype=np.float32)

    @staticmethod
    def _frame_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute similarity between two frames as a 0-1 score.

        1.0 = identical, 0.0 = completely different.
        """
        diff = np.abs(a - b)
        # Normalize: max possible diff per pixel is 255
        mean_diff = diff.mean() / 255.0
        return 1.0 - mean_diff

    def _preprocess_screenshot(self, img: Image.Image) -> Image.Image:
        """Upscale a screenshot for better VLM readability."""
        if self.upscale_factor > 1:
            new_size = (
                img.width * self.upscale_factor,
                img.height * self.upscale_factor,
            )
            img = img.resize(new_size, Image.NEAREST)

        return img
