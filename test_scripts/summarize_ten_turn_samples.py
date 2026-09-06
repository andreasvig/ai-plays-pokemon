"""Audit persisted real-game samples and produce an inspectable comparison."""
import argparse
import json
from pathlib import Path

from src.agent.append_agent import atomic_json
from src.app.trace_build import build_run_trace


def replay_billed_verdict(usage):
    """Did the endpoint tokenize the reasoning we replayed? Inferred from billing.

    Within a segment, prompt growth between consecutive gameplay requests should
    track the previous request's reasoning tokens 1:1 when replay is consumed.
    Only a near-perfect correlation counts: the visible reasoning field in the
    action JSON also grows with thinking length, which alone gives 0.3–0.8.
    """
    import statistics
    game = sorted((e for e in usage if e.get("phase") == "gameplay" and not e.get("error") and e.get("request_tokens")),
                  key=lambda e: (e.get("turn"), e.get("attempt", 1)))
    policy = next((e.get("continuity", {}).get("reasoning_policy") for e in game), None)
    if policy == "omit_prior":
        return {"verdict": "not_replayed_by_policy"}
    growth, reasoning = [], []
    for a, b in zip(game, game[1:]):
        if a.get("segment") == b.get("segment") and a.get("turn") != b.get("turn"):
            growth.append(b["request_tokens"] - a["request_tokens"])
            reasoning.append(a.get("reasoning_tokens") or 0)
    if len(growth) < 3 or statistics.pstdev(reasoning) == 0 or statistics.pstdev(growth) == 0:
        return {"verdict": "insufficient_data", "pairs": len(growth)}
    corr = statistics.correlation(growth, reasoning)
    return {"verdict": "consumed" if corr >= 0.95 else "not_consumed", "correlation": round(corr, 3), "pairs": len(growth)}


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
            replay_billed = replay_billed_verdict(usage)
            gates = summary.get("referee", {}).get("gates", [])
            reached = [{k:g.get(k) for k in ("id", "name", "turn")} for g in gates if g.get("status") in ("done", "auto")]
            row = {"model": item["model"], "profile": item.get("profile"), "repeat": item.get("repeat"), "status": item["status"],
                   "run_dir": str(directory), "manifest": str(manifest_path),
                   "settled_turns": settled, "compactions_after": compactions,
                   "process_restart": len(pids) == 2 and len(set(pids)) == 2,
                   "emulator_restart": len(emulators) == 2 and None not in emulators and len(set(emulators)) == 2,
                   "stable_session": len(sessions) == 1 and None not in sessions,
                   "turn8_replay": [e.get("continuity") for e in turn8], "cache": trace["cache"],
                   "replay_billed": replay_billed,
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
        label = row["profile"] or row["model"]
        if row.get("repeat") and any(r is not row and (r["profile"] or r["model"]) == label for r in rows):
            label = f"{label} #r{row['repeat']}"
        latest[label] = row
    result = {"all_attempts": rows, "latest": latest,
              "total_reported_cost_usd": sum(r["cost_usd"] or 0 for r in rows)}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "results.json", result)
    lines = ["# Ten-turn real Pokémon samples", "", "Same canonical FireRed bedroom save, compaction after turn 5, real agent-process and emulator restart after turn 7. Provider defaults and production action execution; no mock screenshots or emulator actions.", "",
             "| Model/profile | Settled turns | Restart + continuity | Furthest checkpoint | Replayed reasoning billed | Cached input (reported) | Cached input (implied by billing) | Cache economics | Total cost | Trace |",
             "|---|---:|---|---|---|---:|---:|---|---:|---|"]
    for name, row in latest.items():
        fraction = (row["cache"] or {}).get("input_read_fraction")
        cache = "Unknown" if fraction is None else f"{fraction*100:.1f}%"
        implied = (row["cache"] or {}).get("implied_read_fraction")
        implied_text = "No pricing snapshot" if implied is None else f"{implied*100:.1f}%"
        econ = (row["cache"] or {}).get("economics") or {}
        econ_text = {"no_cache_offered": "No cache offered", "no_discount_on_hits": "Hits not discounted",
                     "priced_no_hits": f"{round((econ.get('discount_fraction') or 0)*100)}% discount unused",
                     "saving": f"${econ.get('saved_usd') or 0:.4f} saved"}.get(econ.get("verdict"), "Unknown")
        furthest = next((g["name"] for g in row["reached"] if g["id"] == row["furthest"]), row["furthest"] or "None")
        status = "Pass" if row["checks_passed"] else "Incomplete / inspect"
        cost = "Unknown" if row["cost_usd"] is None else f"${row['cost_usd']:.4f}"
        rb = row.get("replay_billed") or {}
        rb_text = {"consumed": f"Yes (corr {rb.get('correlation')})", "not_consumed": f"**No** (corr {rb.get('correlation')})",
                   "not_replayed_by_policy": "Not replayed (policy)", "insufficient_data": "Insufficient data"}.get(rb.get("verdict"), "Unknown")
        lines.append(f"| {name} | {len(row['settled_turns'])} | {status} | {furthest} | {rb_text} | {cache} | {implied_text} | {econ_text} | {cost} | [Open]({row['url']}) |")
    lines += ["", "Costs include OCR and retries; continuation totals already include the first seven turns and are counted once. Superseded failed attempts are preserved in results.json and included in total reported campaign cost. Cache fractions describe observed requests, including compaction, not a controlled cache-performance benchmark. The implied column backs the cache discount out of the billed prompt cost against the endpoint's list prices, so it stays meaningful when a provider (Google AI Studio) reports no cache figures. These short runs do not establish a reliable ranking.", "", "Local reasoning replay checks show what was sent. They do not establish internal use by the serving provider. Gemma guidance intentionally omits prior raw thoughts; replay preserves them until compaction.", "", f"Total reported spend across retained attempts: ${result['total_reported_cost_usd']:.6f}."]
    (output / "results.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k:{"turns":len(v["settled_turns"]),"checks_passed":v["checks_passed"],"errors":v["errors"]} for k,v in latest.items()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="+")
    parser.add_argument("--output", default="artifacts/ten-turn-samples")
    args = parser.parse_args()
    summarize(args.manifest, args.output)
