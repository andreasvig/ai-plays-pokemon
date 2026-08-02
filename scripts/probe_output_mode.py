"""Is each model in the RIGHT output mode? Vary only that axis and measure.

``configs/models.yaml`` gives every entry an ``output_mode`` (tool /
native_json / prompted), and the choice is load-bearing: on Anthropic routes a
forced ``tool_choice`` silently strips extended thinking, so a model in the
wrong mode is benchmarked as a reasoning model while doing none. Until
2026-08-02 nothing measured this — ``probe_registry_candidates.py`` varies the
effort TIER and always sends the same (tool-less) request shape, so the one
axis that decides ``output_mode`` was never exercised.

This sends each model the identical prompt, image and reasoning block twice:

  text  — no tools at all.            Mimics PromptedOutput / NativeOutput.
  tool  — one ``final_result`` tool,  Mimics pydantic-ai's tool mode, which
          ``tool_choice="required"``. sends exactly this when the agent has no
                                      other tools and no text output is allowed
                                      (``pydantic_ai/models/openai.py:387-392``).

Read the two arms together:

  both arms reason            -> ``tool`` is safe (the default).
  text reasons, tool returns 0-> the model needs ``output_mode: prompted``.
                                 Every Anthropic entry is here.
  tool arm 4xx                -> ``prompted`` is mandatory, not a preference
                                 (qwen3.7-plus rejects forced tool_choice while
                                 thinking).

Caveats learned the hard way, both worth keeping in mind before acting on a row:

* OpenRouter's top-level ``supported_parameters`` is a UNION over endpoints,
  not a guarantee — 3 of gemma-4-31b's 18 endpoints expose no tools at all. A
  model can advertise a capability the endpoint you actually get does not have,
  which is why ``inkling`` needs prompted mode despite advertising tools.
* Reasoning-token counts are provider-reported and some providers do not report
  them: step-3.7-flash returns 0 from Novita and StepFun and ~2600 from
  DeepInfra for identical output. Check the ``served`` column before reading a
  0 as "did not think".
* n=1 per arm. A single row is a hint; repeat before editing the registry.

    ./venv/bin/python scripts/probe_output_mode.py
    ./venv/bin/python scripts/probe_output_mode.py --model anthropic/claude-opus-5
    ./venv/bin/python scripts/probe_output_mode.py --all-registry
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not KEY:
    sys.exit("OPENROUTER_API_KEY not set")

URL = "https://openrouter.ai/api/v1/chat/completions"


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


PNG_B64 = base64.standard_b64encode(_find_screenshot().read_bytes()).decode()

# Hard enough that an ADAPTIVE thinker chooses to think. This matters more than
# it looks: the first version of this prompt (route only, "explain your
# reasoning") left claude-sonnet-5 returning 0 reasoning tokens in BOTH arms,
# which reads as "suppression everywhere" but is really the model deciding an
# easy question needs no thought — and it silently destroys the known positive
# this whole probe is calibrated against. The proof obligation and the timing
# sub-question are what make it bite.
PROMPT = (
    "You are playing Pokemon FireRed from this screenshot. A grid-based game screen "
    "shows a character at the centre tile. A door is 3 tiles right and 2 tiles up, but "
    "a wall runs along the entire row directly above the character except for a gap 4 "
    "tiles to the right. There is also a ledge you can only hop down, 2 tiles left. "
    "Give the shortest button sequence (up/down/left/right) that reaches the door, "
    "prove that it is the shortest, and say what happens if you mistime the ledge."
)

# Mirrors the real GameAction output tool the harness registers, so the tool arm
# is the same shape the Player actually sends.
TOOL = {
    "type": "function",
    "function": {
        "name": "final_result",
        "description": "Output: button presses, reasoning, success grade, memory updates.",
        "parameters": {
            "type": "object",
            "properties": {
                "inputs": {"type": "array", "items": {"type": "string"},
                           "description": "The buttons to press this turn."},
                "reasoning": {"type": "string", "description": "Your reasoning for this turn."},
                "last_turn_succeeded": {"type": ["boolean", "null"],
                                        "description": "Your grade of the previous turn."},
                "memory_updates": {"type": "string", "description": "JSON object or \"none\"."},
            },
            "required": ["inputs", "reasoning", "last_turn_succeeded", "memory_updates"],
        },
    },
}

# Default sweep: the three known positives that prove the probe still bites,
# then the entries whose mode has never been measured on this axis. Keep the
# positives first — a run where they do not reproduce is measuring nothing.
DEFAULT_CASES = [
    # label,            openrouter_id,                tier,       style,    expectation
    ("claude-sonnet-5",  "anthropic/claude-sonnet-5",  "high",     "effort",
     "KNOWN POSITIVE: tool arm must return 0 reasoning"),
    ("gemini-3.5-flash", "google/gemini-3.5-flash",    "high",     "effort",
     "KNOWN POSITIVE: both arms must reason"),
    ("qwen3.7-plus",     "qwen/qwen3.7-plus",          "thinking", "binary",
     "KNOWN POSITIVE: tool arm must 400"),
    ("kimi-k2.7-code",   "moonshotai/kimi-k2.7-code",  "thinking", "binary", ""),
    ("kimi-k2.6",        "moonshotai/kimi-k2.6",       "thinking", "binary", ""),
    ("kimi-k3",          "moonshotai/kimi-k3",         "high",     "effort", ""),
    ("minimax-m3",       "minimax/minimax-m3",         "thinking", "binary", ""),
    ("mimo-v2.5",        "xiaomi/mimo-v2.5",           "thinking", "binary", ""),
    ("step-3.7-flash",   "stepfun/step-3.7-flash",     "high",     "effort", ""),
    ("grok-4.5",         "x-ai/grok-4.5",              "high",     "effort", ""),
    ("grok-build-0.1",   "x-ai/grok-build-0.1",        "high",     "effort", ""),
    ("gpt-5.6-sol",      "openai/gpt-5.6-sol",         "medium",   "effort", ""),
    ("gpt-5.5",          "openai/gpt-5.5",             "high",     "effort", ""),
    ("inkling",          "thinkingmachines/inkling",   "high",     "effort", ""),
    ("gemma-4-31b",      "google/gemma-4-31b-it",      "thinking", "binary", ""),
]


def _registry_cases() -> list[tuple]:
    """Every registry entry at its default (highest) thinking level."""
    reg = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text())
    cases = []
    for alias, entry in reg.items():
        levels = entry.get("thinking_levels") or []
        rtype = entry.get("reasoning_type", "none")
        tier = levels[0] if levels else ""
        cases.append((alias, entry["openrouter_id"], tier, rtype, ""))
    return cases


def probe(model_id: str, tier: str, style: str, arm: str, max_tokens: int) -> dict:
    payload: dict = {
        "model": model_id,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
        ]}],
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if style == "effort" and tier:
        payload["reasoning"] = {"effort": tier}
    elif style == "binary":
        payload["reasoning"] = {"enabled": tier == "thinking"}

    if arm == "tool":
        payload["tools"] = [TOOL]
        payload["tool_choice"] = "required"

    t0 = time.time()
    try:
        r = requests.post(
            URL,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json=payload, timeout=240,
        )
    except requests.RequestException as e:
        return {"status": "EXC", "error": str(e)[:120], "latency_s": round(time.time() - t0, 1)}
    latency = round(time.time() - t0, 1)

    out: dict = {"status": r.status_code, "latency_s": latency}
    try:
        body = r.json()
    except ValueError:
        out["error"] = r.text[:160]
        return out

    if r.status_code != 200:
        err = body.get("error") or {}
        meta = err.get("metadata") or {}
        out["error"] = str(err.get("message") or body)[:120]
        out["upstream"] = str(meta.get("raw") or "")[:240]
        return out

    usage = body.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    msg = ((body.get("choices") or [{}])[0].get("message")) or {}
    # Coerce rather than default: some providers send reasoning_tokens: null.
    out["reasoning_tokens"] = details.get("reasoning_tokens") or 0
    # The count and the human-readable summary come apart — gpt-5.6-sol has
    # returned 183 reasoning tokens with zero summary characters — so record
    # both and never infer one from the other.
    out["reasoning_chars"] = len(msg.get("reasoning") or "")
    out["out_tokens"] = usage.get("completion_tokens") or 0
    out["cost_usd"] = usage.get("cost")
    out["provider"] = body.get("provider") or "?"
    out["called_tool"] = bool(msg.get("tool_calls"))
    return out


def _fmt(arm: dict) -> str:
    if arm["status"] != 200:
        return f"{arm['status']} {arm.get('error', '')[:60]}"
    return (
        f"reasoning={arm['reasoning_tokens']:<6} chars={arm['reasoning_chars']:<6} "
        f"out={arm['out_tokens']:<5} {arm['latency_s']:>5}s  {arm['provider']}"
    )


def _verdict(text: dict, tool: dict, registry_mode: str) -> str:
    if tool["status"] != 200:
        return "prompted REQUIRED (tool arm errored)" if registry_mode == "prompted" \
            else "*** tool arm ERRORED but registry says tool ***"
    t_reasons = text.get("reasoning_tokens", 0) > 0 or text.get("reasoning_chars", 0) > 0
    o_reasons = tool.get("reasoning_tokens", 0) > 0 or tool.get("reasoning_chars", 0) > 0
    if t_reasons and not o_reasons:
        return "SUPPRESSED under tool_choice -> needs prompted" if registry_mode != "prompted" \
            else "suppression confirmed; prompted is correct"
    if not t_reasons and not o_reasons:
        return "no reasoning in EITHER arm (check tier / provider)"
    return "tool mode safe" if registry_mode == "tool" else "tool mode would also work"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Measure whether forced tool_choice changes a model's reasoning."
    )
    ap.add_argument("--model", help="probe only this openrouter id")
    ap.add_argument("--all-registry", action="store_true",
                    help="sweep every configs/models.yaml entry at its default level")
    ap.add_argument("--max-tokens", type=int, default=1500)
    args = ap.parse_args()

    cases = _registry_cases() if args.all_registry else DEFAULT_CASES
    if args.model:
        cases = [c for c in cases if c[1] == args.model]
        if not cases:
            sys.exit(f"no case matches {args.model!r}")

    reg = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text())
    by_oid = {v["openrouter_id"]: (k, v) for k, v in reg.items()}

    results: dict[str, dict] = {}
    total = 0.0
    for label, model_id, tier, style, expectation in cases:
        entry = (by_oid.get(model_id) or (None, {}))[1]
        mode = entry.get("output_mode") or "tool"
        header = f"\n=== {label} ({tier})   registry output_mode = {mode}"
        print(header + (f"   [{expectation}]" if expectation else ""))
        arms = {}
        for arm in ("text", "tool"):
            res = probe(model_id, tier, style, arm, args.max_tokens)
            arms[arm] = res
            total += float(res.get("cost_usd") or 0)
            print(f"   {arm:<5}: {_fmt(res)}")
            sys.stdout.flush()
        verdict = _verdict(arms["text"], arms["tool"], mode)
        print(f"   -> {verdict}")
        results[label] = {"registry_mode": mode, "tier": tier, "verdict": verdict, **arms}

    print(f"\n{'=' * 64}\nTOTAL PROBE COST: ${total:.4f}")
    out_path = ROOT / "local" / "output-mode-probe.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"raw results: {out_path}")


if __name__ == "__main__":
    main()
