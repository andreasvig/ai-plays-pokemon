"""Launch mGBA + Python TCP server for ad-hoc / snapshot work.

For agent runs, use tests/test_phase5.py (one config) or
tests/test_sequential.py (N configs sharing one mGBA + Lua connection).

Usage:
    python launch.py
    python launch.py --snapshot PATH
    python launch.py --config configs/config-3.5.yaml
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.cli.slots import get_slot
from src.config import load_config
from src.emulator import EmulatorClient


def find_mgba() -> str:
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


def open_scripting_window_for_pid(pid: int) -> None:
    """Open the Scripting window in the mGBA instance with the given Unix PID."""
    if sys.platform != "darwin":
        return
    script = f'''
        tell application "System Events"
            set targetProc to first process whose unix id is {pid}
            tell targetProc
                set frontmost to true
                delay 0.3
                click menu item "Scripting..." of menu "Tools" of menu bar 1
            end tell
        end tell
    '''
    try:
        subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)
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

    slot_cfg = get_slot()
    config["emulator"]["port"] = slot_cfg["port"]

    # Ephemeral work dir for per-launch save paths.
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    work_dir = Path("local/launch") / timestamp
    saves_dir = work_dir / "saves"
    saves_dir.mkdir(parents=True, exist_ok=True)

    mgba_path = find_mgba()

    emu = EmulatorClient(config)
    emu.start_server()

    mgba_cmd = [
        mgba_path,
        "-C", "mute=1",
        "-C", f"savegamePath={saves_dir}",
        "-C", f"savestatePath={saves_dir}",
        rom_path,
    ]
    mgba_proc = subprocess.Popen(
        mgba_cmd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"mGBA started (PID: {mgba_proc.pid})")

    caffeinate_proc = None
    if sys.platform == "darwin":
        caffeinate_proc = subprocess.Popen(
            ["caffeinate", "-i", "-w", str(mgba_proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    time.sleep(2)
    open_scripting_window_for_pid(mgba_proc.pid)

    print(f"\n{'='*60}")
    print(f"  In the Scripting window: File > Load recent script")
    print(f"  Pick:  socketserver-1.lua")
    print(f"  Path:  {slot_cfg['lua_path']}")
    print(f"{'='*60}\n")

    try:
        emu.wait_for_connection(timeout=300.0)
        print("=== Connected! ===\n")

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
        if caffeinate_proc is not None and caffeinate_proc.poll() is None:
            caffeinate_proc.terminate()


if __name__ == "__main__":
    main()
