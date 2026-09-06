"""Audit persisted real-game samples and produce an inspectable comparison."""
import argparse
import json
from pathlib import Path

from src.agent.append_agent import atomic_json
from src.app.trace_build import build_run_trace


def summarize(manifests, output):
    rows = []
    for manifest_path in manifests:
        manifest = json.loads(Path(manifest_path).read_text())
        for item in manifest["runs"]:
            if not item.get("final_run_dir"):
                continue
            directory = Path(item["final_run_dir"])
            events = [json.loads(s) for s in (directory / "events.jsonl").read_text().splitlines()]
            summary = json.loads((directory / "run_summary.json").read_text())
            trace = build_run_trace(directory)
            atomic_json(directory / "trace.json", trace)
            requests = {}
            for e in events:
                if e["type"] in ("llm_request_usage", "llm_request_error"):
                    requests[e["request_id"]] = {**requests.get(e["request_id"], {}), **e}
            usage = list(requests.values())
            settled = sorted({e["turn"] for e in events if e["type"] == "screen_settled"})
            compactions = [e.get("after_turn") for e in events if e["type"] == "compaction_complete"]
            pids = [p.get("pid") for p in item["phases"]]
            emulators = [p.get("emulator_pid") for p in item["phases"]]
            sessions = set()
            for request in (directory / "conversation").glob("*-request.json"):
                sessions.add(json.loads(request.read_text()).get("session_id"))
            turn8 = [e for e in usage if e.get("turn") == 8 and e.get("phase") == "gameplay"]
            gates = summary.get("referee", {}).get("gates", [])
            reached = [{k:g.get(k) for k in ("id", "name", "turn")} for g in gates if g.get("status") in ("done", "auto")]
            row = {"model": item["model"], "profile": item.get("profile"), "status": item["status"],
                   "run_dir": str(directory), "manifest": str(manifest_path),
                   "settled_turns": settled, "compactions_after": compactions,
                   "process_restart": len(pids) == 2 and len(set(pids)) == 2,
                   "emulator_restart": len(emulators) == 2 and None not in emulators and len(set(emulators)) == 2,
                   "stable_session": len(sessions) == 1 and None not in sessions,
                   "turn8_replay": [e.get("continuity") for e in turn8], "cache": trace["cache"],
                   "cost_usd": summary.get("cost", {}).get("total_usd"),
                   "duration_seconds": summary.get("session", {}).get("duration_seconds"),
                   "reached": reached, "furthest": summary.get("referee", {}).get("furthest"),
                   "errors": [{k:e.get(k) for k in ("turn", "phase", "attempt", "error")} for e in usage if e.get("error")],
                   "retry_count": sum(e.get("attempt", 1) > 1 for e in usage),
                   "reasoning_replay_intact": all(e.get("continuity", {}).get("local_replay") == "intact" for e in usage),
                   "url": "http://localhost:3420/history/" + directory.name}
            row["checks_passed"] = (settled == list(range(1, 11)) and compactions == [5]
                                    and row["process_restart"] and row["emulator_restart"]
                                    and row["stable_session"] and bool(turn8) and row["reasoning_replay_intact"])
            rows.append(row)
    latest = {}
    for row in rows:
        latest[row["profile"] or row["model"]] = row
    result = {"all_attempts": rows, "latest": latest,
              "total_reported_cost_usd": sum(r["cost_usd"] or 0 for r in rows)}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "results.json", result)
    lines = ["# Ten-turn real Pokémon samples", "", "Same canonical FireRed bedroom save, compaction after turn 5, real agent-process and emulator restart after turn 7. Provider defaults and production action execution; no mock screenshots or emulator actions.", "",
             "| Model/profile | Settled turns | Restart + continuity | Furthest checkpoint | Cached input | Total cost | Trace |",
             "|---|---:|---|---|---:|---:|---|"]
    for name, row in latest.items():
        fraction = (row["cache"] or {}).get("input_read_fraction")
        cache = "Unknown" if fraction is None else f"{fraction*100:.1f}%"
        furthest = next((g["name"] for g in row["reached"] if g["id"] == row["furthest"]), row["furthest"] or "None")
        status = "Pass" if row["checks_passed"] else "Incomplete / inspect"
        cost = "Unknown" if row["cost_usd"] is None else f"${row['cost_usd']:.4f}"
        lines.append(f"| {name} | {len(row['settled_turns'])} | {status} | {furthest} | {cache} | {cost} | [Open]({row['url']}) |")
    lines += ["", "Costs include OCR and retries; continuation totals already include the first seven turns and are counted once. Superseded failed attempts are preserved in results.json and included in total reported campaign cost. Cache fractions describe observed requests, including compaction, not a controlled cache-performance benchmark. These short runs do not establish a reliable ranking.", "", "Local reasoning replay checks show what was sent. They do not establish internal use by the serving provider. Gemma guidance intentionally omits prior raw thoughts; replay preserves them until compaction.", "", f"Total reported spend across retained attempts: ${result['total_reported_cost_usd']:.6f}."]
    (output / "results.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k:{"turns":len(v["settled_turns"]),"checks_passed":v["checks_passed"],"errors":v["errors"]} for k,v in latest.items()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="+")
    parser.add_argument("--output", default="artifacts/ten-turn-samples")
    args = parser.parse_args()
    summarize(args.manifest, args.output)
