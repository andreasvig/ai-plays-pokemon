"""Does an endpoint accept a reasoning floor below its catalog ladder?

OpenRouter's catalog (`reasoning.supported_efforts` + `reasoning.mandatory`) is
what configs/provider-profiles.yaml copies, but it can under-report: haiku-4.5
publishes NO efforts and accepts `minimal` (measured 2026-09-07). Andreas wants
the "minimal / no thinking" rungs Google and others offer natively, so this
sends one live call per shape on the model's PINNED endpoint tag with
`require_parameters: true` — the endpoint has to honour the parameter, not
silently drop it — and reports status + reasoning tokens against a `low`
reference. A 200 with fewer reasoning tokens than `low` is a real rung; a 200
with the same count is a clamp; a 400 is the catalog being right.

    ./venv/bin/python scripts/probe_reasoning_floor.py
    ./venv/bin/python scripts/probe_reasoning_floor.py --model google/gemini-3.8-flash

Paid (cents). Guarded by __main__ so importing it for the helper never spends.
Results land in artifacts/provider-compatibility/reasoning-floor-probe__<slug>.json.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
URL = "https://openrouter.ai/api/v1/chat/completions"
OUT_DIR = ROOT / "artifacts" / "provider-compatibility"

PROMPT = (
    "A grid-based game screen shows a character at the centre tile. A door is 3 tiles "
    "right and 2 tiles up, but a wall runs along the entire row directly above the "
    "character except for a gap 4 tiles to the right. Give the shortest button "
    "sequence (up/down/left/right) that reaches the door, then state how many presses."
)

# Second pass (--compare): is `minimal` a rung or a clamp? One call cannot tell —
# adaptive models think 0 tokens at ANY effort on an easy prompt. A harder prompt,
# repeated, against the ladder's own low/high shows whether the effort moves the
# reasoning-token count at all on this endpoint.
HARD_PROMPT = (
    "You are on tile (0,0) of a 7x7 grid. Walls occupy every tile of row 2 except (5,2), "
    "every tile of row 4 except (1,4), and the tiles (3,5) and (3,6). A ledge on (2,3) can "
    "only be crossed downward. The goal is (6,6). Give the shortest sequence of up/down/"
    "left/right presses, list every tile visited in order, and state the press count. "
    "Then say whether a second, equally short route exists."
)
COMPARE = {
    "google/gemini-3.8-flash": ["minimal", "low", "high"],
    "z-ai/glm-5.3-flash": ["minimal", "low", "max"],
}

# (openrouter_id, pinned endpoint tag from configs/provider-profiles.yaml, reference effort)
TARGETS = [
    ("google/gemini-3.8-flash", "google-ai-studio", "low"),
    ("z-ai/glm-5.3-flash", "z-ai/fp8", "low"),
]

# The shapes below the catalog ladder. `effort: none` and `enabled: false` are
# the two spellings of "no thinking" OpenRouter documents; `minimal` is the
# floor Google/OpenAI name.
SHAPES = [
    ("low (reference)", lambda ref: {"effort": ref}),
    ("effort minimal", lambda ref: {"effort": "minimal"}),
    ("effort none", lambda ref: {"effort": "none"}),
    ("enabled false", lambda ref: {"enabled": False}),
]


def _screenshot() -> str:
    override = os.environ.get("PROBE_SCREENSHOT")
    path = Path(override) if override else next(iter(sorted(ROOT.glob("local/runs/*/screenshots/*.png"))), None)
    if path is None or not path.exists():
        sys.exit("No screenshot under local/runs/*/screenshots/; set PROBE_SCREENSHOT")
    return base64.standard_b64encode(path.read_bytes()).decode()


def call(key: str, model: str, tag: str, reasoning: dict, png_b64: str, max_tokens: int = 1200,
         prompt: str = PROMPT) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
        ]}],
        "max_tokens": max_tokens,
        "usage": {"include": True},
        "reasoning": reasoning,
        "provider": {"only": [tag], "allow_fallbacks": False, "require_parameters": True},
    }
    t0 = time.time()
    try:
        r = requests.post(URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          json=payload, timeout=180)
    except requests.RequestException as e:
        return {"status": "EXC", "error": str(e)[:160]}
    out: dict = {"status": r.status_code, "latency_s": round(time.time() - t0, 1), "reasoning_sent": reasoning}
    try:
        body = r.json()
    except ValueError:
        out["error"] = r.text[:200]
        return out
    if r.status_code != 200:
        err = body.get("error") or {}
        meta = err.get("metadata") or {}
        out["error"] = str(err.get("message") or body)[:160]
        out["upstream"] = str(meta.get("raw") or meta.get("provider_name") or "")[:400]
        return out
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = body.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    text = (msg.get("content") or "").strip()
    out.update({
        "provider": body.get("provider"),
        "finish": choice.get("finish_reason"),
        "reasoning_tokens": details.get("reasoning_tokens") or 0,
        "out_tokens": usage.get("completion_tokens") or 0,
        "cost_usd": usage.get("cost"),
        "has_reasoning_field": bool(msg.get("reasoning") or msg.get("reasoning_details")),
        "coherent": any(d in text.lower() for d in ("right", "up", "left", "down")),
        "reply_head": text[:110].replace("\n", " "),
    })
    return out


def main() -> None:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="probe only this openrouter id")
    ap.add_argument("--compare", action="store_true", help="second pass: minimal vs the ladder, --reps each, hard prompt")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=16000, help="compare pass ceiling; 4000 saturated every effort on 2026-09-09")
    args = ap.parse_args()
    targets = [t for t in TARGETS if not args.model or t[0] == args.model]
    if not targets:
        sys.exit(f"no target matches {args.model!r}")
    png = _screenshot()
    total = 0.0
    if args.compare:
        for model, tag, _ref in targets:
            print(f"\n=== {model} @ {tag}  (hard prompt x{args.reps} per effort)")
            results = {"model": model, "endpoint": tag, "probed": time.strftime("%Y-%m-%d"), "prompt": "hard", "efforts": {}}
            for effort in COMPARE[model]:
                runs = [call(key, model, tag, {"effort": effort}, png, max_tokens=args.max_tokens, prompt=HARD_PROMPT) for _ in range(args.reps)]
                results["efforts"][effort] = runs
                total += sum(float(r.get("cost_usd") or 0) for r in runs)
                rt = [r.get("reasoning_tokens") if r["status"] == 200 else r["status"] for r in runs]
                ot = [r.get("out_tokens") if r["status"] == 200 else "-" for r in runs]
                fin = [r.get("finish") for r in runs]
                print(f"  {effort:<8} reasoning={rt}  out={ot}  finish={fin}  coherent={[r.get('coherent') for r in runs]}")
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            path = OUT_DIR / f"reasoning-floor-compare__{model.replace('/', '--')}.json"
            path.write_text(json.dumps(results, indent=2))
            print(f"  -> {path.relative_to(ROOT)}")
        print(f"\nTOTAL PROBE COST: ${total:.4f}")
        return
    for model, tag, ref in targets:
        print(f"\n=== {model} @ {tag}")
        results = {"model": model, "endpoint": tag, "probed": time.strftime("%Y-%m-%d"), "shapes": {}}
        for label, make in SHAPES:
            res = call(key, model, tag, make(ref), png)
            results["shapes"][label] = res
            total += float(res.get("cost_usd") or 0)
            if res["status"] == 200:
                print(f"  {label:<16} 200  reasoning={res['reasoning_tokens']:<6} out={res['out_tokens']:<5} "
                      f"{res['latency_s']}s coherent={res['coherent']} provider={res['provider']} ${res['cost_usd']}")
            else:
                print(f"  {label:<16} {res['status']}  {res.get('error', '')}")
                if res.get("upstream"):
                    print(f"                   upstream: {res['upstream']}")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"reasoning-floor-probe__{model.replace('/', '--')}.json"
        path.write_text(json.dumps(results, indent=2))
        print(f"  -> {path.relative_to(ROOT)}")
    print(f"\nTOTAL PROBE COST: ${total:.4f}")


if __name__ == "__main__":
    main()
