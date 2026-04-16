"""OCR Benchmark: test different OCR backends against Pokemon game screenshots.

Runs each backend against every image in the dataset, compares output to ground
truth, and reports accuracy metrics.

Usage:
    python tests/ocr_benchmark/run_benchmark.py [--backends tesseract,easyocr,paddleocr,vlm]

Backends:
    tesseract   — Local Tesseract (pytesseract). Already installed.
    easyocr     — Local EasyOCR (pip install easyocr).
    paddleocr   — Local PaddleOCR (pip install paddlepaddle paddleocr).
    vlm         — Vision LLM via OpenRouter API (uses OPENROUTER_API_KEY from .env).

Set VLM_MODEL env var to override the default vision model (default: google/gemini-2.0-flash-001).
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from difflib import SequenceMatcher

from PIL import Image, ImageEnhance

BENCHMARK_DIR = Path(__file__).parent
IMAGES_DIR = BENCHMARK_DIR / "images"
GROUND_TRUTH_PATH = BENCHMARK_DIR / "ground_truth.json"

# Add project root to path for .env loading
sys.path.insert(0, str(BENCHMARK_DIR.parent.parent))


def load_ground_truth() -> dict:
    with open(GROUND_TRUTH_PATH) as f:
        return json.load(f)


def preprocess_for_ocr(
    img: Image.Image,
    upscale: int = 4,
    contrast: float = 3.0,
    threshold: int = 128,
    crop_bottom: float = 0.0,
) -> Image.Image:
    """Preprocessing pipeline for OCR.

    Args:
        img: Input image.
        upscale: Nearest-neighbor upscale factor (preserves pixel art).
        contrast: Contrast enhancement multiplier.
        threshold: Binarization threshold (0-255). Higher = more aggressive,
                   keeps only the darkest pixels as text.
        crop_bottom: If > 0, crop to only the bottom fraction of the image
                     (e.g. 0.38 = bottom 38%, where dialogue boxes live).
    """
    if crop_bottom > 0:
        h = img.height
        top = int(h * (1 - crop_bottom))
        img = img.crop((0, top, img.width, h))

    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale), Image.NEAREST)
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = img.point(lambda x: 255 if x > threshold else 0)
    return img


# ─── Backend: Tesseract variants ─────────────────────────────────────

def run_tesseract(img_path: Path) -> str:
    """Original: full image, PSM 6, default preprocessing."""
    import pytesseract
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    text = pytesseract.image_to_string(processed, config="--psm 6")
    return text.strip()


def run_tesseract_crop(img_path: Path) -> str:
    """Crop to bottom 38% (dialogue box region), PSM 6."""
    import pytesseract
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img, crop_bottom=0.38)
    text = pytesseract.image_to_string(processed, config="--psm 6")
    return text.strip()


def run_tesseract_sparse(img_path: Path) -> str:
    """Full image, PSM 11 (sparse text) — finds text scattered across the image."""
    import pytesseract
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    text = pytesseract.image_to_string(processed, config="--psm 11")
    return text.strip()


def run_tesseract_tuned(img_path: Path) -> str:
    """Tuned: 6x upscale, default threshold, PSM 6."""
    import pytesseract
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img, upscale=6, contrast=3.0, threshold=128)
    text = pytesseract.image_to_string(processed, config="--psm 6")
    return text.strip()


def run_tesseract_best(img_path: Path) -> str:
    """Crop bottom 38%, 6x upscale, PSM 7 (single line)."""
    import pytesseract
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img, upscale=6, contrast=3.0, threshold=128, crop_bottom=0.38)
    text = pytesseract.image_to_string(processed, config="--psm 6")
    return text.strip()


def _tesseract_confidence_filter(img: Image.Image, conf_threshold: int = 40) -> str:
    """Run Tesseract image_to_data and keep only high-confidence words."""
    import pytesseract
    data = pytesseract.image_to_data(
        img, config="--psm 6", output_type=pytesseract.Output.DICT
    )
    lines: dict[tuple, list[str]] = {}
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text:
            continue
        conf = int(data["conf"][i])
        if conf < conf_threshold:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(text)
    output_lines = [" ".join(lines[k]) for k in sorted(lines.keys())]
    return "\n".join(output_lines).strip()


def run_tesseract_confident(img_path: Path) -> str:
    """Per-word confidence filtering (threshold 60)."""
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    return _tesseract_confidence_filter(processed, conf_threshold=60)


def run_tesseract_conf40(img_path: Path) -> str:
    """Per-word confidence filtering (threshold 40 — less aggressive)."""
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    return _tesseract_confidence_filter(processed, conf_threshold=40)


def run_tesseract_combo(img_path: Path) -> str:
    """Combo: confidence filter (40) + regex cleanup."""
    import re
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    text = _tesseract_confidence_filter(processed, conf_threshold=40)

    # Extra pass: drop tokens that are mostly symbols/short noise
    cleaned_lines = []
    for line in text.split("\n"):
        tokens = line.split()
        kept = []
        for tok in tokens:
            alnum = sum(1 for c in tok if c.isalnum())
            # Keep tokens with >= 50% alphanumerics (or single known punct)
            if len(tok) >= 1 and (alnum / len(tok) >= 0.5 or tok in {"!", "?", "."}):
                kept.append(tok)
        if kept:
            cleaned_lines.append(" ".join(kept))
    return "\n".join(cleaned_lines).strip()


def run_tesseract_adaptive(img_path: Path) -> str:
    """Otsu's method for adaptive binarization instead of fixed threshold."""
    import pytesseract
    import numpy as np
    img = Image.open(img_path)
    # Upscale first
    img = img.resize((img.width * 4, img.height * 4), Image.NEAREST)
    img = img.convert("L")
    arr = np.array(img)
    # Otsu's threshold — find optimal threshold automatically
    hist, _ = np.histogram(arr, bins=256, range=(0, 256))
    total = arr.size
    current_max = 0
    threshold = 128
    sum_total = sum(i * hist[i] for i in range(256))
    sum_bg = 0
    weight_bg = 0
    for t in range(256):
        weight_bg += hist[t]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += t * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if variance > current_max:
            current_max = variance
            threshold = t
    # Apply binarization
    binary = Image.fromarray(np.where(arr > threshold, 255, 0).astype(np.uint8))
    text = pytesseract.image_to_string(binary, config="--psm 6")
    return text.strip()


