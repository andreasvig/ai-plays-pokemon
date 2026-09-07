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
def append_config_path():
    """The append harness config. Renamed config-append.yaml -> config-5.0.yaml when
    the append harness became the standard (2026-09-07); accept either so an older
    checkout still probes."""
    for name in ("configs/config-5.0.yaml", "configs/config-append.yaml"):
        candidate = ROOT / name
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("no append harness config under configs/")

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
    system = load_config(append_config_path(), llm_alias=model)["system_prompt"] + "\n\n" + FILLER
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
                                     "prompt_cost": (u.get("cost_details") or {}).get("upstream_inference_prompt_cost"),
                                     "cost": u.get("cost"), "provider": raw.get("provider")})
            except ProviderRequestError as exc:
                row["calls"].append({"error": f"HTTP {exc.status}: {json.dumps(exc.response_body)[:200]}"})
            await asyncio.sleep(1.5)
        print(f"  {model} {tag} [{placement}] -> " + " | ".join(
            f"call{i+1}: {c.get('prompt_tokens')} tok, cached {c.get('cached')}, write {c.get('cache_write')}, ${c.get('prompt_cost')}" if 'error' not in c else f"call{i+1}: {c['error']}"
            for i, c in enumerate(row["calls"])), flush=True)
        out.append(row)
    return out


async def grow(model, tag, key, image, transport, timeout, reasoning, shape="live"):
    """Extension shape: request A, then A + one more turn, immediately.

    Default (`live`): A ends with `user: [text, screenshot]`, exactly like the agent. `split`: each
    screenshot turn is followed by a one-word assistant acknowledgement and a text-only user prompt,
    so the final turn carries no image. An identical-repeat pair cannot witness either. OpenAI via
    OpenRouter (2026-09-06): identical repeat 99.9% cached; live shape cached only the pre-image
    prefix on every turn (2.7x cost in a 25-turn run); split shape extends through the images.
    """
    system = load_config(append_config_path(), llm_alias=model)["system_prompt"] + "\n\n" + FILLER
    def user_turn(n):
        parts = [{"type": "text", "text": f"Turn {n}. Screen attached. Choose your next inputs; answer in one sentence."}]
        if image:
            data = base64.b64encode(Path(image).read_bytes()).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
        return {"role": "user", "content": parts}
    plan = {"role": "assistant", "content": "Turn plan: " + " ".join(["walk right, then up, then left onto the stairs;"] * 60)}
    def screenshot_turn(n):
        if shape == "split":
            return [user_turn(n), {"role": "assistant", "content": "Observed."}, {"role": "user", "content": f"Turn {n}: choose your next inputs."}]
        return [user_turn(n)]
    history = [{"role": "system", "content": system}] + screenshot_turn(1)          # ends like the agent's request
    extended = history + [plan] + screenshot_turn(2)
    calls = []
    for label, msgs in (("A", history), ("A+turn", extended)):
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
                          "cost": u.get("cost"), "provider": raw.get("provider")})
        except ProviderRequestError as exc:
            calls.append({"request": label, "error": f"HTTP {exc.status}: {json.dumps(exc.response_body)[:200]}"})
        await asyncio.sleep(1.5)
    first = calls[0].get("prompt_tokens") or 0
    second = calls[-1]
    # Shortfall in TOKENS, not a ratio. A 10% ratio cannot see a small image: with an 8k system
    # prompt and a 415-token screenshot, "cached everything except the image" is 94.6% of the
    # prompt and passed a 0.9 threshold as prefix_extends (gpt-6-astra, 2026-09-07) while the
    # 2026-09-06 run caught the same behaviour only because its screenshot was 1,640 of 9,364
    # tokens. The question is whether the boundary reached the END of the previous request, so
    # measure the distance to it and allow only tokenizer/boundary slack.
    shortfall = first - (second.get("cached") or 0) if "error" not in second else None
    verdict = ("error" if shortfall is None else
               "prefix_extends" if shortfall <= max(64, round(0.01 * first)) else "prefix_does_not_extend")
    print(f"  {model} {tag} [grow:{shape}{' +image' if image else ''}] -> " + " | ".join(
        f"{c['request']}: {c.get('prompt_tokens')} tok, cached {c.get('cached')}, write {c.get('cache_write')}" if 'error' not in c else f"{c['request']}: {c['error']}"
        for c in calls) + f" => {verdict}" + (f" (shortfall {shortfall} tok)" if shortfall else ""), flush=True)
    return {"placement": f"grow:{shape}", "image": bool(image), "calls": calls,
            "cached_shortfall_tokens": shortfall, "verdict": verdict}


async def main(args):
    key = load_config(append_config_path(), llm_alias=args.model)["openrouter_api_key"]
    transport = OpenRouterTransport()
    reasoning = json.loads(args.reasoning) if args.reasoning else None
    result = {"model": args.model, "image": args.image, "endpoints": {}}
    for tag in args.tag:
        if args.grow or args.grow_split:
            shapes = (["live"] if args.grow else []) + (["split"] if args.grow_split else [])
            result["endpoints"][tag] = [await grow(args.model, tag, key, args.image, transport, args.timeout, reasoning, shape) for shape in shapes]
        else:
            result["endpoints"][tag] = await probe(args.model, tag, key, args.image, args.placement, transport, args.timeout, reasoning, args.tail_turns)
    spent = sum(c.get("cost") or 0 for rows in result["endpoints"].values() for r in rows for c in r["calls"])
    result["reported_spend_usd"] = round(spent, 6)
    out = ROOT / "artifacts/provider-compatibility" / f"cache-hit-probe__{args.model.replace('/', '--')}.json"
    atomic_json(out, result)
    print(f"Saved {out}; reported spend ${spent:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--tag", action="append", required=True)
    parser.add_argument("--placement", action="append", default=None, help="none | system | system+last")
    parser.add_argument("--image", help="PNG to attach on turn 1 (mirrors real requests)")
    parser.add_argument("--reasoning", help="JSON reasoning param")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--grow", action="store_true", help="Extension shape in the agent's live form (A ends with user[text, screenshot]); pass --image for the real case")
    parser.add_argument("--grow-split", action="store_true", help="Extension shape with a text-only final turn (screenshot, assistant ack, text prompt)")
    parser.add_argument("--tail-turns", type=int, default=1, help="Conversation turns after the system prompt (longer tail tests conversation caching)")
    args = parser.parse_args()
    args.placement = args.placement or ["none", "system", "system+last"]
    asyncio.run(main(args))
