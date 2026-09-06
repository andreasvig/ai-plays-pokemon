"""Summarize an old-vs-new system comparison campaign (test_scripts/run_system_comparison.py).

Speed, price and progress per arm, from events.jsonl and run_summary.json of each run:
  python -m test_scripts.summarize_system_comparison [--campaign-dir local/system-comparison] [--out artifacts/system-comparison]
"""
import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_events(run_dir):
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line.strip()]


def per_turn(events):
    """Per-turn wall clock and LLM seconds, identical derivation for both arms."""
    starts = {e["turn"]: e["timestamp"] for e in events if e["type"] == "turn_start"}
    user_msg = {e["turn"]: e["timestamp"] for e in events if e["type"] == "turn_user_message"}
    usage_ts, cost, req_tokens, resp_tokens, reasoning = {}, Counter(), {}, {}, {}
    for e in events:
        if e["type"] == "turn_usage" and e.get("phase", "gameplay") == "gameplay":
            usage_ts[e["turn"]] = max(usage_ts.get(e["turn"], 0), e["timestamp"])
            if e.get("cost_usd") is not None:
                cost[e["turn"]] += e["cost_usd"]
            if e.get("request_tokens") is not None:
                req_tokens[e["turn"]] = e["request_tokens"]
                resp_tokens[e["turn"]] = e.get("response_tokens")
                reasoning[e["turn"]] = e.get("reasoning_tokens")
    end = max((e["timestamp"] for e in events if e["type"] == "run_end"), default=None)
    ordered = sorted(starts)
    rows = []
    for i, t in enumerate(ordered):
        nxt = starts[ordered[i + 1]] if i + 1 < len(ordered) else end
        rows.append({"turn": t,
                     "wall_s": round(nxt - starts[t], 1) if nxt else None,
                     "llm_s": round(usage_ts[t] - user_msg[t], 1) if t in usage_ts and t in user_msg else None,
                     "cost_usd": round(cost[t], 6) if t in cost else None,
                     "request_tokens": req_tokens.get(t), "response_tokens": resp_tokens.get(t),
                     "reasoning_tokens": reasoning.get(t)})
    return rows


def compactions(events):
    starts = [e for e in events if e["type"] == "compaction_start"]
    done = [e for e in events if e["type"] == "compaction_complete"]
    usage = [e for e in events if e["type"] in ("turn_usage", "llm_request_usage") and e.get("phase") == "compaction"]
    seen, cost, latency = set(), 0.0, 0.0
    for e in usage:
        key = e.get("request_id") or e["id"]
        if key in seen:
            continue
        seen.add(key)
        cost += e.get("cost_usd") or 0
        latency += e.get("latency_s") or 0
    wall = sum(d["timestamp"] - s["timestamp"] for s, d in zip(starts, done))
    return {"count": len(done), "attempted": len(starts), "cost_usd": round(cost, 6),
            "llm_s": round(latency, 1), "wall_s": round(wall, 1)}


def cache_share(events):
    """Cached-input share across gameplay requests, when the transport reports it."""
    prompt, cached, reported = 0, 0, 0
    for e in events:
        if e["type"] == "llm_request_usage" and e.get("request_tokens"):
            prompt += e["request_tokens"]
            if e.get("cached_tokens") is not None:
                cached += e["cached_tokens"]
                reported += 1
        elif e["type"] == "turn_usage" and "request_id" not in e and e.get("request_tokens"):
            prompt += e["request_tokens"]  # legacy transport: cached tokens not surfaced
    if not reported:
        return {"reported": False, "fraction": None}
    return {"reported": True, "fraction": round(cached / prompt, 3) if prompt else None}


def summarize_run(row):
    run_dir = Path(row["run_dir"]) if row.get("run_dir") else None
    if not run_dir or not (run_dir / "run_summary.json").exists():
        return {**row, "error": "no run_summary"}
    summary = json.loads((run_dir / "run_summary.json").read_text())
    events = load_events(run_dir)
    turns = per_turn(events)
    walls = [r["wall_s"] for r in turns if r["wall_s"] is not None]
    llms = [r["llm_s"] for r in turns if r["llm_s"] is not None]
    costs = [r["cost_usd"] for r in turns if r["cost_usd"] is not None]
    types = Counter(e["type"] for e in events)
    referee = summary.get("referee") or {}
    reached = {k: v for k, v in (referee.get("checkpoints") or {}).items() if v is not None}
    furthest = max(reached.items(), key=lambda kv: kv[1], default=(None, None))
    grades = [e.get("last_turn_succeeded") for e in events if e["type"] == "turn_explanation"]
    graded = [g for g in grades if g is not None]
    return {**row,
            "turns_completed": len(turns),
            "total_cost_usd": (summary.get("cost") or {}).get("total_usd"),
            "player_llm_cost_usd": round(sum(costs), 6),
            "cost_per_turn_mean": round(statistics.fmean(costs), 6) if costs else None,
            "cost_first5_mean": round(statistics.fmean(costs[:5]), 6) if costs else None,
            "cost_last5_mean": round(statistics.fmean(costs[-5:]), 6) if costs else None,
            "wall_s_mean": round(statistics.fmean(walls), 1) if walls else None,
            "wall_s_median": round(statistics.median(walls), 1) if walls else None,
            "llm_s_mean": round(statistics.fmean(llms), 1) if llms else None,
            "llm_s_median": round(statistics.median(llms), 1) if llms else None,
            "run_duration_s": (summary.get("session") or {}).get("duration_seconds"),
            "request_tokens_first": turns[0]["request_tokens"] if turns else None,
            "request_tokens_last": turns[-1]["request_tokens"] if turns else None,
            "compaction": compactions(events),
            "cache": cache_share(events),
            "checkpoints": reached, "furthest": furthest[0], "furthest_turn": furthest[1],
            "self_grade_true_rate": round(sum(1 for g in graded if g is True) / len(graded), 2) if graded else None,
            "retries": {"output_retry": types.get("output_retry", 0), "llm_request_error": types.get("llm_request_error", 0),
                        "action_error": types.get("action_error", 0)},
            "turns": turns}


