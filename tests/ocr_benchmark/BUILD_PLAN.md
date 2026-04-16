# Build Plan: Integrate Tesseract + Gemma OCR Pipeline into Agent

## Context

This is the **AI Plays Pokemon** project — a Python agent that plays Pokemon FireRed via screen vision (no RAM reading). The agent currently uses `direct_multimodal` vision mode where a Gemini Flash LLM sees raw screenshots and decides button presses each turn.

An **OCR benchmark** was built in `tests/ocr_benchmark/` to evaluate different OCR approaches. The winner for a cheap local-ish pipeline was:

**Tesseract (with confidence filter) + Gemma 4 31B cleanup**
- Exact matches: 7/28
- Avg similarity: 0.678
- Cost: ~$0.00004 per cleanup call
- Time: ~4s per cleanup call

Not as good as pure VLM (Gemini Flash: 0.923), but useful as a **supplement** that captures text *between* turns (scrolling dialogue, rapid animations) that the agent's single screenshot misses.

## Goal

Integrate this pipeline into the live agent:

1. **Background thread** captures screenshots at a fixed frequency during turn execution
2. Each capture → Tesseract OCR (with confidence filter) → appended to a numbered buffer `{ocr_1: "...", ocr_2: "..."}`
3. **At the start of each turn**, flush the buffer to Gemma via OpenRouter with a cleanup prompt
4. Inject the cleaned OCR text as a new input section in the LLM's user message
5. Log raw + cleaned OCR to `events.jsonl`, display in dashboard and report

## Project Structure Reference

```
src/
  agent/
    agent.py         — Pydantic AI GameAction output model, agent builder
    turn.py          — TurnManager: orchestrates VLM → LLM → execute each turn
  core/
    logger.py        — RunLogger: writes events.jsonl
  emulator/
    emulator.py      — EmulatorClient: TCP to mGBA, screenshots
    ocr.py           — OCRRunner: background OCR thread (TO BE REFACTORED)
  dashboard/
    static/index.html — live dashboard
  cli/
    report.py        — post-run HTML report generator
configs/
  config-2.0.yaml    — current config (ocr.enabled = false)
tests/
  test_phase5.py     — main integration test, run with: python tests/test_phase5.py --turns 10
  ocr_benchmark/
    run_benchmark.py — source of the Tesseract+Gemma pipeline logic
```

## Pipeline Diagram

```
┌────────────────────────────────────────────┐
│ Turn N executing (agent pressing buttons)  │
│                                            │
│ OCRRunner background thread:               │
│   every 400ms:                             │
│     screenshot = emulator.capture()        │
│     if hash not in recent_hashes:          │
│       text = tesseract_confidence_filter() │
│       if text and text != last:            │
│         buffer.append({id: "ocr_N",        │
│                         text: "...",       │
│                         ts: t})            │
└────────────────────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────┐
│ Turn N+1 starts:                           │
│                                            │
│ raw, cleaned = ocr.flush_and_cleanup()     │
│   → POST openrouter.ai                     │
│     model: google/gemma-4-31b-it           │
│     input: {ocr_1: "...", ocr_2: "..."}   │
│   → cleaned_text: "OAK: It's unsafe..."    │
│                                            │
│ user_message += "## Recent OCR Text\n" +   │
│                 cleaned_text               │
│                                            │
│ logger.log("ocr_flush", {raw, cleaned})    │
│                                            │
│ agent.run(user_message, screenshot, ...)   │
└────────────────────────────────────────────┘
```

## Implementation Steps

### Step 1: Refactor `src/emulator/ocr.py`

**Replace the entire file.** Key changes vs current:

- Buffer format changes from `deque[str]` (merged) to `list[dict]` (numbered captures)
- OCR changes from `image_to_string` to `image_to_data` with confidence filtering
- New `flush_and_cleanup()` method that calls Gemma via OpenRouter
- Remove the merge-overlapping logic (cleanup LLM handles that now)

**New class:**

