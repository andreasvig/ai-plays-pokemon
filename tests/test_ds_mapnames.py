"""`data/ds-map-names.json` and the extractor that makes it.

A NAME TABLE IS THE KIND OF ARTIFACT THAT LOOKS RIGHT AND IS OFF BY ONE. Shift
the index by one and every map still gets a real place name from the right
game — Route 29 becomes Route 30, New Bark Town becomes Cherrygrove City — and
nothing raises, nothing renders oddly, and "all 540 strings decoded cleanly"
stays true. So these tests do not check that the table decoded. They pin
specific (game, map id, name) pairs against ground truth that did not come
from this code:

  soulsilver-us 60  New Bark Town   the location plaque in run
                                    2026-09-20_22-12-03's recording, t=85.0s,
                                    on the frame captioned TURN 7 — the turn
                                    whose samples record the 63 -> 60 move.
  black-us      317 Route 1         the plaque in run 2026-09-20_23-09-22's
                                    recording, t=1097.5s, frame captioned
                                    TURN 94, the turn that records 389 -> 317.
  soulsilver-us 33  Route 29        src/referee/contracts.py's SoulSilver
                                    notes. No recording exists for a run that
                                    entered 33.
  black-us      389 Nuvema Town     the starting town; also the zone all five
  black2-us     427 Aspertia City   Nuvema / three Aspertia interiors name as
                                    their parent in render_gen5maps.py.

and then prove the pins BITE, with `test_an_off_by_one_in_the_name_index_is_caught`:
it rebuilds the table from the cartridge reading the name index one entry
along, and asserts the pinned names change. A pin that survives its own
mutation is guarding nothing.

The ROM-free tests read the committed JSON and always run. Everything that
needs a cartridge skips without one.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

TABLE = REPO_ROOT / "data" / "ds-map-names.json"
OBSERVED = REPO_ROOT / "artifacts" / "game-map-render" / "observed"

# Ground truth. Every entry's provenance is in this module's docstring.
PINS = {
    "soulsilver-us": {33: "Route 29", 60: "New Bark Town"},
    "black-us": {317: "Route 1", 319: "Route 2", 320: "Accumula Gate",
                 389: "Nuvema Town", 397: "Accumula Town"},
    "black2-us": {427: "Aspertia City"},
}


@pytest.fixture(scope="module")
def table() -> dict:
    if not TABLE.is_file():
        pytest.fail(f"{TABLE} is missing; run scripts/extract_ds_mapnames.py")
    return json.loads(TABLE.read_text())


@pytest.fixture(scope="module")
def extractor():
    module = pytest.importorskip("extract_ds_mapnames")
    missing = [g.rom for g in module.GAMES.values()
               if not (REPO_ROOT / "roms" / g.rom).is_file()]
    if missing:
        pytest.skip(f"no cartridge for {missing}")
    return module


# ---------------------------------------------------------------- the table

def test_the_table_names_every_map_our_runs_entered(table):
    """The acceptance bar: the twenty maps the observed graphs contain.

    `maps` in an observed graph is a COUNT; the ids live in `per_map`. Asserted
    non-empty because reading the wrong key makes this whole test pass
    vacuously.
    """
    for game, names in table.items():
        graph = OBSERVED / f"{game}-observed.json"
        if not graph.is_file():
            continue
        entered = {str(e["map"][0]) for e in json.loads(graph.read_text())["per_map"]}
        assert entered, f"{game}: the observed graph names no maps at all"
        assert entered <= set(names), f"{game}: unnamed {sorted(entered - set(names))}"


@pytest.mark.parametrize("game", sorted(PINS))
def test_pinned_names(table, game):
    for map_id, want in PINS[game].items():
        assert table[game][str(map_id)] == want, \
            f"{game} map {map_id}: expected {want!r}, table says {table[game][str(map_id)]!r}"


def test_keys_are_plain_integers_not_the_atlas_spelling(table):
    """`"427"`, the observed-graph spelling — a consumer adds the `:0` itself.

    Getting this wrong is silent: an atlas keyed the other way loads, matches
    nothing and reports no error.
    """
    for game, names in table.items():
        for key in names:
            assert key.isdigit(), f"{game}: key {key!r} is not a plain integer"


def test_no_name_is_a_placeholder(table):
    """Gen 5's entry 0 is ten full-width dashes; it must never ship as a name."""
    for game, names in table.items():
        for key, name in names.items():
            assert name.strip(), f"{game} {key}: empty name"
            assert set(name) != {"－"}, f"{game} {key}: the blank row leaked in"


