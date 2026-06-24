"""OCR system: background Tesseract capture + LLM cleanup.

Captures screenshots at a fixed interval in a background thread, runs Tesseract
with confidence-based word filtering, and stores entries in a numbered buffer.
At each turn boundary the main thread calls `flush_and_cleanup()` which sends
the buffer to a cleanup LLM (Gemma 4 31B via OpenRouter) and returns the
coherent cleaned text alongside the raw buffer for logging.
"""

import json
import os
import re
import shutil
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import httpx
import pytesseract
from PIL import Image, ImageEnhance


def _tesseract_available() -> bool:
    cmd = pytesseract.pytesseract.tesseract_cmd
    return bool(cmd and (os.path.isfile(cmd) or shutil.which(cmd)))


DEFAULT_CLEANUP_SYSTEM_PROMPT = (
    "You clean up noisy OCR output from Pokemon GBA screenshots. This is the accumulated OCR "
    "buffer from multiple screen captures during a single game turn — so it may contain "
    "repeated fragments, overlapping dialogue as it scrolls, and garbled pixel-art noise mixed "
    "in with real text. "
    "Your job: return the coherent in-game text (dialogue, menu labels, Pokemon names, stats, "
    "HP, levels, moves, numbers) with the gibberish removed. "
    "Fix obvious OCR misreads when confident (e.g. 'unsate' → 'unsafe', 'qrass' → 'grass', "
    "'ltem' → 'Item', 'wrold' → 'world'). "
    "Drop tokens that are clearly pixel-art noise: isolated symbols like '|', '—', random letter "
    "salads like 'eS ES oe', short non-word fragments. "
    "Preserve meaningful line breaks. Merge duplicate/overlapping captures into single coherent text. "
    "Do NOT invent text that isn't supported by the OCR input."
)

DEFAULT_CLEANUP_USER_PROMPT = (
    "Clean up this OCR buffer. Each key is a separate capture from the same "
    "game turn; merge them into coherent text, drop gibberish, fix obvious "
    "OCR misreads.\n\n{raw_json}"
)


def _hamming(a: int, b: int) -> int:
    """Hamming distance between two integers (number of differing bits)."""
    return bin(a ^ b).count("1")


# Regex patterns that match pixel-art noise from GBA tile graphics.
# These fire on individual lines AFTER the Tesseract confidence filter.
_NOISE_PATTERNS = [
    # Lines that are mostly 1-2 char tokens: "ie wir ote", "a a", "=| =|"
    re.compile(r'^[\W\s]*(\S{1,2}[\s,;.|]+){2,}\S{0,2}[\W\s]*$'),
    # Repeated short syllables: "ale ale ale", "oe oe oe", "tt ttt tt"
    re.compile(r'^.*(\b\w{1,3}\b)(\s+\1){2,}.*$'),
    # Purely symbols / punctuation / digits (no letters >2 chars)
    re.compile(r'^[^a-zA-Z]*$'),
    # Lines with ≤3 alphanumeric chars total
    re.compile(r'^(?:[^a-zA-Z0-9]*[a-zA-Z0-9]){0,3}[^a-zA-Z0-9]*$'),
    # Common tile-noise fragments
    re.compile(r'^\s*(?:=f\}|=e\)|=\||—\||ao \|\||sree|Pao ese|int SEES)', re.IGNORECASE),
]


def _strip_noise_lines(text: str) -> str:
    """Remove lines that match known pixel-art noise patterns.

    Keeps lines that look like actual game text (dialogue, menu labels,
    Pokemon names). Drops lines that are clearly garbled tile/sprite OCR.
    """
    if not text:
        return ""
    kept = []
    for line in text.split("\n"):
        line_s = line.strip()
        if not line_s:
            continue
        if any(p.match(line_s) for p in _NOISE_PATTERNS):
            continue
        kept.append(line_s)
    return "\n".join(kept)