def fmt(v, spec=""):
    return "—" if v is None else format(v, spec)


def render(manifest, runs):
    lines = [f"# Old vs new system — {manifest['turns']}-turn comparison",
             "",
             f"Same canonical FireRed bedroom save (sha256 {manifest['snapshot_sha256'][:12]}…), {manifest['turns']} turns per arm, "
             f"compaction every {manifest['compaction_every_n_turns']} turns on the new arm, endpoints pinned on both arms. "
             "Old arm = `config-4.0` (sliding window of 10 turns, 1 historic screenshot); new arm = `config-append` (append-and-compact). One run per arm.",
             "",
             "| Model | Arm | Turns | Furthest checkpoint (turn) | Total cost | LLM cost/turn (first 5 → last 5) | Wall s/turn (median) | LLM s/turn (median) | Compaction | Cached input | Retries |",
             "|---|---|---:|---|---:|---|---:|---:|---|---:|---|"]
    for r in runs:
        if r.get("error"):
            lines.append(f"| {r['model_key']} | {r['arm']} | — | {r['error']} | | | | | | | |")
            continue
        comp = r["compaction"]
        comp_s = f"{comp['count']}× (${comp['cost_usd']:.4f}, {comp['llm_s']}s)" if comp["attempted"] else "—"
        cache = f"{r['cache']['fraction']:.0%}" if r["cache"]["reported"] and r["cache"]["fraction"] is not None else "not reported"
        retries = ", ".join(f"{k} {v}" for k, v in r["retries"].items() if v) or "none"
        furthest = f"{r['furthest']} ({r['furthest_turn']})" if r["furthest"] else "none"
        lines.append(f"| {r['model_key']} | {r['arm']} | {r['turns_completed']} | {furthest} | ${fmt(r['total_cost_usd'], '.4f')} | "
                     f"${fmt(r['cost_first5_mean'], '.4f')} → ${fmt(r['cost_last5_mean'], '.4f')} | {fmt(r['wall_s_median'])} | {fmt(r['llm_s_median'])} | "
                     f"{comp_s} | {cache} | {retries} |")
    lines += ["", "## Checkpoint turns", "", "| Model | Arm | " + " | ".join(CHECKPOINTS) + " |", "|---|---|" + "---:|" * len(CHECKPOINTS)]
    for r in runs:
        if r.get("error"):
            continue
        lines.append(f"| {r['model_key']} | {r['arm']} | " + " | ".join(fmt(r["checkpoints"].get(c)) for c in CHECKPOINTS) + " |")
    lines += ["", "## Per-turn cost and speed", ""]
    for r in runs:
        if r.get("error"):
            continue
        lines += [f"### {r['model_key']} — {r['arm']} (`{Path(r['run_dir']).name}`)", "",
                  "| Turn | Wall s | LLM s | Cost | Prompt tokens | Output tokens | Reasoning tokens |", "|---:|---:|---:|---:|---:|---:|---:|"]
        for t in r["turns"]:
            lines.append(f"| {t['turn']} | {fmt(t['wall_s'])} | {fmt(t['llm_s'])} | ${fmt(t['cost_usd'], '.4f')} | {fmt(t['request_tokens'])} | {fmt(t['response_tokens'])} | {fmt(t['reasoning_tokens'])} |")
        lines.append("")
    lines += ["Wall s/turn is `turn_start` to the next `turn_start` (emulator settle included, shared by both arms); LLM s/turn is `turn_user_message` to the turn's last gameplay usage event, retries included. "
              "Compaction requests are listed separately and excluded from LLM s/turn. Cached input is the share of gameplay prompt tokens the endpoint reported as cached; the legacy transport does not surface it. "
              "Total cost is the run's all-in figure (Player LLM + OCR cleanup + retries)."]
    return "\n".join(lines) + "\n"


CHECKPOINTS = ["left_bedroom", "left_house", "oaks_lab_entered", "starter_chosen", "rival1_done", "route1_reached",
               "viridian_reached", "parcel_delivered", "pokedex_received", "viridian_forest_reached", "pewter_reached", "brock_defeated"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", default=str(ROOT / "local/system-comparison"))
    parser.add_argument("--out", default=str(ROOT / "artifacts/system-comparison"))
    args = parser.parse_args()
    manifest = json.loads((Path(args.campaign_dir) / "manifest.json").read_text())
    runs = [summarize_run(row) for row in manifest["runs"] if row.get("status") != "superseded"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"manifest": {k: v for k, v in manifest.items() if k != "runs"}, "runs": runs}, indent=2))
    (out / "results.md").write_text(render(manifest, runs))
    print(f"wrote {out / 'results.md'}")


if __name__ == "__main__":
    main()