def run_tesseract_regex(img_path: Path) -> str:
    """Run standard Tesseract, then post-process with regex to drop noise lines.

    Noise patterns:
    - Lines with > 40% non-alphanumeric characters
    - Isolated short symbol-heavy tokens
    """
    import pytesseract
    import re
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    text = pytesseract.image_to_string(processed, config="--psm 6")

    # Clean each line
    cleaned_lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Drop line if > 40% non-alphanumeric (excluding spaces)
        non_space = line.replace(" ", "")
        if not non_space:
            continue
        alpha_count = sum(1 for c in non_space if c.isalnum() or c in "'.,!?:-/éÉ")
        alpha_ratio = alpha_count / len(non_space)
        if alpha_ratio < 0.6:
            continue
        # Within the line, drop tokens that look like noise
        tokens = line.split()
        kept = []
        for tok in tokens:
            # Keep if at least 2 chars and mostly alphanumeric, or known punctuation-only tokens
            alnum = sum(1 for c in tok if c.isalnum())
            if len(tok) >= 2 and alnum / len(tok) >= 0.5:
                kept.append(tok)
            elif tok in {"!", "?", ".", ",", "'"}:
                kept.append(tok)
        if kept:
            cleaned = " ".join(kept)
            # Collapse runs of single letters (e.g. "a r r f f") — likely noise
            cleaned = re.sub(r"\b(\w) (\w) (\w) (\w)\b", "", cleaned)
            cleaned = " ".join(cleaned.split())
            if cleaned:
                cleaned_lines.append(cleaned)

    return "\n".join(cleaned_lines).strip()


# ─── Backend: EasyOCR ─────────────────────────────────────────────────

_easyocr_reader = None

