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
from src.referee.contracts import (BLACK, BLACK2, CONTRACTS, CRYSTAL, EMERALD, FIRERED,
                                   PLATINUM, SOULSILVER, Field, attach, contract_for)

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


#: One Crystal trace row, laid out against the four ranges of CRYSTAL.spec:
#: the position record, wEnemyMon..wOtherTrainerClass, wBattleResult, SVBK.
CRYSTAL_MODE_OFF = 0xD22D - 0xD206
CRYSTAL_CLASS_OFF = 0xD22F - 0xD206
CRYSTAL_LEVEL_OFF = 0x0D


def crystal_row(mode=0, species=0, level=0, klass=0, result=0, bank=1,
                pos=(26, 1, 4, 9), name="A"):
    block = bytearray(CRYSTAL_CLASS_OFF + 1)
    block[0] = species
    block[CRYSTAL_LEVEL_OFF] = level
    block[CRYSTAL_MODE_OFF] = mode
    block[CRYSTAL_CLASS_OFF] = klass
    return (name, [bytes(pos), bytes(block), bytes([result]), bytes([bank])])


def test_crystals_battle_flag_is_a_mode_byte_so_the_mask_must_carry_both_bits():
    """0 overworld, 1 wild, 2 trainer. mask=0x01 passes every wild battle and
    reports the rival fight as the overworld — the silent battle_mask typo in
    a new costume, and the reason the live check carries a trainer state."""
    def seen(raw: int, mask: int) -> bool:
        c = dataclasses.replace(CRYSTAL, battle_mask=mask)
        return trace.decode_samples([crystal_row(mode=raw)], None, c)[0]["in_battle"]

    assert CRYSTAL.battle_mask == 0x03
    assert [seen(v, 0x03) for v in (0, 1, 2)] == [False, True, True]
    assert [seen(v, 0x01) for v in (0, 1, 2)] == [False, True, False]


def test_crystals_mode_byte_is_the_kind_as_well_as_the_flag():
    """One value answers "is a battle on" and "whose is it" — 9 of 9 live
    segments against the game's own "Wild X appeared!" / "X wants to battle!"
    wording. battle_kind_trainer is a MASK over the same byte, so a mask of
    0x01 would call every WILD fight a trainer's and every trainer fight wild:
    the inverted answer, not a missing one."""
    assert CRYSTAL.battle_kind.offset == CRYSTAL.battle_flag.offset
    assert CRYSTAL.battle_kind_trainer == 0x02
    kinds = [trace.decode_samples([crystal_row(mode=m)], None, CRYSTAL)[0]["battle_kind"]
             for m in (0, 1, 2)]
    assert kinds == [None, "wild", "trainer"], "mode 0 is not a battle, so it names no kind"


def test_crystal_reads_the_foe_and_the_trainer_class_only_while_a_battle_runs():
    """Measured stale: 0xd22f still read 22 (YOUNGSTER) and 9 (RIVAL1) several
    presses into the overworld after those battles ended, and wEnemyMon holds
    the last opponent. Gated, the same rule gen 3 needed."""
    live = trace.decode_samples([crystal_row(mode=2, species=16, level=2, klass=22)],
                                None, CRYSTAL)[0]
    assert (live["foe_species"], live["foe_level"]) == (16, 2)   # Pidgey, Lv 2
    assert live["trainer_class"] == 22 and live["trainer_id"] is None
    wild = trace.decode_samples([crystal_row(mode=1, species=41, level=3, klass=22)],
                                None, CRYSTAL)[0]
    assert wild["trainer_class"] is None, "a wild fight names no trainer, stale byte or not"
    over = trace.decode_samples([crystal_row(mode=0, species=16, level=2, klass=22)],
                                None, CRYSTAL)[0]
    assert over["foe_species"] is None and over["trainer_class"] is None


def test_a_sample_taken_on_the_wrong_wram_bank_is_unreadable_not_overworld():
    """The GBC pages 0xd000-0xdfff, so a read taken on another bank is a
    DIFFERENT memory rather than a failed one. Live: 84 of 1,318 Crystal samples
    read bank 5 or 6, every one of them also carrying the phantom (0,0) map, and
    their 0xd22d came back 0, 2, 5, 6, 47, 63, 122, 216, 249, 252 and 255 — a
    zero mid-battle and a two on a route, from the same mechanism.

    Refused means every field None, INCLUDING in_battle: False would say
    "the overworld", which is the half of the failure that put four in-battle
    samples on a walk in the 2026-09-20 run."""
    ok = trace.decode_samples([crystal_row(mode=2, species=10, bank=1)], None, CRYSTAL)[0]
    assert ok["in_battle"] is True and ok["foe_species"] == 10
    for bank in (0, 2, 5, 6, 7):
        bad = trace.decode_samples([crystal_row(mode=2, species=10, bank=bank)],
                                   None, CRYSTAL)[0]
        assert bad["in_battle"] is None and bad["foe_species"] is None
        assert bad["x"] is None and bad["map_group"] is None
    assert CRYSTAL.bank_value == 1


