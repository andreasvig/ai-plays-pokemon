"""The per-game memory contract (src/referee/contracts.py).

The load-bearing test here is the FIRST one: FireRed's contract has to equal the
constant it replaced, byte for byte. cross-game-plan.md P-D asks for the move to
happen "with no behaviour change", and a spec that drifted by one offset would
not fail loudly — it would read six bytes from a slightly wrong place inside a
live save block and report coordinates.
"""

import dataclasses
import struct

import pytest

from src.referee import trace
from src.referee.battles import in_battle_from_byte
from src.referee.contracts import (BLACK2, CONTRACTS, CRYSTAL, EMERALD, FIRERED, Field,
                                   attach, contract_for)

# The literal this module was extracted from (src/referee/trace.py, pre-2026-09-19).
HISTORICAL_FIRERED_SPEC = ["*0x3005008+0:6", "0x3003529:1", "*0x3005008+0x121c:4"]


def test_firered_contract_reproduces_the_constant_it_replaced():
    """The historical three ranges are a PREFIX, not the whole spec.

    Prefix rather than equality, and the distinction is the reason this test
    survives: the battle block appended on 2026-09-20 must not move the first
    three, because every sample index below — and every sample already recorded
    in a run's events.jsonl — is a position in that list. Asserting equality
    would have failed for the right reason and been "fixed" by pasting the new
    entries in, which asserts nothing about their order.
    """
    assert list(FIRERED.spec)[:3] == HISTORICAL_FIRERED_SPEC
    assert trace.TRACE_SPEC[:3] == HISTORICAL_FIRERED_SPEC
    assert list(FIRERED.spec) == trace.TRACE_SPEC, "the alias has to stay an alias"
    # And the fields that were there before still read out of the old ranges.
    for f in (FIRERED.x, FIRERED.y, FIRERED.map_group, FIRERED.map_num):
        assert f.sample == 0
    assert FIRERED.battle_flag.sample == 1 and FIRERED.battles_total.sample == 2


def row(x=5, y=7, group=3, num=0, batt=0, stat=3):
    return ("R", [struct.pack("<hhBB", x, y, group, num), bytes([batt]),
                  struct.pack("<I", stat)])


def test_firered_decodes_the_same_fields_as_before():
    d = trace.decode_samples([row()], 0, FIRERED)[0]
    assert (d["x"], d["y"], d["map_group"], d["map_num"]) == (5, 7, 3, 0)
    assert d["battles_total"] == 3


@pytest.mark.parametrize("b", range(256))
def test_the_battle_mask_agrees_with_battles_in_battle_from_byte(b):
    """battle_mask is a MASK and in_battle_from_byte reads a bit INDEX.

    One is `b & 2` and the other `(b >> 1) & 1`; they agree, and writing 1 for
    either would not. This ran green against a wrong mask on 128 of the 256
    values, which is why it is parametrized over all of them rather than spot
    checked."""
    got = trace.decode_samples([row(batt=b)], 0, FIRERED)[0]["in_battle"]
    assert got == in_battle_from_byte(b)


def test_no_contract_decodes_nothing_rather_than_guessing():
    """The safe default. Decoding FireRed's layout by default is what made a DS
    run report coordinates read out of whatever sits at 0x03005008."""
    d = trace.decode_samples([row()], 0)[0]
    assert d["x"] is None and d["y"] is None and d["map_group"] is None
    assert trace.derive([d], None, None)["blind"] is True


def test_a_contract_without_a_battle_flag_reports_overworld_not_unknown():
    """`None` would make derive() file every input under battle_edge and report
    a turn that moved nowhere; False costs only the census. See the decoder.

    The contract is synthetic because as of 2026-09-20 every SHIPPED one has a
    flag — and that is exactly why the property still needs a test: the rule
    belongs to the decoder, not to whichever cartridge happened to lack a flag
    when it was written. Crystal used to be this test's example."""
    flagless = dataclasses.replace(CRYSTAL, spec=("0xdcb5:4",), battle_flag=None)
    d = trace.decode_samples([("R", [bytes([1, 0, 4, 9])])], None, flagless)[0]
    assert d["in_battle"] is False
    assert flagless.census_ok is False and FIRERED.census_ok is True


def test_which_cartridges_can_tell_a_battle_press_from_an_overworld_one():
    """Spelled out per game rather than as "all of them", so that shipping a
    contract without a battle flag is a visible decision.

    It already worked once: this test was written as `all True` when the three
    GBA/GB cartridges had flags, and Platinum landing the same night turned it
    red — which is the point. Platinum ships census-off deliberately. Its
    position comes from pret's `struct Location`, which carries no battle
    field, and finding a DS battle flag is its own hunt. What census-off costs
    is only the INPUT CENSUS: a battle's presses land in `idle_ab` and a
    non-move cannot be told from a textbox. It costs nothing in the walk
    graph, because the Location survives a battle unchanged."""
    assert {g: c.census_ok for g, c in CONTRACTS.items()} == {
        "firered-us": True,
        "emerald-us": True,
        "crystal-us": True,
        "platinum-us": True,
        "soulsilver-us": True,
        "black2-us": True,
        "black-us": True,
    }


