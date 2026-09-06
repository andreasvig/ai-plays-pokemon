"""Can a pinned endpoint be made to cache our conversation prefix?

Sends the same request twice (identical prefix of several thousand tokens,
optionally with the real screenshot) and reports the second call's cached
tokens and billed prompt cost, for several cache_control placements.
"""
import argparse
import asyncio
import base64
import json
from pathlib import Path

from src.agent.append_agent import OpenRouterTransport, ProviderRequestError, atomic_json
from src.config import load_config

ROOT = Path(__file__).resolve().parents[1]
FILLER = ("Reference notes. " + " ".join(f"Tile row {i}: walkable except where furniture stands; doors warp on entry." for i in range(400)))


def build(messages, placement):
    msgs = json.loads(json.dumps(messages))
    marker = {"type": "ephemeral"}
    def mark(msg):
        if isinstance(msg["content"], str):
            msg["content"] = [{"type": "text", "text": msg["content"], "cache_control": marker}]
        else:
            for block in reversed(msg["content"]):
                if block.get("type") == "text":
                    block["cache_control"] = marker
                    break
    if placement in ("system", "system+last"):
        mark(msgs[0])
    if placement in ("system+last", "last"):
        mark(msgs[-1])
    if placement == "last_assistant":
        mark(msgs[-2])
    return msgs


async def probe(model, tag, key, image, placements, transport, timeout, reasoning, tail_turns=1):
    system = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=model)["system_prompt"] + "\n\n" + FILLER
    turn1 = [{"type": "text", "text": "Turn 1. Screen attached. Choose your next inputs; answer in one sentence."}]
    if image:
        data = base64.b64encode(Path(image).read_bytes()).decode()
        turn1.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
    history = [{"role": "system", "content": system}, {"role": "user", "content": turn1}]
    for i in range(tail_turns):
        history.append({"role": "assistant", "content": f"Turn {i+1} plan: " + " ".join(["walk right, then up, then left onto the stairs;"] * 60)})
        history.append({"role": "user", "content": f"Turn {i+2}. Same screen. Confirm your plan in one sentence." + (" " + FILLER[:1500] if tail_turns > 1 else "")})
    out = []
    for placement in placements:
        row = {"placement": placement, "calls": []}
        body = {"model": model, "messages": build(history, placement), "max_tokens": 60, "stream": False,
                "provider": {"only": [tag], "allow_fallbacks": False}, "transforms": []}
        if reasoning:
            body["reasoning"] = reasoning
        for i in range(2):
            try:
                raw = await transport(json.dumps(body).encode(), key, timeout, lambda c: None)
                u = raw.get("usage") or {}
                row["calls"].append({"prompt_tokens": u.get("prompt_tokens"), "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                                     "cache_write": (u.get("prompt_tokens_details") or {}).get("cache_write_tokens"),
                                     "prompt_cost": (u.get("cost_details") or {}).get("upstream_inference_prompt_cost"), "provider": raw.get("provider")})
            except ProviderRequestError as exc:
                row["calls"].append({"error": f"HTTP {exc.status}: {json.dumps(exc.response_body)[:200]}"})
            await asyncio.sleep(1.5)
        print(f"  {model} {tag} [{placement}] -> " + " | ".join(
            f"call{i+1}: {c.get('prompt_tokens')} tok, cached {c.get('cached')}, write {c.get('cache_write')}, ${c.get('prompt_cost')}" if 'error' not in c else f"call{i+1}: {c['error']}"
            for i, c in enumerate(row["calls"])), flush=True)
        out.append(row)
    return out