def _get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(["en"], gpu=False)
    return _easyocr_reader


def run_easyocr(img_path: Path) -> str:
    import numpy as np
    reader = _get_easyocr_reader()
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    arr = np.array(processed)
    results = reader.readtext(arr)
    lines = [text for (_, text, _) in results]
    return "\n".join(lines).strip()


# ─── Backend: PaddleOCR ──────────────────────────────────────────────

_paddle_ocr = None

def _get_paddle_ocr():
    global _paddle_ocr
    if _paddle_ocr is None:
        from paddleocr import PaddleOCR
        _paddle_ocr = PaddleOCR(lang="en")
    return _paddle_ocr


def run_paddleocr(img_path: Path) -> str:
    import logging
    import numpy as np
    logging.disable(logging.WARNING)
    ocr = _get_paddle_ocr()
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    # PaddleOCR expects RGB, convert grayscale back to 3-channel
    rgb = processed.convert("RGB")
    arr = np.array(rgb)
    result = ocr.predict(arr)
    logging.disable(logging.NOTSET)
    # v3.4 returns a generator of dicts with 'rec_texts' and 'rec_scores'
    lines = []
    for res in result:
        if isinstance(res, dict) and "rec_texts" in res:
            lines.extend(res["rec_texts"])
        elif isinstance(res, dict) and "text" in res:
            lines.append(res["text"])
    if not lines:
        # Fallback: try old format
        try:
            if result and result[0]:
                lines = [line[1][0] for line in result[0]]
        except (TypeError, IndexError):
            pass
    return "\n".join(lines).strip()


# ─── Backend: Vision LLM (OpenRouter) ────────────────────────────────

def _run_vlm_with_model(img_path: Path, model: str) -> str:
    """Send screenshot to a vision LLM via OpenRouter and ask it to extract text."""
    import httpx

    # Load API key
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(BENCHMARK_DIR.parent.parent / ".env")
        api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return "[ERROR: OPENROUTER_API_KEY not set]"

    # Encode image as base64
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Extract ALL visible text from this Pokemon game screenshot. "
                                "Return ONLY the text exactly as it appears on screen, "
                                "preserving line breaks. No commentary or explanation. "
                                "If there is no text, return EMPTY."
                            ),
                        },
                    ],
                }
            ],
            "max_tokens": 200,
        },
        timeout=30.0,
    )
    data = response.json()
    if "error" in data:
        return f"[ERROR: {data['error'].get('message', data['error'])}]"
    text = data["choices"][0]["message"]["content"].strip()
    if text.upper() == "EMPTY":
        return ""
    return text


def run_vlm(img_path: Path) -> str:
    """Gemini 2.0 Flash via OpenRouter."""
    return _run_vlm_with_model(img_path, "google/gemini-2.0-flash-001")


def run_vlm_reka(img_path: Path) -> str:
    """Reka Edge via OpenRouter — cheap multimodal ($0.10/M tokens)."""
    return _run_vlm_with_model(img_path, "rekaai/reka-edge")


def _llm_cleanup(raw_text: str, model: str = "openai/gpt-oss-20b") -> str:
    """Send noisy OCR text to a small LLM and ask it to clean up into real text.

    Uses structured JSON output for reliable parsing.
    """
    import httpx

    if not raw_text.strip():
        return ""

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(BENCHMARK_DIR.parent.parent / ".env")
        api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return f"[ERROR: OPENROUTER_API_KEY not set]\n{raw_text}"

    system_prompt = (
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

    user_prompt = f"Clean up this OCR buffer and return the real text:\n\n{raw_text}"

    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
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
                                "description": "The cleaned text with line breaks preserved. Empty string if input is all noise.",
                            },
                        },
                        "required": ["cleaned_text"],
                        "additionalProperties": False,
                    },
                },
            },
        },
        timeout=60.0,
    )

    try:
        data = response.json()
    except Exception as e:
        return f"[LLM JSON ERROR: {e}]\n{raw_text}"

    if "error" in data:
        err = data["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return f"[LLM ERROR: {msg}]\n{raw_text}"

    try:
        choice = data["choices"][0]["message"]
        content = choice.get("content")
        if not content:
            return raw_text  # fallback to raw OCR if LLM returned nothing
        content = content.strip()
    except (KeyError, IndexError, AttributeError) as e:
        return f"[LLM PARSE ERROR: {e}]\n{raw_text}"

    try:
        parsed = json.loads(content)
        cleaned = parsed.get("cleaned_text", "")
        return (cleaned or "").strip()
    except json.JSONDecodeError:
        return content


def run_tesseract_llm(img_path: Path) -> str:
    """Tesseract (with confidence filter) + LLM cleanup via GPT-OSS-20B."""
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=40)
    return _llm_cleanup(raw, model="openai/gpt-oss-20b")