def test_a_missing_bank_register_fails_open_rather_than_blanking_the_turn():
    """The guard refuses the MEASURED event — the register present and saying
    another bank. A spec entry that never arrived is the short-read case, which
    the per-field rule already covers; failing closed on it would throw away
    every turn on a backend that drops the last range."""
    short = ("A", [bytes([26, 1, 4, 9]), bytes(CRYSTAL_CLASS_OFF + 1), b"\x00"])
    d = trace.decode_samples([short], None, CRYSTAL)[0]
    assert (d["map_group"], d["map_num"], d["y"], d["x"]) == (26, 1, 4, 9)
    assert d["in_battle"] is False


def test_crystal_and_gen_3_disagree_about_what_an_outcome_byte_means():
    """The two enums OVERLAP without agreeing, which is the only shape a
    per-game table is needed for: read Crystal's wBattleResult through gen 3's
    B_OUTCOME and a LOSS reads "won" and an escape reads "lost", both legal
    names, nothing to raise on."""
    assert CRYSTAL.outcome_names == {0: "won", 1: "lost", 2: "ran"}
    assert FIRERED.outcome_names is None and EMERALD.outcome_names is None
    from src.app.route import _TRACE_OUTCOMES
    for byte in (1, 2):
        assert _TRACE_OUTCOMES[byte] != CRYSTAL.outcome_names[byte]


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


# --- the gen-4 battle block ---------------------------------------------------
#
# Every address below was MEASURED (v2-experiments/gen4_battle_probe.py,
# gen4_battle_walk.py, gen4_battle_analyse.py) against the screen and never
# against another byte, so the tests state them as literals here rather than
# importing the module's own constants: a test that recomputed the address the
# way the contract does would agree with any mutation of it.
PLATINUM_OVERLAY = 0x022A647C
PLATINUM_FOE_SPECIES = 0x022C57EC       # battleMons[1].species, commit 25e5a6a
PLATINUM_FOE_LEVEL = 0x022C5820         # ... + 0x34, 16/16 against the HUD plate
PLATINUM_PLAYER_LEVEL = 0x022C5760      # battleMons[0].level, the same offset
PLATINUM_BATTLE_TYPE = 0x022BF99C       # BattleSystem + 0x44, 25/25 battles
PLATINUM_TRAINER_ID = 0x022BFA12        # BattleSystem + 0xba, trainers[1]
PLATINUM_OUTCOME = 0x022C1D90           # BattleSystem struct + 0x2420, the one byte
                                        # that changed on the press that ended a
                                        # driven battle (2026-09-21, five battles)
SOULSILVER_PACKED = 0x021D05C8          # foe species low, own active species high;
                                        # the spec samples only the low half, which is
                                        # the flag AND the species
SOULSILVER_FOE_SPECIES = 0x022C60D8     # battleMons[1].species, 315/315 = the above
SOULSILVER_FOE_LEVEL = 0x022C610C       # ... + 0x34, 8/8 against the HUD plate


def spec_ranges(contract):
    """``[(base, length)]`` read off the contract's OWN spec strings.

    Gen 4 needs no pointer — the DS heap layout is deterministic across a map
    load — so every entry here is a flat ``<addr>:<len>`` and this parse is
    total. Asserting that keeps a future pointer form from being silently
    mis-parsed into a plausible base address.
    """
    out = []
    for entry in contract.spec:
        assert "*" not in entry, f"{contract.game} grew a pointer spec: {entry}"
        addr, _, length = entry.partition(":")
        out.append((int(addr, 0), int(length, 0)))
    return out


def gen4_row(contract, name="A", **at):
    """One trace row for a gen-4 contract, written at ABSOLUTE addresses.

    ``at`` is ``{"0x22c57ec": ("<H", 396)}`` spelled as keyword-safe hex. The
    point of writing by address rather than by ``Field.offset`` is that the test
    then fails when a Field moves, when a spec range shrinks past the field it
    is supposed to cover, and when a measured address is edited — three
    different mistakes, one oracle. An address outside every range is an error
    rather than a silent no-op, which is the spec-shrank case.
    """
    ranges = spec_ranges(contract)
    blobs = [bytearray(n) for _, n in ranges]
    for key, (fmt, value) in at.items():
        addr = int(key, 0)
        for i, (base, n) in enumerate(ranges):
            if base <= addr and addr + struct.calcsize(fmt) <= base + n:
                struct.pack_into(fmt, blobs[i], addr - base, value)
                break
        else:
            raise AssertionError(
                f"{addr:#x} is outside every {contract.game} spec range "
                f"{[(hex(b), hex(n)) for b, n in ranges]}")
    return (name, [bytes(b) for b in blobs])


