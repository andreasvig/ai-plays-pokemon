"""The two species sprite sets, and the join that says which cartridge reads which.

A species id means a DIFFERENT MON depending on the cartridge that reported it
(src/referee/contracts.py): the two gen-3 games report a gen-3 INTERNAL index
and the other five report a NATIONAL DEX number. ``public/pokemon/<id>.png`` is
the first keyspace and ``public/pokemon/dex/<n>.png`` (scripts/extract_dex_sprites.py)
is the second. Feeding one keyspace's number to the other set is the defect
Andreas found on 2026-09-21 — Platinum's rival Chimchar drew Anorith.

``GROUND_TRUTH`` below is not derived from either table. Every row is a species
id read out of emulator memory by a recorded run, paired with the name the
game's own battle HUD prints in the screenshot for that turn. Two of the rows
(Emerald's 286 and Platinum's 390) are numbers that mean different mons under
the two numberings, so they fail if that game is filed under the wrong one; the
rest are numbers the two numberings agree on and are there as controls that
routing them anywhere still names them correctly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "src" / "dashboard" / "web"
GEN3_DIR = WEB / "public" / "pokemon"
DEX_DIR = GEN3_DIR / "dex"
SPECIES_JS = WEB / "src" / "lib" / "species.js"

#: scripts/extract_dex_sprites.py's own bounds, repeated so a silent change to
#: the script is a failure here rather than a set that quietly lost Unova.
MAX_DEX = 649
MAX_GEN3_INTERNAL = 411

#: (game, species id as the run recorded it, the name on the screen, where).
GROUND_TRUTH = [
    # DISCRIMINATING — the id means a different mon under the other numbering.
    ("emerald-us", 286, "Poochyena",
     "run 2026-09-20_17-59-44 turn 91; National Dex 286 is Breloom"),
    ("platinum-us", 390, "Chimchar",
     "run 2026-09-20_21-42-46 turn 83; gen-3 internal 390 is Anorith"),
    ("platinum-us", 403, "Shinx",
     "run 2026-09-20_09-53-29 turn 25; gen-3 internal 403 is Registeel"),
    ("black-us", 495, "Snivy",
     "run 2026-09-20_23-09-22 turn 11; past the end of the gen-3 set entirely"),
    ("black-us", 504, "Patrat", "run 2026-09-20_23-09-22 turn 97"),
    ("black2-us", 501, "Oshawott",
     "run 2026-09-20_00-51-00 turn 168; local/gen5-battle/black2_sp savepoint 170"),
    # CONTROLS — below 252, where the two numberings agree, so these say
    # nothing about the routing and everything about it not breaking.
    ("crystal-us", 161, "Sentret", "run 2026-09-20_19-14-38 turn 46"),
    ("soulsilver-us", 161, "Sentret", "run 2026-09-21_09-03-49 turn 154"),
    ("firered-us", 4, "Charmander", "run 2026-09-20_17-42-57 turn 22, the rival"),
]


def keyspaces() -> dict[str, str]:
    """``SPECIES_KEYSPACE`` read out of the browser module that owns it.

    Parsed rather than duplicated: the frontend is the only consumer, so a copy
    here would be a second implementation of the table and could agree with
    nothing.
    """
    src = SPECIES_JS.read_text()
    body = src[src.index("export const SPECIES_KEYSPACE"):]
    body = body[:body.index("}")]
    return dict(re.findall(r"'([a-z0-9-]+)':\s*'([a-z0-9-]+)'", body))


def index_for(keyspace: str, game: str) -> dict[str, str]:
    """The id -> name table that keyspace reads, or an empty one."""
    if keyspace == "national-dex":
        return json.loads((DEX_DIR / "index.json").read_text())["species"]
    path = GEN3_DIR.parent / "trainers" / game / "index.json"
    return json.loads(path.read_text())["species"] if path.exists() else {}


def sprite_for(keyspace: str, species: int) -> Path:
    return (DEX_DIR if keyspace == "national-dex" else GEN3_DIR) / f"{species}.png"


def test_the_dex_set_is_complete_and_keyed_by_national_dex():
    have = sorted(int(p.stem) for p in DEX_DIR.glob("*.png"))
    assert have == list(range(1, MAX_DEX + 1)), f"{DEX_DIR} is not 1..{MAX_DEX}"
    index = json.loads((DEX_DIR / "index.json").read_text())
    assert index["keyspace"] == "national-dex"
    assert sorted(int(k) for k in index["species"]) == list(range(1, MAX_DEX + 1))


def test_the_placeholder_exists_and_is_a_real_image():
    """A species with no sprite has to DRAW something.

    The card used to hide the image, which leaves a battle card that looks like
    it met nobody — and that is what every Black card did, because gen 5's
    numbers run past the end of the only set there was.
    """
    unknown = GEN3_DIR / "unknown.png"
    assert unknown.exists(), f"{unknown} is missing; the card has nothing to fall back to"
    assert unknown.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # It is the ROM's own "?" pic — gen-3 internal 252, the first of the unused
    # band — so the fallback is a picture the games themselves draw.
    assert unknown.read_bytes() == (GEN3_DIR / "252.png").read_bytes()


def test_every_cartridge_has_a_keyspace_and_it_is_one_of_the_two():
    from src.referee.contracts import CONTRACTS

    table = keyspaces()
    assert set(table) == set(CONTRACTS), (
        "every game with a contract reports a species id, so every game with a "
        "contract needs a keyspace")
    assert set(table.values()) == {"gen3-internal", "national-dex"}


@pytest.mark.parametrize("game,species,name,where",
                         GROUND_TRUTH, ids=[f"{g}-{s}" for g, s, _n, _w in GROUND_TRUTH])
def test_a_recorded_species_id_resolves_to_what_the_screen_said(game, species, name, where):
    keyspace = keyspaces()[game]
    sprite = sprite_for(keyspace, species)
    assert sprite.exists(), f"{game} species {species} ({name}) has no sprite at {sprite} — {where}"
    got = index_for(keyspace, game).get(str(species))
    assert got == name, f"{game} species {species} is named {got!r}, the screen says {name!r} — {where}"


def test_the_two_sets_really_are_two_keyspaces():
    """286 is Poochyena in one set and Breloom in the other, in PIXELS.

    The mutation this is here for is a dex set regenerated from gen-3 internal
    ids, which would pass every name check above only if the name table were
    regenerated the same wrong way — and would still leave these two files
    byte-identical.
    """
    for species in (286, 288, 290, 390, 403):
        gen3 = (GEN3_DIR / f"{species}.png").read_bytes()
        dex = (DEX_DIR / f"{species}.png").read_bytes()
        assert gen3 != dex, f"pokemon/{species}.png and pokemon/dex/{species}.png are the same image"


def test_gen_five_species_exist_only_in_the_dex_set():
    for species in (495, 501, 504, 649):
        assert (DEX_DIR / f"{species}.png").exists()
        assert not (GEN3_DIR / f"{species}.png").exists(), (
            "the gen-3 set stops at 411; a file past it means the two keyspaces "
            "have been mixed in one directory")
    assert not (GEN3_DIR / f"{MAX_GEN3_INTERNAL + 1}.png").exists()


def test_no_component_builds_a_sprite_url_without_a_game():
    """``mapatlas.js`` still exports the game-blind ``pokemonSpriteUrl``.

    It is another agent's file this week, so it is left alone — but nothing may
    IMPORT it: that function is the bug, and its docstring still claims a
    species id is the National Dex number on every cartridge.
    """
    offenders = []
    for path in sorted((WEB / "src").rglob("*.svelte")) + sorted((WEB / "src").rglob("*.js")):
        if path.name == "mapatlas.js":
            continue
        text = path.read_text()
        if "pokemonSpriteUrl" in text and "mapatlas.js" in text:
            imports = re.findall(r"import\s*\{([^}]*)\}\s*from\s*'[^']*mapatlas\.js'", text)
            if any("pokemonSpriteUrl" in i for i in imports):
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"these import the game-blind sprite URL: {offenders}"
