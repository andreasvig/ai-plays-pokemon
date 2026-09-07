"""Follow-ups to probe_gemini_shape_vs_size.py.
 (a) Is the floor shape-independent? lite t9 live (15k) sent twice identically -> hit or not.
 (b) Is the cache prefix-strict? 3.8 t10 live sent with nonce X, then nonce Y at the START of the
     system prompt (different prefix, same content after), then nonce Y again (identical control).
 (c) Was the 43k split no-hit a fluke? 3.8 t15->t16 split pair again with a fresh nonce, then t15 split identical repeat."""
import asyncio, json, sys, copy, uuid, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd())); sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_gemini_shape_vs_size import RUNS, TAG, artifacts, rehydrate, shape, send
from src.agent.append_agent import OpenRouterTransport
from src.config import load_config

async def main():
    t = OpenRouterTransport(); out = []; spend = 0
    def body_for(model, run, n, kind, nonce):
        art = artifacts(run); b = rehydrate(json.load(open(Path(run) / art[n])), run)
        b["messages"] = shape(b["messages"], kind, nonce); b["max_tokens"] = 60; b.pop("session_id", None); b["stream"] = False
        b["provider"] = {"only": [TAG], "allow_fallbacks": False}; return b
    async def go(label, model, run, n, kind, nonce):
        nonlocal spend
        key = load_config("configs/config-5.0.yaml", llm_alias=model)["openrouter_api_key"]
        r = await send(t, key, body_for(model, run, n, kind, nonce)); spend += r.get("cost") or 0
        r.update(label=label, model=model, turn=n, shape=kind, nonce=nonce); out.append(r)
        print(f"{label:34} {r.get('prompt')} tok, cached {r.get('cached')}, write {r.get('write')}" if "error" not in r else f"{label:34} {r['error']}", flush=True)
        await asyncio.sleep(2.5)
    lite, lrun = "google/gemini-3.5-flash-lite", RUNS["google/gemini-3.5-flash-lite"][0]
    g38, grun = "google/gemini-3.8-flash", RUNS["google/gemini-3.8-flash"][0]
    na = uuid.uuid4().hex[:8]
    await go("(a) lite t9 live #1", lite, lrun, 9, "live", na)
    await go("(a) lite t9 live #2 identical", lite, lrun, 9, "live", na)
    await go("(a) lite t9 live #3 identical", lite, lrun, 9, "live", na)
    nx, ny = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    await go("(b) 3.8 t10 live nonce X", g38, grun, 10, "live", nx)
    await go("(b) 3.8 t10 live nonce Y (new prefix)", g38, grun, 10, "live", ny)
    await go("(b) 3.8 t10 live nonce Y identical", g38, grun, 10, "live", ny)
    nc = uuid.uuid4().hex[:8]
    await go("(c) 3.8 t15 split #1", g38, grun, 15, "split", nc)
    await go("(c) 3.8 t16 split (extends)", g38, grun, 16, "split", nc)
    await go("(c) 3.8 t15 split identical", g38, grun, 15, "split", nc)
    print(f"spend ${spend:.4f}")
    Path("artifacts/provider-compatibility/gemini-cache-followup-probe.json").write_text(json.dumps({"tag": TAG, "at": time.time(), "results": out, "spend_usd": spend}, indent=1))
asyncio.run(main())
