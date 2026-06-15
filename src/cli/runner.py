"""Agent runner: launch mGBA, connect Lua, loop the agent for one or more (config, model) pairs.

Single (config, model) pair → one run.
Multiple pairs → sequential runs sharing one mGBA + Lua connection.

Pairing rules between --config and --model:
  - 1 × 1            → single run
  - 1 × N            → fan-out (one config, N models)
  - N × 1            → fan-out (N configs, one model)
  - N × N (same N)   → paired 1:1
  - N × M (N≠M, >1)  → error (Cartesian not supported; use a shell loop)
"""

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*additionalProperties.*")
warnings.filterwarnings("ignore", module="pydantic_ai")

sys.stdout.reconfigure(line_buffering=True)

from src.cli.slots import get_slot
from src.config import load_config
from src.core import RunLogger, StateManager
from src.emulator import EmulatorClient, VisionPipeline, OCRRunner
from src.agent import TurnManager


def _slug(s: str) -> str:
    """Filesystem-safe slug for model aliases like 'gemini-3.5-flash(medium)'."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()


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


def _resolve_task_master_model(config: dict, tm_model_alias: str | None) -> None:
    """Resolve the --task-master-model alias into config, in place.

    Mirrors the Player's `--model` resolution: the alias is looked up in
    configs/models.yaml the same way and expanded into `task_master_model`
    (the OpenRouter id) + `_task_master_llm_resolved` (the registry entry, which
    carries reasoning/temperature/provider/output_mode). B2's
    create_task_master_agent reads exactly those two keys.

    When `tm_model_alias` is None the flag was omitted — leave both keys unset so
    create_task_master_agent falls back to the Player's `llm_model` + `_llm_resolved`
    (the documented default: "defaults to the Player's --model when omitted").
    """
    if not tm_model_alias:
        return
    from src.config import _load_models_registry, _is_raw_model_id

    if _is_raw_model_id(tm_model_alias):
        # Raw provider/model id — use verbatim, no registry entry to resolve.
        config["task_master_model"] = tm_model_alias
        config["_task_master_alias"] = tm_model_alias
        return
    registry = _load_models_registry()
    entry = registry.get(tm_model_alias)
    if entry is None or not entry.get("openrouter_id"):
        known = ", ".join(sorted(registry)) or "(registry empty)"
        sys.exit(
            f"ERROR: --task-master-model alias {tm_model_alias!r} not found in "
            f"models.yaml. Known aliases: {known}."
        )
    config["task_master_model"] = entry["openrouter_id"]
    config["_task_master_llm_resolved"] = entry
    config["_task_master_alias"] = tm_model_alias


def prepare_config(
    path: str | None,
    model_alias: str,
    tm_model_alias: str | None = None,
) -> dict:
    """Load a config + bind it to a model alias.

    The model alias drives registry resolution (reasoning/temperature/provider/
    output_mode from configs/models.yaml). run_name combines the config stem
    with the model alias so multi-model sequences produce distinguishable
    run dirs and dashboard labels.

    `tm_model_alias` (from --task-master-model) is resolved the same way for the
    TaskMaster agent; when None it falls back to the Player's model.
    """
    config = load_config(path, llm_alias=model_alias)
    config = copy.deepcopy(config)
    _resolve_task_master_model(config, tm_model_alias)
    actual_path = config.get("_config_path") or path or ""
    stem = Path(actual_path).stem if actual_path else "default"
    slug = _slug(model_alias)
    config["run_name"] = f"{stem}__{slug}"
    config["run_label"] = f"{stem} · {model_alias}"
    return config


def run_prepare_phase(config: dict, saves_dir: Path) -> dict:
    """One-time setup: TCP server, mGBA launch, AppleScript window positioning.

    Returns a handle dict for downstream phases: emu, mgba_proc,
    caffeinate_proc, slot_cfg. Reusable across multiple `run_single_loop` calls.
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


def _maybe_open_report(report_path: Path, *, open_report: bool) -> bool:
    """Open the generated report in the OS viewer unless suppressed.

    Returns True iff it actually invoked the opener. The app/executor passes
    open_report=False (it uses the SPA); standalone `pokemon run` keeps True.
    """
    if open_report and sys.platform == "darwin":
        subprocess.run(["open", str(report_path)], capture_output=True)
        return True
    return False


