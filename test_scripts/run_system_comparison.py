"""Old vs new agent system: 25 turns per arm from the canonical bedroom save.

Plan: artifacts/system-comparison/plan.md (decisions 2026-09-06). Old arm = config-4.0
(self-directed sliding window, registry alias with a pinned endpoint); new arm =
config-append (append-and-compact, provider profile, compaction after turn 15).
One run per arm per model; cheapest model first so failures surface before Astra spends.

  python -m test_scripts.run_system_comparison                 # full campaign
  python -m test_scripts.run_system_comparison --only glm-5.3-flash --turns 2   # smoke
  python -m test_scripts.run_system_comparison --arm new --only gpt-6-astra
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "local/system-comparison"
TURNS = 25
COMPACT_EVERY = 15
# (key, registry alias for the old arm, raw OpenRouter id for the new arm, spend cap per run)
MODELS = [
    ("glm-5.3-flash", "glm-5.3-flash(high)", "z-ai/glm-5.3-flash", 1.0),
    ("gemini-3.8-flash", "gemini-3.8-flash(medium)", "google/gemini-3.8-flash", 2.0),
    ("gpt-6-astra", "gpt-6-astra(medium)", "openai/gpt-6-astra", 12.0),
]
ARMS = ("old", "new")


def atomic_json(path, payload):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def child(args):
    from src.cli.runner import (prepare_config, run_prepare_phase, run_connect_phase,
                                run_single_loop, cleanup_handle)
    config = prepare_config(args.config, args.alias)
    config["max_spend_usd"] = args.max_spend
    snapshot = ROOT / "configs/saves/pokebench-v1"
    handle = None
    pointer = Path(args.pointer)
    atomic_json(pointer, {"pid": os.getpid(), "status": "starting", "started_at": datetime.now().isoformat()})
    try:
        saves = Path(args.campaign_dir) / "emulator-saves"
        saves.mkdir(parents=True, exist_ok=True)
        handle = run_prepare_phase(config, saves)
        connect_with_retry(handle)

        def published(path):
            atomic_json(pointer, {"pid": os.getpid(), "emulator_pid": handle["mgba_proc"].pid,
                                  "status": "running", "run_dir": str(path)})
        run_dir = run_single_loop(handle, config, turns=args.turns, snapshot=str(snapshot),
                                  open_browser=False, on_run_dir=published)
        summary = json.loads((run_dir / "run_summary.json").read_text())
        atomic_json(pointer, {"pid": os.getpid(), "status": "finished", "run_dir": str(run_dir),
                              "finished_at": datetime.now().isoformat(), "summary": summary})
    finally:
        if handle:
            cleanup_handle(handle)


def connect_with_retry(handle, attempts=4, timeout=45):
    """The Lua connector is loaded into mGBA by AppleScript, best-effort; the smoke on
    2026-09-06 saw it miss on the second launch of a campaign. Re-issue the load and wait again."""
    from src.cli.runner import run_connect_phase, load_lua_script_in_mgba_for_pid
    for attempt in range(1, attempts + 1):
        try:
            run_connect_phase(handle, timeout=timeout)
            return
        except ConnectionError:
            if attempt == attempts:
                raise
            print(f"mGBA did not connect (attempt {attempt}); reloading the Lua connector", flush=True)
            load_lua_script_in_mgba_for_pid(handle["mgba_proc"].pid, handle["slot_cfg"]["lua_path"])


def write_configs(campaign, compact_every):
    common = {"runs_directory": str(ROOT / "local/runs"), "mode": "benchmark",
              "referee": {"checkpoints": str(ROOT / "configs/checkpoints-firered-firstbadge.yaml"), "enforce": False}}
    old = yaml.safe_load((ROOT / "configs/config-4.0.yaml").read_text())
    old.update(common)
    old["savepoints"].update(every_n_turns=TURNS, at_end=True, on_crash=True)
    (campaign / "config-old.yaml").write_text(yaml.safe_dump(old, sort_keys=False))
    new = yaml.safe_load((ROOT / "configs/config-append.yaml").read_text())
    new.update(common)
    new["savepoints"].update(every_n_turns=TURNS, at_end=True, on_crash=True)
    new["player_agent"]["compaction"]["every_n_turns"] = compact_every
    new["player_agent"]["provider_profiles"]["path"] = str(ROOT / "configs/provider-profiles.yaml")
    (campaign / "config-new.yaml").write_text(yaml.safe_dump(new, sort_keys=False))


def campaign(args):
    campaign_dir = Path(args.campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    write_configs(campaign_dir, args.compact_every)
    save = ROOT / "configs/saves/pokebench-v1/emulator.state"
    manifest_path = campaign_dir / "manifest.json"
    if manifest_path.exists():
        # A rerun (--only/--arm) extends the campaign; earlier rows for the same arm are kept as superseded.
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {"started_at": datetime.now().isoformat(), "snapshot": str(save.parent),
                    "snapshot_sha256": hashlib.sha256(save.read_bytes()).hexdigest(),
                    "turns": args.turns, "compaction_every_n_turns": args.compact_every,
                    "old_config": "configs/config-4.0.yaml", "new_config": "configs/config-append.yaml", "runs": []}
    atomic_json(manifest_path, manifest)
    for key, alias, model, max_spend in MODELS:
        if args.only and key not in args.only:
            continue
        for arm in ARMS:
            if args.arm and arm != args.arm:
                continue
            name = f"{key}--{arm}"
            for prior in manifest["runs"]:
                if prior["model_key"] == key and prior["arm"] == arm and prior.get("status") != "superseded":
                    prior["status"] = "superseded"
            row = {"model_key": key, "arm": arm, "alias": alias if arm == "old" else model,
                   "config": str(campaign_dir / f"config-{arm}.yaml"), "status": "running",
                   "started_at": datetime.now().isoformat()}
            manifest["runs"].append(row)
            atomic_json(manifest_path, manifest)
            print(f"START {name}", flush=True)
            pointer = campaign_dir / f"{name}.json"
            log = campaign_dir / f"{name}.log"
            command = [sys.executable, "-u", "-m", "test_scripts.run_system_comparison", "--child",
                       "--campaign-dir", str(campaign_dir), "--config", row["config"],
                       "--alias", row["alias"], "--turns", str(args.turns),
                       "--max-spend", str(max_spend), "--pointer", str(pointer)]
            with log.open("w") as stream:
                code = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).wait()
            result = json.loads(pointer.read_text()) if pointer.exists() else {}
            summary = result.get("summary", {})
            turns_done = (summary.get("conversation", {}).get("completed_game_turns")
                          or summary.get("session", {}).get("player_turns"))
            row.update(returncode=code, log=str(log), run_dir=result.get("run_dir"),
                       completed_turns=turns_done, cost_usd=(summary.get("cost") or {}).get("total_usd"),
                       finished_at=datetime.now().isoformat(),
                       status="finished" if code == 0 and result.get("status") == "finished" else "failed")
            atomic_json(manifest_path, manifest)
            print(f"END {name} rc={code} turns={turns_done} run={result.get('run_dir')}", flush=True)
    manifest["finished_at"] = datetime.now().isoformat()
    atomic_json(manifest_path, manifest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--campaign-dir", default=str(CAMPAIGN))
    parser.add_argument("--config")
    parser.add_argument("--alias")
    parser.add_argument("--turns", type=int, default=TURNS)
    parser.add_argument("--compact-every", type=int, default=COMPACT_EVERY)
    parser.add_argument("--max-spend", type=float, default=2.0)
    parser.add_argument("--pointer")
    parser.add_argument("--only", action="append", help="model key(s) to run")
    parser.add_argument("--arm", choices=ARMS)
    args = parser.parse_args()
    if args.child:
        sys.path.insert(0, str(ROOT))
        child(args)
    else:
        campaign(args)


if __name__ == "__main__":
    main()