```python
"""OCR system: background Tesseract capture + LLM cleanup."""

import base64
import hashlib
import json
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

import httpx
import pytesseract
from PIL import Image, ImageEnhance


CLEANUP_SYSTEM_PROMPT = (
    "You clean up noisy OCR output from Pokemon GBA screenshots. This is the accumulated OCR "
    "buffer from multiple screen captures during a single game turn — so it may contain "
    "repeated fragments, overlapping dialogue as it scrolls, and garbled pixel-art noise mixed "
    "in with real text. "
    "Your job: return the coherent in-game text (dialogue, menu labels, Pokemon names, stats, "
    "HP, levels, moves, numbers) with the gibberish removed. "
    "Fix obvious OCR misreads when confident (e.g. 'SMERRGEE' → 'SMEARGLE', 'unsate' → 'unsafe', "
    "'POK&MON' → 'POKéMON', 'BRASSWHISTLE' → 'GRASSWHISTLE'). "
    "Drop tokens that are clearly pixel-art noise: isolated symbols like '|', '—', random letter "
    "salads like 'eS ES oe', short non-word fragments. "
    "Preserve meaningful line breaks. Merge duplicate/overlapping captures into single coherent text. "
    "Do NOT invent text that isn't supported by the OCR input."
)


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

        self._screenshot_fn = screenshot_fn
        self._buffer: list[dict] = []  # [{"id": "ocr_1", "text": "...", "ts": float}]
        self._counter = 0
        self._recent_hashes: deque[str] = deque(maxlen=self.dedup_window)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.enabled:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_buffer(self) -> list[dict]:
        """Return a copy of the current buffer (for logging/debugging)."""
        with self._lock:
            return list(self._buffer)

    def get_buffer_dict(self) -> dict[str, str]:
        """Return buffer as {ocr_1: "...", ocr_2: "..."}."""
        with self._lock:
            return {entry["id"]: entry["text"] for entry in self._buffer}

    def clear(self) -> None:
        """Clear buffer without running cleanup."""
        with self._lock:
            self._buffer.clear()

    def flush_and_cleanup(self) -> tuple[str, dict[str, str]]:
        """Flush buffer, run LLM cleanup, clear buffer.

        Returns:
            (cleaned_text, raw_buffer_dict)
            - cleaned_text is empty if buffer was empty or cleanup failed
            - raw_buffer_dict is always what was in the buffer (may be {} if empty)
        """
        raw = self.get_buffer_dict()
        self.clear()

        if not raw:
            return "", {}

        if not self.cleanup_enabled:
            # Fallback: just concatenate
            return "\n".join(raw.values()), raw

        try:
            cleaned = self._llm_cleanup(raw)
            return cleaned, raw
        except Exception as e:
            # Fallback to raw concatenation on error
            fallback = "\n".join(raw.values())
            return f"[cleanup failed: {e}]\n{fallback}", raw

    # ── Background loop ───────────────────────────────────────────────

    def _run_loop(self) -> None:
        while self._running:
            try:
                image = self._screenshot_fn()
                self._process_single(image)
            except Exception:
                pass  # never crash the thread
            time.sleep(self.capture_interval)

    def _process_single(self, image: Image.Image) -> None:
        # Hash-dedup
        img_hash = self._image_hash(image)
        if img_hash in self._recent_hashes:
            return
        self._recent_hashes.append(img_hash)

        # Preprocess + OCR with confidence filter
        processed = self._preprocess(image)
        text = self._tesseract_confidence_filter(processed)

        if not text:
            return

        # Skip if identical to last entry
        with self._lock:
            if self._buffer and self._buffer[-1]["text"] == text:
                return
            if len(self._buffer) >= self.max_buffer_size:
                return  # safety cap
            self._counter += 1
            self._buffer.append({
                "id": f"ocr_{self._counter}",
                "text": text,
                "ts": time.time(),
            })

    # ── OCR pipeline ──────────────────────────────────────────────────

    def _image_hash(self, image: Image.Image) -> str:
        small = image.resize((16, 16)).convert("L")
        return hashlib.md5(small.tobytes()).hexdigest()

    def _preprocess(self, image: Image.Image) -> Image.Image:
        img = image.resize((image.width * 4, image.height * 4), Image.NEAREST)
        img = img.convert("L")
        img = ImageEnhance.Contrast(img).enhance(3.0)
        img = img.point(lambda p: 255 if p > 128 else 0)
        return img

    def _tesseract_confidence_filter(self, img: Image.Image) -> str:
        """Run Tesseract image_to_data and keep only words with conf >= threshold."""
        try:
            data = pytesseract.image_to_data(
                img, config="--psm 6", output_type=pytesseract.Output.DICT
            )
        except Exception:
            return ""

        lines: dict[tuple, list[str]] = {}
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

    def _llm_cleanup(self, raw: dict[str, str]) -> str:
        """Send buffer dict to Gemma via OpenRouter, return cleaned text."""
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        # Format as JSON for the prompt
        raw_json = json.dumps(raw, indent=2, ensure_ascii=False)
        user_prompt = (
            f"Clean up this OCR buffer. Each key is a separate capture from the same "
            f"game turn; merge them into coherent text, drop gibberish, fix obvious "
            f"OCR misreads.\n\n{raw_json}"
        )

        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.cleanup_model,
                "messages": [
                    {"role": "system", "content": CLEANUP_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
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

        content = data["choices"][0]["message"].get("content", "")
        if not content:
            return ""
        try:
            parsed = json.loads(content)
            return parsed.get("cleaned_text", "").strip()
        except json.JSONDecodeError:
            return content.strip()
```

