"""Snapshot CLI - save, load, and list snapshots.

Usage:
    python snapshot_cli.py save <name> [--description "..."]
    python snapshot_cli.py load <path>
    python snapshot_cli.py list

Flow:
1. Run this script with a command
2. mGBA launches, you load the Lua script
3. The command executes
4. For 'save': play the game to a desired point, then press Enter to save
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.cli.launch import find_mgba
from src.config import load_config
from src.core import SnapshotManager
from src.emulator import EmulatorClient


def launch_and_connect(config):
    """Launch mGBA and wait for connection."""
    rom_path = config["emulator"]["rom_path"]
    mgba_path = find_mgba()

    emu = EmulatorClient(config)
    emu.start_server()

    mgba_proc = subprocess.Popen(
        [mgba_path, rom_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    time.sleep(2)
    # Open scripting window
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

    return emu, mgba_proc


def cmd_save(args, config):
    """Save a snapshot."""
    emu, mgba_proc = launch_and_connect(config)
    snap = SnapshotManager(config, emu)

    print("Play the game to the point you want to snapshot.")
    print("Press Enter here when ready to save...")
    input()

    path = snap.save_snapshot(
        name=args.name,
        description=args.description or "",
    )
    print(f"\nSnapshot saved: {path}")

    # Take a screenshot for reference
    img = emu.capture_screenshot()
    img.save(str(path / "preview.png"))
    print(f"Preview screenshot saved.")

    emu.disconnect()
    mgba_proc.terminate()
    mgba_proc.wait(timeout=5)


def cmd_load(args, config):
    """Load a snapshot and let the user play."""
    emu, mgba_proc = launch_and_connect(config)
    snap = SnapshotManager(config, emu)

    metadata = snap.load_snapshot(args.path)
    print(f"Snapshot loaded: {metadata.get('name', args.path)}")
    if metadata.get("description"):
        print(f"  Description: {metadata['description']}")
    if metadata.get("timestamp_human"):
        print(f"  Saved at: {metadata['timestamp_human']}")

    print("\nGame is running. Press Ctrl+C to stop.")
    try:
        while True:
            if mgba_proc.poll() is not None:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    emu.disconnect()
    if mgba_proc.poll() is None:
        mgba_proc.terminate()
        mgba_proc.wait(timeout=5)


def cmd_list(args, config):
    """List all snapshots."""
    snap_dir = Path(config.get("snapshots_directory", "local/snapshots"))
    if not snap_dir.exists():
        print("No snapshots directory found.")
        return

    snapshots = []
    for entry in sorted(snap_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta_file = entry / "metadata.json"
        if meta_file.exists():
            import json
            with open(meta_file) as f:
                meta = json.load(f)
            snapshots.append((entry, meta))
        elif (entry / "emulator.state").exists():
            snapshots.append((entry, {"name": entry.name}))

    if not snapshots:
        print("No snapshots found.")
        return

    print(f"{'Name':<30} {'Date':<22} {'Description'}")
    print("-" * 80)
    for path, meta in snapshots:
        name = meta.get("name", path.name)
        date = meta.get("timestamp_human", "")
        desc = meta.get("description", "")
        print(f"{name:<30} {date:<22} {desc}")
    print(f"\n{len(snapshots)} snapshot(s) found in {snap_dir}/")


def main():
    parser = argparse.ArgumentParser(description="Snapshot Manager")
    sub = parser.add_subparsers(dest="command")

    save_parser = sub.add_parser("save", help="Save a snapshot")
    save_parser.add_argument("name", help="Name for the snapshot")
    save_parser.add_argument("--description", "-d", help="Description")

    load_parser = sub.add_parser("load", help="Load a snapshot")
    load_parser.add_argument("path", help="Path to snapshot folder")

    sub.add_parser("list", help="List all snapshots")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    config = load_config()

    if args.command == "list":
        cmd_list(args, config)
    elif args.command == "save":
        cmd_save(args, config)
    elif args.command == "load":
        cmd_load(args, config)


if __name__ == "__main__":
    main()