async def grow(model, tag, key, image, transport, timeout, reasoning):
    """Extension shape: request A, then A + one more turn (text + the same screenshot), immediately.

    An identical-repeat pair cannot witness this. OpenAI via OpenRouter (2026-09-06) cached 99.9% of an
    identical repeat yet only the pre-image prefix (system + first user text) when the conversation
    grew by a turn — every append-and-compact turn re-paid the 1.25x cache-write price on the rest.
    """
    system = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=model)["system_prompt"] + "\n\n" + FILLER
    def user_turn(n):
        parts = [{"type": "text", "text": f"Turn {n}. Screen attached. Choose your next inputs; answer in one sentence."}]
        if image:
            data = base64.b64encode(Path(image).read_bytes()).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
        return {"role": "user", "content": parts}
    history = [{"role": "system", "content": system}, user_turn(1),
               {"role": "assistant", "content": "Turn 1 plan: " + " ".join(["walk right, then up, then left onto the stairs;"] * 60)}]
    calls = []
    for label, msgs in (("A", history), ("A+turn", history + [user_turn(2), {"role": "assistant", "content": "Turn 2 plan: same."}, user_turn(3)])):
        body = {"model": model, "messages": msgs, "max_tokens": 60, "stream": False,
                "provider": {"only": [tag], "allow_fallbacks": False}, "transforms": []}
        if reasoning:
            body["reasoning"] = reasoning
        try:
            raw = await transport(json.dumps(body).encode(), key, timeout, lambda c: None)
            u = raw.get("usage") or {}
            d = u.get("prompt_tokens_details") or {}
            calls.append({"request": label, "prompt_tokens": u.get("prompt_tokens"), "cached": d.get("cached_tokens"),
                          "cache_write": d.get("cache_write_tokens"), "prompt_cost": (u.get("cost_details") or {}).get("upstream_inference_prompt_cost"),
                          "provider": raw.get("provider")})
        except ProviderRequestError as exc:
            calls.append({"request": label, "error": f"HTTP {exc.status}: {json.dumps(exc.response_body)[:200]}"})
        await asyncio.sleep(1.5)
    first = calls[0].get("prompt_tokens") or 0
    second = calls[-1]
    verdict = ("prefix_extends" if (second.get("cached") or 0) >= 0.9 * first else "prefix_does_not_extend") if "error" not in second else "error"
    print(f"  {model} {tag} [grow{' +image' if image else ''}] -> " + " | ".join(
        f"{c['request']}: {c.get('prompt_tokens')} tok, cached {c.get('cached')}, write {c.get('cache_write')}" if 'error' not in c else f"{c['request']}: {c['error']}"
        for c in calls) + f" => {verdict}", flush=True)
    return {"placement": "grow", "image": bool(image), "calls": calls, "verdict": verdict}


async def main(args):
    key = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=args.model)["openrouter_api_key"]
    transport = OpenRouterTransport()
    reasoning = json.loads(args.reasoning) if args.reasoning else None
    result = {"model": args.model, "image": args.image, "endpoints": {}}
    for tag in args.tag:
        if args.grow:
            result["endpoints"][tag] = [await grow(args.model, tag, key, args.image, transport, args.timeout, reasoning)]
        else:
            result["endpoints"][tag] = await probe(args.model, tag, key, args.image, args.placement, transport, args.timeout, reasoning, args.tail_turns)
    out = ROOT / "artifacts/provider-compatibility" / f"cache-hit-probe__{args.model.replace('/', '--')}.json"
    atomic_json(out, result)
    print("Saved", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--tag", action="append", required=True)
    parser.add_argument("--placement", action="append", default=None, help="none | system | system+last")
    parser.add_argument("--image", help="PNG to attach on turn 1 (mirrors real requests)")
    parser.add_argument("--reasoning", help="JSON reasoning param")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--grow", action="store_true", help="Extension shape (A, then A + one turn) instead of an identical repeat; pass --image for the real case")
    parser.add_argument("--tail-turns", type=int, default=1, help="Conversation turns after the system prompt (longer tail tests conversation caching)")
    args = parser.parse_args()
    args.placement = args.placement or ["none", "system", "system+last"]
    asyncio.run(main(args))