class OCRRunner:
    """Background OCR: captures screenshots, runs Tesseract with confidence filter,
    stores numbered buffer. Flush + LLM cleanup called at turn boundary.
    """

    def __init__(
        self,
        config: dict[str, Any],
        screenshot_fn: Callable[[], Image.Image],
    ):
        ocr_config = config.get("ocr", {})
        self.enabled = ocr_config.get("enabled", False)
        self.capture_interval = ocr_config.get("capture_interval", 0.4)
        self.confidence_threshold = ocr_config.get("confidence_threshold", 40)
        self.dedup_window = ocr_config.get("dedup_window", 5)
        self.max_buffer_size = ocr_config.get("max_buffer_size", 30)
        self.cleanup_enabled = ocr_config.get("cleanup_enabled", True)
        self.cleanup_model = ocr_config.get("cleanup_model", "google/gemma-4-31b-it")
        self.cleanup_temperature = ocr_config.get("cleanup_temperature", 0.1)
        self.cleanup_top_p = ocr_config.get("cleanup_top_p", 0.95)
        self.cleanup_provider = ocr_config.get("cleanup_provider", {"sort": "latency"})
        # dHash dedup: a new capture is treated as a duplicate if its Hamming
        # distance to any of the recent hashes is ≤ this value. 0 = exact,
        # 64 = always-dup. ~5 catches animation flicker (cursor blink, sprite
        # bob) on the same scene.
        self.dedup_hamming_threshold = ocr_config.get("dedup_hamming_threshold", 5)
        self.cleanup_system_prompt = ocr_config.get(
            "cleanup_system_prompt", DEFAULT_CLEANUP_SYSTEM_PROMPT
        )
        self.cleanup_user_prompt = ocr_config.get(
            "cleanup_user_prompt", DEFAULT_CLEANUP_USER_PROMPT
        )

        self._screenshot_fn = screenshot_fn
        self._buffer: list[dict] = []  # [{"id": "ocr_1", "text": "...", "ts": float}]
        self._counter = 0
        self._recent_hashes: deque = deque(maxlen=self.dedup_window)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._active = False  # whether we should actually capture (gated by turn.py)
        self._lock = threading.Lock()

        # Accumulated cleanup cost (USD) across all flushes
        self.total_cost_usd = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        # Per-window stats (reset on each flush). Track the current OCR capture
        # window — from set_active(True) to flush_and_cleanup().
        self._window_start_ts: Optional[float] = None
        self._window_total_s = 0.0          # accumulated active seconds since last flush
        self._stats_attempts = 0            # poll attempts (screenshot_fn calls)
        self._stats_hash_dupes = 0          # skipped by dHash dedup
        self._stats_text_dupes = 0          # skipped because text == last buffer entry
        self._stats_empty_ocr = 0           # Tesseract returned nothing after conf filter
        self._stats_tesseract_runs = 0      # actual Tesseract calls
        self._stats_buffer_full = 0         # skipped due to buffer cap
        self._warned_missing_tesseract = False

    # ── Public API ────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.enabled:
            return
        if not _tesseract_available():
            print(
                "WARNING: tesseract not found — OCR will stay empty. "
                "Install it, then restart the run: macOS: brew install tesseract · "
                "Debian/Ubuntu: sudo apt install tesseract-ocr"
            )
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._active = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def set_active(self, active: bool) -> None:
        """Toggle whether the background thread actually captures frames.

        The thread keeps running either way — it just skips the capture step
        when inactive. Used to scope captures to the button-execution + settle
        window (where the screen actually changes).
        """
        now = time.time()
        if active and not self._active:
            self._window_start_ts = now
            # Start each capture window fresh — stale hashes from the previous
            # turn's settle would otherwise dedup the first frames of this window.
            self._recent_hashes.clear()
        elif not active and self._active and self._window_start_ts is not None:
            self._window_total_s += now - self._window_start_ts
            self._window_start_ts = None
        self._active = active

    def get_buffer(self) -> list[dict]:
        """Return a copy of the current buffer (for logging/debugging)."""
        with self._lock:
            return list(self._buffer)

    def get_buffer_dict(self) -> dict:
        """Return buffer as {ocr_1: "...", ocr_2: "..."}."""
        with self._lock:
            return {entry["id"]: entry["text"] for entry in self._buffer}

    def clear(self) -> None:
        """Clear buffer without running cleanup."""
        with self._lock:
            self._buffer.clear()

    # Legacy alias used by older callers
    clear_buffer = clear

    def flush_and_cleanup(self) -> tuple:
        """Flush buffer, run LLM cleanup, clear buffer.

        Returns:
            (cleaned_text, raw_buffer_dict, usage_info, stats)
            - cleaned_text is empty if buffer was empty or cleanup failed
            - raw_buffer_dict is always what was in the buffer (may be {})
            - usage_info is a dict: cost_usd, input_tokens, output_tokens
            - stats is a dict describing the OCR capture window since last flush
        """
        # Snapshot + reset window stats
        # If we're still active when flushed, count time up to now.
        now = time.time()
        window_s = self._window_total_s
        if self._active and self._window_start_ts is not None:
            window_s += now - self._window_start_ts
            self._window_start_ts = now  # restart count from here

        stats = {
            "window_s": round(window_s, 2),
            "attempts": self._stats_attempts,
            "tesseract_runs": self._stats_tesseract_runs,
            "hash_dupes": self._stats_hash_dupes,
            "text_dupes": self._stats_text_dupes,
            "empty_ocr": self._stats_empty_ocr,
            "buffer_full": self._stats_buffer_full,
        }
        # Reset for next window
        self._window_total_s = 0.0
        self._stats_attempts = 0
        self._stats_tesseract_runs = 0
        self._stats_hash_dupes = 0
        self._stats_text_dupes = 0
        self._stats_empty_ocr = 0
        self._stats_buffer_full = 0

        raw = self.get_buffer_dict()
        self.clear()

        empty_usage = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}

        if not raw:
            return "", {}, empty_usage, stats

        if not self.cleanup_enabled:
            return "\n".join(raw.values()), raw, empty_usage, stats

        try:
            cleaned, usage = self._llm_cleanup(raw)
            self.total_cost_usd += usage["cost_usd"]
            self.total_input_tokens += usage["input_tokens"]
            self.total_output_tokens += usage["output_tokens"]
            return cleaned, raw, usage, stats
        except Exception as e:
            fallback = "\n".join(raw.values())
            return f"[cleanup failed: {e}]\n{fallback}", raw, empty_usage, stats

    # ── Background loop ───────────────────────────────────────────────

    def _run_loop(self) -> None:
        while self._running:
            if self._active:
                try:
                    image = self._screenshot_fn()
                    self._process_single(image)
                except Exception:
                    pass  # never crash the thread
            time.sleep(self.capture_interval)

    def _process_single(self, image: Image.Image) -> None:
        self._stats_attempts += 1

        # Perceptual-hash dedup (dHash with Hamming tolerance).
        img_hash = self._image_hash(image)
        for prev in self._recent_hashes:
            if _hamming(img_hash, prev) <= self.dedup_hamming_threshold:
                self._stats_hash_dupes += 1
                return
        self._recent_hashes.append(img_hash)

        # Preprocess + OCR with confidence filter + regex noise strip
        processed = self._preprocess(image)
        self._stats_tesseract_runs += 1
        text = self._tesseract_confidence_filter(processed)
        text = _strip_noise_lines(text)

        if not text:
            self._stats_empty_ocr += 1
            return

        # Skip if identical to last entry, cap buffer size
        with self._lock:
            if self._buffer and self._buffer[-1]["text"] == text:
                self._stats_text_dupes += 1
                return
            if len(self._buffer) >= self.max_buffer_size:
                self._stats_buffer_full += 1
                return
            self._counter += 1
            self._buffer.append({
                "id": f"ocr_{self._counter}",
                "text": text,
                "ts": time.time(),
            })

    # ── OCR pipeline ──────────────────────────────────────────────────

    def _image_hash(self, image: Image.Image) -> int:
        """High-resolution dHash (256-bit).

        Resize to 17×16 grayscale, compare adjacent pixels per row →
        16 bits × 16 rows = 256-bit hash. Higher resolution than the
        standard 8×8 so text changes (which occupy a small screen region)
        flip enough bits to exceed the dedup threshold, while pure
        animation flicker (cursor blink, sprite bob) stays under it.
        """
        small = image.resize((17, 16)).convert("L")
        pixels = list(small.getdata())
        bits = 0
        for row in range(16):
            for col in range(16):
                left = pixels[row * 17 + col]
                right = pixels[row * 17 + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return bits

    def _preprocess(self, image: Image.Image) -> Image.Image:
        img = image.resize((image.width * 4, image.height * 4), Image.NEAREST)
        img = img.convert("L")
        img = ImageEnhance.Contrast(img).enhance(3.0)
        img = img.point(lambda p: 255 if p > 128 else 0)
        return img

    def _tesseract_confidence_filter(self, img: Image.Image) -> str:
        """Run Tesseract image_to_data and keep only words with conf >= threshold."""
        if not _tesseract_available():
            if not self._warned_missing_tesseract:
                self._warned_missing_tesseract = True
                print(
                    "WARNING: tesseract not found — OCR captures will be empty until "
                    "you install it (macOS: brew install tesseract)."
                )
            return ""
        try:
            data = pytesseract.image_to_data(
                img, config="--psm 6", output_type=pytesseract.Output.DICT
            )
        except Exception:
            return ""

        lines: dict = {}
        for i, word in enumerate(data["text"]):
            word = word.strip()
            if not word:
                continue
            try:
                conf = int(data["conf"][i])
            except (ValueError, TypeError):
                continue
            if conf < self.confidence_threshold:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(word)

        output_lines = [" ".join(lines[k]) for k in sorted(lines.keys())]
        return "\n".join(output_lines).strip()

    # ── LLM cleanup ───────────────────────────────────────────────────

    def _llm_cleanup(self, raw: dict) -> tuple:
        """Send buffer dict to cleanup LLM via OpenRouter.

        Returns:
            (cleaned_text, usage_dict) where usage_dict has
            cost_usd, input_tokens, output_tokens.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv()
            except ImportError:
                pass
            api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        raw_json = json.dumps(raw, indent=2, ensure_ascii=False)
        user_prompt = self.cleanup_user_prompt.format(raw_json=raw_json)

        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.cleanup_model,
                "messages": [
                    {"role": "system", "content": self.cleanup_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.cleanup_temperature,
                "top_p": self.cleanup_top_p,
                "provider": self.cleanup_provider,
                "usage": {"include": True},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "cleaned_ocr",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "cleaned_text": {
                                    "type": "string",
                                    "description": "Cleaned text with line breaks preserved.",
                                },
                            },
                            "required": ["cleaned_text"],
                            "additionalProperties": False,
                        },
                    },
                },
            },
            timeout=30.0,
        )
        data = response.json()
        if "error" in data:
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"LLM error: {msg}")

        # Extract usage / cost
        usage_block = data.get("usage") or {}
        usage = {
            "cost_usd": float(usage_block.get("cost", 0.0) or 0.0),
            "input_tokens": int(usage_block.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage_block.get("completion_tokens", 0) or 0),
        }

        content = data["choices"][0]["message"].get("content", "")
        if not content:
            return "", usage
        try:
            parsed = json.loads(content)
            return parsed.get("cleaned_text", "").strip(), usage
        except json.JSONDecodeError:
            return content.strip(), usage
