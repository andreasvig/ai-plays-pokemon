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

from src.config import (
    CONFIGS_DIR,
    _load_models_registry,
    model_default_level,
    model_thinking_levels,
)


def _observed_pair(observed_raw: Any) -> dict[str, Any] | None:
    """``{avg_turn_cost_usd, avg_turn_latency_s}`` when both are present, else None."""
    if isinstance(observed_raw, dict):
        cost = observed_raw.get("avg_turn_cost_usd")
        latency = observed_raw.get("avg_turn_latency_s")
        if cost is not None and latency is not None:
            return {"avg_turn_cost_usd": cost, "avg_turn_latency_s": latency}
    return None


def list_models() -> list[dict[str, Any]]:
    """Project the collapsed ``models.yaml`` into the picker shape (one row/model).

    Each row carries the model name, its OpenRouter id, ``reasoning_type``, the
    ordered ``levels`` (each with its own ``observed`` telemetry), and the
    ``default_level`` (highest). The run identity submitted by the picker is
    ``model(level)`` — so each level is still benchmarked separately — except for
    ``reasoning_type: none`` models (e.g. grok-4.3) which have no levels and submit
    the bare model name. ``observed`` at the top level mirrors the default level so
    the picker can show a headline cost/latency.
    """
    registry = _load_models_registry()
    out: list[dict[str, Any]] = []
    for base in sorted(registry):
        entry = registry.get(base)
        if not isinstance(entry, dict):
            continue
        observed_map = entry.get("observed") or {}
        levels = model_thinking_levels(entry)
        level_rows = [
            {"level": lvl, "observed": _observed_pair(observed_map.get(lvl))}
            for lvl in levels
        ]
        default_level = model_default_level(entry)
        # Headline observed = the default level's, or the lone "_" block (type none).
        if level_rows:
            headline = level_rows[0]["observed"]
        else:
            headline = _observed_pair(observed_map.get("_"))
        out.append(
            {
                "model": base,
                "openrouter_id": entry.get("openrouter_id"),
                "reasoning_type": entry.get("reasoning_type", "none"),
                "default_level": default_level,
                "levels": level_rows,
                "observed": headline,
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
