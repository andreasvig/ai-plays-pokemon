"""OCR system that continuously captures and processes text from the game screen."""

import hashlib
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter


class OCRRunner:
    """Background OCR process that captures text from periodic screenshots.

    Runs in a background thread. Captures screenshots at a configurable interval,
    deduplicates identical frames, preprocesses for OCR, runs OCR, and merges
    scrolling text into a clean buffer.
    """

    def __init__(
        self,
        config: dict[str, Any],
        screenshot_fn: Callable[[], Image.Image],
    ):
        """
        Args:
            config: Full config dict
            screenshot_fn: Function that returns a raw (unprocessed) screenshot
        """
        ocr_config = config.get("ocr", {})
        self.enabled = ocr_config.get("enabled", True)
        self.capture_interval = ocr_config.get("capture_interval", 0.5)
        self.buffer_size = ocr_config.get("log_buffer_size", 50)
        self.dedup_window = ocr_config.get("dedup_window", 3)
        self.backend = ocr_config.get("backend", "tesseract")

        self._screenshot_fn = screenshot_fn
        self._buffer: deque[str] = deque(maxlen=self.buffer_size)
        self._recent_hashes: deque[str] = deque(maxlen=self.dedup_window)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the background OCR thread."""
        if not self.enabled:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background OCR thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_buffer(self) -> list[str]:
        """Get the current OCR text buffer (deduplicated, merged)."""
        with self._lock:
            return list(self._buffer)

    def clear_buffer(self) -> None:
        """Clear the OCR buffer."""
        with self._lock:
            self._buffer.clear()

    def process_single(self, image: Image.Image) -> Optional[str]:
        """Run OCR on a single image. Useful for testing.

        Returns the extracted text, or None if the image is a duplicate.
        """
        # Check for duplicate
        img_hash = self._image_hash(image)
        if img_hash in self._recent_hashes:
            return None
        self._recent_hashes.append(img_hash)

        # Preprocess and OCR
        processed = self._preprocess(image)
        text = self._run_ocr(processed)

        if text:
            self._add_to_buffer(text)

        return text

    # --- Internal ---

    def _run_loop(self) -> None:
        """Main background loop: capture, dedup, OCR, merge."""
        while self._running:
            try:
                image = self._screenshot_fn()
                self.process_single(image)
            except Exception:
                pass  # Don't crash the background thread

            time.sleep(self.capture_interval)

    def _image_hash(self, image: Image.Image) -> str:
        """Compute a perceptual hash for deduplication.

        Resizes to 16x16 grayscale and hashes the pixels.
        This is tolerant of minor rendering differences.
        """
        small = image.resize((16, 16)).convert("L")
        pixel_data = small.tobytes()
        return hashlib.md5(pixel_data).hexdigest()

    def _preprocess(self, image: Image.Image) -> Image.Image:
        """Preprocess an image for OCR: upscale, binarize, high contrast."""
        # Upscale 4x
        img = image.resize(
            (image.width * 4, image.height * 4),
            Image.NEAREST,
        )

        # Convert to grayscale
        img = img.convert("L")

        # Increase contrast
        img = ImageEnhance.Contrast(img).enhance(3.0)

        # Binarize (threshold)
        threshold = 128
        img = img.point(lambda p: 255 if p > threshold else 0)

        return img

    def _run_ocr(self, image: Image.Image) -> str:
        """Run OCR on a preprocessed image."""
        if self.backend == "tesseract":
            try:
                text = pytesseract.image_to_string(
                    image,
                    config="--psm 6",  # Assume a single uniform block of text
                )
                return self._clean_text(text)
            except Exception:
                return ""
        else:
            # API backend - placeholder for future implementation
            return ""

    def _clean_text(self, text: str) -> str:
        """Clean up raw OCR output."""
        # Remove excessive whitespace
        lines = [line.strip() for line in text.strip().split("\n")]
        lines = [line for line in lines if line]
        return "\n".join(lines)

    def _add_to_buffer(self, text: str) -> None:
        """Add text to the buffer, merging with previous entries if overlapping."""
        with self._lock:
            if not self._buffer:
                self._buffer.append(text)
                return

            last = self._buffer[-1]
            merged = self._merge_overlapping(last, text)

            if merged and merged != last:
                # Text was an extension of the previous entry
                self._buffer[-1] = merged
            elif text != last:
                # New distinct text
                self._buffer.append(text)

    def _merge_overlapping(self, prev: str, new: str) -> Optional[str]:
        """Merge two text strings if the new one extends the previous one.

        Detects scrolling text where capture 1 has "Welcome to t" and
        capture 2 has "Welcome to the Pokemon Center".

        Returns the merged string, or None if no overlap found.
        """
        if not prev or not new:
            return None

        # Check if new text starts with a portion that matches the end of prev
        # Try progressively shorter suffixes of prev
        min_overlap = 5  # Minimum characters to consider an overlap
        prev_flat = prev.replace("\n", " ")
        new_flat = new.replace("\n", " ")

        for i in range(min_overlap, len(prev_flat)):
            suffix = prev_flat[i:]
            if new_flat.startswith(suffix):
                # Found overlap - new text extends prev
                return prev_flat[:i] + new_flat
            # Also check if prev ends with a prefix of new
            if suffix == new_flat[:len(suffix)]:
                return prev_flat[:i] + new_flat

        # Check if new completely contains prev (prev is a substring)
        if prev_flat in new_flat:
            return new_flat

        return None