def test_crystals_battle_flag_is_a_mode_byte_so_the_mask_must_carry_both_bits():
    """0 overworld, 1 wild, 2 trainer. mask=0x01 passes every wild battle and
    reports the rival fight as the overworld — the silent battle_mask typo in
    a new costume, and the reason the live check carries a trainer state."""
    def seen(raw: int, mask: int) -> bool:
        c = dataclasses.replace(CRYSTAL, battle_mask=mask)
        row = ("A", [bytes([24, 3, 4, 9]), bytes([raw])])
        return trace.decode_samples([row], None, c)[0]["in_battle"]

    assert CRYSTAL.battle_mask == 0x03
    assert [seen(v, 0x03) for v in (0, 1, 2)] == [False, True, True]
    assert [seen(v, 0x01) for v in (0, 1, 2)] == [False, True, False]


def test_black2_reads_a_16_16_fixed_point_coordinate_as_a_tile():
    """Gen 5 stores the coordinate in sixteenths. The tile is value >> 16, and
    the low half is 0x8000 at rest because the player stands at the tile
    CENTRE — which is what makes the shift exact rather than a rounding choice.

    Without the shift the walk graph sees x jump by 65536 a step, so no two
    samples are ever adjacent and every move is filed as a warp: the graph
    would be empty of edges and full of doors, and it would not raise."""
    centre = struct.pack("<IiiI", 427, (47 << 16) | 0x8000, 1, (764 << 16) | 0x8000)
    d = trace.decode_samples([("R", [centre])], None, BLACK2)[0]
    assert (d["x"], d["y"], d["map_id"]) == (47, 764, 427)
    assert BLACK2.map_key(d) == (427,)


def test_a_fixed_point_field_floors_rather_than_truncating_toward_zero():
    """Python's >> on a negative int floors, and a tile index wants that: half
    a tile left of the origin is tile -1, not tile 0. Pinned because a
    hand-rolled `int(value / 65536)` would round the other way and the error
    only ever shows up west or north of the origin."""
    assert Field(0, 0, "<i", shift=16).read([struct.pack("<i", -(1 << 15))]) == -1
    assert Field(0, 0, "<i", shift=16).read([struct.pack("<i", 0)]) == 0


def test_a_field_without_a_shift_is_untouched():
    """The control: shift defaults to 0, so every contract written before Gen 5
    reads exactly as it did."""
    raw = [struct.pack("<i", 12345)]
    assert Field(0, 0, "<i").read(raw) == 12345 == Field(0, 0, "<i", shift=0).read(raw)


def test_crystal_reads_one_unsigned_byte_per_axis_in_gen_2_order():
    """group, number, y, x — NOT the Gen 3 order, which is the whole reason the
    decoder had to stop knowing one layout."""
    d = trace.decode_samples([("R", [bytes([26, 3, 9, 5])])], None, CRYSTAL)[0]
    assert (d["map_group"], d["map_num"], d["y"], d["x"]) == (26, 3, 9, 5)


def test_a_short_sample_loses_one_field_not_the_whole_turn():
    torn = ("R", [struct.pack("<hh", 10, 11)])  # the group/number bytes never arrived
    d = trace.decode_samples([torn], 0, FIRERED)[0]
    assert (d["x"], d["y"]) == (10, 11)
    assert d["map_group"] is None and d["map_num"] is None


def test_attach_sets_the_spec_and_its_decoder_together():
    class Emu:
        pass

    e = Emu()
    attach(e, EMERALD)
    assert e.trace_spec == list(EMERALD.spec) and e.trace_contract is EMERALD
    # None must leave an EMPTY spec, not the previous game's.
    attach(e, None)
    assert e.trace_spec == [] and e.trace_contract is None


def test_map_key_has_the_shape_its_generation_has():
    assert FIRERED.map_key({"map_group": 3, "map_num": 0}) == (3, 0)
    assert FIRERED.map_key({"map_group": None, "map_num": 0}) is None
    gen4 = type(FIRERED)(game="x", console="NDS", spec=("0x0:1",),
                         x=Field(0, 0, "<i"), y=Field(0, 4, "<i"),
                         map_id=Field(0, 8, "<H"))
    assert gen4.map_key({"map_id": 17}) == (17,)


def test_every_registered_game_is_keyed_by_its_own_game_string():
    """The join to configs/roms.yaml. A contract filed under another game's key
    is the same silent-wrong-cartridge failure, one table earlier."""
    for key, c in CONTRACTS.items():
        assert key == c.game
        assert contract_for(key) is c
    assert contract_for("no-such-game") is None
    assert contract_for(None) is None
