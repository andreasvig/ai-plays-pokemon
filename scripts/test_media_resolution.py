"""Test whether OpenRouter forwards Gemini's media_resolution dial.

Sends the same 720x480 screenshot to google/gemini-3-flash-preview via OpenRouter
under several parameter shapes. Compares prompt_tokens across runs — if LOW costs
fewer image tokens than HIGH, the knob works. If they're identical, OpenRouter
strips the param and Gemini falls back to default.

Cost: ~8 requests, <$0.02 total.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_IMAGE = (
    PROJECT_ROOT
    / "local/runs/2026-04-25_17-34-39_phase5_test/screenshots/00001_turn_1.png"
)
MODEL = "google/gemini-3-flash-preview"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
USER_PROMPT = "Describe what you see in this Pokemon GBA screenshot in one sentence. Mention the player position and visible objects."


def load_api_key() -> str:
    env = PROJECT_ROOT / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OPENROUTER_API_KEY not found in .env")


def build_image_block(b64: str, *, detail: str | None = None,
                      per_part_media_res: str | None = None) -> dict:
    image_url = {"url": f"data:image/png;base64,{b64}"}
    if detail is not None:
        image_url["detail"] = detail
    block = {"type": "image_url", "image_url": image_url}
    # Per-part media_resolution (Gemini 3 experimental shape — speculative
    # via OpenAI-compatible relay).
    if per_part_media_res is not None:
        block["media_resolution"] = {"level": per_part_media_res}
    return block


def make_request(
    client: httpx.Client,
    api_key: str,
    label: str,
    b64: str,
    *,
    detail: str | None = None,
    per_part_media_res: str | None = None,
    extra_body: dict | None = None,
    top_level_extras: dict | None = None,
) -> dict:
    """Send one variant and return a summary dict."""
    body = {
        "model": MODEL,
        "max_tokens": 80,
        "messages": [
            {
                "role": "user",
                "content": [
                    build_image_block(
                        b64, detail=detail, per_part_media_res=per_part_media_res
                    ),
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
        "usage": {"include": True},  # Ask OpenRouter for full usage breakdown.
    }
    if top_level_extras:
        body.update(top_level_extras)
    # `extra_body` is the OpenAI-SDK convention for "passthrough fields" — we
    # emulate it by merging its keys into the top-level JSON since OpenRouter
    # accepts unknown top-level keys and forwards them to the upstream provider.
    if extra_body:
        for k, v in extra_body.items():
            body[k] = v

    try:
        resp = client.post(
            ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/andreas-marvin/ai-plays-pokemon",
                "X-Title": "ai-plays-pokemon-mediares-experiment",
            },
            json=body,
            timeout=120.0,
        )
    except Exception as e:
        return {"label": label, "error": f"transport: {e!r}"}

    out: dict = {"label": label, "status": resp.status_code}
    try:
        data = resp.json()
    except Exception as e:
        out["error"] = f"json decode: {e!r}; body={resp.text[:300]}"
        return out

    if resp.status_code != 200:
        out["error"] = data.get("error", data)
        return out

    usage = data.get("usage", {}) or {}
    out["prompt_tokens"] = usage.get("prompt_tokens")
    out["completion_tokens"] = usage.get("completion_tokens")
    out["total_tokens"] = usage.get("total_tokens")
    # OpenAI-style image-token breakdown if upstream returns it.
    pt_details = usage.get("prompt_tokens_details") or {}
    out["image_tokens"] = pt_details.get("image_tokens") or pt_details.get(
        "cached_tokens"
    )
    out["pt_details_full"] = pt_details
    # OpenRouter cost passthrough.
    out["cost_usd"] = usage.get("cost") or usage.get("total_cost")
    # First 60 chars of the model reply — confirms it actually saw the image.
    try:
        text = data["choices"][0]["message"]["content"]
        out["reply_preview"] = (text or "")[:80].replace("\n", " ")
    except Exception:
        out["reply_preview"] = "<no reply>"
    return out


def main() -> int:
    if not SAMPLE_IMAGE.exists():
        print(f"Missing sample image: {SAMPLE_IMAGE}", file=sys.stderr)
        return 1

    b64 = base64.b64encode(SAMPLE_IMAGE.read_bytes()).decode("ascii")
    api_key = load_api_key()

    # Probes — same image, different parameter shapes. The hypothesis: at least
    # one of these causes prompt_tokens to fall (LOW) or rise (ULTRA_HIGH)
    # versus the baseline. If all eight return identical prompt_tokens, the
    # dial doesn't pass through.
    probes = [
        # 1. Pure baseline.
        ("baseline", {}),
        # 2. OpenAI-style detail on image_url (probably no-op for Gemini).
        ("openai_detail=low", {"detail": "low"}),
        ("openai_detail=high", {"detail": "high"}),
        # 3. extra_body shape — Google's native generation_config nesting,
        #    relayed through OpenRouter as a top-level passthrough field.
        (
            "extra_body.generation_config.media_resolution=LOW",
            {
                "extra_body": {
                    "generation_config": {
                        "media_resolution": "MEDIA_RESOLUTION_LOW"
                    }
                }
            },
        ),
        (
            "extra_body.generation_config.media_resolution=HIGH",
            {
                "extra_body": {
                    "generation_config": {
                        "media_resolution": "MEDIA_RESOLUTION_HIGH"
                    }
                }
            },
        ),
        (
            "extra_body.generation_config.media_resolution=ULTRA_HIGH",
            {
                "extra_body": {
                    "generation_config": {
                        "media_resolution": "MEDIA_RESOLUTION_ULTRA_HIGH"
                    }
                }
            },
        ),
        # 4. Flat top-level media_resolution (long shot).
        (
            "top_level.media_resolution=LOW",
            {"top_level_extras": {"media_resolution": "MEDIA_RESOLUTION_LOW"}},
        ),
        # 5. Per-part on the image content block (Gemini 3 experimental).
        ("per_part.media_resolution=LOW", {"per_part_media_res": "MEDIA_RESOLUTION_LOW"}),
        ("per_part.media_resolution=ULTRA_HIGH",
         {"per_part_media_res": "MEDIA_RESOLUTION_ULTRA_HIGH"}),
    ]

    print(f"Image: {SAMPLE_IMAGE.name}  (720x480 PNG)")
    print(f"Model: {MODEL}")
    print(f"Endpoint: {ENDPOINT}")
    print(f"Probes: {len(probes)}")
    print("-" * 90)

    results = []
    with httpx.Client() as client:
        for label, kwargs in probes:
            r = make_request(client, api_key, label, b64, **kwargs)
            results.append(r)
            if "error" in r:
                print(f"[{label}]  ERROR  {r['error']}")
            else:
                print(
                    f"[{label}]  prompt={r['prompt_tokens']:>5}  "
                    f"image_tokens={r['image_tokens']!s:<6}  "
                    f"completion={r['completion_tokens']:>3}  "
                    f"cost=${r['cost_usd']!s:<10}  "
                    f"reply={r['reply_preview']!r}"
                )

    print("-" * 90)
    print("\nSummary table (prompt_tokens — lower = cheaper image processing):\n")
    for r in results:
        if "error" in r:
            continue
        print(f"  {r['prompt_tokens']:>5}  {r['label']}")

    out_path = PROJECT_ROOT / "scripts/test_media_resolution_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results saved to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
