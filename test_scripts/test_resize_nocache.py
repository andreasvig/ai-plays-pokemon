"""Rule out caching as the explanation for the byte-identical resize results.

If Gemini-via-OpenRouter were caching by image-hash + prompt, four resolutions
of the same image would always return the same answer because OpenRouter sees
4 different cache keys but might dedupe somehow.

Better check: temperature=0.7. If two back-to-back calls at the SAME resolution
give DIFFERENT answers, there's no cache. Then if 4 resolutions ALSO give
non-identical answers, we'd be measuring real fidelity sensitivity. If they
STILL converge, fidelity truly doesn't matter.
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

PROBE = """Pokemon FireRed top-down screenshot, red grid = tile boundaries, player (red hat) at (0,0).
x: left=neg right=pos. y: south=neg north=pos.

Give single-tile (x,y) for: console (in front of player), bed (left side), stairs (going up).

Format exactly: console:(x,y) bed:(x,y) stairs:(x,y)"""


def load_api_key() -> str:
    env = PROJECT_ROOT / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("missing key")


def encode(src, w, h):
    buf = io.BytesIO()
    src.resize((w, h), Image.NEAREST).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), len(buf.getvalue())


def call(client, key, b64, *, temp):
    body = {
        "model": MODEL,
        "max_tokens": 100,
        "temperature": temp,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
                {"type": "text", "text": PROBE},
            ],
        }],
        "usage": {"include": True},
    }
    r = client.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body, timeout=120.0,
    )
    d = r.json()
    if r.status_code != 200:
        return {"error": d}
    return {
        "reply": d["choices"][0]["message"]["content"].strip(),
        "prompt_tokens": d["usage"]["prompt_tokens"],
    }


def main() -> int:
    src = Image.open(SAMPLE_IMAGE).convert("RGB")
    key = load_api_key()

    sizes = [(240, 160), (480, 320), (720, 480), (1440, 960)]
    print(f"Model: {MODEL}  temperature=0.7  same image, different resolutions\n")
    print("=" * 88)

    with httpx.Client() as client:
        # Control: same resolution, called twice. If outputs differ → no cache.
        b64, _ = encode(src, 720, 480)
        print("\nControl — 720x480 called twice (should DIFFER if no cache):")
        for i in range(2):
            r = call(client, key, b64, temp=0.7)
            print(f"  call {i+1}: {r.get('reply', r.get('error'))!r}")

        print("\nMain — 4 resolutions, single call each (do answers vary with size?):")
        results = []
        for w, h in sizes:
            b64, nbytes = encode(src, w, h)
            r = call(client, key, b64, temp=0.7)
            r["size"] = f"{w}x{h}"
            r["bytes"] = nbytes
            results.append(r)
            print(f"  {w}x{h:<5} ({nbytes:>6,}b, prompt={r.get('prompt_tokens')}): "
                  f"{r.get('reply', r.get('error'))!r}")

    out = PROJECT_ROOT / "test_scripts/test_resize_nocache_results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
