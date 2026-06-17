"""Does image resolution affect Gemini 3 Flash's spatial reasoning?

Sends the same Pokemon FireRed bedroom screenshot at four resolutions to
google/gemini-3-flash-preview via OpenRouter, asking the model for (x, y)
tile coordinates of known objects. Compares answers + token cost.

Ground truth (verified by reading the 720x480 saved screenshot directly):
  player:    (0, 0)        — center of the green rug, facing north
  console:   (0, 1)        — directly in front of player (north)
  bed:       (-3, 0)..(-3, 1)  — left wall
  stairs:    (3, 3)..(4, 3) — top right corner
  bookshelf/desk: (0..1, 3)    — top center

Resolutions tested:
  240x160   — 1x (native GBA, no upscale)
  480x320   — 2x
  720x480   — 3x (current production)
  1440x960  — 6x (does extra fidelity help?)
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_IMAGE = (
    PROJECT_ROOT
    / "local/runs/2026-04-25_17-34-39_phase5_test/screenshots/00001_turn_1.png"
)
MODEL = "google/gemini-3-flash-preview"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

PROBE_PROMPT = """You are looking at a Pokemon FireRed top-down screenshot of a bedroom.

The red grid overlay marks tile boundaries. Each square is one tile. Your character (red hat) is the player.

Coordinate system:
- Player is at (0, 0)
- x axis: negative = left, positive = right (in tiles)
- y axis: negative = south/down, positive = north/up (in tiles)
- Example: (-3, 1) means 3 tiles left and 1 tile north of the player

Identify the (x, y) tile coordinate of each object below. Give your best single-tile estimate even if uncertain — do NOT use ranges.

Reply in this exact format (one line, no extra text):
console:(x,y) bed:(x,y) stairs:(x,y) bookshelf:(x,y)

Where:
- console = the small electronic console/TV the player is facing
- bed = the bed with pink/red blanket
- stairs = the staircase going up to next floor
- bookshelf = the bookshelf or desk at the top of the room"""


def load_api_key() -> str:
    env = PROJECT_ROOT / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OPENROUTER_API_KEY not found in .env")


def render_at_size(src_img: Image.Image, target_w: int, target_h: int) -> bytes:
    """Resize source 720x480 image to target dimensions.
    NEAREST preserves the pixel-art + grid look. LANCZOS would smooth them out
    in a way the production pipeline doesn't.
    """
    out = src_img.resize((target_w, target_h), resample=Image.NEAREST)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def probe(client: httpx.Client, api_key: str, label: str, png_bytes: bytes) -> dict:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    body = {
        "model": MODEL,
        "max_tokens": 200,
        "temperature": 0.0,  # deterministic-ish for comparison
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": PROBE_PROMPT},
                ],
            }
        ],
        "usage": {"include": True},
    }
    resp = client.post(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/andreas-marvin/ai-plays-pokemon",
            "X-Title": "ai-plays-pokemon-resize-experiment",
        },
        json=body,
        timeout=180.0,
    )
    out: dict = {"label": label, "status": resp.status_code,
                 "image_bytes": len(png_bytes)}
    try:
        data = resp.json()
    except Exception as e:
        out["error"] = f"json: {e!r}; body={resp.text[:300]}"
        return out
    if resp.status_code != 200:
        out["error"] = data.get("error", data)
        return out
    usage = data.get("usage", {}) or {}
    out["prompt_tokens"] = usage.get("prompt_tokens")
    out["completion_tokens"] = usage.get("completion_tokens")
    out["cost_usd"] = usage.get("cost") or usage.get("total_cost")
    out["reply"] = (data["choices"][0]["message"]["content"] or "").strip()
    return out


# Ground truth — used only for visual comparison in the report, NOT shown
# to the model. Wide tolerance because tile-counting from screenshots is
# imprecise even for humans.
GROUND_TRUTH = {
    "console": "(0, 1)",
    "bed": "(-3, 0) or (-3, 1)",
    "stairs": "(3, 3) or (4, 3)",
    "bookshelf": "(0, 3) or (1, 3)",
}


def main() -> int:
    if not SAMPLE_IMAGE.exists():
        print(f"Missing sample image: {SAMPLE_IMAGE}", file=sys.stderr)
        return 1

    src = Image.open(SAMPLE_IMAGE).convert("RGB")
    assert src.size == (720, 480), f"unexpected source size: {src.size}"

    sizes = [
        ("240x160_native", 240, 160),
        ("480x320_2x",     480, 320),
        ("720x480_current", 720, 480),
        ("1440x960_6x",   1440, 960),
    ]

    api_key = load_api_key()
    print(f"Source: {SAMPLE_IMAGE.name} (720x480)")
    print(f"Model:  {MODEL}\n")
    print("Ground truth (rough — ranges OK):")
    for k, v in GROUND_TRUTH.items():
        print(f"  {k:>10}: {v}")
    print()
    print("-" * 90)

    results = []
    with httpx.Client() as client:
        for label, w, h in sizes:
            png = render_at_size(src, w, h)
            r = probe(client, api_key, label, png)
            r["width"] = w
            r["height"] = h
            results.append(r)
            if "error" in r:
                print(f"[{label:<20}] ERROR  {r['error']}")
            else:
                print(f"[{label:<20}] bytes={r['image_bytes']:>7,}  "
                      f"prompt={r['prompt_tokens']:>5}  "
                      f"completion={r['completion_tokens']:>3}  "
                      f"cost=${r['cost_usd']:<10}")
                print(f"  reply: {r['reply']}")
                print()

    print("-" * 90)
    print("\nQuick visual scoring guide — count how close each answer is to ground truth:\n")
    for r in results:
        if "error" in r:
            continue
        print(f"  [{r['label']}]\n    {r['reply']}\n")

    out_path = PROJECT_ROOT / "test_scripts/test_resize_spatial_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Full results: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