def run_tesseract_llm_qwen(img_path: Path) -> str:
    """Tesseract (with confidence filter) + LLM cleanup via Qwen 3.5 9B."""
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=40)
    return _llm_cleanup(raw, model="qwen/qwen3.5-9b")


def run_tesseract_llm_120b(img_path: Path) -> str:
    """Tesseract (with confidence filter) + LLM cleanup via GPT-OSS-120B."""
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=40)
    return _llm_cleanup(raw, model="openai/gpt-oss-120b")


def run_tesseract_llm_gemma(img_path: Path) -> str:
    """Tesseract (conf 40) + Gemma 4 31B cleanup. Pipeline used in production."""
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=40)
    return _llm_cleanup(raw, model="google/gemma-4-31b-it")


def run_tesseract_llm_gemma_conf20(img_path: Path) -> str:
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=20)
    return _llm_cleanup(raw, model="google/gemma-4-31b-it")


def run_tesseract_llm_gemma_conf30(img_path: Path) -> str:
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=30)
    return _llm_cleanup(raw, model="google/gemma-4-31b-it")


def run_tesseract_llm_gemma_conf50(img_path: Path) -> str:
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=50)
    return _llm_cleanup(raw, model="google/gemma-4-31b-it")


def run_tesseract_llm_gemma_conf60(img_path: Path) -> str:
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=60)
    return _llm_cleanup(raw, model="google/gemma-4-31b-it")


def run_tesseract_llm_gemma_conf70(img_path: Path) -> str:
    img = Image.open(img_path)
    processed = preprocess_for_ocr(img)
    raw = _tesseract_confidence_filter(processed, conf_threshold=70)
    return _llm_cleanup(raw, model="google/gemma-4-31b-it")


def run_vlm_gemma(img_path: Path) -> str:
    """Gemma 4 31B used directly as VLM — sees the image and extracts text."""
    return _run_vlm_with_model(img_path, "google/gemma-4-31b-it")


# ─── Scoring ──────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, collapse whitespace, strip punctuation variance."""
    text = text.lower().strip()
    # Normalize unicode (POKéMON → pokemon)
    text = text.replace("é", "e").replace("É", "e")
    # Collapse all whitespace to single spaces
    text = " ".join(text.split())
    return text


def score_text(predicted: str, expected: str) -> dict:
    """Score predicted text against expected. Returns metrics dict."""
    if not expected:
        # No text expected — score based on whether the OCR also returned nothing
        is_empty = len(predicted.strip()) == 0
        return {
            "exact_match": is_empty,
            "similarity": 1.0 if is_empty else 0.0,
            "char_accuracy": 1.0 if is_empty else 0.0,
            "category": "no_text",
        }

    pred_norm = normalize_text(predicted)
    exp_norm = normalize_text(expected)

    # Exact match (after normalization)
    exact = pred_norm == exp_norm

    # Sequence similarity (0-1)
    similarity = SequenceMatcher(None, pred_norm, exp_norm).ratio()

    # Character-level accuracy: what fraction of expected chars appear in output
    if len(exp_norm) > 0:
        matches = sum(1 for c in exp_norm if c in pred_norm)
        char_accuracy = matches / len(exp_norm)
    else:
        char_accuracy = 1.0

    return {
        "exact_match": exact,
        "similarity": round(similarity, 4),
        "char_accuracy": round(char_accuracy, 4),
        "category": "text",
    }


