"""Small read-only catalog helpers for the control-center APIs (Plan §P4).

These back ``GET /api/models`` and ``GET /api/configs`` — they project the
on-disk ``configs/models.yaml`` and ``configs/config-*.yaml`` files into the flat
shapes the SPA's ``MODELS`` / ``CONFIGS`` exports consume. Pure reads, no caching,
no state: the files are the source of truth and re-read on each call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.config import CONFIGS_DIR, _load_models_registry


def list_models() -> list[dict[str, Any]]:
    """Project ``models.yaml`` into ``[{alias, openrouter_id, observed|None}, ...]``.

    ``observed`` is ``{avg_turn_cost_usd, avg_turn_latency_s}`` when BOTH numbers
    are recorded for the alias, else ``None`` — so entries lacking an ``observed``
    block (or carrying only ``sample_turns: 0``) yield ``observed: null`` rather
    than crashing the dialog. Only the two numeric fields the dialog uses are
    surfaced (the registry's ``last_updated`` date is intentionally dropped so the
    payload stays JSON-clean).
    """
    registry = _load_models_registry()
    out: list[dict[str, Any]] = []
    for alias in sorted(registry):
        entry = registry.get(alias)
        if not isinstance(entry, dict):
            continue
        observed_raw = entry.get("observed")
        observed: dict[str, Any] | None = None
        if isinstance(observed_raw, dict):
            cost = observed_raw.get("avg_turn_cost_usd")
            latency = observed_raw.get("avg_turn_latency_s")
            if cost is not None and latency is not None:
                observed = {
                    "avg_turn_cost_usd": cost,
                    "avg_turn_latency_s": latency,
                }
        out.append(
            {
                "alias": alias,
                "openrouter_id": entry.get("openrouter_id"),
                "observed": observed,
            }
        )
    return out


_CONFIG_STEM_RE = re.compile(r"^(config-\d+\.\d+)\.yaml$")


def list_configs(configs_dir: Path | None = None) -> list[str]:
    """Casual config stems discovered from ``configs/config-*.yaml`` (e.g. ``config-3.13``).

    Sorted by version (major, minor) so the dialog shows them in a stable order.
    ``configs_dir`` is injectable for tests; defaults to the repo ``configs/``.
    """
    base = Path(configs_dir) if configs_dir is not None else CONFIGS_DIR
    if not base.exists():
        return []
    stems: list[tuple[tuple[int, int], str]] = []
    for path in base.glob("config-*.yaml"):
        m = _CONFIG_STEM_RE.match(path.name)
        if not m:
            continue
        stem = m.group(1)
        major, minor = stem.split("config-")[1].split(".")
        stems.append(((int(major), int(minor)), stem))
    stems.sort(key=lambda t: t[0])
    return [stem for _, stem in stems]