**Verification:** Write a small script or shell snippet:

```python
from PIL import Image
from src.emulator.ocr import OCRRunner

config = {"ocr": {"enabled": True, "confidence_threshold": 40, "cleanup_enabled": True,
                   "cleanup_model": "google/gemma-4-31b-it"}}
# Use a saved screenshot from tests/ocr_benchmark/images/
img = Image.open("tests/ocr_benchmark/images/dialogue_oak_unsafe_clean.png")
runner = OCRRunner(config, screenshot_fn=lambda: img)
runner._process_single(img)
runner._process_single(img)  # should be deduplicated
runner._counter = 0  # reset so ids look clean in test
# Add another synthetic entry
runner._buffer.append({"id": "ocr_1", "text": "OAK: Its unsate!", "ts": 0})
runner._buffer.append({"id": "ocr_2", "text": "Wild POK&MON live in tall qrass", "ts": 0})

cleaned, raw = runner.flush_and_cleanup()
print("RAW:", raw)
print("CLEANED:", cleaned)
# Expected: cleaned ≈ "OAK: It's unsafe! Wild POKéMON live in tall grass"
```

### Step 2: Wire into `src/agent/turn.py`

Find the `run_turn()` method (or whatever builds the user message each turn). Add this logic **at the start of the turn**, before the screenshot + LLM call:

```python
# Flush OCR buffer accumulated since last turn and clean with LLM
ocr_cleaned = ""
ocr_raw: dict[str, str] = {}
if self.ocr_runner is not None and self.ocr_runner.enabled:
    t0 = time.time()
    ocr_cleaned, ocr_raw = self.ocr_runner.flush_and_cleanup()
    ocr_elapsed = time.time() - t0
    self.logger.log_custom("ocr_flush", {
        "turn": self.turn_number,
        "raw": ocr_raw,
        "cleaned": ocr_cleaned,
        "n_captures": len(ocr_raw),
        "duration": round(ocr_elapsed, 2),
    })
    print(f"  [Turn {self.turn_number}] OCR: {len(ocr_raw)} captures, cleaned in {ocr_elapsed:.1f}s")
```

Then inject `ocr_cleaned` into the user message. Find the message-building code (search for where `## Memory` or similar sections get added) and add:

```python
if ocr_cleaned:
    user_message_parts.append(
        f"## Recent OCR Text\n"
        f"Cleaned text captured from {len(ocr_raw)} screen captures between the last "
        f"turn and now. Includes scrolling dialogue, menu text, and UI labels. "
        f"Use as ground-truth for exact strings; trust the screenshot for spatial layout.\n\n"
        f"{ocr_cleaned}"
    )
```

(Exact variable names depend on the current `turn.py` structure — adapt as needed.)

### Step 3: Create `configs/config-2.1.yaml`

Copy `configs/config-2.0.yaml` and change the `ocr:` section:

```yaml
# --- OCR ---
ocr:
  enabled: true
  capture_interval: 0.4        # 2.5 captures/sec while agent is executing
  confidence_threshold: 40      # drop Tesseract words with conf < 40
  dedup_window: 5               # hash-dedup last 5 frames
  max_buffer_size: 30           # safety cap — never exceed 30 captures per turn
  cleanup_enabled: true         # set false to skip Gemma call (debug mode)
  cleanup_model: "google/gemma-4-31b-it"
```

