"""Backfill "did this turn start inside a battle?" from the per-turn screenshots.

Plan §4.2, ``artifacts/battle-and-movement-fidelity/plan.md`` (2026-09-14). The
screenshot ``screenshots/NNNNN_turn_T.png`` is the frame the model saw at the
START of turn T, so its battle/overworld state is exactly rule A's attribution
for turn T. The classifier is one deterministic feature: the share of dark-navy
pixels (the battle text box) in the bottom 28 % of the frame — above 0.15 is a
battle. Against the 610 savepoint labels of the 25 published runs it agrees on
596 (97.7 %); the misses are battle boundaries — a black transition frame at a
battle start, the Pokédex page after a catch — and Oak's Lab floor sat at 0.09
under the earlier 0.08 cut.

Calibration is built in: ``battle_backfill.json`` (scripts/backfill_battles.py)
holds the exact in-battle bit AFTER turn t for every savepoint turn t, which
labels screenshot t+1. Every run reports its agreement; a run below 0.95 is
printed with its disagreeing turns so a human can look.

Writes ``state_backfill.json`` in the run dir:

    {"source": "screenshot", "threshold": 0.15, "states": {"T": true|false, ...},
     "chain": [...], "calibration": {"labelled": n, "agree": n, "disagree": [[T, label, feature], ...]}}

Usage: venv/bin/python scripts/backfill_states.py [--runs-root local/runs] [run_id ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.backfill_battles import chain  # noqa: E402

THRESHOLD = 0.15
BAND_FROM = 0.72  # bottom 28 % of the frame
_SHOT = re.compile(r"^\d+_turn_(\d+)\.png$")


def navy_share(path: Path) -> float:
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    band = a[int(a.shape[0] * BAND_FROM):, :, :]
    r, g, b = band[..., 0], band[..., 1], band[..., 2]
    return float(((b > r + 20) & (b > g + 10) & (b < 120)).mean())


def screenshots(run_dir: Path) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for p in (run_dir / "screenshots").glob("*.png"):
        m = _SHOT.match(p.name)
        if m:
            out[int(m.group(1))] = p  # a retaken turn keeps the later file (sorted below)
    return out


def classify_run(run_dir: Path, runs_root: Path) -> dict | None:
    states: dict[int, bool] = {}
    features: dict[int, float] = {}
    used = []
    for d, upto in chain(run_dir, runs_root):
        shots = screenshots(d)
        for t in sorted(shots):
            if upto is not None and t > upto:
                continue
            try:
                f = navy_share(shots[t])
            except OSError:
                continue
            features[t] = f
            states[t] = f > THRESHOLD
        used.append(d.name)
    if not states:
        return None
    calibration = {"labelled": 0, "agree": 0, "disagree": []}
    bf = run_dir / "battle_backfill.json"
    if bf.is_file():
        try:
            records = json.loads(bf.read_text()).get("records") or []
        except json.JSONDecodeError:
            records = []
        for rec in records:
            t, in_battle = int(rec[0]), bool(rec[1])
            if (t + 1) in states:
                calibration["labelled"] += 1
                if states[t + 1] == in_battle:
                    calibration["agree"] += 1
                else:
                    calibration["disagree"].append([t + 1, in_battle, round(features[t + 1], 3)])
    return {
        "source": "screenshot",
        "threshold": THRESHOLD,
        "chain": used,
        "states": {str(t): v for t, v in sorted(states.items())},
        "calibration": calibration,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--runs-root", default=str(REPO_ROOT / "local" / "runs"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.runs_root)
    dirs = [root / r for r in args.run_ids] if args.run_ids else sorted(d for d in root.iterdir() if (d / "screenshots").is_dir())
    for d in dirs:
        result = classify_run(d, root)
        if result is None:
            print(f"skip  {d.name}: no screenshots")
            continue
        c = result["calibration"]
        n_battle = sum(result["states"].values())
        rate = c["agree"] / c["labelled"] if c["labelled"] else None
        flag = "" if rate is None or rate >= 0.95 else f"  <-- LOOK: {c['disagree']}"
        print(f"ok    {d.name[:70]:70} turns={len(result['states']):4} battle_start={n_battle:4} "
              f"calibration={c['agree']}/{c['labelled']}{flag}")
        if not args.dry_run:
            (d / "state_backfill.json").write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
