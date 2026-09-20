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
    assert missing == [], f"no sprite for {[atlas['species'][i] for i in missing]}"


# -- the second cartridge (2026-09-20) ----------------------------------------
# Emerald has no gate ladder, so there is no TRAINER_NAMES to select by and no
# referee wording to label with. Its index is the whole ROM roster, labelled
# from the ROM's own class and given name — which is the only way a card that
# meets Lass Tiana can say "Lass Tiana".

EMERALD_INDEX = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "trainers" / "emerald-us" / "index.json"


@pytest.fixture(scope="module")
def emerald() -> dict:
    return json.loads(EMERALD_INDEX.read_text())


def test_each_index_names_the_cartridge_it_was_read_from(atlas, emerald):
    # The same rule the map atlas has, for the same reason: trainer 114 is Lady
    # Cindy on Emerald and somebody else entirely on FireRed, and nothing but
    # the directory keeps the two apart.
    assert emerald["game"] == "emerald-us" == EMERALD_INDEX.parent.name
    assert atlas.get("game", "firered-us") == INDEX.parent.name
    assert emerald["trainers"].keys() != atlas["trainers"].keys()


def test_the_emerald_roster_is_labelled_from_the_rom_itself(emerald):
    # The two trainers the address probe actually met, which is what makes this
    # a join test and not a spelling test: gTrainerBattleOpponent_A read 114 and
    # 603 in two real battles, and gBattleMons read a Zigzagoon L7 in the first
    # and a Shroomish L4 in the second. The ROM's own roster agrees with both.
    cindy = emerald["trainers"]["114"]
    assert cindy["label"] == "Lady Cindy"
    assert [(m["species"], m["level"]) for m in cindy["party"]] == [("Zigzagoon", 7)]

    tiana = emerald["trainers"]["603"]
    assert tiana["label"] == "Lass Tiana"
    assert ("Shroomish", 4) in [(m["species"], m["level"]) for m in tiana["party"]]


def test_a_wild_foe_can_be_named_from_the_species_map(emerald):
    # A wild card shows a name, not an id. The species map is the only thing
    # that turns gBattleMons' 286 into "Poochyena", and it is per game because
    # the index is — though the NUMBERS are shared gen-3 internal indices, which
    # is why one sprite set serves both.
    assert emerald["species"]["286"] == "Poochyena"
    assert emerald["species"]["283"] == "Mudkip"      # the Emerald starter
    assert emerald["species"]["306"] == "Shroomish"


def test_every_emerald_trainer_sprite_is_on_disk(emerald):
    named = {t["pic"] for t in emerald["trainers"].values() if t["pic"]}
    assert len(named) > 50, "93 trainer pics cover the roster; a handful means the parse broke"
    for pic in named:
        assert (EMERALD_INDEX.parent / pic).is_file(), pic


def test_the_species_sprites_are_shared_not_duplicated(emerald):
    # Both indexes name their party members by the same id, and there is ONE
    # directory of those pictures. A per-game copy would be the same pixels
    # under the same names.
    ids = {m["id"] for t in emerald["trainers"].values() for m in t["party"] if m.get("id")}
    assert len(ids) > 100
    missing = [i for i in ids if not (MON_DIR / f"{i}.png").is_file()]
    assert missing == [], f"{len(missing)} Emerald party members have no sprite: {missing[:10]}"
