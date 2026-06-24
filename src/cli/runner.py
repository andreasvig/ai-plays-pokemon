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

from src.cli.launch import find_mgba


# Chained continues used to append ``_continued_from_turn_<N>`` to the *full*
# prior run_name, so names grew without bound and eventually hit the macOS
# 255-byte path limit after several resume cycles.
_CONTINUE_SUFFIX_RE = re.compile(r"(_continued_from_turn_\d+)+$")


def _root_run_name(name: str) -> str:
    """Return the original run slug, stripping any continue suffix chain."""
    return _CONTINUE_SUFFIX_RE.sub("", name)
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
    try:
        subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        pass


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


def set_mgba_mute_for_pid(pid: int, mute: bool) -> bool:
    """Set mGBA's ``Audio/Video → Mute`` toggle to ``mute`` for the given pid.

    mGBA exposes no runtime audio control over the Lua socket (its scripting API
    is input/memory/savestate only), so the warm emulator is muted/unmuted by
    driving its native macOS menu via Accessibility — the same mechanism used to
    position the window + open the Scripting window. The menu item is a checkbox:
    we read its mark and click ONLY when the current state differs, so this is an
    idempotent set-to-state (not a blind toggle). Raises the game window first so
    the menu bar is the game window's (the Scripting window collapses it to File).

    macOS-only; a no-op returning True elsewhere. Returns True iff the desired
    state was confirmed/achieved. Best-effort — callers should not depend on it.
    """
    if sys.platform != "darwin":
        return True
    want = "true" if mute else "false"
    script = f'''
        tell application "System Events"
            set targetProc to first process whose unix id is {pid}
            set wantMuted to {want}
            repeat 5 times
                try
                    tell targetProc
                        set gameWin to first window whose name starts with "mGBA -"
                        perform action "AXRaise" of gameWin
                    end tell
                    delay 0.25
                    tell targetProc
                        set mi to menu item "Mute" of menu 1 of menu bar item "Audio/Video" of menu bar 1
                        set isMuted to ((value of attribute "AXMenuItemMarkChar" of mi) is not missing value)
                        if isMuted is not wantMuted then click mi
                    end tell
                    return "ok"
                on error
                    delay 0.4
                end try
            end repeat
            return "fail"
        end tell
    '''
    try:
        result = subprocess.run(
            ['osascript', '-e', script], capture_output=True, timeout=15, text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.stdout.strip() == "ok"


# Default TaskMaster for custom/casual (freeplay) runs. Gemma-as-Player is fine,
# but Gemma prompted JSON is too flaky for TaskMaster handoffs — Gemini tool mode
# is reliable. Overridable via task_master.model in the config YAML or --task-master-model.
DEFAULT_FREEPLAY_TASK_MASTER_MODEL = "gemini-3.5-flash(medium)"


def _resolve_task_master_model(config: dict, tm_model_alias: str | None) -> None:
    """Resolve the TaskMaster model alias into config, in place.

    Mirrors the Player's ``--model`` resolution: the alias is looked up in
    ``configs/models.yaml`` and expanded into ``task_master_model`` (OpenRouter id)
    + ``_task_master_llm_resolved`` (registry entry with output_mode etc.).

    Precedence when ``tm_model_alias`` is omitted:
      1. ``task_master.model`` in the loaded config YAML/JSON
      2. ``DEFAULT_FREEPLAY_TASK_MASTER_MODEL`` when ``task_master.mode`` is ``freeplay``
      3. unset → create_task_master_agent falls back to the Player's model
    """
    alias = tm_model_alias
    if not alias:
        tm_cfg = config.get("task_master") or {}
        alias = tm_cfg.get("model")
        if not alias and tm_cfg.get("mode") == "freeplay":
            alias = DEFAULT_FREEPLAY_TASK_MASTER_MODEL
    if not alias:
        return
    from src.config import _is_raw_model_id, _load_models_registry, resolve_model_selection

    if _is_raw_model_id(alias):
        config["task_master_model"] = alias
        config["_task_master_alias"] = alias
        config.pop("_task_master_llm_resolved", None)
        return
    registry = _load_models_registry()
    try:
        resolved = resolve_model_selection(alias, registry)
    except ValueError as e:
        label = tm_model_alias or alias
        sys.exit(f"ERROR: --task-master-model {label!r}: {e}")
    config["task_master_model"] = resolved["openrouter_id"]
    config["_task_master_llm_resolved"] = resolved
    config["_task_master_alias"] = resolved["_alias"]


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
    mgba_path = find_mgba()
    # NOTE: launch with audio ENABLED — do NOT pass `-C mute=1`. That core-option
    # override mutes at a level the Audio/Video → Mute menu cannot clear, so it
    # would defeat the runtime mute toggle (verified 2026-06-16: focused + menu
    # unmuted but still silent under the override). Default-muted is instead
    # achieved by toggling the menu Mute on right after connect (see the app
    # supervisor + the headless `run` path), which the toggle can later reverse.
    mgba_cmd = [
        mgba_path,
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
    on_run_dir=None,
    should_stop=None,
) -> Path:
    """One agent run against the already-prepared mGBA + Lua connection.

    Builds fresh RunLogger, StateManager, VisionPipeline, OCRRunner,
    and dashboard session for THIS run. Reloads the snapshot to reset
    game state. Returns run_dir.

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
        # Restore the referee gate latch from the savepoint BUNDLE (not the
        # source run dir's separately-written file), capped to the savepoint
        # turn. Must run before setup() builds the Referee (which auto-loads
        # run_dir/referee_state.json). An official continue scores against this.
        if isinstance(prior_turn, int):
            _restore_referee_state(snapshot, run_dir, prior_turn)
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
    # Cooperative stop hook (control-center executor passes a predicate that's
    # true once a stop is requested for this run) — checked at each turn boundary.
    turn_mgr._should_stop = should_stop

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

    # Player history restore (--continue path): the emulator + TaskMaster state
    # are back, but the Player's transient context (turn_explanations, the
    # historic-image buffer, the global turn counter, the in-progress task's
    # evidence) lived only in memory and would otherwise restart empty — the
    # resumed agent's first turn would show "(none — this is the first turn.)"
    # and forget everything. Rebuild it from the (already-copied) events.jsonl so
    # continuing is indistinguishable from never having stopped.
    if continued_from:
        sp_turn = config.get("_continued_from_turn")
        if isinstance(sp_turn, int):
            turn_mgr.restore_player_history(
                run_dir / "events.jsonl",
                run_dir / "screenshots",
                sp_turn,
            )

            # Cumulative cost / time / tokens: seed the run summary accounting AND
            # the live stats baseline from the source run's summary, so a resumed
            # run keeps counting up instead of restarting from zero (Andreas).
            source_summary_path = Path(continued_from) / "run_summary.json"
            if source_summary_path.exists():
                source_summary = None
                try:
                    with open(source_summary_path) as _sf:
                        source_summary = _json.load(_sf)
                except (OSError, ValueError):
                    source_summary = None
                if source_summary:
                    turn_mgr.restore_run_accounting(source_summary)
                    _cost = source_summary.get("cost") or {}
                    _sess = source_summary.get("session") or {}
                    session.bridge.seed_stats(
                        cost=float(_cost.get("llm_usd", 0) or 0)
                        + float(_cost.get("ocr_usd", 0) or 0),
                        turns=sp_turn,
                        input_tokens=int(_cost.get("total_input_tokens", 0) or 0),
                        output_tokens=int(_cost.get("total_output_tokens", 0) or 0),
                        prior_duration_s=float(_sess.get("duration_seconds", 0) or 0),
                    )

            # The source's task_started is in the copied events.jsonl but not in
            # this session's live stream, so re-announce the restored task to the
            # live spectate (else its "Current task" panel shows "No task yet").
            if turn_mgr.task_master_enabled and turn_mgr.current_task:
                _ct = turn_mgr.current_task
                session.bridge.inject({
                    "type": "task_started",
                    "task_index": turn_mgr.current_task_index,
                    "title": _ct.get("title", ""),
                    "description": _ct.get("description", ""),
                    "success_criteria": _ct.get("success_criteria", ""),
                    "global_turn": sp_turn + 1,
                })

            # Same reason for the referee gate HUD: the latch is restored in
            # backend state, but the spectate HUD is built only from
            # referee_checkpoint events on this session's live stream (the prior
            # run's are in the copied events.jsonl, not the in-memory bridge). So
            # re-announce every already-stamped gate, else a resumed official run
            # shows its cleared gates as un-reached ("gates not persisted").
            if turn_mgr.referee is not None:
                for _ev in turn_mgr.referee.stamped_events():
                    session.bridge.inject(_ev)

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
            # Stamp the LAST SETTLED turn, not turn_number: a cooperative stop can
            # abort mid-turn (the in-flight turn was cancelled before its buttons
            # pressed), so the live emulator state is the prior settled turn. This
            # makes resume re-run the exact turn the agent was killed on.
            turn_mgr.save_savepoint("crash", turn=turn_mgr._last_settled_turn)
        # A cooperative stop aborts the loop before its clean finalize. Write the
        # full summary now so the killed run still has a readable report; status
        # stays None and the executor stamps `cancelled` (voided if official).
        turn_mgr.finalize_run_summary(status=None)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        if turn_mgr.savepoint_on_crash:
            # A fault can surface mid-turn before the action settled; stamp the
            # last settled turn so resume replays from a clean boundary.
            turn_mgr.save_savepoint("crash", turn=turn_mgr._last_settled_turn)
        # A mid-run fault (e.g. a model erroring out — the Gemma run, 2026-06-17).
        # Record the run as `crashed` so it lands in History as INCOMPLETE with
        # its report intact and never posts to the leaderboard, instead of
        # defaulting to `completed` and masquerading as a real result.
        turn_mgr.finalize_run_summary(status="crashed")
    finally:
        if ocr_runner:
            ocr_runner.stop()
        logger.close()
        unregister_run(session.run_id)

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
    - Returns (continuation_config, savepoint_dir). The caller feeds these into
      run_single_loop, which restores the emulator + TaskMaster state from the
      savepoint AND rebuilds the Player's turn history / turn counter / historic
      images from the copied events.jsonl (TurnManager.restore_player_history),
      so the resumed run picks up exactly where it left off.
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

    base_name = _root_run_name(cfg.get("run_name") or source.name.split("_", 2)[-1])
    cfg["run_name"] = f"{base_name}_continued_from_turn_{sp_turn}"
    label = _root_run_name(cfg.get("run_label") or base_name)
    cfg["run_label"] = f"{label} · continued from turn {sp_turn}"
    cfg["_continued_from"] = str(source)
    cfg["_continued_from_turn"] = sp_turn

    _resolve_task_master_model(cfg, None)

    return cfg, savepoint_dir


def _restore_referee_state(savepoint_dir, new_run_dir: Path, up_to_turn: int) -> None:
    """Restore the referee gate latch from a savepoint bundle into a continued run.

    Writes a capped ``referee_state.json`` into ``new_run_dir`` so the Referee
    built in ``setup()`` auto-loads it (``Referee._load_state``). Caps stamps to
    turns ``<= up_to_turn``: this defends the hard-kill case where the source
    run's separately-written latch was AHEAD of the savepoint's emulator state —
    a gate stamped after the savepoint turn must NOT be credited, or the resumed
    run would score a gate its restored game state hasn't actually reached.

    Best-effort: a missing/corrupt bundle file leaves the continued run with a
    fresh (empty) latch rather than raising.
    """
    if savepoint_dir is None:
        return
    src = Path(savepoint_dir) / "referee_state.json"
    if not src.exists():
        return
    try:
        data = json.loads(src.read_text())
    except (OSError, ValueError):
        return
    stamps = data.get("stamps", {}) if isinstance(data, dict) else {}
    kept = {
        cid: int(t)
        for cid, t in stamps.items()
        if isinstance(t, (int, float)) and int(t) <= up_to_turn
    }
    autofilled = [c for c in data.get("autofilled", []) if c in kept]
    try:
        (Path(new_run_dir) / "referee_state.json").write_text(
            json.dumps({"stamps": kept, "autofilled": autofilled}, indent=2)
        )
    except OSError:
        pass


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
  pokemon run --config configs/config-3.13.yaml --model "claude-opus-4.7(medium)"

  # Fan-out: one config across N models
  pokemon run --config configs/config-3.13.yaml \\
              --model "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" --turns 50

  # Paired 1:1: N configs × N models
  pokemon run --config configs/config-3.13.yaml configs/config-tm-smoke.yaml \\
              --model "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" --turns 50

  # Continue a prior run from its latest savepoint (fresh turn counter)
  pokemon run --continue local/runs/2026-05-26_..._config-3.13__claude-opus-4-7 --turns 30
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
                "The source run's config and Player model are reused automatically; "
                "use --task-master-model to override only the TaskMaster model."
            )
        cfg, savepoint_dir = continue_from_run(args.continue_from)
        _resolve_task_master_model(cfg, args.task_master_model)
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