# ─── Main Benchmark ──────────────────────────────────────────────────

BACKENDS = {
    "tesseract": run_tesseract,
    "tesseract_crop": run_tesseract_crop,
    "tesseract_sparse": run_tesseract_sparse,
    "tesseract_tuned": run_tesseract_tuned,
    "tesseract_best": run_tesseract_best,
    "tesseract_confident": run_tesseract_confident,
    "tesseract_conf40": run_tesseract_conf40,
    "tesseract_combo": run_tesseract_combo,
    "tesseract_llm": run_tesseract_llm,
    "tesseract_llm_qwen": run_tesseract_llm_qwen,
    "tesseract_llm_120b": run_tesseract_llm_120b,
    "tesseract_llm_gemma": run_tesseract_llm_gemma,
    "tesseract_llm_gemma_conf20": run_tesseract_llm_gemma_conf20,
    "tesseract_llm_gemma_conf30": run_tesseract_llm_gemma_conf30,
    "tesseract_llm_gemma_conf50": run_tesseract_llm_gemma_conf50,
    "tesseract_llm_gemma_conf60": run_tesseract_llm_gemma_conf60,
    "tesseract_llm_gemma_conf70": run_tesseract_llm_gemma_conf70,
    "vlm_gemma": run_vlm_gemma,
    "tesseract_adaptive": run_tesseract_adaptive,
    "tesseract_regex": run_tesseract_regex,
    "easyocr": run_easyocr,
    "paddleocr": run_paddleocr,
    "vlm": run_vlm,
    "vlm_reka": run_vlm_reka,
}


def load_existing_results() -> dict:
    """Load previously saved results so we can append without re-running."""
    output_path = BENCHMARK_DIR / "results.json"
    if output_path.exists():
        with open(output_path) as f:
            return json.load(f)
    return {}


def print_summary(backend_name: str, backend_results: list[dict]):
    """Print summary stats for one backend's results."""
    text_results = [r for r in backend_results if r["category"] == "text"]
    no_text_results = [r for r in backend_results if r["category"] == "no_text"]

    if text_results:
        avg_sim = sum(r["similarity"] for r in text_results) / len(text_results)
        exact_matches = sum(1 for r in text_results if r["exact_match"])
        avg_char = sum(r["char_accuracy"] for r in text_results) / len(text_results)

        grid_sims = [r["similarity"] for r in text_results if r["has_grid"]]
        clean_sims = [r["similarity"] for r in text_results if not r["has_grid"]]

        print(f"\n  --- {backend_name} Summary (text images) ---")
        print(f"  Exact matches: {exact_matches}/{len(text_results)}")
        print(f"  Avg similarity: {avg_sim:.3f}")
        print(f"  Avg char accuracy: {avg_char:.3f}")
        if grid_sims:
            print(f"  Avg sim (grid):  {sum(grid_sims)/len(grid_sims):.3f}")
        if clean_sims:
            print(f"  Avg sim (clean): {sum(clean_sims)/len(clean_sims):.3f}")

    if no_text_results:
        false_positives = sum(1 for r in no_text_results if not r["exact_match"])
        print(f"  False positives (no-text): {false_positives}/{len(no_text_results)}")


def print_comparison(results: dict):
    """Print comparison table across all backends in results."""
    all_backends = sorted(results.keys())
    if not all_backends:
        return

    print(f"\n{'=' * 60}")
    print("  Comparison (all backends in results.json)")
    print(f"{'=' * 60}")
    print(f"  {'Backend':<15} {'Exact':>6} {'Sim':>6} {'Char':>6} {'Time':>6}  {'Images':>6}")
    print(f"  {'-'*15} {'-'*6} {'-'*6} {'-'*6} {'-'*6}  {'-'*6}")
    for name in all_backends:
        text_r = [r for r in results[name] if r["category"] == "text"]
        if not text_r:
            continue
        exact = sum(1 for r in text_r if r["exact_match"])
        avg_s = sum(r["similarity"] for r in text_r) / len(text_r)
        avg_c = sum(r["char_accuracy"] for r in text_r) / len(text_r)
        total_t = sum(r["time"] for r in results[name])
        n_imgs = len(results[name])
        print(f"  {name:<15} {exact:>4}/{len(text_r):<2} {avg_s:>5.3f} {avg_c:>5.3f} {total_t:>5.1f}s  {n_imgs:>6}")