def platinum_row(*, overlay=16, kind=0, trainer=0, species=0, level=0, outcome=0):
    return gen4_row(
        PLATINUM,
        **{hex(PLATINUM_OVERLAY): ("<I", overlay),
           hex(PLATINUM_BATTLE_TYPE): ("<I", kind),
           hex(PLATINUM_TRAINER_ID): ("<H", trainer),
           hex(PLATINUM_FOE_SPECIES): ("<H", species),
           hex(PLATINUM_FOE_LEVEL): ("<B", level),
           hex(PLATINUM_OUTCOME): ("<B", outcome)})


def test_the_gen_4_battle_fields_are_offsets_into_two_named_heap_blocks():
    """The structural claim, and the one that separates this from a byte that
    correlates with battle state.

    Both cartridges allocate a BattleContext of 0x3168 bytes and a BattleSystem
    of ~0x24a0, at addresses that were identical in every state dumped, and
    battleMons[0] sits 0x2d58 into the context's data on BOTH. The check that
    makes that a measurement rather than a story: adding the struct offsets to
    SoulSilver's context base has to land on the species address that a 4 MB
    search for (my species, foe species, both levels) returned as the unique
    hit — and adding them to Platinum's has to land on the species address
    commit 25e5a6a measured a day earlier against 20 samples and 4 species,
    which nothing in this change was allowed to move.
    """
    from src.referee.contracts import (_G4_BATTLE_MON_LEVEL, _G4_BATTLE_MON_SIZE,
                                       _G4_BATTLE_MONS, _G4_BATTLE_TYPE, _G4_FOE_OFF,
                                       _PLATINUM_BATTLE_CONTEXT, _PLATINUM_BATTLE_SYSTEM,
                                       _SOULSILVER_BATTLE_CONTEXT)
    # _G4_FOE_OFF is the module's OWN spelling of "battler 1", and it is read
    # here rather than recomputed: a test that rebuilt the offset the way the
    # contract does would agree with any mutation of it, which is how the first
    # version of this file let a foe read slide onto the player's battler.
    assert _G4_FOE_OFF == _G4_BATTLE_MONS + _G4_BATTLE_MON_SIZE
    foe = _G4_FOE_OFF
    assert _PLATINUM_BATTLE_CONTEXT + foe == PLATINUM_FOE_SPECIES
    assert _PLATINUM_BATTLE_CONTEXT + foe + _G4_BATTLE_MON_LEVEL == PLATINUM_FOE_LEVEL
    assert _PLATINUM_BATTLE_CONTEXT + _G4_BATTLE_MONS + _G4_BATTLE_MON_LEVEL \
        == PLATINUM_PLAYER_LEVEL
    assert _PLATINUM_BATTLE_SYSTEM + _G4_BATTLE_TYPE == PLATINUM_BATTLE_TYPE
    assert _SOULSILVER_BATTLE_CONTEXT + foe == SOULSILVER_FOE_SPECIES
    assert _SOULSILVER_BATTLE_CONTEXT + foe + _G4_BATTLE_MON_LEVEL == SOULSILVER_FOE_LEVEL
    # And the two contracts read the struct at the SAME offsets, because it is
    # the same struct — HGSS is the Platinum engine, measured here rather than
    # assumed from the box.
    assert PLATINUM.foe_level.offset == SOULSILVER.foe_level.offset == _G4_BATTLE_MON_LEVEL


