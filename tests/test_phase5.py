"""Phase 5 evaluation: Single-agent turn loop.

Launches mGBA, loads a snapshot, and runs the agent for N turns.

Usage:
    python tests/test_phase5.py [--turns N] [--snapshot PATH] [--config PATH]
    python tests/test_phase5.py --kill-existing   # pkill mgba before launching

For multiple configs sharing one mGBA + Lua connection, see
tests/test_sequential.py — same underlying helpers, loops the
single_run step over a list of configs.
"""

import argparse
import os
import subprocess
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*additionalProperties.*")
warnings.filterwarnings("ignore", module="pydantic_ai")

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cli.slots import get_slot
from src.config import load_config
from src.core import RunLogger, StateManager
from src.emulator import EmulatorClient, VisionPipeline, OCRRunner
from src.agent import TurnManager


def position_mgba_window_for_pid(pid: int, x: int, y: int) -> None:
    """Move the main mGBA window to a known position so the user can find it."""
    if sys.platform != "darwin":
        return
    script = f'''
        tell application "System Events"
            tell (first process whose unix id is {pid})
                repeat 8 times
                    try
                        set targetWin to first window whose name starts with "mGBA"
                        set position of targetWin to {{{x}, {y}}}
                        exit repeat
                    on error
                        delay 0.4
                    end try
                end repeat
            end tell
        end tell
    '''
    subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)


def open_scripting_window_for_pid(pid: int) -> bool:
    """Open the Scripting window in the target mGBA via AXRaise + menu click.

    Returns True iff a Scripting window was confirmed after the click.
    Single-instance mGBA means AXRaise + menu click is straightforward;
    the retry loop just absorbs slow window initialisation.
    """
    if sys.platform != "darwin":
        return True
    script = f'''
        tell application "System Events"
            set targetProc to first process whose unix id is {pid}
            set succeeded to false
            repeat 6 times
                try
                    tell targetProc
                        set gameWin to first window whose name starts with "mGBA"
                        perform action "AXRaise" of gameWin
                    end tell
                    delay 0.8
                    tell targetProc
                        click menu item "Scripting..." of menu "Tools" of menu bar 1
                    end tell
                    delay 0.7
                    if exists (first window of targetProc whose name contains "Scripting") then
                        set succeeded to true
                        exit repeat
                    end if
                on error
                    -- swallow, retry
                end try
                delay 0.7
            end repeat
            if succeeded then
                return "ok"
            else
                return "fail"
            end if
        end tell
    '''
    result = subprocess.run(
        ['osascript', '-e', script], capture_output=True, timeout=20, text=True,
    )
    return result.stdout.strip() == "ok"


