"""Cheap multi-iteration OpenRouter probe — no agent loop, no mGBA.

Sends a small multimodal request to gemma-4-31b-it with provider.sort=throughput
N times in a row, logs the provider name + finish_reason + latency each call.
Used to find which provider returns the bad shape that crashes pydantic-ai.

    OPENROUTER_API_KEY=... ./venv/bin/python scripts/probe_throughput.py [N]

Default N=8. Skip providers via env: PROBE_IGNORE=Together,Chutes
"""

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

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
IGNORE = [s.strip() for s in os.environ.get("PROBE_IGNORE", "").split(",") if s.strip()]

KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not KEY:
    sys.exit("OPENROUTER_API_KEY not set")

# Tiny 1×1 transparent PNG so providers see a real image_url
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

payload = {
    "model": "google/gemma-4-31b-it",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "What color is the pixel in this image? Reply in one word."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
        ],
    }],
    "reasoning": {"enabled": True},
    "temperature": 0.3,
    "top_p": 0.95,
    "max_tokens": 64,
    "provider": {
        "sort": "throughput",
        **({"ignore": IGNORE} if IGNORE else {}),
    },
}

URL = "https://openrouter.ai/api/v1/chat/completions"
headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

print(f"Probing {N}× with provider.sort=throughput" + (f", ignore={IGNORE}" if IGNORE else ""))
print()
print(f"{'#':>3}  {'provider':25s} {'fr':>10s}  {'latency':>8s}  {'tok_in/out':>11s}  status")
print("-" * 90)

stats = {}
for i in range(1, N + 1):
    t0 = time.time()
    try:
        r = requests.post(URL, headers=headers, json=payload, timeout=120)
        body = r.json()
    except Exception as e:
        print(f"{i:>3}  HTTP error: {e}")
        continue
    dt = time.time() - t0
    provider = body.get("provider", "?")
    choices = body.get("choices") or []
    if not choices:
        # Wrapped-5xx shape: http 200 but no choices
        err = body.get("error", {})
        print(f"{i:>3}  {provider:25s} {'(no choices)':>10s}  {dt:>7.1f}s  {'-':>11s}  err={err.get('code')} {err.get('message', '')[:60]}")
        stats[provider] = stats.get(provider, {"ok": 0, "bad": 0, "lat": []})
        stats[provider]["bad"] += 1
        continue
    msg = choices[0].get("message") or {}
    fr = choices[0].get("finish_reason")
    content = msg.get("content") or ""
    usage = body.get("usage", {})
    tin = usage.get("prompt_tokens", "?")
    tout = usage.get("completion_tokens", "?")
    status = "OK" if fr in ("stop", "length") else f"BAD fr={fr!r}"
    print(f"{i:>3}  {provider:25s} {str(fr):>10s}  {dt:>7.1f}s  {str(tin)+'/'+str(tout):>11s}  {status}  | {content[:40]!r}")
    stats.setdefault(provider, {"ok": 0, "bad": 0, "lat": []})
    if fr in ("stop", "length"):
        stats[provider]["ok"] += 1
    else:
        stats[provider]["bad"] += 1
    stats[provider]["lat"].append(dt)

print()
print("Summary by provider:")
for p, s in sorted(stats.items()):
    lat = s["lat"]
    avg = sum(lat) / len(lat) if lat else 0
    print(f"  {p:25s}  ok={s['ok']}  bad={s['bad']}  avg_lat={avg:.1f}s")