# ---------------------------------------------------------------- the cartridge

def test_regenerating_reproduces_the_committed_table(extractor, table):
    assert extractor.build() == table


def test_an_off_by_one_in_the_name_index_is_caught(extractor, monkeypatch):
    """THE MUTATION CONTROL, and the reason the pins above are worth anything.

    Reading the name index one entry along is the exact failure that survives
    every other check: it is what a mis-measured field offset, a table located
    one record early, or a 1-based/0-based mixup all look like. Both
    generations are mutated at their own read site. The extractor's own
    known-answer checks must reject the result, AND the pinned names must
    change — if a shifted table still spelled `Route 1`, the pin would be
    guarding nothing.
    """
    from ds3d import dstext

    real = dstext.find_text_file

    def shifted(subfiles, must_contain):
        index, strings = real(subfiles, must_contain)
        return index, strings[1:] + strings[:1]      # every index now reads +1

    # Patched where the extractor looks it up, so the mutation runs through the
    # real production path rather than a reimplementation of it.
    monkeypatch.setattr(extractor.dstext, "find_text_file", shifted)

    for game in PINS:
        with pytest.raises(SystemExit):
            extractor.names_for(game)

    # ... and specifically, a pinned map comes back with a DIFFERENT real place
    # name rather than with nothing, which is what makes this failure invisible
    # without ground truth.
    wrong = extractor.gen5_names(extractor.GAMES["black-us"])
    assert wrong[317] and wrong[317] != "Route 1"


def test_the_gen5_name_field_is_the_only_offset_that_works(extractor):
    """+0x1A, and neither neighbour, for both Gen 5 cartridges.

    A field found by fitting two known zones is worth only as much as the
    refutation of the offsets beside it. This is that refutation, and it is
    also a second mutation control: move the read one byte and the known-answer
    checks must fail.
    """
    for game in ("black-us", "black2-us"):
        for offset in (extractor.ZONE_LABEL - 1, extractor.ZONE_LABEL + 1):
            real = extractor.ZONE_LABEL
            try:
                extractor.ZONE_LABEL = offset
                with pytest.raises(SystemExit):
                    extractor.names_for(game)
            finally:
                extractor.ZONE_LABEL = real


def test_platinum_control(extractor):
    """The same method, on the one cartridge whose answer is already known."""
    cache = REPO_ROOT / "local" / "pret-cache-platinum" / "include" / "data" / "map_headers.h"
    if not cache.is_file():
        pytest.skip("the cached Platinum decomp is not present")
    result = extractor.platinum_control()
    assert result["labelled_headers"] > 500
    assert not result["missing_from_rom_table"]
    assert not result["bijection_broken_for"]
    assert result["identical"] >= 588
    assert result["passed"]


def test_platinum_plaques_read_off_the_recording(extractor):
    """The control's own ground truth, independent of the decomp.

    Four location plaques from run 2026-09-20_00-17-45's recording, each paired
    with the TURN its frame is captioned with and the map that turn's samples
    record. The decomp comparison above and these four frames are two different
    oracles; the extractor's Platinum checks hold both.
    """
    names = extractor.names_for("platinum-us")
    assert names[418] == "Sandgem Town"    # turn 6,   t=78.0s
    assert names[342] == "Route 201"       # turn 24,  t=216.5s
    assert names[343] == "Route 202"       # turn 82,  t=801.5s
    assert names[411] == "Twinleaf Town"   # turn 107, t=1290.0s