def test_the_gen_4_outcome_byte_is_the_decomps_field_and_not_a_new_address():
    """Where the outcome sits, spelled the way the two decomps spell it.

    pokeheartgold publishes ``BattleSystem.battleOutcomeFlag`` at struct
    +0x2420 and pokeplatinum calls the same field ``resultMask``. The contract
    keeps its offsets in the OTHER frame — from the allocator header + 8, which
    an earlier note called the block's "data" — and the two frames differ by
    0x18, which is not a fudge: subtracting it from each of the three offsets
    already in the contract lands on a published field, and the game's own
    BattleSystem->battleCtx pointer reads the BattleContext header + 0x20.

    So this test pins the ARITHMETIC against the address that was measured by
    diffing the whole allocation across the press that ends a battle. Change
    either the frame or the offset and it fails."""
    from src.referee.contracts import (_G4_BATTLE_MONS, _G4_BATTLE_OUTCOME,
                                       _G4_BATTLE_TYPE, _G4_STRUCT_FROM_DATA,
                                       _G4_TRAINERS, _PLATINUM_BATTLE_CONTEXT,
                                       _PLATINUM_BATTLE_SYSTEM)
    assert _PLATINUM_BATTLE_SYSTEM + _G4_BATTLE_OUTCOME == PLATINUM_OUTCOME
    # the same 0x18 explains every other gen-4 offset in the module
    assert _G4_BATTLE_TYPE - _G4_STRUCT_FROM_DATA == 0x2C     # ::battleType
    assert _G4_TRAINERS - _G4_STRUCT_FROM_DATA == 0xA0        # ::trainerIDs
    assert _G4_BATTLE_MONS - _G4_STRUCT_FROM_DATA == 0x2D40   # ::battleMons
    assert _G4_BATTLE_OUTCOME - _G4_STRUCT_FROM_DATA == 0x2420
    # and the outcome is in the BattleSystem block, not the context one
    assert _PLATINUM_BATTLE_CONTEXT > PLATINUM_OUTCOME


def test_gen_4s_outcome_enum_is_not_gen_3s_and_the_two_disagree():
    """Reading gen 4's byte through gen 3's B_OUTCOME renames two of the six.

    Gen 3: 4 is RAN, 7 is CAUGHT. Gen 4: 4 is MON_CAUGHT and 5 is PLAYER_FLED,
    which gen 3's table calls "teleported". So a Platinum run that fled would
    be published as a teleport and a caught mon as an escape — both legal
    values, both wrong, nothing to raise on. This is Crystal's lesson applied
    before it could cost anything."""
    from src.app.route import _TRACE_OUTCOMES
    assert PLATINUM.outcome_names is not None, "gen 4 must not inherit B_OUTCOME"
    assert PLATINUM.outcome_names[5] == "ran" and _TRACE_OUTCOMES[5] == "teleported"
    assert PLATINUM.outcome_names[4] == "caught" and _TRACE_OUTCOMES[4] == "ran"
    assert PLATINUM.outcome_names[1] == "won" and PLATINUM.outcome_names[2] == "lost"


def test_platinum_reads_its_outcome_only_while_the_battle_is_still_up():
    """The opposite of gen 3's rule, because gen 4 frees the block.

    Gen 3 writes the outcome as the fight closes and keeps it, so its contract
    reads the byte OUTSIDE the flag. Gen 4 hands the whole 0x2494 allocation
    back when the battle overlay unloads, and the address then reads whatever
    moved in — 0x78 on every one of the five driven battles, which is not a
    stale outcome but a different object. Ungated it would decode as an outcome
    byte on every overworld press for the rest of the run."""
    live = trace.decode_samples([platinum_row(outcome=2, species=396, level=3)],
                                None, PLATINUM)[0]
    assert live["in_battle"] is True and live["battle_outcome"] == 2

    freed = trace.decode_samples(
        [platinum_row(overlay=0xFFFFFFFF, outcome=0x78)], None, PLATINUM)[0]
    assert freed["in_battle"] is False
    assert freed["battle_outcome"] is None, \
        "the block is freed by here; 0x78 is the allocator's, not the game's"


def test_platinum_reads_the_kind_the_foe_and_the_trainer_only_while_a_battle_runs():
    """Measured stale, exactly as on Emerald and Crystal: with the overlay back
    to FS_OVERLAY_ID_NONE the freed battle heap still reads the last fight's
    leftovers — battle_type 36, a level-8 Bidoof and a level-72 "Chimchar" were
    live in the replay corpus on overworld presses. Ungated, a card would carry
    a foe nobody fought."""
    live = trace.decode_samples(
        [platinum_row(kind=1, trainer=1, species=396, level=5)], None, PLATINUM)[0]
    assert live["in_battle"] is True
    assert live["battle_kind"] == "trainer" and live["trainer_id"] == 1
    assert (live["foe_species"], live["foe_level"]) == (396, 5)   # Starly, Lv 5
    assert live["trainer_class"] is None, "gen 4 names a trainer by id, not by class"

    wild = trace.decode_samples(
        [platinum_row(kind=0, trainer=1, species=399, level=2)], None, PLATINUM)[0]
    assert wild["battle_kind"] == "wild"
    assert wild["trainer_id"] is None, "a wild fight names no trainer, stale word or not"

    over = trace.decode_samples(
        [platinum_row(overlay=0xFFFFFFFF, kind=36, trainer=1, species=399, level=8)],
        None, PLATINUM)[0]
    assert over["in_battle"] is False
    assert over["battle_kind"] is None and over["trainer_id"] is None
    assert over["foe_species"] is None and over["foe_level"] is None


