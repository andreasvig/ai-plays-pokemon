"""Load + validate the PokeBench benchmarks.

A *benchmark* is a SELF-CONTAINED ladder file: each
``configs/checkpoints-firered-*.yaml`` carries a top-level ``benchmark:`` block
(id, name, goal, default) alongside its gate ladder. The overall ``goal`` is the
meta-goal the agent plays toward (shown in the UI); the executor injects the
ladder (enforced) and overrides the frozen config's ``task.goal`` with it. The
leaderboard + history views filter by benchmark ``id`` so each has its own
ranking.

``configs/benchmarks.yaml`` is only a MANIFEST — an ordered list of the ladder
file paths (display order). This module reads the manifest, then pulls each
benchmark's identity + goal from its own ladder file. Pure reads, no caching —
the files are the source of truth and re-read on each call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from src.config import CONFIGS_DIR

# The manifest file. Injectable in tests. Its ``ladders`` entries are authored as
# repo-root-relative strings — e.g. "configs/checkpoints-firered-easy.yaml" —
# matching the executor's OFFICIAL_LADDER convention; they're loaded relative to
# the process CWD (the repo root for `pokemon app`/`pokemon run`). For reading
# the files here, they're resolved relative to the manifest's repo root
# (``manifest.parent.parent``) so an injected test manifest works the same way.
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
    # The ladder's own ``game:`` (e.g. "firered-us") — the join key that decides
    # which ROMs can play this benchmark (see ``src.app.roms.benchmark_games``).
    # Optional: a ladder that omits it simply matches no ROM.
    game: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Flat JSON shape for ``GET /api/benchmarks`` (the SPA's picker)."""
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "ladder": self.ladder,
            "default": self.is_default,
            "game": self.game,
        }


def _benchmark_from_ladder(ladder_entry: str, ladder_file: Path) -> Benchmark:
    """Read the ``benchmark:`` block out of one ladder file into a Benchmark.

    ``ladder_entry`` is the path as authored in the manifest (kept verbatim on
    the Benchmark so the executor injects the same repo-root-relative string);
    ``ladder_file`` is the resolved path actually opened here. Only the
    ``benchmark:`` block is read — the gate ladder itself is validated later by
    ``src.referee.checkpoints.load_ladder`` at run time.
    """
    if not ladder_file.exists():
        raise FileNotFoundError(
            f"benchmarks.yaml: ladder file not found: {ladder_entry} "
            f"(resolved to {ladder_file})"
        )
    with open(ladder_file) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"{ladder_file.name}: top level must be a mapping, "
            f"got {type(data).__name__}"
        )
    block = data.get("benchmark")
    if not isinstance(block, dict):
        raise ValueError(
            f"{ladder_file.name}: missing or invalid 'benchmark' block"
        )
    for field in ("id", "name", "goal"):
        value = block.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{ladder_file.name}: benchmark block missing or invalid {field!r}"
            )
    game = data.get("game")
    return Benchmark(
        id=block["id"],
        name=block["name"],
        goal=block["goal"],
        ladder=ladder_entry,
        is_default=bool(block.get("default", False)),
        # Top-level, not inside the benchmark block: `game` belongs to the LADDER
        # (it is what the gate addresses are valid for). Read leniently — the
        # strict requirement lives in ``referee.checkpoints.load_ladder``, which
        # every real ladder goes through at run time.
        game=game if isinstance(game, str) else "",
    )


def load_benchmarks(path: Union[str, Path, None] = None) -> list[Benchmark]:
    """Load + validate the ordered benchmarks named by the manifest YAML.

    The manifest (``path``, default ``BENCHMARKS_FILE``) lists ladder file paths;
    each benchmark's identity + goal is read from its ladder file's ``benchmark:``
    block.

    Validation (raises ``ValueError`` on violation):
      - manifest top level is a mapping with a non-empty ``ladders`` list of strings;
      - each ladder file exists and has a ``benchmark:`` mapping with a non-empty
        string ``id``, ``name``, ``goal``;
      - ``id`` values are unique across the manifest;
      - at most one benchmark sets ``default: true``.

    Order is preserved as written in the manifest.
    """
    manifest_path = Path(path) if path is not None else BENCHMARKS_FILE
    if not manifest_path.exists():
        raise FileNotFoundError(f"Benchmark manifest not found: {manifest_path}")

    with open(manifest_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"{manifest_path.name}: top level must be a mapping, "
            f"got {type(data).__name__}"
        )

    raw = data.get("ladders")
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"{manifest_path.name}: 'ladders' must be a non-empty list"
        )

    # Resolve ladder paths relative to the manifest's repo root, so the committed
    # registry (configs/benchmarks.yaml → repo root) and an injected test manifest
    # (tmp/configs/benchmarks.yaml → tmp) both resolve their sibling ladders.
    repo_root = manifest_path.resolve().parent.parent

    out: list[Benchmark] = []
    seen_ids: set[str] = set()
    default_count = 0
    for i, entry in enumerate(raw):
        if not isinstance(entry, str) or not entry:
            raise ValueError(
                f"{manifest_path.name}: ladder #{i} must be a non-empty string path"
            )
        ladder_file = Path(entry)
        if not ladder_file.is_absolute():
            ladder_file = repo_root / entry
        bench = _benchmark_from_ladder(entry, ladder_file)
        if bench.id in seen_ids:
            raise ValueError(
                f"{manifest_path.name}: duplicate benchmark id {bench.id!r}"
            )
        seen_ids.add(bench.id)
        if bench.is_default:
            default_count += 1
        out.append(bench)

    if default_count > 1:
        raise ValueError(
            f"{manifest_path.name}: at most one benchmark may set 'default: true' "
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