def run_benchmark(backend_names: list[str], rerun: bool = False, only: str = None):
    ground_truth = load_ground_truth()
    all_images = sorted(ground_truth.keys())
    if only:
        all_images = [img for img in all_images if only in img]
        print(f"Filtering images to those matching: {only!r}")
    results = load_existing_results()

    print(f"Dataset: {len(all_images)} images")
    print(f"Backends to run: {', '.join(backend_names)}")
    if results:
        print(f"Existing results: {', '.join(sorted(results.keys()))}")
    print()

    for backend_name in backend_names:
        if backend_name not in BACKENDS:
            print(f"Unknown backend: {backend_name}")
            continue

        backend_fn = BACKENDS[backend_name]

        # Figure out which images already have results for this backend
        existing = {}
        if not rerun and backend_name in results:
            for r in results[backend_name]:
                existing[r["image"]] = r

        # Images that need running
        to_run = [img for img in all_images if img not in existing]
        cached = len(all_images) - len(to_run)

        print(f"{'=' * 60}")
        print(f"  Backend: {backend_name} ({cached} cached, {len(to_run)} to run)")
        print(f"{'=' * 60}")

        backend_results = list(existing.values())
        total_time = 0

        for image_name in all_images:
            gt = ground_truth[image_name]
            grid_tag = "grid" if gt.get("has_grid") else "clean"

            # Use cached result if available
            if image_name in existing:
                r = existing[image_name]
                status = "✓" if r["exact_match"] else "✗"
                print(f"  {status} {image_name:<40} sim={r['similarity']:.2f}  cached  [{grid_tag}]")
                continue

            img_path = IMAGES_DIR / f"{image_name}.png"
            if not img_path.exists():
                print(f"  SKIP: {image_name} (not found)")
                continue

            t0 = time.time()
            try:
                predicted = backend_fn(img_path)
            except Exception as e:
                predicted = f"[ERROR: {e}]"
            elapsed = time.time() - t0
            total_time += elapsed

            score = score_text(predicted, gt["expected_text"])
            score["image"] = image_name
            score["predicted"] = predicted
            score["expected"] = gt["expected_text"]
            score["has_grid"] = gt.get("has_grid", False)
            score["time"] = round(elapsed, 2)
            backend_results.append(score)

            # Print per-image result
            status = "✓" if score["exact_match"] else "✗"
            sim = score["similarity"]
            print(f"  {status} {image_name:<40} sim={sim:.2f}  {elapsed:.1f}s  [{grid_tag}]")
            if not score["exact_match"] and gt["expected_text"]:
                pred_short = predicted.replace("\n", "\\n")[:60]
                exp_short = gt["expected_text"].replace("\n", "\\n")[:60]
                print(f"    expected: {exp_short}")
                print(f"    got:      {pred_short}")

        print_summary(backend_name, backend_results)
        if total_time > 0:
            print(f"  New runs time: {total_time:.1f}s")
        print()

        results[backend_name] = backend_results

    # Save merged results
    output_path = BENCHMARK_DIR / "results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Results saved to: {output_path}")

    print_comparison(results)


def main():
    parser = argparse.ArgumentParser(description="OCR Benchmark")
    parser.add_argument(
        "--backends",
        type=str,
        default="tesseract",
        help="Comma-separated list of backends: tesseract,easyocr,paddleocr,vlm",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Re-run all images even if cached results exist",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Only run images whose names contain this substring (for debugging)",
    )
    args = parser.parse_args()
    backend_names = [b.strip() for b in args.backends.split(",")]
    run_benchmark(backend_names, rerun=args.rerun, only=args.only)


if __name__ == "__main__":
    main()
