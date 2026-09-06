"""Does an OpenRouter endpoint tokenize the reasoning we replay in assistant history?

For each endpoint of a model: one live turn to obtain real reasoning, then the
same two-turn history sent with the reasoning carried in different fields (or
none). Billed prompt tokens tell whether the replayed thoughts reached the
model; ``provider_feedback`` never does. Text only, a few hundred tokens each.
"""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import time

from src.agent.append_agent import OpenRouterTransport, ProviderRequestError, atomic_json
from src.agent.provider_profiles import fetch_endpoint_pricing
from src.config import load_config

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ("You are playing Pokemon FireRed. Think carefully before answering. "
          "Answer in one short sentence.")
USER1 = "You are in the bedroom at game start. What is your plan to leave the house?"
USER2 = "Good. Now, in one sentence, what do you do once you are outside?"
PAD = " ".join(["The stairs are entered from the mat side."] * 120)  # ~1,100 tokens of filler


def variants(assistant):
    base = {"role": "assistant", "content": assistant.get("content") or ""}
    text = assistant.get("reasoning") or "\n".join(
        b.get("text") or b.get("summary") or "" for b in assistant.get("reasoning_details") or [] if isinstance(b, dict))
    details = assistant.get("reasoning_details") or [{"type": "reasoning.text", "text": text, "format": "unknown", "index": 0}]
    padded = [dict(b, text=(b.get("text") or "") + " " + PAD) if b.get("type") == "reasoning.text" else b for b in details]
    return {
        "none": dict(base),
        "harness": {**base, "reasoning": text, "reasoning_details": details},
        "details_only": {**base, "reasoning_details": details},
        "reasoning_only": {**base, "reasoning": text},
        "reasoning_content": {**base, "reasoning_content": text},
        "details_padded": {**base, "reasoning_details": padded},
        "reasoning_padded": {**base, "reasoning": text + " " + PAD},
    }, text


async def probe_endpoint(model, tag, key, reasoning, transport, timeout, semaphore):
    async with semaphore:
        def body(messages):
            return json.dumps({"model": model, "messages": messages, "reasoning": reasoning, "max_tokens": 600,
                               "provider": {"only": [tag], "allow_fallbacks": False}, "transforms": [],
                               "stream": False}).encode()
        row = {"tag": tag, "requests": [], "error": None}
        try:
            start = time.monotonic()
            first = await transport(body([{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER1}]),
                                    key, timeout, lambda c: None)
            assistant = first["choices"][0]["message"]
            usage = first.get("usage") or {}
            row["provider"] = first.get("provider")
            row["first_turn"] = {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                                 "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                                 "returned_fields": sorted(k for k in ("reasoning", "reasoning_details", "reasoning_content") if assistant.get(k)),
                                 "detail_formats": sorted({b.get("type") for b in assistant.get("reasoning_details") or [] if isinstance(b, dict)}),
                                 "reasoning_chars": len(assistant.get("reasoning") or ""), "latency_s": round(time.monotonic() - start, 1),
                                 "cost": usage.get("cost")}
            shapes, text = variants(assistant)
            row["reasoning_text_chars"] = len(text)
            for name, message in shapes.items():
                messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER1}, message,
                            {"role": "user", "content": USER2}]
                start = time.monotonic()
                try:
                    raw = await transport(body(messages), key, timeout, lambda c: None)
                    u = raw.get("usage") or {}
                    row["requests"].append({"variant": name, "prompt_tokens": u.get("prompt_tokens"), "cost": u.get("cost"),
                                            "provider": raw.get("provider"), "latency_s": round(time.monotonic() - start, 1),
                                            "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens")})
                except ProviderRequestError as exc:
                    row["requests"].append({"variant": name, "error": f"HTTP {exc.status}: {json.dumps(exc.response_body)[:300]}"})
            base = next((r["prompt_tokens"] for r in row["requests"] if r["variant"] == "none"), None)
            if base is not None:
                for r in row["requests"]:
                    if r.get("prompt_tokens") is not None:
                        r["delta_vs_none"] = r["prompt_tokens"] - base
                harness = next((r.get("delta_vs_none") for r in row["requests"] if r["variant"] == "harness"), None)
                padded = next((r.get("delta_vs_none") for r in row["requests"] if r["variant"] == "details_padded"), None)
                row["verdict"] = ("consumed" if (harness or 0) > 20 or (padded or 0) > 200
                                  else "dropped" if harness is not None else "unknown")
        except ProviderRequestError as exc:
            row["error"] = f"HTTP {exc.status}: {json.dumps(exc.response_body)[:400]}"
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  {model} {tag:<24} -> {row.get('verdict') or 'error'} {row.get('error') or ''}"[:160], flush=True)
        return row


async def effort_sweep(model, tag, key, transport, timeout, efforts):
    out = []
    for effort in efforts:
        reasoning = {"effort": effort} if effort not in ("off",) else {"enabled": False}
        try:
            raw = await transport(json.dumps({"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER1}],
                                              "reasoning": reasoning, "max_tokens": 1500, "provider": {"only": [tag], "allow_fallbacks": False},
                                              "stream": False}).encode(), key, timeout, lambda c: None)
            u = raw.get("usage") or {}
            m = raw["choices"][0]["message"]
            out.append({"effort": effort, "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                        "completion_tokens": u.get("completion_tokens"), "reasoning_chars": len(m.get("reasoning") or ""),
                        "detail_formats": sorted({b.get("type") for b in m.get("reasoning_details") or [] if isinstance(b, dict)}), "cost": u.get("cost")})
        except ProviderRequestError as exc:
            out.append({"effort": effort, "error": f"HTTP {exc.status}: {json.dumps(exc.response_body)[:200]}"})
        print(f"  effort {effort}: {out[-1]}", flush=True)
    return out


async def main(args):
    key = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=args.model)["openrouter_api_key"]
    snapshot = await fetch_endpoint_pricing(args.model, key)
    tags = args.tag or [e["tag"] for e in snapshot["endpoints"]]
    reasoning = json.loads(args.reasoning)
    transport = OpenRouterTransport()
    semaphore = asyncio.Semaphore(args.concurrency)
    print(f"Probing {args.model} on {len(tags)} endpoints with reasoning={reasoning}")
    rows = await asyncio.gather(*(probe_endpoint(args.model, tag, key, reasoning, transport, args.timeout, semaphore) for tag in tags))
    result = {"model": args.model, "probed_at": datetime.now().isoformat(), "reasoning_param": reasoning,
              "pricing": snapshot["endpoints"], "endpoints": rows}
    if args.effort_sweep:
        result["effort_sweep"] = {"tag": args.effort_sweep, "results": await effort_sweep(args.model, args.effort_sweep, key, transport, args.timeout, args.efforts.split(","))}
    out = ROOT / "artifacts/provider-compatibility" / f"reasoning-replay-probe__{args.model.replace('/', '--')}.json"
    atomic_json(out, result)
    spent = sum((r.get("first_turn") or {}).get("cost") or 0 for r in rows) + sum(q.get("cost") or 0 for r in rows for q in r["requests"])
    print(f"Saved {out}; reported spend ${spent:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--tag", action="append", help="Probe only these endpoint tags")
    parser.add_argument("--reasoning", default='{"enabled": true}', help="JSON for the reasoning parameter")
    parser.add_argument("--effort-sweep", help="Endpoint tag on which to sweep reasoning efforts")
    parser.add_argument("--efforts", default="off,low,high,max")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    asyncio.run(main(parser.parse_args()))
