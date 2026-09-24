"""Snapshot the list price of a model that was played under a CLOAKED listing.

Usage:
    python scripts/snapshot_revealed_pricing.py --run <run_dir> --model <slug>

OpenRouter lists an unannounced model under a `stealth/*` slug and serves it
FREE while it is in evaluation; when the lab announces it, the stealth slug is
retired and the same weights appear under a real name with a real price. A run
played during that window has a true bill of $0 and a true endpoint price of
$0/$0 — and `conversation/endpoint-pricing.json` records exactly that. Both
stay as they are: they are what happened.

What they cannot answer is "what would this run have cost", which is the only
form in which a free run can be compared with the rest of the board. This
writes the second half of that answer next to the first, as its own snapshot:

    conversation/endpoint-pricing-revealed.json

Same shape as the run-time file (so `_endpoint_price` reads either), plus
`revealed_from` — the slug the run actually used. That field is the identity
claim, written down where the projection can see it, rather than assumed from
the fact that two files sit in one directory.

The derived cost is computed at READ time from this price and the run's own
token counts (src/app/projection.py); nothing here writes a dollar figure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.provider_profiles import fetch_endpoint_pricing  # noqa: E402

FILENAME = "endpoint-pricing-revealed.json"


def _played_as(run_dir: Path) -> str | None:
    """The slug that actually went on the wire, from the run's own record."""
    try:
        summary = json.loads((run_dir / "run_summary.json").read_text())
    except (OSError, ValueError):
        return None
    return (summary.get("session") or {}).get("llm_model")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, type=Path, help="run directory")
    ap.add_argument("--model", required=True, help="the model's REVEALED OpenRouter slug")
    ap.add_argument("--force", action="store_true", help="overwrite an existing snapshot")
    args = ap.parse_args()

    run_dir: Path = args.run
    if not (run_dir / "run_summary.json").is_file():
        print(f"not a run directory: {run_dir}", file=sys.stderr)
        return 1
    target = run_dir / "conversation" / FILENAME
    if target.exists() and not args.force:
        print(f"already there (use --force): {target}", file=sys.stderr)
        return 1

    snapshot = asyncio.run(fetch_endpoint_pricing(args.model, os.environ.get("OPENROUTER_API_KEY")))
    if not snapshot.get("endpoints"):
        print(f"{args.model}: no endpoints listed — is the slug right?", file=sys.stderr)
        return 1
    snapshot["revealed_from"] = _played_as(run_dir)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {target}")
    for endpoint in snapshot["endpoints"]:
        pricing = endpoint.get("pricing") or {}
        print(f"  {endpoint.get('tag')}: prompt {pricing.get('prompt')} "
              f"completion {pricing.get('completion')} cache_read {pricing.get('input_cache_read')}")
    print(f"  played as: {snapshot['revealed_from']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
