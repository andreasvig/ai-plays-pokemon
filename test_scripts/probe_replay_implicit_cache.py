"""No cache_control at all: send turn 10 three times, then turn 11 once. If Gemini implicit caching
works through OpenRouter, the 2nd/3rd sends and the extension should report cached tokens."""
import asyncio, json, sys, copy, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_replay_cache_placements import rehydrate, strip, RUN, REQS, TAG
from src.agent.append_agent import OpenRouterTransport, ProviderRequestError
from src.config import load_config
async def main():
    wait = int(sys.argv[2]); print(f"waiting {wait}s for earlier cache entries to age out", flush=True); await asyncio.sleep(wait)
    key = load_config("configs/config-5.0.yaml", llm_alias="google/gemini-3.8-flash")["openrouter_api_key"]
    t = OpenRouterTransport(); rows = []
    bodies = {n: rehydrate(json.load(open(RUN / f"conversation/{h}-request.json"))) for n, h in REQS.items()}
    for label, n in (("t10 #1", 10), ("t10 #2", 10), ("t10 #3", 10), ("t11 (extends)", 11)):
        body = copy.deepcopy(bodies[n]); body["messages"] = strip(body["messages"]); body["max_tokens"] = 60; body.pop("session_id", None)
        body["stream"] = False; body["provider"] = {"only": [TAG], "allow_fallbacks": False}
        assert "cache_control" not in json.dumps(body)
        raw = await t(json.dumps(body).encode(), key, 180, lambda c: None)
        u = raw.get("usage") or {}; d = u.get("prompt_tokens_details") or {}
        r = {"label": label, "prompt": u.get("prompt_tokens"), "cached": d.get("cached_tokens"), "write": d.get("cache_write_tokens"),
             "prompt_cost": (u.get("cost_details") or {}).get("upstream_inference_prompt_cost"), "cost": u.get("cost"), "at": time.time()}
        rows.append(r); print(f"{label:14} {r['prompt']} in, cached {r['cached']}, write {r['write']}, prompt ${(r['prompt_cost'] or 0):.4f}", flush=True)
        await asyncio.sleep(3)
    print(f"spend ${sum(r['cost'] or 0 for r in rows):.4f}")
    Path("artifacts/provider-compatibility/gemini-implicit-cache-probe__google--gemini-3.8-flash.json").write_text(json.dumps({"run": str(RUN), "tag": TAG, "placement": "none", "waited_s": wait, "results": rows}, indent=1))
asyncio.run(main())