Also update the top-of-file description comment to reference the new OCR integration, and bump the system prompt with a new section:

```
## Recent OCR Text (optional input)
A cleaned transcript of text captured from the screen between the last turn and
now (via background OCR + LLM cleanup). This may include dialogue that scrolled
by, menu labels, Pokemon names, HP numbers, and UI text.

Use this as a ground-truth for exact text — especially useful when the screenshot
captures a dialogue mid-scroll or when text is too small to read clearly. Trust
the screenshot for spatial layout and current game state; trust the OCR text for
exact character sequences.

This input may be empty if no text was captured or if cleanup failed.
```

### Step 4: Dashboard (`src/dashboard/static/index.html`)

Look at how `memory_update_output` events are rendered (there's a CSS class `.box-memory` and a handler in the JS). Add parallel handling for `ocr_flush`:

- New CSS class `.box-ocr` (suggest amber/yellow tone to differentiate)
- In the WebSocket message handler, render `ocr_flush` events inside the current turn's chat block with:
  - Collapsed header: "📝 OCR (N captures)"
  - Expanded: cleaned text + raw buffer dict (collapsible sub-section)

### Step 5: Report (`src/cli/report.py`)

Find where `memory_update_output` is rendered in each turn card. Add an OCR section showing:
- Number of captures
- Cleaned text (primary display)
- Collapsible raw buffer

### Step 6: Test

```bash
source venv/bin/activate
python tests/test_phase5.py --turns 3 --config configs/config-2.1.yaml --snapshot local/snapshots/before_oak
```

Verify:
1. Terminal shows `[Turn N] OCR: X captures, cleaned in Ys` per turn
2. `events.jsonl` contains `ocr_flush` events with both `raw` and `cleaned` fields
3. Dashboard shows an OCR panel in each turn's chat block
4. Report shows the OCR section per turn
5. Confirm cleaned text makes it into the user message (grep events.jsonl for "Recent OCR Text")

## Gotchas

1. **Thread safety**: `_buffer` is accessed from background thread (captures) and main thread (flush). Always use `self._lock`.

2. **Screenshot contention**: The existing agent already uses `/tmp/mgba_screenshot.png` via TCP. The OCR thread will call the same `capture_screenshot` function. If you see socket timeouts, consider giving OCR its own screenshot path (the Lua already writes to `/tmp/mgba_stream.png` at 15fps for the dashboard — could be reused for OCR).

3. **Gemma provider reliability**: The benchmark showed `google/gemma-4-31b-it` via Parasail occasionally returns provider errors. Make sure the error fallback path works — the agent should never crash because of OCR issues.

4. **Python 3.9**: The project uses Python 3.9 — don't use `X | Y` union syntax; use `Optional[X]` and `Union[X, Y]`.

5. **Config path tracking**: `config["_config_path"]` tracks which config was loaded — preserve this when copying config-2.0 → config-2.1.

6. **Existing logger.log_custom signature**: Check `src/core/logger.py` to confirm `log_custom(name, data)` is the right signature.

## Cost Estimate

- Gemma 4 31B: $0.14/M input, $0.40/M output
- Per turn: ~200 input tokens + ~100 output = ~$0.00007
- Per 100-turn run: ~$0.007 (negligible)

## Rollback

If OCR breaks anything, set `ocr.enabled: false` in the config — the agent reverts to current behavior (pure direct_multimodal, no OCR input).

## Benchmark Reference

All OCR approach comparisons live in `tests/ocr_benchmark/`:
- `run_benchmark.py` — runnable benchmark with all backends
- `ground_truth.json` — 36 labeled images
- `results.json` — cached results for all backends tested

Key winning function in the benchmark (same logic to port to ocr.py):
- `_tesseract_confidence_filter()` — confidence-based word filtering
- `_llm_cleanup()` — Gemma cleanup call
- `CLEANUP_SYSTEM_PROMPT` — the prompt that scored 0.678

---

Start with **Step 1** (refactor ocr.py). Verify with the small test script before moving to Step 2. Don't touch the dashboard/report until the core pipeline works in events.jsonl.