def test_the_gen_4_trainer_bit_is_bit_zero_and_gen_3s_mask_erases_the_distinction():
    """BATTLE_TYPE_TRAINER is bit 0 here and bit 3 on gen 3, which is why
    `battle_kind_trainer` is per contract. The failure is not an inverted
    answer, it is a UNIFORM one: 1 & 0x08 is 0 and 0 & 0x08 is 0, so every
    Platinum battle — the rival included — comes back "wild", which is what the
    card said before this change and cannot be told from a correct wild run."""
    assert PLATINUM.battle_kind_trainer == 1
    assert EMERALD.battle_kind_trainer == 0x08

    def kinds(mask):
        c = dataclasses.replace(PLATINUM, battle_kind_trainer=mask)
        return [trace.decode_samples([platinum_row(kind=k, species=396, level=5)],
                                     None, c)[0]["battle_kind"] for k in (0, 1)]

    assert kinds(1) == ["wild", "trainer"]
    assert kinds(0x08) == ["wild", "wild"], "gen 3's mask loses every trainer battle"


def test_the_foe_level_is_the_opponents_slot_and_not_the_players():
    """battleMons[0] is the PLAYER's battler and battleMons[1] the opponent's,
    0xc0 apart. A spec that started one struct early would read a level that is
    real, plausible and the wrong battler's — which is invisible in a corpus
    where both sides are level 5, and that is most of the corpus."""
    from src.referee.contracts import (_G4_BATTLE_MON_SIZE, _G4_FOE_OFF,
                                       _PLATINUM_BATTLE_CONTEXT)
    base, length = spec_ranges(PLATINUM)[PLATINUM.foe_level.sample]
    assert base == PLATINUM_FOE_SPECIES == _PLATINUM_BATTLE_CONTEXT + _G4_FOE_OFF
    assert base + length > PLATINUM_FOE_LEVEL, "the range must still cover the level"

    # The same contract pointed one battler earlier, reading a blob in which the
    # player is level 50 and the foe level 5.
    early = dataclasses.replace(
        PLATINUM,
        spec=tuple(f"{PLATINUM_FOE_SPECIES - _G4_BATTLE_MON_SIZE:#x}:{length + _G4_BATTLE_MON_SIZE:#x}"
                   if i == PLATINUM.foe_level.sample else e
                   for i, e in enumerate(PLATINUM.spec)))
    row = gen4_row(early, **{hex(PLATINUM_OVERLAY): ("<I", 16),
                             hex(PLATINUM_PLAYER_LEVEL): ("<B", 50),
                             hex(PLATINUM_FOE_SPECIES): ("<H", 396),
                             hex(PLATINUM_FOE_LEVEL): ("<B", 5)})
    assert trace.decode_samples([row], None, early)[0]["foe_level"] == 50
    assert trace.decode_samples(
        [platinum_row(species=396, level=5)], None, PLATINUM)[0]["foe_level"] == 5


def test_soulsilver_reads_its_level_off_the_battle_mon_and_its_flag_off_the_packed_word():
    """Two addresses found by two independent searches, kept apart on purpose.

    0x021d05c8 packs the foe's species and the player's own into one u32 and
    carries no level; the BattleMon carries both. They agreed on the species in
    315 of 315 in-battle samples, which is corroboration — collapsing them into
    one read would throw that away and hand the flag to an address that was
    never tested as one."""
    def row(**kw):
        return gen4_row(SOULSILVER, **kw)

    live = trace.decode_samples(
        [row(**{hex(SOULSILVER_PACKED): ("<H", 163),
                hex(SOULSILVER_FOE_SPECIES): ("<H", 163),
                hex(SOULSILVER_FOE_LEVEL): ("<B", 3)})], None, SOULSILVER)[0]
    assert live["in_battle"] is True
    assert (live["foe_species"], live["foe_level"]) == (163, 3)   # Hoothoot, Lv 3

    over = trace.decode_samples(
        [row(**{hex(SOULSILVER_PACKED): ("<H", 0),
                hex(SOULSILVER_FOE_SPECIES): ("<H", 163),
                hex(SOULSILVER_FOE_LEVEL): ("<B", 3)})], None, SOULSILVER)[0]
    assert over["in_battle"] is False
    assert over["foe_species"] is None and over["foe_level"] is None