def run_prepare_phase(config: dict, saves_dir: Path) -> dict:
    """One-time setup: TCP server, mGBA launch, AppleScript window positioning.

    Returns a handle dict for downstream phases: emu, mgba_proc,
    caffeinate_proc, slot_cfg. The handle is reusable across multiple
    `run_single_loop` calls (sequential mode).

    `saves_dir` is where mGBA writes battery saves + savestates. Must
    exist before this call.
    """
    slot_cfg = get_slot(1)
    config["emulator"]["port"] = slot_cfg["port"]
    paths = config.setdefault("paths", {})
    paths["stream"] = slot_cfg["stream_path"]
    paths["screenshot"] = slot_cfg["screenshot_path"]
    paths["lua"] = str(slot_cfg["lua_path"])

    emu = EmulatorClient(config)
    emu.start_server()

    rom_path = config["emulator"]["rom_path"]
    mgba_path = "/opt/homebrew/bin/mgba"
    mgba_cmd = [
        mgba_path,
        "-C", "mute=1",
        "-C", f"savegamePath={saves_dir}",
        "-C", f"savestatePath={saves_dir}",
        rom_path,
    ]
    mgba_proc = subprocess.Popen(
        mgba_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"mGBA launched (PID {mgba_proc.pid})")

    caffeinate_proc = None
    if sys.platform == "darwin":
        caffeinate_proc = subprocess.Popen(
            ["caffeinate", "-i", "-w", str(mgba_proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    time.sleep(2.5)
    win_x, win_y = slot_cfg["window_pos"]
    position_mgba_window_for_pid(mgba_proc.pid, win_x, win_y)
    open_scripting_window_for_pid(mgba_proc.pid)

    print(f"window at ({win_x},{win_y}). "
          f"In its Scripting window: File > Load recent script > socketserver-1.lua")

    return {
        "emu": emu,
        "mgba_proc": mgba_proc,
        "caffeinate_proc": caffeinate_proc,
        "slot_cfg": slot_cfg,
    }


def run_connect_phase(handle: dict, timeout: float = 300.0) -> None:
    """Block until the Lua client connects to the TCP server."""
    handle["emu"].wait_for_connection(timeout=timeout)
    print("Connected.")


def run_single_loop(
    handle: dict,
    config: dict,
    *,
    turns: int,
    snapshot: str | None,
    open_browser: bool = True,
) -> Path:
    """One agent run against the already-prepared mGBA + Lua connection.

    Builds fresh RunLogger, StateManager, VisionPipeline, OCRRunner,
    and dashboard session for THIS run. Reloads the snapshot to reset
    game state. Generates report HTML at the end. Returns run_dir.

    The handle's emu/mgba/caffeinate stay live across calls — callers
    can invoke this repeatedly for sequential multi-config runs, then
    call `cleanup_handle` once at the end.

    Raises KeyboardInterrupt after cleanup if the user interrupted, so
    sequential orchestrators can abort the remainder.
    """
    emu = handle["emu"]
    slot_cfg = handle["slot_cfg"]

    # Each call's `config` is deep-copied from disk and doesn't carry the
    # slot paths populated in run_prepare_phase. Re-stamp them so the
    # dashboard's ScreenStreamer reads /tmp/mgba_stream_1.png (where Lua
    # writes) instead of falling back to <run_dir>/mgba_stream.png.
    paths = config.setdefault("paths", {})
    paths["stream"] = slot_cfg["stream_path"]
    paths["screenshot"] = slot_cfg["screenshot_path"]
    paths["lua"] = str(slot_cfg["lua_path"])
    config["emulator"]["port"] = slot_cfg["port"]

    logger = RunLogger(config)
    run_dir = Path(logger.run_dir)
    print(f"Run log: {run_dir}")

    if snapshot and os.path.exists(snapshot):
        state_file = os.path.join(snapshot, "emulator.state")
        if os.path.exists(state_file):
            emu.load_state(state_file)
            print(f"Snapshot loaded: {snapshot}")
            time.sleep(0.5)

    import json as _json
    import shutil as _shutil

    state_path = str(run_dir / "state.json")
    snapshot_state = os.path.join(snapshot, "state.json") if snapshot else None
    if snapshot_state and os.path.exists(snapshot_state):
        _shutil.copy2(snapshot_state, state_path)
    else:
        with open(state_path, "w") as _f:
            _json.dump({}, _f)

    snapshot_tasks = os.path.join(snapshot, "tasks.json") if snapshot else None
    tasks_path = str(run_dir / "tasks.json")
    if snapshot_tasks and os.path.exists(snapshot_tasks):
        _shutil.copy2(snapshot_tasks, tasks_path)

    state = StateManager(state_path)
    vision = VisionPipeline(config)
    ocr_runner = None
    if config.get("ocr", {}).get("enabled", False):
        from PIL import Image as _PILImage
        stream_path = slot_cfg["stream_path"]

        def _ocr_screenshot():
            with _PILImage.open(stream_path) as im:
                return im.copy()

        ocr_runner = OCRRunner(config, screenshot_fn=_ocr_screenshot)
        ocr_runner.start()

    # Fresh dashboard session: new EventBridge (empty event buffer) +
    # new ScreenStreamer. Previous run's live history is implicitly
    # cleared by the unregister at the end of that run.
    #
    # open_browser=False on runs 2..N in sequential mode — avoids
    # spawning a new tab per run. The user keeps the dashboard index
    # (http://localhost:3420/) open as the live "active runs" board.
    from src.dashboard import start_dashboard, unregister_run
    session = start_dashboard(
        logger=logger, state_manager=state, config=config,
        open_browser=open_browser,
    )

    turn_mgr = TurnManager(config)
    turn_mgr.setup(emu, state, vision, logger, ocr_runner)

    print(f"Running {turns} turns...")
    print(f"Task: {config.get('task', {}).get('goal', 'Play the game')}")
    llm_alias = config.get("_llm_alias")
    llm_label = f"{llm_alias} → {config['llm_model']}" if llm_alias else config['llm_model']
    print(f"LLM: {llm_label}")

    user_interrupted = False
    try:
        turn_mgr.run_loop(max_turns=turns)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        user_interrupted = True
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if ocr_runner:
            ocr_runner.stop()
        logger.close()
        unregister_run(session.run_id)

        try:
            from src.cli.report import load_events, group_events_by_turn, generate_html
            events = load_events(run_dir)
            turns_data = group_events_by_turn(events)
            html = generate_html(run_dir, events, turns_data)
            report_path = run_dir / "report.html"
            with open(report_path, "w") as f:
                f.write(html)
            print(f"Report: {report_path}")
            if sys.platform == "darwin":
                subprocess.run(["open", str(report_path)], capture_output=True)
        except Exception as report_err:
            print(f"\nReport generation failed: {report_err}")

        print(f"Run log: {run_dir}")

    if user_interrupted:
        raise KeyboardInterrupt
    return run_dir


def cleanup_handle(handle: dict) -> None:
    """Disconnect emu, terminate mGBA + caffeinate. Idempotent."""
    emu = handle.get("emu")
    if emu is not None:
        try:
            emu.disconnect()
        except Exception:
            pass

    mgba_proc = handle.get("mgba_proc")
    if mgba_proc is not None and mgba_proc.poll() is None:
        mgba_proc.terminate()
        try:
            mgba_proc.wait(timeout=5)
        except Exception:
            pass

    caffeinate_proc = handle.get("caffeinate_proc")
    if caffeinate_proc is not None and caffeinate_proc.poll() is None:
        caffeinate_proc.terminate()


def main():
    parser = argparse.ArgumentParser(description="Phase 5: Single-config agent run")
    parser.add_argument("--turns", type=int, default=5, help="Number of turns to run")
    parser.add_argument("--snapshot", type=str, default="local/snapshots/bedroom_start",
                        help="Snapshot to load")
    parser.add_argument("--config", type=str, default=None,
                        help="Config file path (default: latest from configs/)")
    parser.add_argument("--kill-existing", action="store_true",
                        help="pkill any existing mGBA before launching.")
    args = parser.parse_args()

    if args.kill_existing:
        subprocess.run(["pkill", "-f", "mgba"], capture_output=True)
        time.sleep(1)

    if args.snapshot and not os.path.exists(args.snapshot):
        print(f"  ⚠ snapshot not found: {args.snapshot} (continuing without)")
        args.snapshot = None

    config = load_config(args.config)
    print(f"Using config: {config.get('_config_path', 'unknown')}")
    config["run_name"] = "phase5_test"

    rom_path = config["emulator"]["rom_path"]
    if not os.path.exists(rom_path):
        print(f"ERROR: ROM not found at {rom_path}")
        sys.exit(1)

    # mGBA needs a savegamePath/savestatePath, but the agent never
    # uses in-game saves (snapshot reloads handle state). Use a shared
    # session-scoped saves dir so the per-run dirs stay clean.
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    saves_dir = Path(f"local/runs/_session_{ts}/saves")
    saves_dir.mkdir(parents=True, exist_ok=True)

    handle = run_prepare_phase(config, saves_dir)
    try:
        run_connect_phase(handle)
        run_single_loop(handle, config, turns=args.turns, snapshot=args.snapshot)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_handle(handle)


if __name__ == "__main__":
    main()