def test_the_header_table_address_is_found_not_hard_coded(extractor):
    """The Gen 4 search must leave exactly one address, or refuse.

    Mutated by handing the search a matrix list with no header planes: with
    nothing to constrain it, it has to refuse rather than return its first
    candidate.
    """
    game = extractor.GAMES["soulsilver-us"]
    rom = extractor.Rom(REPO_ROOT / "roms" / game.rom)
    matrices = [extractor.parse_matrix(b) for b in extractor.narc(rom.named(game.matrices))]
    assert isinstance(extractor.locate_header_table(rom.arm9(), matrices), int)
    with pytest.raises(SystemExit):
        extractor.locate_header_table(rom.arm9(), [{"headers": None} for _ in matrices])


# ---------------------------------------------------------------- the decoder

def test_gen4_decoder_reads_strings_only_the_right_cipher_could_produce(extractor):
    """`scripts/ds3d/dstext.py` against Platinum, including the packed form.

    File 618 is stored in the `0xF100` 9-bits-in-15 form; at any other
    container width the first name decodes as `T` followed by noise. Species
    names pin the charmap's three letter blocks and its two gender symbols.
    """
    from ds3d import dstext

    rom = extractor.Rom(REPO_ROOT / "roms" / extractor.GAMES["platinum-us"].rom)
    msg = extractor.narc(rom.named("/msgdata/pl_msg.narc"))

    species = dstext.gen4_text(msg[412])
    assert species[1] == "BULBASAUR"
    assert species[29] == "NIDORAN♀" and species[32] == "NIDORAN♂"
    assert species[83] == "FARFETCH’D" and species[122] == "MR. MIME"

    trainers = dstext.gen4_text(msg[618])          # the 0xF100 packed form
    assert trainers[1] == "Tristan" and trainers[3] == "Natalie"

    places = dstext.gen4_text(msg[433])
    assert places[1] == "Twinleaf Town" and places[91] == "Pokétch Co."


def test_gen5_decoder_reads_strings_only_the_right_cipher_could_produce(extractor):
    from ds3d import dstext

    rom = extractor.Rom(REPO_ROOT / "roms" / extractor.GAMES["black-us"].rom)
    places = dstext.gen5_text(extractor.narc(rom.named("/a/0/0/2"))[89])
    assert places[4] == "Nuvema Town" and places[14] == "Route 1"


def test_a_wrong_gen5_key_does_not_decode(extractor):
    """The mutation for the Gen 5 cipher: the advance constant is load-bearing.

    Every entry but the first is keyed off it, so getting it wrong leaves
    entry 0 readable and turns the rest to noise — the failure that looks like
    success if you only ever print the first string.
    """
    from ds3d import dstext

    rom = extractor.Rom(REPO_ROOT / "roms" / extractor.GAMES["black-us"].rom)
    blob = extractor.narc(rom.named("/a/0/0/2"))[89]
    real = dstext.GEN5_KEY_ADVANCE
    try:
        dstext.GEN5_KEY_ADVANCE = real + 1
        wrong = dstext.gen5_text(blob)
    finally:
        dstext.GEN5_KEY_ADVANCE = real
    assert wrong[4] != "Nuvema Town"


def test_find_text_file_refuses_rather_than_guessing(extractor):
    """Nothing in either archive spells this, so it must raise, not pick one."""
    from ds3d import dstext

    rom = extractor.Rom(REPO_ROOT / "roms" / extractor.GAMES["black-us"].rom)
    subfiles = extractor.narc(rom.named("/a/0/0/2"))
    with pytest.raises(SystemExit):
        dstext.find_text_file(subfiles, {"Vermilion City", "Aspertia City"})


def test_gen4_charmap_leaves_a_gap_rather_than_guessing():
    """An unmapped code must render as `{n}`, never as a plausible letter."""
    from ds3d import dstext

    assert 0x1FF not in dstext.GEN4_CHARMAP
    assert dstext.gen4_render([0x12B, 0x1FF, 0x12C]) == "A{511}B"


def test_gen4_zone_table_stride_matches_the_renderers(extractor):
    """24 and 48 are the two struct sizes render_dsmaps/render_gen5maps use.

    Pinned because a stride is exactly the constant that can be changed here
    and nowhere else and still produce a full, plausible, wrong table.
    """
    assert extractor.HEADER_STRIDE == 24
    assert extractor.ZONE_STRIDE == 48
