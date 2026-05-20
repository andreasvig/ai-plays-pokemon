"""Launch script for AI Plays Pokemon.

Starts the Python TCP server, launches mGBA, and opens the Scripting window.
The user must load the Lua script manually (one click from recent scripts).

Usage:
    python launch.py                  # Normal launch
    python launch.py --snapshot PATH  # Launch from a snapshot
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import load_config
from src.emulator import EmulatorClient


def find_mgba() -> str:
    """Find the mGBA executable."""
    candidates = [
        "mgba-qt",
        "mgba",
        "/opt/homebrew/bin/mgba",
        "/usr/local/bin/mgba",
        "/Applications/mGBA.app/Contents/MacOS/mGBA",
    ]
    for path in candidates:
        full = subprocess.run(
            ["which", path], capture_output=True, text=True
        ).stdout.strip()
        if full:
            return full
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        "Could not find mGBA. Install with: brew install mgba"
    )


def open_scripting_window():
    """Open mGBA's Scripting window via AppleScript (macOS)."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(['osascript', '-e', '''
            tell application "System Events"
                tell process "mGBA"
                    set frontmost to true
                    delay 0.3
                    click menu item "Scripting..." of menu "Tools" of menu bar 1
                end tell
            end tell
        '''], capture_output=True, timeout=10)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Launch AI Plays Pokemon")
    parser.add_argument("--snapshot", type=str, default=None,
                        help="Path to a snapshot folder to start from")
    parser.add_argument("--config", type=str, default=None,
                        help="Config file path (default: latest from configs/)")
    args = parser.parse_args()

    config = load_config(args.config)
    rom_path = config["emulator"]["rom_path"]

    if not os.path.exists(rom_path):
        print(f"ERROR: ROM not found at {rom_path}")
        sys.exit(1)

    lua_script = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "lua", "socketserver.lua")
    )
    mgba_path = find_mgba()

    # Start TCP server first
    emu = EmulatorClient(config)
    emu.start_server()

    # Launch mGBA muted (-C mute=1 is a per-launch override; doesn't touch ~/.config/mGBA/config.ini).
    # caffeinate prevents macOS App Nap when backgrounded.
    mgba_cmd = [mgba_path, "-C", "mute=1", rom_path]
    if sys.platform == "darwin":
        mgba_cmd = ["caffeinate", "-i"] + mgba_cmd

    mgba_proc = subprocess.Popen(
        mgba_cmd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print(f"mGBA started (PID: {mgba_proc.pid})")

    # Open the scripting window automatically
    time.sleep(2)
    open_scripting_window()

    print(f"\n{'='*50}")
    print("Load the Lua script in mGBA Scripting window:")
    print(f"  File > Load script... > socketserver.lua")
    print(f"  (should be in recent scripts)")
    print(f"{'='*50}\n")

    try:
        emu.wait_for_connection(timeout=120.0)
        print("=== Connected! ===\n")

        # Load snapshot if requested
        snapshot_path = args.snapshot or config.get("load_snapshot")
        if snapshot_path:
            state_file = os.path.join(snapshot_path, "emulator.state")
            if os.path.exists(state_file):
                emu.load_state(state_file)
                print(f"Snapshot loaded: {snapshot_path}")

        alive = emu.ping()
        print(f"Ping: {'OK' if alive else 'FAILED'}")

        print("Press Ctrl+C to stop.\n")
        while True:
            if mgba_proc.poll() is not None:
                print("mGBA closed.")
                break
            if not emu.ping():
                print("Lost connection to mGBA.")
                break
            time.sleep(2)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        emu.disconnect()
        if mgba_proc.poll() is None:
            mgba_proc.terminate()
            mgba_proc.wait(timeout=5)
            print("mGBA closed.")


if __name__ == "__main__":
    main()