def run_single_loop(
    handle: dict,
    config: dict,
    *,
    turns: int,
    snapshot: str | None,
    open_browser: bool = True,
    open_report: bool = True,
    on_run_dir=None,
) -> Path:
    """One agent run against the already-prepared mGBA + Lua connection.

    Builds fresh RunLogger, StateManager, VisionPipeline, OCRRunner,
    and dashboard session for THIS run. Reloads the snapshot to reset
    game state. Generates report HTML at the end. Returns run_dir.

    ``on_run_dir`` (optional): called with the run_dir Path the moment it is
    known — BEFORE the (blocking) turn loop — so a long-lived caller (the
    control-center executor) can publish the active run id while the run is in
    flight (live spectate + a matchable stop target), not only after it returns.

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
    # Publish the run dir to a long-lived caller (executor) the instant it's
    # known, so the control plane can expose the active run id DURING the run.
    if on_run_dir is not None:
        try:
            on_run_dir(run_dir)
        except Exception:
            pass

    # Continuation mode: copy prior run artifacts BEFORE the new logger writes
    # more events, and seed the screenshot id so new captures extend the
    # sequence instead of starting at 1 (would collide with copied files).
    continued_from = config.get("_continued_from")
    if continued_from:
        source_run = Path(continued_from)
        _copy_prior_run_artifacts(source_run, run_dir)
        logger.seed_screenshot_id()
        prior_turn = config.get("_continued_from_turn", "?")
        print(f"Continued from: {source_run} (savepoint turn {prior_turn})")

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

    # Fresh dashboard session per run. open_browser=False on runs 2..N in
    # sequential mode — user keeps the dashboard index (http://localhost:3420/)
    # open as the live "active runs" board.
    from src.dashboard import start_dashboard, unregister_run
    session = start_dashboard(
        logger=logger, state_manager=state, config=config,
        open_browser=open_browser,
    )

    turn_mgr = TurnManager(config)
    turn_mgr.setup(emu, state, vision, logger, ocr_runner)

    # TaskMaster state restore (--continue path): when TaskMaster is enabled and
    # the snapshot carries task_master_state.json, reload current_task +
    # current_task_index + task_history so the resumed run keeps its task tree
    # (and skips the cold-start). Legacy tasks.json restore (above) covers the
    # TM-disabled path.
    if turn_mgr.task_master_enabled and snapshot:
        from src.core.snapshots import SnapshotManager as _SnapMgr
        tm_state = _SnapMgr.load_task_master_state(snapshot)
        if tm_state is not None:
            turn_mgr.restore_task_master_state(tm_state)
            print(
                f"  TaskMaster state restored: task "
                f"{tm_state.get('current_task_index')} "
                f"({len(tm_state.get('task_history') or [])} prior in history)"
            )

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
        if turn_mgr.savepoint_on_crash:
            turn_mgr.save_savepoint("crash")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        if turn_mgr.savepoint_on_crash:
            turn_mgr.save_savepoint("crash")
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
            _maybe_open_report(report_path, open_report=open_report)
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


def _find_latest_savepoint(run_dir: Path) -> tuple[Path, int]:
    """Find the highest-numbered savepoint in <run_dir>/savepoints/.

    Returns (savepoint_dir, turn_number). Raises FileNotFoundError if no
    savepoint exists or the run dir is missing.
    """
    sp_root = run_dir / "savepoints"
    if not sp_root.is_dir():
        raise FileNotFoundError(
            f"No savepoints/ dir in {run_dir}. Was the source run configured "
            "with savepoints.every_n_turns > 0 or savepoints.at_end: true?"
        )
    candidates: list[tuple[int, Path]] = []
    for entry in sp_root.iterdir():
        if not entry.is_dir():
            continue
        m = re.match(r"turn_(\d+)$", entry.name)
        if m:
            candidates.append((int(m.group(1)), entry))
    if not candidates:
        raise FileNotFoundError(f"No turn_<N>/ savepoints found in {sp_root}")
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1], candidates[-1][0]


def continue_from_run(source_run_dir: str) -> tuple[dict, Path]:
    """Set up a continuation of a prior run.

    - Locates the latest savepoint inside <source_run_dir>/savepoints/.
    - Reads the source run's config.json to recover model alias + all settings.
    - Creates a new run dir at local/runs/<ts>_<run_name>_continued_from_turn_<N>/
      and copies events.jsonl + screenshots/ + ocr/ + terminal.log over verbatim.
    - Returns (continuation_config, savepoint_dir). The caller feeds these
      into run_single_loop; the agent's turn counter and history start fresh.
    """
    source = Path(source_run_dir).resolve()
    if not source.is_dir():
        sys.exit(f"ERROR: --continue path is not a directory: {source}")

    savepoint_dir, sp_turn = _find_latest_savepoint(source)

    config_path = source / "config.json"
    if not config_path.exists():
        sys.exit(f"ERROR: source run has no config.json: {config_path}")
    with open(config_path) as f:
        cfg = json.load(f)

    # load_config normally calls load_dotenv() to populate OPENROUTER_API_KEY
    # in os.environ — pydantic-ai's OpenRouter provider reads it from env when
    # no api_key arg is passed. We bypass load_config in the continue path,
    # so do the same step here.
    from dotenv import load_dotenv
    load_dotenv()
    cfg["openrouter_api_key"] = os.environ.get("OPENROUTER_API_KEY", "")

    base_name = cfg.get("run_name") or source.name.split("_", 2)[-1]
    cfg["run_name"] = f"{base_name}_continued_from_turn_{sp_turn}"
    label = cfg.get("run_label") or base_name
    cfg["run_label"] = f"{label} · continued from turn {sp_turn}"
    cfg["_continued_from"] = str(source)
    cfg["_continued_from_turn"] = sp_turn

    return cfg, savepoint_dir


def _copy_prior_run_artifacts(source_run_dir: Path, new_run_dir: Path) -> None:
    """Copy events.jsonl, screenshots/, ocr/, terminal.log from source to new run.

    Called AFTER RunLogger has created the new run dir (so it doesn't clobber
    the new events file header). The new events.jsonl gets the prior one's
    contents prepended, then the new logger continues appending.
    """
    src_events = source_run_dir / "events.jsonl"
    dst_events = new_run_dir / "events.jsonl"
    if src_events.exists():
        # Logger has already written run_start to dst_events. Prepend prior
        # content so chronology stays intact.
        prior = src_events.read_text()
        new_header = dst_events.read_text() if dst_events.exists() else ""
        dst_events.write_text(prior + new_header)

    for sub in ("screenshots", "ocr"):
        src_sub = source_run_dir / sub
        dst_sub = new_run_dir / sub
        if src_sub.is_dir():
            dst_sub.mkdir(exist_ok=True)
            for entry in src_sub.iterdir():
                shutil.copy2(entry, dst_sub / entry.name)

    src_term = source_run_dir / "terminal.log"
    dst_term = new_run_dir / "terminal.log"
    if src_term.exists():
        prior_text = src_term.read_text()
        existing = dst_term.read_text() if dst_term.exists() else ""
        dst_term.write_text(prior_text + existing)


def _resolve_pairs(
    configs: list[str | None], models: list[str],
) -> list[tuple[str | None, str]]:
    """Pair --config and --model into (config_path, model_alias) tuples.

    Rules: 1×1 single; 1×N or N×1 fan-out; N×N paired 1:1; N×M (N≠M, both>1) error.
    """
    nc, nm = len(configs), len(models)
    if nc == 1 and nm == 1:
        return [(configs[0], models[0])]
    if nc == 1:
        return [(configs[0], m) for m in models]
    if nm == 1:
        return [(c, models[0]) for c in configs]
    if nc == nm:
        return list(zip(configs, models))
    sys.exit(
        f"ERROR: --config ({nc}) × --model ({nm}) must be 1×1, 1×N, N×1, or N×N. "
        "Cartesian N×M (N≠M, both >1) is not supported — use a shell loop."
    )


def main():
    parser = argparse.ArgumentParser(
        prog="pokemon run",
        description="Run the agent for one or more (config, model) pairs against mGBA.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Single run with the latest config
  pokemon run --model "gemini-3.5-flash(medium)" --turns 50

  # Single run, specific config
  pokemon run --config configs/config-3.12.yaml --model "claude-opus-4.7(medium)"

  # Fan-out: one config across N models
  pokemon run --config configs/config-3.12.yaml \\
              --model "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" --turns 50

  # Paired 1:1: N configs × N models
  pokemon run --config configs/config-3.11.yaml configs/config-3.12.yaml \\
              --model "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" --turns 50

  # Continue a prior run from its latest savepoint (fresh turn counter)
  pokemon run --continue local/runs/2026-05-26_..._config-3.12__claude-opus-4-7 --turns 30
""",
    )
    parser.add_argument(
        "--config", nargs="+", default=[None],
        help="One or more config files. Default: latest config in configs/.",
    )
    parser.add_argument(
        "--model", nargs="+", default=None,
        help='One or more model aliases (e.g. "gemini-3.5-flash(medium)") or raw '
             '"provider/model" OpenRouter ids. Required unless --continue is set.',
    )
    parser.add_argument(
        "--task-master-model", dest="task_master_model", default=None,
        help='TaskMaster model alias (or raw "provider/model" id), resolved '
             "through models.yaml the same way as --model. Defaults to the "
             "Player's --model when omitted. Only used when task_master.enabled.",
    )
    parser.add_argument(
        "--continue", dest="continue_from", default=None,
        help="Path to a prior run dir. Continues from its latest savepoint with "
             "a fresh turn counter. Single-run only (no sequential). Ignores "
             "--config, --model, --snapshot — those are read from the source run.",
    )
    parser.add_argument(
        "--turns", type=int, default=10,
        help="Turns per run (applied to every pair). Default: 10.",
    )
    parser.add_argument(
        "--snapshot", default="local/snapshots/bedroom_start",
        help="Snapshot reloaded before each run's turn loop.",
    )
    parser.add_argument(
        "--connect-timeout", type=float, default=300.0,
        help="Timeout (seconds) for the initial Lua connection. Default: 300.",
    )
    parser.add_argument(
        "--kill-existing", action="store_true",
        help="pkill any existing mGBA before launching.",
    )
    args = parser.parse_args()

    if args.continue_from:
        if args.model or args.config != [None]:
            sys.exit(
                "ERROR: --continue is exclusive with --config/--model. "
                "The source run's config and model are reused automatically."
            )
        cfg, savepoint_dir = continue_from_run(args.continue_from)
        prepared = [cfg]
        # The savepoint dir is what run_single_loop's --snapshot path expects.
        args.snapshot = str(savepoint_dir)
    else:
        if not args.model:
            sys.exit("ERROR: --model is required (unless --continue is set).")
        pairs = _resolve_pairs(args.config, args.model)
        prepared = [
            prepare_config(c, m, tm_model_alias=args.task_master_model)
            for c, m in pairs
        ]

    if args.kill_existing:
        subprocess.run(["pkill", "-f", "mgba"], capture_output=True)
        time.sleep(1)

    if args.snapshot and not os.path.exists(args.snapshot):
        print(f"  ⚠ snapshot not found: {args.snapshot} (continuing without)")
        args.snapshot = None

    rom_path = prepared[0]["emulator"]["rom_path"]
    if not os.path.exists(rom_path):
        sys.exit(f"ERROR: ROM not found at {rom_path}")

    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    saves_dir = Path(f"local/runs/_session_{ts}/saves")
    saves_dir.mkdir(parents=True, exist_ok=True)

    multi = len(prepared) > 1
    if multi:
        print(f"\n=== Sequential run: {len(prepared)} (config × model) pairs × {args.turns} turns ===")
        for i, c in enumerate(prepared, 1):
            label = c.get("_llm_alias") or c["llm_model"]
            print(f"  {i}. {c.get('_config_path', '?')}  →  {label}")
        print("Dashboard index: http://localhost:3420/   (each run gets its own tab)\n")
    else:
        cfg = prepared[0]
        print(f"Using config: {cfg.get('_config_path', 'unknown')}")

    handle = run_prepare_phase(prepared[0], saves_dir)
    try:
        run_connect_phase(handle, timeout=args.connect_timeout)
    except Exception as e:
        print(f"Initial Lua connect failed: {e}")
        cleanup_handle(handle)
        sys.exit(1)

    completed = 0
    try:
        for i, cfg in enumerate(prepared, 1):
            if multi:
                label = cfg.get("_llm_alias") or cfg["llm_model"]
                print(f"\n{'=' * 60}")
                print(f"  RUN {i}/{len(prepared)}  —  {label}")
                print(f"{'=' * 60}")
            run_single_loop(
                handle, cfg, turns=args.turns, snapshot=args.snapshot,
                open_browser=(i == 1),
            )
            completed += 1
    except KeyboardInterrupt:
        print("\nInterrupted — aborting remaining runs.")
    finally:
        cleanup_handle(handle)

    if multi:
        print(f"\n=== Done. {completed}/{len(prepared)} runs completed. mGBA shut down. ===")


if __name__ == "__main__":
    main()
