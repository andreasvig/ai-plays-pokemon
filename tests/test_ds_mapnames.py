"""``scripts/ds3d/mapnames.py`` — the join both DS renderers use for a name.

The file it reads was extracted on 2026-09-21 and then shipped UNUSED for a
day, which is the failure this test is really guarding: an atlas whose every
`name` is null while the answer sits in the repo.
"""

from __future__ import annotations

import json

from scripts.ds3d.mapnames import NAMES_PATH, PLACEHOLDER, games, map_name


def test_the_three_games_in_the_file_are_the_three_without_a_decomp():
    """Platinum's absence is the design, not a gap.

    Its decomp names a map ``TwinleafTown_RivalHouse_1F`` — unique, and richer
    than the "Twinleaf Town" the cartridge prints for all four of that town's
    maps. Adding Platinum here would LOSE information, so the file stops at
    the three games that have no decomp to read.
    """
    assert games() == ["black-us", "black2-us", "soulsilver-us"]
    assert map_name("platinum-us", 411) is None


def test_a_real_map_reads_the_name_the_cartridge_prints():
    assert map_name("soulsilver-us", 33) == "Route 29"
    assert map_name("black-us", 317) == "Route 1"
    assert map_name("black2-us", 427) == "Aspertia City"
    # str and int are the same map: the atlas keys are strings, the renderers
    # carry ints, and a silent miss here would ship a null name.
    assert map_name("soulsilver-us", "33") == map_name("soulsilver-us", 33)


def test_a_name_is_not_a_key_and_the_file_says_so():
    """The caveat that decides how the viewer may use this.

    A map header holds an index into a PLACE-name table, so an interior
    carries its town's name. Both of Aspertia's rendered maps read the same
    string; `building` stays the uniqueness field and the popup header may
    show "Map 428" beside a tooltip reading "Aspertia City". Both are true.
    """
    assert map_name("black2-us", 427) == map_name("black2-us", 428)
    b2 = json.loads(NAMES_PATH.read_text())["black2-us"]
    named = [v for v in b2.values() if v != PLACEHOLDER]
    assert len(set(named)) < len(named), "no duplicate name means the caveat is wrong"


def test_the_placeholder_never_reaches_an_atlas():
    """``Mystery Zone`` is the cartridge's own filler for an unused header slot.

    Passing it through would put those two words on a map popup, which reads
    as a rendering bug. None already means "say the map id instead", which is
    what the atlas and the viewer do.
    """
    raw = json.loads(NAMES_PATH.read_text())
    slots = [k for k, v in raw["soulsilver-us"].items() if v == PLACEHOLDER]
    assert slots, "the fixture assumes SoulSilver has placeholder slots"
    assert all(map_name("soulsilver-us", k) is None for k in slots)


def test_an_unknown_game_or_map_is_none_and_not_a_crash():
    assert map_name("firered-us", 3) is None
    assert map_name("soulsilver-us", 99999) is None