def test_soulsilver_claims_neither_a_kind_nor_an_outcome_and_platinum_claims_both():
    """Two absences on one cartridge, each a corpus result rather than an
    omission — and their presence on the other, which is what makes the
    absences a statement about evidence and not about the engine.

    THE KIND ON SOULSILVER. Its battleType is at 0x022c0238 by the same
    structural argument that works on Platinum, and it reads 0 in all 315
    in-battle samples the cartridge had produced — all 8 battles WILD, because
    HGSS's first trainer is past Cherrygrove and no run has left Route 29. A
    248-turn continuation on 2026-09-21 added 7 more battles and left the
    corpus at about 15, still 100% wild and still inside maps 33 and 60-66. A
    wild-only corpus cannot refute a wild/trainer discriminator, so wiring it
    would make every SoulSilver battle claim "wild" on an analogy — which is
    what commit f03dd4b took out.

    THE OUTCOME ON SOULSILVER. Same hole, one step further along: the offset
    is Platinum's measured one and the enum is the shared decomp's, but every
    battle this cartridge can produce is wild, so the trainer half of the
    outcome x kind table is empty. On Platinum that exact hole made a candidate
    score six for six and be wrong (0x022a64cc, commit a377f67). The
    measurement that unblocks both is one trainer battle, not one more search.

    PLATINUM HAS BOTH, and the outcome was measured with the decoupling
    controls that corpus lacked: a trainer battle WON and a wild battle LOST,
    the two cells 0x022a64cc never had.
    """
    assert SOULSILVER.battle_outcome is None and SOULSILVER.outcome_names is None
    assert SOULSILVER.battle_kind is None
    assert PLATINUM.battle_kind is not None, "Platinum's kind IS measured, 25/25"
    assert PLATINUM.battle_outcome is not None, "and so is its outcome, 5 driven battles"
    assert PLATINUM.outcome_while_in_battle is True
    # Nobody else reads the outcome from inside the fight: gens 1-3 write it at
    # the close and keep it, and reading THOSE early gets the previous fight's
    # result on 35 of 146 FireRed states.
    assert [c.game for c in CONTRACTS.values() if c.outcome_while_in_battle] \
        == ["platinum-us"]


def test_the_gen_4_battle_block_does_not_move_position_or_the_flag():
    """The same control FireRed's entry carries: the ranges added on top must
    leave every field the old spec read exactly where it was, because a sample
    index is a position in the spec list and every run already recorded stores
    its samples decoded against one.
    """
    for contract, before in ((PLATINUM, 2), (SOULSILVER, 1)):
        assert contract.map_id.sample == contract.x.sample == contract.y.sample == 0
        assert contract.battle_flag.sample < before or contract.game == "soulsilver-us"
        assert spec_ranges(contract)[0] == (int(contract.spec[0].split(":")[0], 0), 16)
    d = trace.decode_samples(
        [gen4_row(PLATINUM, **{hex(0x0227F408): ("<i", 343),
                               hex(0x0227F408 + 8): ("<i", 166),
                               hex(0x0227F408 + 12): ("<i", 817),
                               hex(PLATINUM_OVERLAY): ("<I", 16)})], None, PLATINUM)[0]
    assert (d["map_id"], d["x"], d["y"]) == (343, 166, 817)
    assert d["map_group"] is None and d["map_num"] is None


# --- gen 5 ------------------------------------------------------------------
#
# Addresses spelled out once, so a test fails when a measured number is edited
# and not only when a Field moves. Every one was read off a labelled dump; the
# score behind them is in the contract's own comment block.
BLACK_ACTOR = 0x0224F90C
BLACK_FOE_SPECIES = 0x0226D8D4          # btl_pokeparam.c data 0x0226d8c0 + 0x14
BLACK_FOE_LEVEL = 0x0226D8E0            # ... + 0xc, 33/33 against the HUD plate
BLACK_TRAINER_NAME = 0x022697B8         # battle proc work 0x02269760 + 0x58
BLACK2_FOE_SPECIES = 0x0225B414         # btl_pokeparam.c data 0x0225b400 + 0x14
BLACK2_FOE_LEVEL = 0x0225B420
BLACK2_TRAINER_NAME = 0x022572EC        # battle proc work 0x02257294 + 0x58
BLACK2_IN_BATTLE = 0x0213B2E0


def black_row(*, species=0, level=0, name_ptr=0):
    return gen4_row(BLACK, **{hex(BLACK_FOE_SPECIES): ("<H", species),
                              hex(BLACK_FOE_LEVEL): ("<B", level),
                              hex(BLACK_TRAINER_NAME): ("<I", name_ptr)})


