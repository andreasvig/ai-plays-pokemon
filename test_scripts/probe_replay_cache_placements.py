"""Replay two consecutive real requests (turn 10 -> turn 11) from a config-5.0 Gemini run against
google-ai-studio with different cache_control placements. Question: does the accumulated TEXT history
cache when the marker sits late in the conversation, and are images the only leak?"""
import asyncio, json, sys, copy
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from src.agent.append_agent import OpenRouterTransport, ProviderRequestError
from src.config import load_config

RUN = Path("local/runs/2026-09-07_12-20-20_config-5.0__gemini-3-8-flash-medium")
REQS = {10: "a185dcbe44764639a102b5d5d1d7e67a", 11: "00e08228eb2a4695bd6db7c4e0a8c9f1"}
TAG = "google-ai-studio"

def rehydrate(v):
    if isinstance(v, dict) and set(v) == {"$image_asset"}:
        return (RUN / "conversation/assets" / v["$image_asset"]).read_text()
    if isinstance(v, dict): return {k: rehydrate(x) for k, x in v.items()}
    if isinstance(v, list): return [rehydrate(x) for x in v]
    return v

def strip(msgs):
    for m in msgs:
        if isinstance(m["content"], list):
            for b in m["content"]: b.pop("cache_control", None)
    return msgs

def mark_block(block): block["cache_control"] = {"type": "ephemeral"}
def as_list(m):
    if isinstance(m["content"], str): m["content"] = [{"type": "text", "text": m["content"]}]
    return m["content"]

def place(msgs, placement):
    msgs = strip(copy.deepcopy(msgs))
    if placement == "system": mark_block(as_list(msgs[0])[-1])
    elif placement == "last_text":
        blocks = as_list(msgs[-1]); mark_block([b for b in blocks if b["type"] == "text"][-1])
    elif placement == "last_any": mark_block(as_list(msgs[-1])[-1])
    elif placement == "last_assistant":
        i = max(j for j, m in enumerate(msgs) if m["role"] in ("assistant", "tool"))
        mark_block(as_list(msgs[i])[-1])
    elif placement == "system+last_assistant":
        mark_block(as_list(msgs[0])[-1])
        i = max(j for j, m in enumerate(msgs) if m["role"] in ("assistant", "tool"))
        mark_block(as_list(msgs[i])[-1])
    return msgs

async def main():
    key = load_config("configs/config-5.0.yaml", llm_alias="google/gemini-3.8-flash")["openrouter_api_key"]
    t = OpenRouterTransport()
    bodies = {n: rehydrate(json.load(open(RUN / f"conversation/{h}-request.json"))) for n, h in REQS.items()}
    n_tokens = {}
    out = {}
    for placement in ["system", "last_text", "last_any", "last_assistant", "system+last_assistant"]:
        rows = []
        for n in (10, 11):
            body = copy.deepcopy(bodies[n]); body["messages"] = place(body["messages"], placement)
            body["max_tokens"] = 60; body.pop("session_id", None); body["stream"] = False
            body["provider"] = {"only": [TAG], "allow_fallbacks": False}
            try:
                raw = await t(json.dumps(body).encode(), key, 180, lambda c: None)
                u = raw.get("usage") or {}; d = u.get("prompt_tokens_details") or {}
                rows.append({"turn": n, "prompt": u.get("prompt_tokens"), "cached": d.get("cached_tokens"), "write": d.get("cache_write_tokens"),
                             "prompt_cost": (u.get("cost_details") or {}).get("upstream_inference_prompt_cost"), "cost": u.get("cost"), "provider": raw.get("provider")})
            except ProviderRequestError as e:
                rows.append({"turn": n, "error": f"HTTP {e.status}: {json.dumps(e.response_body)[:300]}"})
            await asyncio.sleep(2)
        out[placement] = rows
        print(placement.ljust(24), " | ".join(f"t{r['turn']}: {r.get('prompt')} in, cached {r.get('cached')}, write {r.get('write')}, prompt ${(r.get('prompt_cost') or 0):.4f}" if "error" not in r else f"t{r['turn']}: {r['error']}" for r in rows), flush=True)
    spend = sum((r.get("cost") or 0) for rows in out.values() for r in rows)
    print(f"spend ${spend:.4f}")
    Path("artifacts/provider-compatibility/gemini-history-cache-probe__google--gemini-3.8-flash.json").write_text(json.dumps({"run": str(RUN), "requests": REQS, "tag": TAG, "results": out, "spend_usd": spend}, indent=1))
if __name__ == "__main__": asyncio.run(main())
