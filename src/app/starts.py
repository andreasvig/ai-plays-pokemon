"""Load + validate the start-state registry — the openings a casual run may pick.

Sibling of :mod:`src.app.roms` and :mod:`src.app.benchmarks`: a YAML registry
under ``configs/``, a dataclass, ``load_*`` / ``default_*`` / ``get_*``, and the
same "the file is the source of truth, re-read on each call, no caching"
contract.

Why this exists separately from ``Rom.start_save``: a ROM has exactly one
*default* opening, but FireRed can legitimately be started as either protagonist
from the same point in the game. Encoding that as a second ROM entry would be
wrong — same cartridge, same sha1, same game code, same ladder eligibility — so
the choosable openings get their own registry keyed on ``(rom, label)``.

Official runs never consult this module. A benchmark starts from
``executor.CANONICAL_SAVE`` so that every score is comparable; a choosable
opening is a casual-only affordance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from src.config import CONFIGS_DIR

# The registry file. Injectable in tests, like ROMS_FILE.
STARTS_FILE = CONFIGS_DIR / "starts.yaml"

# The three files a resumable savepoint dir must carry. Kept in sync with what
# SnapshotManager writes and what the executor hands the run loop; `preview.png`
# is optional decoration and deliberately not required.
REQUIRED_FILES = ("emulator.state", "state.json", "tasks.json")


@dataclass
class Start:
    """One choosable opening for one ROM.

    ``label`` is what the user passes (``--start girl``) and is unique only
    *within* a ROM. ``path`` is a committed savepoint dir. ``is_default`` marks
    the label a casual run on this ROM gets when it names none.
    """

    rom: str
    label: str
    name: str
    path: str
    description: str = ""
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Flat JSON shape for ``GET /api/starts`` (the new-run dialog's picker)."""
        return {
            "rom": self.rom,
            "label": self.label,
            "name": self.name,
            "description": self.description,
            "default": self.is_default,
            "exists": self.exists(),
        }

    def exists(self) -> bool:
        """True when the savepoint dir is on disk AND complete.

        A dir missing one of :data:`REQUIRED_FILES` would fail at dispatch, deep
        inside a run, so the picker and the enqueue validator both surface it
        here instead.
        """
        base = Path(self.path)
        return base.is_dir() and all((base / f).exists() for f in REQUIRED_FILES)


def _start_from_entry(entry: Any, index: int, registry_name: str) -> Start:
    """Validate one ``starts:`` list entry into a :class:`Start`."""
    if not isinstance(entry, dict):
        raise ValueError(
            f"{registry_name}: start #{index} must be a mapping, "
            f"got {type(entry).__name__}"
        )
    for field in ("rom", "label", "name", "path"):
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{registry_name}: start #{index} missing or invalid {field!r}"
            )
    description = entry.get("description", "")
    if not isinstance(description, str):
        raise ValueError(
            f"{registry_name}: start {entry['label']!r} has a non-string 'description'"
        )
    return Start(
        rom=entry["rom"],
        label=entry["label"],
        name=entry["name"],
        path=entry["path"],
        description=description,
        is_default=bool(entry.get("default", False)),
    )


def load_starts(path: Union[str, Path, None] = None) -> list[Start]:
    """Load + validate the ordered start registry.

    Validation (raises ``ValueError`` on violation):
      - top level is a mapping with a ``starts`` list (an EMPTY list is legal —
        a deployment that offers no choosable openings is not broken, it just
        falls back to each ROM's own default);
      - every entry has non-empty string ``rom``/``label``/``name``/``path``;
      - ``(rom, label)`` pairs are unique;
      - at most one ``default: true`` per rom.

    Missing savepoint *files* are NOT an error here — a partially-synced clone
    should still be able to list what exists. Callers check :meth:`Start.exists`.
    """
    registry_path = Path(path) if path is not None else STARTS_FILE
    if not registry_path.exists():
        raise FileNotFoundError(f"start registry not found: {registry_path}")

    with open(registry_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"{registry_path.name}: top level must be a mapping, "
            f"got {type(data).__name__}"
        )

    raw = data.get("starts")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError(
            f"{registry_path.name}: 'starts' must be a list, got {type(raw).__name__}"
        )

    starts = [
        _start_from_entry(entry, i, registry_path.name) for i, entry in enumerate(raw)
    ]

    seen: set[tuple[str, str]] = set()
    defaults: dict[str, int] = {}
    for start in starts:
        key = (start.rom, start.label)
        if key in seen:
            raise ValueError(
                f"{registry_path.name}: duplicate start {start.label!r} "
                f"for rom {start.rom!r}"
            )
        seen.add(key)
        if start.is_default:
            defaults[start.rom] = defaults.get(start.rom, 0) + 1

    for rom, count in defaults.items():
        if count > 1:
            raise ValueError(
                f"{registry_path.name}: rom {rom!r} has {count} defaults; "
                f"exactly one entry per rom may set 'default: true'"
            )

    return starts


def starts_for_rom(
    rom_id: str, path: Union[str, Path, None] = None
) -> list[Start]:
    """Every choosable opening for one ROM, in registry order."""
    return [s for s in load_starts(path) if s.rom == rom_id]


def default_start(
    rom_id: str, path: Union[str, Path, None] = None
) -> Optional[Start]:
    """The opening a casual run on ``rom_id`` gets when it names no label.

    ``None`` when the ROM offers no choosable openings at all, which is the
    signal to fall back to the pre-registry behaviour (``Rom.start_save``, or
    ``executor.CANONICAL_SAVE`` for the default ROM).
    """
    candidates = starts_for_rom(rom_id, path)
    if not candidates:
        return None
    for start in candidates:
        if start.is_default:
            return start
    # A rom with entries but no explicit default: first wins, matching how
    # benchmarks.yaml resolves an absent default.
    return candidates[0]


def get_start(
    rom_id: str, label: str, path: Union[str, Path, None] = None
) -> Start:
    """Look up one opening by ``(rom, label)``.

    Raises ``KeyError`` naming the valid labels, so the caller can turn it into a
    400 that tells the user what they *could* have said.
    """
    candidates = starts_for_rom(rom_id, path)
    for start in candidates:
        if start.label == label:
            return start
    known = ", ".join(s.label for s in candidates) or "(none for this rom)"
    raise KeyError(
        f"unknown start {label!r} for rom {rom_id!r}; known: {known}"
    )
