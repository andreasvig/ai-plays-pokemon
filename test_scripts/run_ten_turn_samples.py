"""Real mGBA compatibility campaign: seven turns, process restart, three turns.

Run only when no control center owns the real emulator. Uses the production
runner and sealed savepoints; no emulator, model, or conversation mocks.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml

from src.agent.append_agent import atomic_json

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "local/ten-turn-samples"
MATRIX = [
    ("google/gemma-4-31b-it", "gemma-guidance"),
    ("google/gemma-4-31b-it", "gemma-replay"),
    ("google/gemini-3.8-flash", None),
    ("anthropic/claude-opus-5", None),
    ("anthropic/claude-fable-5.1", None),
    ("qwen/qwen3.8-flash", None),
    ("z-ai/glm-5.3-flash", None),
    ("deepseek/deepseek-v4-flash-vision-exp", None),
    ("x-ai/grok-4.6", None),
    ("moonshotai/kimi-k3", None),
]


def child(args):
    from src.cli.runner import (prepare_config, continue_from_run, run_prepare_phase,
                                run_connect_phase, run_single_loop, cleanup_handle)
    if args.source:
        config, snapshot = continue_from_run(args.source)
    else:
        config = prepare_config(str(CAMPAIGN / "config-append-sample.yaml"), args.model,
                                provider_profile=args.profile)
        snapshot = ROOT / "configs/saves/pokebench-v1"
    handle = None
    pointer = Path(args.pointer)
    atomic_json(pointer, {"pid": os.getpid(), "status": "starting", "started_at": datetime.now().isoformat()})
    try:
        saves = CAMPAIGN / "emulator-saves"
        saves.mkdir(exist_ok=True)
        handle = run_prepare_phase(config, saves)
        run_connect_phase(handle, timeout=60)
        def published(path):
            atomic_json(pointer, {"pid": os.getpid(), "emulator_pid": handle["mgba_proc"].pid,
                                  "status": "running", "run_dir": str(path)})
        run_dir = run_single_loop(handle, config, turns=args.turns, snapshot=str(snapshot),
                                  open_browser=False, on_run_dir=published)
        summary = json.loads((run_dir / "run_summary.json").read_text())
        atomic_json(pointer, {"pid": os.getpid(), "emulator_pid": handle["mgba_proc"].pid,
                              "status": "finished", "run_dir": str(run_dir), "summary": summary})
    finally:
        if handle:
            cleanup_handle(handle)


def campaign(only=None):
    CAMPAIGN.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load((ROOT / "configs/config-append.yaml").read_text())
    config["player_agent"]["compaction"]["every_n_turns"] = 5
    config["player_agent"]["provider_profiles"]["path"] = str(ROOT / "configs/provider-profiles.yaml")
    config["savepoints"].update(every_n_turns=7, at_end=True, on_crash=True)
    config["runs_directory"] = str(ROOT / "local/runs")
    config["mode"] = "benchmark"
    config["max_spend_usd"] = 5
    config["referee"] = {"checkpoints": str(ROOT / "configs/checkpoints-firered-firstbadge.yaml"), "enforce": False}
    (CAMPAIGN / "config-append-sample.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    save = ROOT / "configs/saves/pokebench-v1/emulator.state"
    manifest_path = CAMPAIGN / "manifest.json"
    manifest = {"started_at": datetime.now().isoformat(), "snapshot": str(save.parent),
                "snapshot_sha256": hashlib.sha256(save.read_bytes()).hexdigest(),
                "compaction_interval": 5, "restart_after": 7, "target_turns": 10,
                "excluded": {"meta/muse-spark-1.3": "OpenRouter account requires age attestation"}, "runs": []}
    atomic_json(manifest_path, manifest)
    for model, profile in MATRIX:
        name = profile or model.replace("/", "--")
        if only and name not in only:
            continue
        row = {"model": model, "profile": profile, "status": "running", "phases": []}
        manifest["runs"].append(row)
        atomic_json(manifest_path, manifest)
        print(f"START {name}", flush=True)
        source = None
        for stage, turns in (("first7", 7), ("resume3", 3)):
            pointer = CAMPAIGN / f"{name}-{stage}.json"
            log = CAMPAIGN / f"{name}-{stage}.log"
            command = [sys.executable, "-u", "-m", "test_scripts.run_ten_turn_samples", "--child",
                       "--campaign-dir", str(CAMPAIGN),
                       "--model", model, "--turns", str(turns), "--pointer", str(pointer)]
            if profile:
                command += ["--profile", profile]
            if source:
                command += ["--source", source]
            with log.open("w") as stream:
                process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
                code = process.wait()
            result = json.loads(pointer.read_text()) if pointer.exists() else {}
            row["phases"].append({"stage": stage, "returncode": code, "log": str(log), **result})
            source = result.get("run_dir")
            summary = result.get("summary", {})
            completed = summary.get("conversation", {}).get("completed_game_turns", 0)
            row.update(completed_turns=completed, final_run_dir=source,
                       cost_usd=summary.get("cost", {}).get("total_usd"),
                       progress=summary.get("referee", {}))
            atomic_json(manifest_path, manifest)
            print(f"{name} {stage}: {completed} completed turns; {summary.get('status', result.get('status'))}", flush=True)
            if code or completed != (7 if stage == "first7" else 10) or summary.get("status") == "crashed":
                row["status"] = "incomplete"
                break
            time.sleep(1)  # Let the terminated emulator and TCP listener release.
        else:
            row["status"] = "completed"
        atomic_json(manifest_path, manifest)
    manifest["ended_at"] = datetime.now().isoformat()
    atomic_json(manifest_path, manifest)
    print("CAMPAIGN FINISHED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--campaign-dir", default=str(CAMPAIGN))
    parser.add_argument("--only", action="append", help="Profile/model label for a separate rerun campaign")
    parser.add_argument("--model")
    parser.add_argument("--profile")
    parser.add_argument("--source")
    parser.add_argument("--pointer")
    parser.add_argument("--turns", type=int)
    args = parser.parse_args()
    CAMPAIGN = Path(args.campaign_dir).resolve()
    child(args) if args.child else campaign(args.only)
