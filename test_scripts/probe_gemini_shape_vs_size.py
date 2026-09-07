"""Gemini implicit caching through OpenRouter: is the miss a SIZE floor or the OpenAI-style
FINAL-TURN-IMAGE rule? Replays real consecutive requests (turn N -> N+1) from config-5.0 runs at
several prompt sizes, in two shapes: `live` (request ends with user[text, screenshot], as the
agent sends it) and `split` (each screenshot turn is followed by assistant "Observed." and a
text-only action prompt, as final_turn_text_only produces). A per-condition nonce at the start of
the system prompt was MEANT to keep conditions from hitting each other's cache entries. It does not:
OpenRouter sends the system message as Gemini `systemInstruction`, and implicit caching keys on the
`contents`, so a nonce there changes nothing (follow-up (b): nonce X 0 cached, nonce Y 24,316). Put a
nonce in the first USER message if you need isolation. Results 2026-09-07: no hit below ~16k prompt
tokens in either shape on either model; above it the live shape extends (27k, 43k) while the split
shape's extension missed both times (identical repeats hit). Keep Gemini on final_turn_text_only: false."""
import asyncio, json, sys, copy, uuid, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from src.agent.append_agent import OpenRouterTransport, ProviderRequestError, TURN_ACK
from src.config import load_config

TAG = "google-ai-studio"
RUNS = {
    "google/gemini-3.5-flash-lite": ("local/runs/2026-09-07_13-29-11_config-5.0__gemini-3-5-flash-lite-high", [(3, 4), (6, 7), (9, 10)]),
    "google/gemini-3.8-flash": ("local/runs/2026-09-07_12-20-20_config-5.0__gemini-3-8-flash-medium", [(5, 6), (10, 11), (15, 16)]),
}

def artifacts(run):
    out = {}
    for line in open(Path(run) / "events.jsonl"):
        e = json.loads(line)
        if e["type"] == "llm_request_usage" and e["phase"] == "gameplay":
            out[e["turn"]] = e["request_artifact"]
    return out

def rehydrate(v, run):
    if isinstance(v, dict) and set(v) == {"$image_asset"}:
        return (Path(run) / "conversation/assets" / v["$image_asset"]).read_text()
    if isinstance(v, dict): return {k: rehydrate(x, run) for k, x in v.items()}
    if isinstance(v, list): return [rehydrate(x, run) for x in v]
    return v

def has_image(m):
    return isinstance(m.get("content"), list) and any(b.get("type") == "image_url" for b in m["content"])

def shape(msgs, kind, nonce):
    msgs = copy.deepcopy(msgs)
    for m in msgs:
        if isinstance(m.get("content"), list):
            for b in m["content"]: b.pop("cache_control", None)
    sysm = msgs[0]
    if isinstance(sysm["content"], str): sysm["content"] = f"Probe {nonce}. " + sysm["content"]
    else: sysm["content"][0]["text"] = f"Probe {nonce}. " + sysm["content"][0]["text"]
    if kind == "live": return msgs
    out = []
    for m in msgs:
        out.append(m)
        if m["role"] == "user" and has_image(m):
            text = next(b["text"] for b in m["content"] if b.get("type") == "text")
            turn = text.split("\n", 1)[0].strip()
            out.append({"role": "assistant", "content": TURN_ACK})
            out.append({"role": "user", "content": f"{turn}: respond with your next action now."})
    return out

async def send(t, key, body):
    try:
        raw = await t(json.dumps(body).encode(), key, 180, lambda c: None)
    except ProviderRequestError as e:
        return {"error": f"HTTP {e.status}: {json.dumps(e.response_body)[:200]}"}
    u = raw.get("usage") or {}; d = u.get("prompt_tokens_details") or {}
    return {"prompt": u.get("prompt_tokens"), "cached": d.get("cached_tokens"), "write": d.get("cache_write_tokens"),
            "prompt_cost": (u.get("cost_details") or {}).get("upstream_inference_prompt_cost"), "cost": u.get("cost"), "provider": raw.get("provider")}

async def main():
    t = OpenRouterTransport(); results = []; spend = 0
    for model, (run, pairs) in RUNS.items():
        key = load_config("configs/config-5.0.yaml", llm_alias=model)["openrouter_api_key"]
        art = artifacts(run)
        for a, b in pairs:
            bodies = {n: rehydrate(json.load(open(Path(run) / art[n])), run) for n in (a, b)}
            for kind in ("live", "split"):
                nonce = uuid.uuid4().hex[:8]; rows = []
                for n in (a, b):
                    body = copy.deepcopy(bodies[n]); body["messages"] = shape(body["messages"], kind, nonce)
                    body["max_tokens"] = 60; body.pop("session_id", None); body["stream"] = False
                    body["provider"] = {"only": [TAG], "allow_fallbacks": False}
                    r = await send(t, key, body); r["turn"] = n; rows.append(r); spend += r.get("cost") or 0
                    await asyncio.sleep(2)
                A, B = rows
                shortfall = None if "error" in A or "error" in B else (A["prompt"] or 0) - (B["cached"] or 0)
                verdict = "error" if shortfall is None else ("extends" if shortfall <= max(64, round(0.01 * (A["prompt"] or 0))) else ("partial" if (B["cached"] or 0) > 0 else "no_hit"))
                results.append({"model": model, "pair": [a, b], "shape": kind, "nonce": nonce, "A": A, "B": B, "shortfall": shortfall, "verdict": verdict})
                fa = f"{A.get('prompt')} tok" if "error" not in A else A["error"]
                fb = f"{B.get('prompt')} tok, cached {B.get('cached')}" if "error" not in B else B["error"]
                print(f"{model.split('/')[1]:22} t{a}->t{b} {kind:5} | A {fa} (cached {A.get('cached')}) | B {fb} => {verdict}" + (f" (shortfall {shortfall})" if shortfall else ""), flush=True)
    print(f"spend ${spend:.4f}")
    Path("artifacts/provider-compatibility/gemini-shape-vs-size-probe.json").write_text(json.dumps({"tag": TAG, "at": time.time(), "results": results, "spend_usd": spend}, indent=1))
if __name__ == "__main__":
    asyncio.run(main())
