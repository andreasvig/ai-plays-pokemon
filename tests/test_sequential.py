"""Sequential agent runs over N configs sharing one mGBA + Lua connection.

Each config gets:
  - its own run directory (local/runs/<timestamp>_<run_name>)
  - its own RunLogger (events, tokens, costs, turn JSONs)
  - its own dashboard session — fresh EventBridge + ScreenStreamer so
    the live UI's history clears between runs; the previous run's
    persisted log/report is unaffected
  - its own report.html
  - a snapshot reload before turn 1, so each run starts from an
    identical game state

The user loads the Lua script exactly once (after the first config's
mGBA + Scripting window appear). Every subsequent config in the list
runs against the same warm connection.

Behaviour:
  - Per-run errors are caught inside run_single_loop and printed; the
    next config still runs.
  - Ctrl+C aborts the remainder of the sequence after the current run
    cleans up (logger closed, report generated).

Usage:
    # Paired 1:1 — N configs × N models (same length)
    python tests/test_sequential.py \\
        --configs configs/config-3.11.yaml configs/config-3.12.yaml \\
        --models "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" \\
        --turns 50

    # Fan-out — 1 config × N models (same baseline, different models)
    python tests/test_sequential.py \\
        --config configs/config-3.9.yaml \\
        --models "gemini-3.5-flash(medium)" "claude-opus-4.7(medium)" \\
        --turns 50

    # Single — 1 config × 1 model (equivalent to test_phase5.py)
    python tests/test_sequential.py --config configs/config-3.5.yaml \\
        --models "gpt-5.5(medium)" --turns 20

Model aliases come from configs/models.yaml; raw "provider/model" ids also
work. Cartesian (N configs × M models, M≠N) is intentionally NOT supported
— use a shell `for` loop if you need it.
"""

import argparse
import copy
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

from src.config import load_config


def _slug(s: str) -> str:
    """Filesystem-safe slug for model aliases like 'gemini-3.5-flash(medium)'."""
    import re
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()


def prepare_config(path: str, model_alias: str) -> dict:
    """Load a config + bind it to a model alias.

    The model alias drives registry resolution (reasoning/temperature/provider/
    output_mode from configs/models.yaml). run_name combines the config stem
    with the model alias so multi-model sequences produce distinguishable
    run dirs and dashboard labels.
    """
    config = load_config(path, llm_alias=model_alias)
    config = copy.deepcopy(config)
    stem = Path(path).stem
    slug = _slug(model_alias)
    config["run_name"] = f"{stem}__{slug}"
    config["run_label"] = f"{stem} · {model_alias}"
    return config


def main():
    parser = argparse.ArgumentParser(description="Sequential agent runs over N (config, model) pairs.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--configs", nargs="+",
                       help="N config files (paired 1:1 with --models — same length required).")
    group.add_argument("--config", type=str,
                       help="One config file (fan-out: N runs share this baseline, one per --models entry).")
    parser.add_argument("--models", nargs="+", required=True,
                        help='Model aliases from configs/models.yaml (e.g. "gemini-3.5-flash(medium)") '
                             'or raw "provider/model" OpenRouter ids. Paired 1:1 with --configs, OR '
                             'fanned-out across the single --config.')
    parser.add_argument("--turns", type=int, default=50,
                        help="Turns per run (applied to every (config, model) pair).")
    parser.add_argument("--snapshot", type=str,
                        default="local/snapshots/bedroom_start",
                        help="Snapshot reloaded before each run's turn loop.")
    parser.add_argument("--connect-timeout", type=float, default=300.0,
                        help="Timeout (seconds) for the initial Lua connection.")
    parser.add_argument("--kill-existing", action="store_true",
                        help="pkill any existing mGBA before launching.")
    args = parser.parse_args()

    if args.kill_existing:
        subprocess.run(["pkill", "-f", "mgba"], capture_output=True)
        time.sleep(1)

    if args.snapshot and not os.path.exists(args.snapshot):
        print(f"  ⚠ snapshot not found: {args.snapshot} (continuing without)")
        args.snapshot = None

    # Resolve (config, model) pairs. Fan-out: 1 config × N models. Paired 1:1
    # otherwise — mismatched lengths are an error (Cartesian isn't supported,
    # use a shell loop if you need it).
    if args.config is not None:
        pairs = [(args.config, m) for m in args.models]
    else:
        if len(args.configs) != len(args.models):
            print(
                f"ERROR: --configs (N={len(args.configs)}) and --models (N={len(args.models)}) "
                "must have the same length for 1:1 pairing. Use --config (singular) for fan-out."
            )
            sys.exit(1)
        pairs = list(zip(args.configs, args.models))

    configs = [prepare_config(path, model) for path, model in pairs]
    if not configs:
        print("At least one (config, model) pair required.")
        sys.exit(1)

    rom_path = configs[0]["emulator"]["rom_path"]
    if not os.path.exists(rom_path):
        print(f"ERROR: ROM not found at {rom_path}")
        sys.exit(1)

    # Late import — pydantic-ai pulls a lot of code.
    from tests.test_phase5 import (
        run_prepare_phase, run_connect_phase, run_single_loop, cleanup_handle,
    )

    print(f"\n=== Sequential run: {len(configs)} configs × {args.turns} turns ===")
    for i, c in enumerate(configs, 1):
        label = c.get("_llm_alias") or c["llm_model"]
        print(f"  {i}. {c.get('_config_path', '?')}  →  {label}")
    print("Dashboard index: http://localhost:3420/   (each run gets its own tab)")
    print()

    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    saves_dir = Path(f"local/runs/_session_{ts}/saves")
    saves_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== PREPARE: launching mGBA (config 1/{len(configs)} drives the prep) ===")
    handle = run_prepare_phase(configs[0], saves_dir)

    try:
        run_connect_phase(handle, timeout=args.connect_timeout)
    except Exception as e:
        print(f"Initial Lua connect failed: {e}")
        cleanup_handle(handle)
        sys.exit(1)

    completed = 0
    try:
        for i, cfg in enumerate(configs, 1):
            label = cfg.get("_llm_alias") or cfg["llm_model"]
            print(f"\n{'=' * 60}")
            print(f"  RUN {i}/{len(configs)}  —  {label}")
            print(f"{'=' * 60}")
            # Only the FIRST run auto-opens a browser tab. Runs 2..N
            # register silently — user follows along on the index page
            # (http://localhost:3420/) which lists currently-active runs.
            run_single_loop(
                handle, cfg, turns=args.turns, snapshot=args.snapshot,
                open_browser=(i == 1),
            )
            completed += 1
    except KeyboardInterrupt:
        print("\n\nInterrupted — aborting remaining runs.")
    finally:
        cleanup_handle(handle)

    print(f"\n=== Done. {completed}/{len(configs)} runs completed. mGBA shut down. ===")


if __name__ == "__main__":
    main()
