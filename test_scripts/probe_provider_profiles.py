"""Small paid protocol probes using recorded screenshots, without driving an emulator.

Run from the repo: ./venv/bin/python -m test_scripts.probe_provider_profiles --image PATH
Artifacts contain complete model responses; inspect before sharing publicly.
"""
import argparse
import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path
import time

from PIL import Image
import yaml

from src.agent.append_agent import AppendAgent, OpenRouterTransport, SpendLimitReached, atomic_json, cache_totals
from src.agent.provider_profiles import PROFILE_PATH
from src.config import load_config
from src.core.logger import RunLogger
from src.app.trace_build import build_run_trace

ROOT = Path(__file__).resolve().parents[1]


def append_config_path():
    """The append harness config. Renamed config-append.yaml -> config-5.0.yaml when
    the append harness became the standard (2026-09-07); accept either so an older
    checkout still probes."""
    for name in ("configs/config-5.0.yaml", "configs/config-append.yaml"):
        candidate = ROOT / name
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("no append harness config under configs/")


async def main(args):
    catalog = yaml.safe_load(PROFILE_PATH.read_text())
    models = args.model or list(catalog["profiles"])
    image_path = Path(args.image)
    image = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    spent = 0.0
    snapshot = json.loads(Path("artifacts/provider-compatibility/snapshot-summary.json").read_text())
    for model in models:
        config = load_config(append_config_path(), llm_alias=model, provider_profile=args.provider_profile)
        if not config.get("openrouter_api_key"):
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        profile = config["_provider_profile"]
        config["compaction"].update(every_n_turns=2, max_output_tokens=args.max_output, max_retries=0)
        config["transport"].update(max_output_tokens=args.max_output, max_retries=0, timeout_seconds=args.timeout)
        config["runs_directory"] = str(output / "runs")
        config["run_name"] = "protocol-probe__" + model.replace("/", "--")
        if profile.get("name"):
            config["run_name"] += "__" + profile["name"]
        config["run_label"] = "Protocol probe · recorded screenshots · no emulator actions"
        safe_config = deepcopy(config)
        safe_config.pop("openrouter_api_key", None)
        logger = RunLogger(safe_config)
        usage = []
        def on_usage(event):
            nonlocal spent
            usage.append(deepcopy(event))
            if event.get("cost_usd") is not None:
                spent += event["cost_usd"]
        endpoint = next(e for m in snapshot["models"] if m["model"] == model
                        for e in m["endpoints"] if e["tag"] == profile["endpoint"])
        atomic_json(logger.run_dir / "endpoint-snapshot.json", endpoint)
        real_transport = OpenRouterTransport()
        async def transport(body, key, timeout, on_chunk):
            # Conservative preflight spend reserve, using bytes as an upper bound
            # for text tokens and an intentionally large image allowance.
            request = json.loads(body)
            text_request = deepcopy(request)
            images = 0
            for message in text_request["messages"]:
                if isinstance(message.get("content"), list):
                    for block in message["content"]:
                        if block.get("type") == "image_url":
                            block["image_url"] = {}
                            images += 1
            upper_input = len(json.dumps(text_request).encode()) + images * 20000
            price = endpoint["pricing"]
            input_price = max(float(price.get(k) or 0) for k in ("prompt", "input_cache_write"))
            reserve = upper_input * input_price + args.max_output * float(price["completion"])
            if spent + reserve > args.max_cost:
                raise SpendLimitReached("Probe cost cap would be exceeded by next request reserve")
            return await real_transport(body, key, timeout, on_chunk)
        agent = AppendAgent(config, logger.run_dir, logger.log_event, transport, on_usage)
        result = {"model": model, "profile": profile.get("name"), "endpoint": profile["endpoint"], "run_dir": str(logger.run_dir),
                  "protocol_passed": False, "completed_actions": 0, "compactions": 0, "error": None}
        started = time.monotonic()
        try:
            for turn in (1, 2, 3):
                logger.log_turn_start(turn)
                with Image.open(image_path) as png:
                    logger.log_screenshot(png, f"turn_{turn}")
                action = await agent.play(turn, "Recorded-screen protocol test: screenshot is unchanged; no emulator action was executed.", image)
                logger.log_turn_explanation(turn, action.model_dump())
                agent.commit_action(turn)  # Protocol boundary only, explicitly no emulator.
                result["completed_actions"] = turn
                result["compactions"] = agent.state["segment"] - 1
                if turn == 1:
                    packed = agent.export_checkpoint()
                    atomic_json(logger.run_dir / "probe-checkpoint.json", packed)
                    agent = AppendAgent(config, logger.run_dir, logger.log_event, transport, on_usage)
                    agent.restore(json.loads((logger.run_dir / "probe-checkpoint.json").read_text()), turn)
            result["protocol_passed"] = True
        except Exception as exc:
            result["error"] = str(exc)[:2500]
        result.update(requests=len(usage), cost_usd=sum(u.get("cost_usd") or 0 for u in usage),
                      cache=cache_totals(usage), latency_s=time.monotonic() - started,
                      replay=[{"phase": u["phase"], "segment": u["segment"], **u["continuity"]} for u in usage])
        atomic_json(logger.run_dir / "probe-result.json", result)
        logger.close()
        atomic_json(logger.run_dir / "trace.json", build_run_trace(logger.run_dir))
        results.append(result)
        atomic_json(output / "results.json", {"total_cost_usd": spent, "results": results})
        print(json.dumps({k: result[k] for k in ("model", "protocol_passed", "requests", "cost_usd", "error")}), flush=True)
        if any(u.get("cost_usd") is None for u in usage):
            print("Stopping: a request did not report cost.", flush=True)
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--model", action="append")
    parser.add_argument("--provider-profile")
    parser.add_argument("--output", default="local/provider-profile-probes")
    parser.add_argument("--max-output", type=int, default=4096)
    parser.add_argument("--max-cost", type=float, default=3.0)
    parser.add_argument("--timeout", type=int, default=90)
    asyncio.run(main(parser.parse_args()))
