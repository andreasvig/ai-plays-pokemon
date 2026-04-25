"""Phase 5 evaluation: Single-agent turn loop.

Launches mGBA, loads a snapshot, and runs the agent for a few turns.

Usage:
    python tests/test_phase5.py [--turns N] [--snapshot PATH]
"""

import argparse
import os
import subprocess
import sys
import time
import warnings
from pathlib import Path

# Silence pydantic-ai Gemini schema warnings
warnings.filterwarnings("ignore", message=".*additionalProperties.*")
warnings.filterwarnings("ignore", module="pydantic_ai")

# Unbuffered stdout so background runs show output in real time
sys.stdout.reconfigure(line_buffering=True)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.core import RunLogger, StateManager
from src.emulator import EmulatorClient, VisionPipeline, OCRRunner
from src.agent import TurnManager


def main():
    parser = argparse.ArgumentParser(description="Phase 5: Agent Turn Loop Test")
    parser.add_argument("--turns", type=int, default=5, help="Number of turns to run")
    parser.add_argument("--snapshot", type=str, default="local/snapshots/bedroom_start",
                        help="Snapshot to load")
    parser.add_argument("--config", type=str, default=None,
                        help="Config file path (default: latest from configs/)")
    args = parser.parse_args()

    # Kill any existing mGBA instances first
    subprocess.run(["pkill", "-f", "mgba"], capture_output=True)
    time.sleep(1)

    config = load_config(args.config)
    print(f"Using config: {config.get('_config_path', 'unknown')}")
    config["run_name"] = "phase5_test"

    rom_path = config["emulator"]["rom_path"]
    lua_script = os.path.abspath("lua/socketserver.lua")
    mgba_path = "/opt/homebrew/bin/mgba"

    # Start components
    emu = EmulatorClient(config)
    emu.start_server()

    # Wrap with caffeinate to prevent macOS App Nap when mGBA loses focus
    mgba_cmd = [mgba_path, rom_path]
    if sys.platform == "darwin":
        mgba_cmd = ["caffeinate", "-i"] + mgba_cmd

    mgba_proc = subprocess.Popen(
        mgba_cmd,
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
    if args.snapshot and os.path.exists(args.snapshot):
        state_file = os.path.join(args.snapshot, "emulator.state")
        if os.path.exists(state_file):
            emu.load_state(state_file)
            print(f"Snapshot loaded: {args.snapshot}")
            time.sleep(0.5)

    # Initialize all components
    logger = RunLogger(config)

    # Copy state and tasks from snapshot into run folder (self-contained)
    import json as _json
    import shutil as _shutil

    state_path = str(logger.run_dir / "state.json")
    snapshot_state = os.path.join(args.snapshot, "state.json") if args.snapshot else None
    if snapshot_state and os.path.exists(snapshot_state):
        _shutil.copy2(snapshot_state, state_path)
    else:
        with open(state_path, "w") as _f:
            _json.dump({}, _f)

    snapshot_tasks = os.path.join(args.snapshot, "tasks.json") if args.snapshot else None
    tasks_path = str(logger.run_dir / "tasks.json")
    if snapshot_tasks and os.path.exists(snapshot_tasks):
        _shutil.copy2(snapshot_tasks, tasks_path)

    state = StateManager(state_path)
    vision = VisionPipeline(config)
    ocr_runner = None
    if config.get("ocr", {}).get("enabled", False):
        # Read the Lua-managed stream file rather than calling CAP over TCP —
        # avoids socket contention with the main agent thread.
        from PIL import Image as _PILImage
        _stream_path = "/tmp/mgba_stream.png"

        def _ocr_screenshot():
            with _PILImage.open(_stream_path) as im:
                return im.copy()

        ocr_runner = OCRRunner(config, screenshot_fn=_ocr_screenshot)
        ocr_runner.start()

    # Start live dashboard
    from src.dashboard import start_dashboard
    start_dashboard(logger=logger, state_manager=state, config=config)

    # Create and run the turn manager
    turn_mgr = TurnManager(config)
    turn_mgr.setup(emu, state, vision, logger, ocr_runner)

    print(f"\nRunning {args.turns} turns...")
    print(f"Task: {config.get('task', {}).get('goal', 'Play the game')}")
    llm_alias = config.get("_llm_alias")
    llm_label = f"{llm_alias} → {config['llm_model']}" if llm_alias else config['llm_model']
    print(f"LLM: {llm_label}")
    print(f"VLM: {config['vlm_model']}")
    print(f"Run log: {logger.run_dir}\n")

    try:
        turn_mgr.run_loop(max_turns=args.turns)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if ocr_runner:
            ocr_runner.stop()
        logger.close()
        emu.disconnect()
        if mgba_proc.poll() is None:
            mgba_proc.terminate()
            mgba_proc.wait(timeout=5)

        # Always generate report
        try:
            from src.cli.report import load_events, group_events_by_turn, generate_html
            events = load_events(logger.run_dir)
            turns = group_events_by_turn(events)
            html = generate_html(logger.run_dir, events, turns)
            report_path = logger.run_dir / "report.html"
            with open(report_path, "w") as f:
                f.write(html)
            print(f"\nReport: {report_path}")
            if sys.platform == "darwin":
                subprocess.run(["open", str(report_path)], capture_output=True)
        except Exception as report_err:
            print(f"\nReport generation failed: {report_err}")

        print(f"Run log: {logger.run_dir}")
        print("mGBA closed.")


if __name__ == "__main__":
    main()
