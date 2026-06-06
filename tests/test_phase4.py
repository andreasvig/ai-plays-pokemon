"""Phase 4 evaluation: OCR + Vision Pipeline tests.

OCR tests run locally against saved screenshots.
Vision pipeline tests require an OpenRouter API key in config.yaml.

Usage:
    python test_phase4.py ocr          # Test OCR only (no API needed)
    python test_phase4.py vision       # Test vision pipeline (needs API key)
    python test_phase4.py              # Test both
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_ocr():
    """Test OCR on game screenshots."""
    from PIL import Image
    from src.emulator import OCRRunner

    print("=== OCR Tests ===\n")

    config = {"ocr": {"enabled": True, "backend": "tesseract"}}

    # Dummy screenshot function (not used in manual tests)
    runner = OCRRunner(config, screenshot_fn=lambda: Image.new("RGB", (240, 160)))

    # Test with the snapshot preview if it exists
    preview = Path("local/snapshots/bedroom_start/preview.png")
    if not preview.exists():
        print("No snapshot preview found. Skipping screenshot OCR test.")
        print("Run: python snapshot_cli.py save 'bedroom_start' first.\n")
    else:
        print("1. OCR on bedroom screenshot...")
        img = Image.open(preview)
        text = runner.process_single(img)
        if text:
            print(f"   Extracted text:\n   ---\n   {text}\n   ---")
        else:
            print("   No text extracted (or duplicate)")
        print("   OK\n")

    # Test deduplication
    print("2. Deduplication test...")
    img1 = Image.new("RGB", (240, 160), color=(100, 100, 100))
    img2 = Image.new("RGB", (240, 160), color=(100, 100, 100))  # Same
    img3 = Image.new("RGB", (240, 160), color=(200, 50, 50))    # Different

    runner2 = OCRRunner(config, screenshot_fn=lambda: img1)
    result1 = runner2.process_single(img1)
    result2 = runner2.process_single(img2)  # Should be None (duplicate)
    result3 = runner2.process_single(img3)  # Should process

    assert result2 is None, f"Expected None for duplicate, got: {result2}"
    print("   Duplicate correctly rejected")
    print("   OK\n")

    # Test text merging
    print("3. Text merging test...")
    runner3 = OCRRunner(config, screenshot_fn=lambda: img1)
    runner3._add_to_buffer("Welcome to t")
    runner3._add_to_buffer("Welcome to the Pokemon Center")
    buffer = runner3.get_buffer()
    assert len(buffer) == 1, f"Expected 1 merged entry, got {len(buffer)}"
    assert "Pokemon Center" in buffer[0]
    print(f"   Merged: '{buffer[0]}'")
    print("   OK\n")

    # Test buffer retrieval
    print("4. Buffer operations...")
    runner4 = OCRRunner(config, screenshot_fn=lambda: img1)
    runner4._add_to_buffer("Line 1")
    runner4._add_to_buffer("Line 2")
    runner4._add_to_buffer("Line 3")
    buf = runner4.get_buffer()
    assert len(buf) == 3
    runner4.clear_buffer()
    assert len(runner4.get_buffer()) == 0
    print("   Buffer add/get/clear works")
    print("   OK\n")

    print("=== OCR Tests Passed ===\n")


def test_vision():
    """Test the screenshot-encoding pipeline (direct multimodal, no API)."""
    from PIL import Image
    from src.config import load_config
    from src.emulator import VisionPipeline

    print("=== Vision Pipeline Tests ===\n")

    config = load_config()

    # Load a real screenshot if available, otherwise synthesize one.
    preview = Path("local/snapshots/bedroom_start/preview.png")
    if not preview.exists():
        print("No snapshot preview found. Creating a test image.")
        img = Image.new("RGB", (720, 480), color=(100, 150, 200))
    else:
        img = Image.open(preview)

    vision = VisionPipeline(config)

    print("1. Testing analyze_screenshot (direct multimodal encoding)...")
    analysis = vision.analyze_screenshot(img)
    assert "image_base64" in analysis
    print(f"   Image base64 length: {len(analysis['image_base64'])} chars")
    print("   OK\n")

    print("2. Testing format_for_llm...")
    content = vision.format_for_llm(analysis)
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    print("   OK\n")

    print("3. Testing image_to_data_url...")
    url = vision.image_to_data_url(img)
    assert url.startswith("data:image/png;base64,")
    print("   OK\n")

    print("=== Vision Pipeline Tests Passed ===\n")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "ocr" in args:
        test_ocr()

    if not args or "vision" in args:
        test_vision()

    print("Phase 4: DONE")
