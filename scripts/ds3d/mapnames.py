"""The place name a DS cartridge prints for one map id.

``data/ds-map-names.json`` was extracted by ``scripts/extract_ds_mapnames.py``
straight out of the cartridges (the ciphers live in :mod:`scripts.ds3d.dstext`)
and then sat unused: every gen 4/5 atlas entry shipped ``name: null`` and the
viewer's popup header read "Map 428". This is the one lookup both renderers
call, so neither grows its own copy of the join.

**A map header holds an index into a PLACE-name table, so an interior carries
its town's name.** There is no string for "Elm's Lab" anywhere in SoulSilver —
five of its six rendered maps read "New Bark Town" and all four of Black 2's
read "Aspertia City". That is the cartridge's own answer and not a defect in
the extraction, which is why :func:`map_name` returns it rather than trying to
be cleverer. It also means the name is NOT unique and must never be used as a
key: ``building`` stays the uniqueness field.

Platinum is deliberately absent from the file. Its decomp gives richer,
already-unique symbols (``TwinleafTown_RivalHouse_1F``), and replacing those
with four copies of "Twinleaf Town" would lose information.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

NAMES_PATH = Path(__file__).resolve().parents[2] / "data" / "ds-map-names.json"

#: What the cartridges call a header slot with no place behind it. Kept out of
#: the atlas: "Mystery Zone" on a popup reads as a rendering bug, and a null
#: name already has a defined meaning downstream (fall back to the map id).
PLACEHOLDER = "Mystery Zone"


@lru_cache(maxsize=1)
def _table() -> dict[str, dict[str, str]]:
    if not NAMES_PATH.exists():
        return {}
    return json.loads(NAMES_PATH.read_text())


def map_name(game: str, map_id: int | str) -> Optional[str]:
    """The cartridge's place name for ``map_id``, or None.

    None for: a game not in the file (Platinum, by design), a map id the
    cartridge has no header for, and the ``Mystery Zone`` placeholder. Every
    None means "say the map id instead", which is what the atlas and the
    viewer already do for a null name.
    """
    name = _table().get(game, {}).get(str(map_id))
    if not name or name == PLACEHOLDER:
        return None
    return name


def games() -> list[str]:
    """The games the file can name, for a caller that wants to check first."""
    return sorted(_table())
