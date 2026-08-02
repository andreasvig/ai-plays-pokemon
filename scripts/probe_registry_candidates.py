"""Probe candidate models before adding them to configs/models.yaml.

Registry entries claim things — "resolves 200 on OpenRouter", "reads the screen",
"the effort ladder is accepted", "max is a genuine tier not a clamp". Those
claims are only worth writing down if something checked them, so this sends the
smallest request that can check each one and prints what came back.

Per (model, tier) it reports: HTTP status, whether the reply is coherent, the
reasoning-token count (0 = the tier produced no thinking), latency, and the
serving provider. A tier that 400s is the useful negative result — it means the
level does not belong in that model's `thinking_levels`.

    OPENROUTER_API_KEY=... ./venv/bin/python scripts/probe_registry_candidates.py
    ./venv/bin/python scripts/probe_registry_candidates.py --model anthropic/claude-opus-5

Text+image by default (the Player sends screenshots, so a text-only pass would
not tell us whether the model can be a Player at all); --text-only skips the
image for text-only candidates.
"""

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
load_dotenv(ROOT / ".env")

KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not KEY:
    sys.exit("OPENROUTER_API_KEY not set")

URL = "https://openrouter.ai/api/v1/chat/completions"

# A REAL game screenshot, not a synthetic 1x1 pixel. A 1x1 PNG is rejected
# outright by some providers ("Could not process image" on Anthropic, "Invalid
# PNG image." on xAI) while Google accepts it — so probing with one produces
# fake 400s that look like the model refusing the effort tier. The screenshot
# also makes the coherence check meaningful: the model has to actually read a
# game screen, which is the job.
# Resolved from the gitignored run archive rather than committed here — a probe
# asset does not belong in version control, and any real screenshot works.
def _find_screenshot() -> Path:
    override = os.environ.get("PROBE_SCREENSHOT")
    if override:
        return Path(override)
    shots = sorted(ROOT.glob("local/runs/*/screenshots/*.png"))
    if not shots:
        sys.exit(
            "No screenshot found under local/runs/*/screenshots/. Point "
            "PROBE_SCREENSHOT at any real game PNG and re-run."
        )
    return shots[0]


SCREEN_PATH = _find_screenshot()
PNG_B64 = base64.standard_b64encode(SCREEN_PATH.read_bytes()).decode()

# A prompt with enough substance that a reasoning model has something to think
# about — a trivial one returns 0 reasoning tokens on adaptive models and would
# make a real tier look like a dead one.
PROMPT = (
    "A grid-based game screen shows a character at the centre tile. A door is 3 tiles "
    "right and 2 tiles up, but a wall runs along the entire row directly above the "
    "character except for a gap 4 tiles to the right. Give the shortest button "
    "sequence (up/down/left/right) that reaches the door, then state how many presses."
)

# (label, openrouter_id, [tiers], reasoning_style)
#   reasoning_style: "effort"  → {"effort": tier}
#                    "binary"  → {"enabled": tier == "thinking"}
#                    "none"    → no reasoning block
CANDIDATES = [
    ("claude-opus-5",       "anthropic/claude-opus-5",      ["max", "high"],        "effort"),
    ("claude-fable-5",      "anthropic/claude-fable-5",     ["max", "high"],        "effort"),
    ("gemini-3.6-flash",    "google/gemini-3.6-flash",      ["high", "low"],        "effort"),
    ("gemini-3.5-flash-lite", "google/gemini-3.5-flash-lite", ["high", "low"],      "effort"),
    ("grok-4.5",            "x-ai/grok-4.5",                ["high", "low"],        "effort"),
    ("kimi-k3",             "moonshotai/kimi-k3",           ["high", "low"],        "effort"),
    ("qwen3.7-flash",       "qwen/qwen3.7-flash",           ["thinking"],           "binary"),
    ("step-3.7-flash",      "stepfun/step-3.7-flash",       ["high"],               "effort"),
    ("muse-spark-1.1",      "meta/muse-spark-1.1",          ["high"],               "effort"),
    ("inkling",             "thinkingmachines/inkling",     ["high"],               "effort"),
]


def probe(model_id: str, tier: str, style: str, *, with_image: bool, max_tokens: int) -> dict:
    content: list | str
    if with_image:
        content = [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
        ]
    else:
        content = PROMPT

    payload: dict = {
        "model": model_id,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if style == "effort":
        payload["reasoning"] = {"effort": tier}
    elif style == "binary":
        payload["reasoning"] = {"enabled": tier == "thinking"}

    t0 = time.time()
    try:
        r = requests.post(
            URL,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=180,
        )
    except requests.RequestException as e:
        return {"status": "EXC", "error": str(e)[:160], "latency_s": round(time.time() - t0, 1)}
    latency = round(time.time() - t0, 1)

    out: dict = {"status": r.status_code, "latency_s": latency}
    try:
        body = r.json()
    except ValueError:
        out["error"] = r.text[:200]
        return out

    if r.status_code != 200:
        err = body.get("error") or {}
        meta = err.get("metadata") or {}
        # OpenRouter wraps upstream failures as a generic "Provider returned
        # error"; the real cause is in error.metadata.raw. Surface both.
        raw = meta.get("raw") or meta.get("provider_name") or ""
        out["error"] = str(err.get("message") or body)[:120]
        out["upstream"] = str(raw)[:400]
        return out

    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = body.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    out["provider"] = body.get("provider") or "?"
    out["finish"] = choice.get("finish_reason")
    # Providers may return these as null rather than omitting them (observed:
    # stepfun/step-3.7-flash returns reasoning_tokens: null), so coerce instead
    # of relying on the dict default.
    out["reasoning_tokens"] = details.get("reasoning_tokens") or 0
    out["out_tokens"] = usage.get("completion_tokens") or 0
    out["cost_usd"] = usage.get("cost")
    text = (msg.get("content") or "").strip()
    out["reply_len"] = len(text)
    out["reply_head"] = text[:110].replace("\n", " ")
    # Did it actually answer? A coherent reply mentions at least one direction.
    out["coherent"] = any(d in text.lower() for d in ("right", "up", "left", "down"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="probe only this openrouter id")
    ap.add_argument("--text-only", action="store_true", help="omit the image block")
    ap.add_argument("--max-tokens", type=int, default=1200)
    args = ap.parse_args()

    rows = [c for c in CANDIDATES if not args.model or c[1] == args.model]
    if not rows:
        sys.exit(f"no candidate matches {args.model!r}")

    results: dict[str, dict] = {}
    total_cost = 0.0
    for label, model_id, tiers, style in rows:
        print(f"\n=== {label}  ({model_id})")
        for tier in tiers:
            res = probe(
                model_id, tier, style,
                with_image=not args.text_only, max_tokens=args.max_tokens,
            )
            results[f"{label}({tier})"] = res
            total_cost += float(res.get("cost_usd") or 0)
            if res["status"] == 200:
                print(
                    f"  {tier:<9} 200  reasoning={res['reasoning_tokens']:<6} "
                    f"out={res['out_tokens']:<5} {res['latency_s']}s  "
                    f"coherent={res['coherent']}  provider={res['provider']}  "
                    f"${res.get('cost_usd')}"
                )
                print(f"            reply: {res['reply_head']}")
            else:
                print(f"  {tier:<9} {res['status']}  {res.get('error', '')}")
                if res.get("upstream"):
                    print(f"            upstream: {res['upstream']}")

    print(f"\n{'=' * 60}\nTOTAL PROBE COST: ${total_cost:.4f}")
    out_path = ROOT / "local" / "registry-probe.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"raw results: {out_path}")


if __name__ == "__main__":
    main()
