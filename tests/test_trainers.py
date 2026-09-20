"""The committed trainer atlas (src/dashboard/web/public/trainers/<game>/index.json).

Generated offline by scripts/extract_trainers.py from pret at the pinned SHA and
committed, so the battle hover cards are fixed until someone regenerates it.
What these tests guard is that the extraction did not quietly lose anything: a
party parsed as empty still renders a card, just a wrong one (which is how Bug
Catcher Anthony shipped with no Pokemon on the first run of that script).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.referee.battles import TRAINER_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
# Per game since 2026-09-20: trainer 4 is a different person on every
# cartridge, so the sprite namespace is split exactly as the map one is.
INDEX = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "trainers" / "firered-us" / "index.json"


@pytest.fixture(scope="module")
def atlas() -> dict:
    return json.loads(INDEX.read_text())


def test_every_trainer_the_referee_can_name_has_an_entry(atlas):
    assert set(atlas["trainers"]) == {str(t) for t in TRAINER_NAMES}


def test_every_trainer_has_a_party_and_a_sprite(atlas):
    for tid, t in atlas["trainers"].items():
        assert t["party"], f"{tid} ({t['label']}) has no Pokemon"
        assert t["pic"], f"{tid} has no sprite"
        assert (INDEX.parent / t["pic"]).is_file()
        for mon in t["party"]:
            assert 1 <= mon["level"] <= 100
            assert mon["species"] and mon["species"][0].isupper()


def test_the_rosters_match_the_rom(atlas):
    # Three fights a first-badge run cannot avoid, checked against the game.
    brock = atlas["trainers"]["414"]
    assert brock["class"] == "Leader" and brock["name"] == "Brock"
    assert [(m["species"], m["level"]) for m in brock["party"]] == [("Geodude", 12), ("Onix", 14)]
    assert [(m["species"], m["level"]) for m in atlas["trainers"]["104"]["party"]] == [("Weedle", 9)]
    # The rival's id says which starter he took — the three ids share one name.
    assert atlas["trainers"]["326"]["party"][0]["species"] == "Squirtle"
    assert atlas["trainers"]["327"]["party"][0]["species"] == "Bulbasaur"
    assert atlas["trainers"]["328"]["party"][0]["species"] == "Charmander"


def test_the_referees_label_is_carried_through(atlas):
    # Every other surface shows the referee's label, so the card must agree.
    for tid, name in TRAINER_NAMES.items():
        assert atlas["trainers"][str(tid)]["label"] == name


def test_the_sprites_are_transparent_where_the_game_draws_nothing(atlas):
    png = (INDEX.parent / atlas["trainers"]["414"]["pic"]).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[25] == 6, "colour type 6 — RGBA; the background index must be cut out"


# -- the Pokémon sprites (2026-09-16) -----------------------------------------
# Andreas: "for the battle I would like Pokemon sprites on the hover, for both
# trainer Pokemon and wild Pokemon." The card names the file by SPECIES ID, so
# a roster entry needs its id and the file has to be there — a card that shows
# a broken image is worse than one that shows none, and nothing else notices.

MON_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "pokemon"


def test_every_roster_member_carries_the_id_its_sprite_is_named_by(atlas):
    assert atlas["version"] >= 3, "index version 2 has no species ids on the roster"
    for tid, t in atlas["trainers"].items():
        for m in t["party"]:
            assert isinstance(m.get("id"), int) and m["id"] > 0, (tid, m)
            assert (MON_DIR / f"{m['id']}.png").is_file(), (tid, m)


def test_the_species_sprites_are_not_namespaced_per_game(atlas):
    # A species id is the National Dex number on every cartridge we run, so one
    # set serves all seven; a per-game copy would be the same pixels seven
    # times. The trainers beside them ARE per game, and that difference is the
    # point — this asserts the split is where it belongs.
    assert INDEX.parent.name == "firered-us"
    assert MON_DIR.parent.name == "public"
    assert (MON_DIR / "1.png").is_file(), "Bulbasaur, by National Dex number"


def test_a_wild_foes_sprite_exists_for_every_species_the_index_names(atlas):
    # The card draws the foe from `battle.foe.species`, which is whatever the
    # ROM read returns — so the set has to cover every species the index can
    # name, not only the ones a first-badge trainer happens to carry.
    missing = [i for i in atlas["species"] if not (MON_DIR / f"{i}.png").is_file()]
    # Castform keeps its front pic per form and has none at the expected path;
    # named here rather than silently tolerated by a loose threshold.
    assert [atlas["species"][i] for i in missing] == ["Castform"], missing
