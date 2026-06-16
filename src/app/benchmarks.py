"""Load + validate the PokeBench benchmark registry (``configs/benchmarks.yaml``).

A *benchmark* bundles an overall goal (the meta-goal the agent plays toward,
shown in the UI) with its own gate-ladder file. The executor uses it to drive an
official run: inject the benchmark's ladder (enforced) and override the frozen
config's ``task.goal`` with the benchmark's ``goal``. The leaderboard + history
views filter by benchmark ``id`` so each benchmark has its own ranking.

The ladders are separate, independently-editable YAML files under ``configs/``;
this module owns only the small registry that names them + their goals. Pure
reads, no caching — the file is the source of truth and re-read on each call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from src.config import CONFIGS_DIR

# The registry file. Injectable in tests. (Ladder paths inside it stay authored
# as repo-root-relative strings — e.g. "configs/checkpoints-firered-easy.yaml" —
# matching the executor's OFFICIAL_LADDER convention; they're loaded relative to
# the process CWD, which is the repo root for `pokemon app`/`pokemon run`.)
BENCHMARKS_FILE = CONFIGS_DIR / "benchmarks.yaml"


@dataclass
class Benchmark:
    """One benchmark: an overall goal + its own gate ladder.

    ``ladder`` is the path (as authored, relative to the repo root) to this
    benchmark's checkpoint YAML. ``is_default`` marks the benchmark pre-selected
    in the new-run dialog and shown first on the main page (exactly one).
    """

    id: str
    name: str
    goal: str
    ladder: str
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Flat JSON shape for ``GET /api/benchmarks`` (the SPA's picker)."""
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "ladder": self.ladder,
            "default": self.is_default,
        }


def load_benchmarks(path: Union[str, Path, None] = None) -> list[Benchmark]:
    """Load + validate the ordered list of benchmarks from the registry YAML.

    Validation (raises ``ValueError`` on violation):
      - top level is a mapping with a non-empty ``benchmarks`` list;
      - each entry has a non-empty string ``id``, ``name``, ``goal``, ``ladder``;
      - ``id`` values are unique;
      - at most one entry sets ``default: true``.

    Order is preserved as written. ``path`` defaults to ``BENCHMARKS_FILE``.
    """
    registry_path = Path(path) if path is not None else BENCHMARKS_FILE
    if not registry_path.exists():
        raise FileNotFoundError(f"Benchmark registry not found: {registry_path}")

    with open(registry_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"{registry_path.name}: top level must be a mapping, "
            f"got {type(data).__name__}"
        )

    raw = data.get("benchmarks")
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"{registry_path.name}: 'benchmarks' must be a non-empty list"
        )

    out: list[Benchmark] = []
    seen_ids: set[str] = set()
    default_count = 0
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{registry_path.name}: benchmark #{i} must be a mapping, "
                f"got {type(entry).__name__}"
            )
        for field in ("id", "name", "goal", "ladder"):
            value = entry.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"{registry_path.name}: benchmark #{i} missing or invalid "
                    f"{field!r}"
                )
        bid = entry["id"]
        if bid in seen_ids:
            raise ValueError(f"{registry_path.name}: duplicate benchmark id {bid!r}")
        seen_ids.add(bid)
        is_default = bool(entry.get("default", False))
        if is_default:
            default_count += 1
        out.append(
            Benchmark(
                id=bid,
                name=entry["name"],
                goal=entry["goal"],
                ladder=entry["ladder"],
                is_default=is_default,
            )
        )

    if default_count > 1:
        raise ValueError(
            f"{registry_path.name}: at most one benchmark may set 'default: true' "
            f"(found {default_count})"
        )

    return out


def default_benchmark(path: Union[str, Path, None] = None) -> Benchmark:
    """The benchmark marked ``default: true``, else the first one defined."""
    benchmarks = load_benchmarks(path)
    for b in benchmarks:
        if b.is_default:
            return b
    return benchmarks[0]


def get_benchmark(
    benchmark_id: Optional[str], path: Union[str, Path, None] = None
) -> Benchmark:
    """Look up a benchmark by ``id``; ``None``/unknown falls back to the default.

    Unknown ids fall back rather than raise so a stale queue item (a benchmark
    removed from the registry) still runs the default rather than wedging the
    drain. Callers that need strict validation (the enqueue API) check membership
    against :func:`load_benchmarks` first.
    """
    benchmarks = load_benchmarks(path)
    if benchmark_id is not None:
        for b in benchmarks:
            if b.id == benchmark_id:
                return b
    for b in benchmarks:
        if b.is_default:
            return b
    return benchmarks[0]
