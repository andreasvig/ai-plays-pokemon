"""Load + validate the ROM registry — which games the harness can boot.

Sibling of :mod:`src.app.benchmarks`: same shape (a YAML registry under
``configs/``, a dataclass, ``load_*`` / ``default_*`` / ``get_*``), same "the
file is the source of truth, re-read on each call, no caching" contract.

The emulator holds exactly ONE ROM at a time, so a ROM is a property of the
*session* (which game mGBA has loaded) that a queued run also *declares* (which
game it needs). :class:`~src.app.supervisor.AppSupervisor` owns the former;
``QueuedRun.rom`` carries the latter, and the executor reconciles them.

Benchmark capability is DERIVED, never declared: a ROM can play a benchmark iff
some ladder's ``game:`` equals the ROM's. Emerald is casual-only today purely
because no ladder claims ``emerald-us`` — authoring one is the whole of what
turns it on, with no flag to remember to flip.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from src.config import CONFIGS_DIR

# The registry file. Injectable in tests. ``path`` entries are authored
# repo-root-relative (like benchmarks.yaml's ladders) and are handed to the
# emulator as written, so they resolve against the process CWD — the repo root
# for ``pokemon app`` / ``pokemon run``.
ROMS_FILE = CONFIGS_DIR / "roms.yaml"

# Where a GBA cartridge header keeps its 4-character game code ("BPRE" FireRed,
# "BPEE" Emerald). Readable over the Lua socket with the existing READMEM, which
# is how we check what mGBA ACTUALLY has loaded rather than what we launched it
# with. See ``AppSupervisor.verify_loaded_rom``.
GAME_CODE_ADDR = 0x080000AC
GAME_CODE_LEN = 4


@dataclass
class Rom:
    """One playable ROM.

    ``game`` is the join key to a ladder's ``game:``; ``game_name`` is what the
    agent prompts call it; ``game_code`` is the cartridge header code used to
    verify the loaded ROM; ``start_save`` is an optional committed savepoint dir
    that casual runs on this ROM start from (``None`` → boot from the title
    screen). ``is_default`` marks the ROM the app boots with.
    """

    id: str
    name: str
    path: str
    game: str
    game_name: str
    game_code: str
    sha1: str
    start_save: Optional[str] = None
    is_default: bool = False

    def to_dict(self, *, benchmark_ok: bool = False) -> dict[str, Any]:
        """Flat JSON shape for ``GET /api/roms`` (the new-run dialog's picker).

        ``benchmark_ok`` is passed in rather than computed here so one ladder
        read serves the whole list (see :func:`list_roms`).
        """
        return {
            "id": self.id,
            "name": self.name,
            "game": self.game,
            "game_name": self.game_name,
            "default": self.is_default,
            "benchmark_ok": benchmark_ok,
            "has_start_save": self.start_save is not None,
        }

    def exists(self) -> bool:
        """True when the ROM file is actually on disk (``roms/`` is gitignored)."""
        return Path(self.path).exists()


def _rom_from_entry(entry: Any, index: int, registry_name: str) -> Rom:
    """Validate one ``roms:`` list entry into a :class:`Rom`."""
    if not isinstance(entry, dict):
        raise ValueError(
            f"{registry_name}: rom #{index} must be a mapping, "
            f"got {type(entry).__name__}"
        )
    for field in ("id", "name", "path", "game", "game_name", "game_code", "sha1"):
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{registry_name}: rom #{index} missing or invalid {field!r}"
            )
    start_save = entry.get("start_save")
    if start_save is not None and (not isinstance(start_save, str) or not start_save):
        raise ValueError(
            f"{registry_name}: rom {entry['id']!r} has an invalid 'start_save'"
        )
    return Rom(
        id=entry["id"],
        name=entry["name"],
        path=entry["path"],
        game=entry["game"],
        game_name=entry["game_name"],
        game_code=entry["game_code"],
        sha1=entry["sha1"],
        start_save=start_save,
        is_default=bool(entry.get("default", False)),
    )


def load_roms(path: Union[str, Path, None] = None) -> list[Rom]:
    """Load + validate the ordered ROM registry.

    Validation (raises ``ValueError`` on violation):
      - top level is a mapping with a non-empty ``roms`` list;
      - every entry has non-empty string ``id``/``name``/``path``/``game``/
        ``game_name``/``game_code``/``sha1``;
      - ``id`` values are unique;
      - exactly one entry sets ``default: true`` — the app has to boot SOMETHING,
        so unlike benchmarks (where zero means "first one wins") an absent
        default is an error rather than a silent pick.

    Order is preserved as written. Missing ROM *files* are NOT an error here:
    ``roms/`` is gitignored, so a fresh clone legitimately has entries whose
    files are absent. Callers that need the file check :meth:`Rom.exists`.
    """
    registry_path = Path(path) if path is not None else ROMS_FILE
    if not registry_path.exists():
        raise FileNotFoundError(f"ROM registry not found: {registry_path}")

    with open(registry_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"{registry_path.name}: top level must be a mapping, "
            f"got {type(data).__name__}"
        )

    raw = data.get("roms")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{registry_path.name}: 'roms' must be a non-empty list")

    out: list[Rom] = []
    seen_ids: set[str] = set()
    defaults = 0
    for i, entry in enumerate(raw):
        rom = _rom_from_entry(entry, i, registry_path.name)
        if rom.id in seen_ids:
            raise ValueError(f"{registry_path.name}: duplicate rom id {rom.id!r}")
        seen_ids.add(rom.id)
        defaults += int(rom.is_default)
        out.append(rom)

    if defaults != 1:
        raise ValueError(
            f"{registry_path.name}: exactly one rom must set 'default: true' "
            f"(found {defaults})"
        )

    return out


def default_rom(path: Union[str, Path, None] = None) -> Rom:
    """The ROM marked ``default: true`` — what the app boots with."""
    for rom in load_roms(path):
        if rom.is_default:
            return rom
    # load_roms guarantees exactly one default; this is unreachable in practice.
    raise ValueError("ROM registry has no default rom")


def get_rom(rom_id: Optional[str], path: Union[str, Path, None] = None) -> Rom:
    """Look up a ROM by ``id``; ``None``/unknown falls back to the default.

    Same forgiving contract as ``benchmarks.get_benchmark``: a stale queue item
    naming a removed ROM still runs (on the default) rather than wedging the
    drain. Callers that need strict validation — the enqueue API — check
    membership against :func:`load_roms` first.
    """
    roms = load_roms(path)
    if rom_id is not None:
        for rom in roms:
            if rom.id == rom_id:
                return rom
    for rom in roms:
        if rom.is_default:
            return rom
    return roms[0]


def rom_for_path(
    rom_path: Optional[str], path: Union[str, Path, None] = None
) -> Optional[Rom]:
    """The registry entry whose ``path`` is this one, or ``None`` if off-registry.

    Compared as resolved filesystem paths, so the repo-root-relative form in the
    registry matches an absolute one that came back from a run config. Used to
    put a NAME on whatever the supervisor happens to be holding; an off-registry
    ROM is a legitimate answer (a hand-rolled config), not an error.
    """
    if not rom_path:
        return None
    target = Path(rom_path).resolve()
    for rom in load_roms(path):
        if Path(rom.path).resolve() == target:
            return rom
    return None


def rom_for_game(
    game: Optional[str], path: Union[str, Path, None] = None
) -> Rom:
    """The ROM that plays ``game`` (e.g. a benchmark ladder's ``game:``).

    Falls back to the default ROM when nothing declares that game — the same
    forgiving contract as :func:`get_rom`, so a ladder naming a game with no dump
    on the registry still dispatches rather than wedging the drain.
    """
    roms = load_roms(path)
    if game:
        for rom in roms:
            if rom.game == game:
                return rom
    for rom in roms:
        if rom.is_default:
            return rom
    return roms[0]


def benchmark_games(benchmarks_path: Union[str, Path, None] = None) -> set[str]:
    """The set of ``game`` values some benchmark ladder can score.

    A ROM whose ``game`` is in this set can run benchmarks; every other ROM is
    casual-only. Derived from the benchmark registry on each call so authoring an
    Emerald ladder is the only step needed to enable Emerald benchmarks.
    """
    from src.app.benchmarks import load_benchmarks

    return {b.game for b in load_benchmarks(benchmarks_path) if b.game}


def list_roms(
    path: Union[str, Path, None] = None,
    benchmarks_path: Union[str, Path, None] = None,
) -> list[dict[str, Any]]:
    """The registry as JSON rows for ``GET /api/roms``, benchmark flag included.

    One benchmark-registry read for the whole list. A benchmark registry that
    fails to load is NOT fatal — the ROM picker still works, with every ROM
    marked casual-only, because being unable to score is a lesser failure than
    being unable to pick a game at all.
    """
    try:
        games = benchmark_games(benchmarks_path)
    except (FileNotFoundError, ValueError):
        games = set()
    return [r.to_dict(benchmark_ok=r.game in games) for r in load_roms(path)]


def rom_supports_benchmarks(
    rom: Rom, benchmarks_path: Union[str, Path, None] = None
) -> bool:
    """True when some benchmark ladder is authored for this ROM's game."""
    return rom.game in benchmark_games(benchmarks_path)


def validate_rom(
    rom_id: Optional[str], path: Union[str, Path, None] = None
) -> Optional[str]:
    """Strict check for the enqueue API: return the id, or raise ``ValueError``.

    ``None``/``""`` → ``None`` (meaning "the default ROM"), so a client that
    doesn't know about ROMs at all keeps working unchanged.
    """
    if rom_id is None or rom_id == "":
        return None
    if not isinstance(rom_id, str):
        raise ValueError(f"rom must be a string id, got {type(rom_id).__name__}")
    known = [r.id for r in load_roms(path)]
    if rom_id not in known:
        raise ValueError(f"unknown rom {rom_id!r}; known: {', '.join(known)}")
    return rom_id


def apply_rom(cfg: dict, rom: Rom) -> None:
    """Point a run config at ``rom``, in place. The ONE place this wiring lives.

    Three sinks, because three different layers consume the choice:
      - ``emulator.rom_path`` — what mGBA is launched with.
      - top-level ``game_name`` — read by the prompt builders at construction
        time (``agent.build_agent``, ``task_master.create_task_master_agent``,
        the research tool).
      - every ``{{game_name}}`` placeholder in the run's authored text, resolved
        HERE (see :func:`fill_game_name`) rather than left to each consumer.

    Leaving any of them behind tells a model playing Emerald that it is playing
    FireRed — which is worse than saying nothing, because it will act on it.

    Shared by the executor and the CLI so the two entry points cannot drift.
    """
    emulator = cfg.get("emulator")
    if not isinstance(emulator, dict):
        emulator = {}
        cfg["emulator"] = emulator
    emulator["rom_path"] = rom.path
    cfg["game_name"] = rom.game_name
    fill_game_name(cfg, rom.game_name)


# Where a config may name the game it is playing. The task text is the important
# one and the reason this exists: the prompts are filled by their builders
# (which are handed ``game_name``), but the GOAL is passed through as a value —
# so a placeholder inside it would survive substitution and reach the model raw.
_GAME_NAME_FIELDS = (
    ("task", "goal"),
    ("task", "description"),
    (None, "system_prompt"),
    ("task_master", "system_prompt"),
)


def fill_game_name(cfg: dict, game_name: str) -> int:
    """Resolve ``{{game_name}}`` throughout a run config, in place.

    Returns how many fields changed — mostly so a test can prove it did anything.

    Done at config-build time, not at prompt-build time, so the resolved text is
    what gets logged, replayed and read back in a report: the run record says
    which game the model was actually told it was playing, rather than a template
    that has to be re-rendered to be understood.
    """
    from src.core.prompts import fill_prompt

    changed = 0
    for section, key in _GAME_NAME_FIELDS:
        holder = cfg if section is None else cfg.get(section)
        if not isinstance(holder, dict):
            continue
        value = holder.get(key)
        if not isinstance(value, str) or "{{game_name}}" not in value:
            continue
        holder[key] = fill_prompt(value, game_name=game_name)
        changed += 1
    return changed