def black2_row(*, flag=1, species=0, level=0, name_ptr=0):
    return gen4_row(BLACK2, **{hex(BLACK2_IN_BATTLE): ("<B", flag),
                               hex(BLACK2_FOE_SPECIES): ("<H", species),
                               hex(BLACK2_FOE_LEVEL): ("<B", level),
                               hex(BLACK2_TRAINER_NAME): ("<I", name_ptr)})


def test_the_gen_5_battle_fields_are_offsets_into_named_heap_blocks():
    """The structural claim, and what separates this from a byte that
    correlates with battle state — which on THIS cartridge is a measured
    quantity: `local/battleflag/black/NOTES.md` is 18 hours and 14,670
    indistinguishable candidates for a byte chosen that way.

    Gen 5 tags every heap block with the source file that asked for it, so both
    battle fields are an OFFSET into a named allocation. The level is the
    species' own btl_pokeparam.c block read 0xc further in, and the kind is the
    battle proc's work read 0x58 in — the SAME two offsets on both cartridges,
    which is why they are one pair of constants and not two that agree.
    """
    from src.referee.contracts import (_BLACK_BATTLE_PROC, _BLACK_FOE_SPECIES,
                                       _BLACK2_BATTLE_PROC, _BLACK2_FOE_SPECIES,
                                       _G5_FOE_LEN, _G5_FOE_LEVEL, _G5_TRAINER_NAME)
    # Read the module's OWN offsets rather than recomputing them: a test that
    # rebuilt them the way the contract does would agree with any mutation.
    assert (_BLACK_FOE_SPECIES, _BLACK2_FOE_SPECIES) == (BLACK_FOE_SPECIES,
                                                         BLACK2_FOE_SPECIES)
    assert _BLACK_FOE_SPECIES + _G5_FOE_LEVEL == BLACK_FOE_LEVEL
    assert _BLACK2_FOE_SPECIES + _G5_FOE_LEVEL == BLACK2_FOE_LEVEL
    assert _BLACK_BATTLE_PROC + _G5_TRAINER_NAME == BLACK_TRAINER_NAME
    assert _BLACK2_BATTLE_PROC + _G5_TRAINER_NAME == BLACK2_TRAINER_NAME
    # One measurement, two cartridges: the Fields must read the same offsets.
    assert BLACK.foe_level.offset == BLACK2.foe_level.offset == _G5_FOE_LEVEL
    assert BLACK.battle_kind.offset == BLACK2.battle_kind.offset == 0
    # And the species range has to still reach the level it now carries.
    for contract, species, level in ((BLACK, BLACK_FOE_SPECIES, BLACK_FOE_LEVEL),
                                     (BLACK2, BLACK2_FOE_SPECIES, BLACK2_FOE_LEVEL)):
        base, length = spec_ranges(contract)[contract.foe_level.sample]
        assert base == species and base + length > level
        assert length == _G5_FOE_LEN


def test_gen_5_reads_the_kind_the_foe_and_the_level_only_while_a_battle_runs():
    """Gating is load-bearing here in a way it is not on gen 3.

    Gen 5 FREES the whole battle heap when the fight ends — the btl_pokeparam
    block and the battle proc's work are absent from every overworld dump, no
    allocation at those headers at all — so an ungated read is not "the last
    fight's leftovers", it is whatever the allocator has since put there. On
    Black the trainer-name pointer reads 0x50 in every overworld state
    measured, which is non-zero and would call every overworld press a trainer
    battle if the kind were ever reported bare.
    """
    live = trace.decode_samples([black_row(species=504, level=7,
                                           name_ptr=0x02270F04)], None, BLACK)[0]
    assert live["in_battle"] is True
    assert (live["battle_kind"], live["foe_species"], live["foe_level"]) == (
        "trainer", 504, 7)          # the Youngster's Patrat Lv7, Route 2

    wild = trace.decode_samples([black_row(species=506, level=2)], None, BLACK)[0]
    assert wild["battle_kind"] == "wild" and wild["foe_level"] == 2

    over = trace.decode_samples([black_row(species=0, level=7,
                                           name_ptr=0x50)], None, BLACK)[0]
    assert over["in_battle"] is False
    assert over["battle_kind"] is None and over["foe_level"] is None

    hugh = trace.decode_samples([black2_row(species=501, level=5,
                                            name_ptr=0x0225EA4C)], None, BLACK2)[0]
    assert (hugh["battle_kind"], hugh["foe_species"], hugh["foe_level"]) == (
        "trainer", 501, 5)          # Hugh's Oshawott Lv5, the one reachable battle
    b2_over = trace.decode_samples([black2_row(flag=0, species=501, level=5,
                                               name_ptr=0x50)], None, BLACK2)[0]
    assert b2_over["battle_kind"] is None and b2_over["foe_level"] is None


