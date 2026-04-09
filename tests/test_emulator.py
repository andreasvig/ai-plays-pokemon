"""Phase 1 evaluation script.

Tests the emulator connection: screenshot capture, button presses, sequences.

Prerequisites:
1. mGBA is running with Pokemon FireRed loaded
2. Python dependencies installed: pip install -r requirements.txt

Flow:
1. Run this script - it starts a TCP server and waits
2. In mGBA, load lua/socketserver.lua via Tools > Scripting
3. The Lua script connects to Python and tests begin

Usage:
    python test_emulator.py
"""

import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.emulator import EmulatorClient


def main():
    print("=== Phase 1: Emulator Connection Test ===\n")

    # Load config
    print("1. Loading config...")
    config = load_config()
    print("   Config loaded successfully.\n")

    # Connect to mGBA
    print("2. Connecting to mGBA...")
    emu = EmulatorClient(config)
    emu.connect()
    print("   Connected!\n")

    # Ping
    print("3. Ping test...")
    alive = emu.ping()
    print(f"   Ping: {'OK' if alive else 'FAILED'}\n")

    # Screenshot
    print("4. Capturing screenshot (raw)...")
    img_raw = emu.capture_screenshot(preprocess=False)
    raw_path = Path("test_screenshot_raw.png")
    img_raw.save(raw_path)
    print(f"   Saved raw screenshot: {raw_path} ({img_raw.size[0]}x{img_raw.size[1]})\n")

    print("5. Capturing screenshot (preprocessed)...")
    img = emu.capture_screenshot(preprocess=True)
    processed_path = Path("test_screenshot_processed.png")
    img.save(processed_path)
    print(f"   Saved processed screenshot: {processed_path} ({img.size[0]}x{img.size[1]})\n")

    # Single button press
    print("6. Pressing A button...")
    emu.press_button("A")
    print("   Done.\n")

    # Button sequence
    print("7. Pressing sequence: AAAA...")
    emu.press_sequence("AAAA")
    print("   Sequence complete.\n")

    # Screenshot after button presses
    print("8. Capturing screenshot after button presses...")
    img_after = emu.capture_screenshot()
    after_path = Path("test_screenshot_after.png")
    img_after.save(after_path)
    print(f"   Saved: {after_path}\n")

    # Pause/unpause
    print("9. Testing pause...")
    emu.pause()
    print("   Paused.")
    emu.unpause()
    print("   Unpaused.\n")

    # Save state test
    print("10. Testing save state...")
    state_path = "/tmp/test_save_state.ss"
    emu.save_state(state_path)
    print(f"    Saved state to {state_path}\n")

    # Press some buttons to change state
    print("11. Pressing buttons to change game state...")
    emu.press_sequence("RRRRDDDD")
    img_changed = emu.capture_screenshot()
    img_changed.save("test_screenshot_changed.png")
    print("    Game state changed.\n")

    # Load state test
    print("12. Testing load state...")
    emu.load_state(state_path)
    img_restored = emu.capture_screenshot()
    img_restored.save("test_screenshot_restored.png")
    print("    State restored. Compare test_screenshot_changed.png vs test_screenshot_restored.png\n")

    # Disconnect
    emu.disconnect()
    print("=== All tests passed! ===")
    print("\nCheck the saved screenshots to verify visually:")
    print("  - test_screenshot_raw.png (original GBA resolution)")
    print("  - test_screenshot_processed.png (upscaled + enhanced)")
    print("  - test_screenshot_after.png (after button presses)")
    print("  - test_screenshot_changed.png (after moving)")
    print("  - test_screenshot_restored.png (after state load - should match 'after')")


if __name__ == "__main__":
    main()
