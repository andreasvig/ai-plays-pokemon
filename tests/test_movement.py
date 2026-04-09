"""Movement calibration test.

Loads a snapshot, runs a series of movement commands, and captures
before/after screenshots for each. Saves results to a test folder
for visual inspection.

Usage:
    python tests/test_movement.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.emulator import EmulatorClient


def main():
    # Kill any existing mGBA
    subprocess.run(["pkill", "-f", "mgba"], capture_output=True)
    time.sleep(1)

    config = load_config()
    rom_path = config["emulator"]["rom_path"]
    mgba_path = "/opt/homebrew/bin/mgba"

    # Output folder
    out_dir = Path("local/test_movement")
    out_dir.mkdir(parents=True, exist_ok=True)

    emu = EmulatorClient(config)
    emu.start_server()

    mgba_proc = subprocess.Popen(
        [mgba_path, rom_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"mGBA launched (PID: {mgba_proc.pid})")

    time.sleep(2)
    if sys.platform == "darwin":
        subprocess.run(['osascript', '-e', '''
            tell application "System Events"
                tell process "mGBA"
                    set frontmost to true
                    delay 0.3
                    click menu item "Scripting..." of menu "Tools" of menu bar 1
                end tell
            end tell
        '''], capture_output=True, timeout=10)

    print("Load the Lua script in mGBA Scripting window.")
    emu.wait_for_connection(timeout=120.0)
    print("Connected!\n")

    # Load snapshot
    snapshot = "local/snapshots/bedroom_start"
    state_file = os.path.join(snapshot, "emulator.state")
    if os.path.exists(state_file):
        emu.load_state(state_file)
        print(f"Snapshot loaded: {snapshot}")
        time.sleep(0.5)

    # First close the menu (bedroom_start has menu open)
    print("\n--- Closing menu with B ---")
    emu.press_button("B")
    time.sleep(0.5)

    # Define test movements
    # Each test: (description, button_list, expected_facing_after)
    tests = [
        ("Single down (should move 1 tile down)", ["down"], "down"),
        ("Single down again (same dir, no turn needed)", ["down"], "down"),
        ("Single right (was facing down, needs turn)", ["right"], "right"),
        ("Single right again (already facing right)", ["right"], "right"),
        ("Two ups (facing right, first turns then moves)", ["up", "up"], "up"),
        ("Left then down (two direction changes)", ["left", "down"], "down"),
        ("Three rights (facing down, should move 3 tiles right)", ["right", "right", "right"], "right"),
        ("Up + A (move up then interact)", ["up", "a"], "up"),
    ]

    results = []

    for i, (desc, buttons, expected_facing) in enumerate(tests):
        test_num = i + 1
        print(f"\n{'='*50}")
        print(f"Test {test_num}: {desc}")
        print(f"  Buttons: {buttons}")
        print(f"  Current facing: {emu.facing or 'unknown'}")

        # Before screenshot
        before = emu.capture_screenshot(preprocess=True)
        before_path = out_dir / f"{test_num:02d}_before.png"
        before.save(str(before_path))

        # Show what the turning compensation does
        normalized = emu.normalize_button_list(buttons)
        adjusted = emu._insert_turning_frames(normalized)
        print(f"  Normalized: {normalized}")
        print(f"  After turn compensation: {adjusted}")

        # Execute
        try:
            emu.press_button_list(buttons)
            success = True
        except Exception as e:
            print(f"  ERROR: {e}")
            success = False

        # After screenshot
        after = emu.capture_screenshot(preprocess=True)
        after_path = out_dir / f"{test_num:02d}_after.png"
        after.save(str(after_path))

        print(f"  Facing after: {emu.facing}")
        print(f"  Screenshots: {before_path.name} -> {after_path.name}")

        # Check if the screenshot actually changed
        from PIL import Image
        import hashlib

        def img_hash(img):
            small = img.resize((32, 32)).convert("L")
            return hashlib.md5(small.tobytes()).hexdigest()

        moved = img_hash(before) != img_hash(after)
        print(f"  Screen changed: {moved}")

        results.append({
            "test": test_num,
            "description": desc,
            "buttons": buttons,
            "normalized": normalized,
            "adjusted": adjusted,
            "facing_before": tests[i-1][2] if i > 0 else "unknown",
            "facing_after": emu.facing,
            "screen_changed": moved,
            "success": success,
        })

    # Summary
    print(f"\n{'='*50}")
    print("SUMMARY")
    print(f"{'='*50}")
    for r in results:
        status = "MOVED" if r["screen_changed"] else "NO CHANGE"
        extra = f" (adjusted: {r['adjusted']})" if r["adjusted"] != r["normalized"] else ""
        print(f"  Test {r['test']}: {status} - {r['description']}{extra}")

    # Save results
    with open(out_dir / "results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nScreenshots saved to: {out_dir}/")
    print("Compare before/after pairs visually to verify movement accuracy.")

    emu.disconnect()
    if mgba_proc.poll() is None:
        mgba_proc.terminate()
        mgba_proc.wait(timeout=5)
    print("mGBA closed.")


if __name__ == "__main__":
    main()