def test_the_gen_5_kind_is_a_whole_pointer_and_not_one_of_its_address_bits():
    """battle_kind_trainer is 0xffffffff here because the test is "the
    trainer-name pointer is not NULL". Masking a pointer down to a bit would be
    picking one of its ADDRESS bits, and the failure is silent: Black's name
    buffer landed at 0x02270f04 in all four trainer battles, so a mask of 0x01
    or 0x02 reads "wild" for every trainer on the cartridge — which is exactly
    what the card said before this change and cannot be told from a run that
    happened to fight nothing.
    """
    assert BLACK.battle_kind_trainer == BLACK2.battle_kind_trainer == 0xFFFFFFFF

    def kinds(mask):
        c = dataclasses.replace(BLACK, battle_kind_trainer=mask)
        return [trace.decode_samples([black_row(species=504, level=7, name_ptr=p)],
                                     None, c)[0]["battle_kind"]
                for p in (0, 0x02270F04)]

    assert kinds(0xFFFFFFFF) == ["wild", "trainer"]
    assert kinds(0x01) == ["wild", "wild"], "every trainer battle lost to an address bit"
    assert kinds(0x02) == ["wild", "wild"]


def test_neither_gen_5_cartridge_claims_an_outcome_or_a_trainer():
    """Two gaps, two different reasons, both worth a failing test if someone
    fills them without the measurement.

    THE OUTCOME, and after 2026-09-20 the reason is NOT the pointer bound. That
    bound is fixed — the backend's dereference window is per console now, and
    the chase into the DS heap works; it reads the BATTLE_SETUP_PARAM's own
    competitor word and agrees with battle_kind on 352/352 in-battle presses.
    What it does not reach is a result. The param is the only carrier, the
    CALLER allocates it (four battles, four addresses: 0x0225d610, 0x02259750,
    0x0225ddd4, 0x0225dc60), it is byte-identical from a battle's first
    in-battle sample to its last, and by the first sample after the flag clears
    it is zeroed or already reallocated to a ``msgdata.`` block with every
    pointer to it gone. Gen 5 acts on the result while the flag is still SET —
    the whiteout line plays in-battle — so there is no "after" to read.

    THE TRAINER. The battle proc work carries the opponent's record at +0x5c
    and +0x5e, zero in all 30 wild battles and populated in all 4 trainer ones.
    One is the id and the other the class, and all four Black trainers differ
    in both, so nothing in this corpus marks which. Writing the class into
    trainer_id is the keyspace mistake Crystal's trainer_class exists to
    prevent.
    """
    for contract in (BLACK, BLACK2):
        assert contract.battle_outcome is None and contract.outcome_names is None
        assert contract.trainer_id is None and contract.trainer_class is None
        assert contract.battle_kind is not None, "the kind IS measured, 34/34"
        assert contract.foe_level is not None


def test_the_gen_5_battle_block_does_not_move_position_or_the_flag():
    """The control every cartridge's entry carries: ranges added on top must
    leave the fields the old spec read exactly where they were, because a
    sample index is a position in the spec list and every run already recorded
    stores its samples decoded against one. Black's flag and species are the
    SAME field — sample 1 offset 0 — so widening that range to reach the level
    is the change most likely to move them silently.
    """
    for contract in (BLACK, BLACK2):
        assert contract.map_id.sample == contract.x.sample == contract.y.sample == 0
        assert spec_ranges(contract)[0] == (int(contract.spec[0].split(":")[0], 0), 16)
    assert BLACK.battle_flag.sample == BLACK.foe_species.sample == 1
    assert BLACK.battle_flag.offset == BLACK.foe_species.offset == 0
    assert BLACK2.battle_flag.sample == 1 and BLACK2.foe_species.sample == 2
    assert spec_ranges(BLACK2)[1] == (BLACK2_IN_BATTLE, 1)
    d = trace.decode_samples(
        [gen4_row(BLACK, **{hex(BLACK_ACTOR): ("<I", 319),
                            hex(BLACK_ACTOR + 4): ("<i", 754 << 16),
                            hex(BLACK_ACTOR + 12): ("<i", 636 << 16)})], None, BLACK)[0]
    assert (d["map_id"], d["x"], d["y"]) == (319, 754, 636)   # Route 2, the Youngster's tile
    assert d["map_group"] is None and d["map_num"] is None
