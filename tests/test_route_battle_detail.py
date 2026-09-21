"""A battle card's DETAIL, built from the trace rather than from the referee.

Until 2026-09-20 everything a card shows past "a fight happened here" came from
``referee_battle_state``, which only a run with the FireRed gate ladder attached
emits. A casual run — which is what the control centre launches when no "ends
at" event is picked — wrote none, so every card on every cartridge read
"Battle · wild or trainer, this game does not say yet", including 405-turn
FireRed runs.

The fields now ride on the per-turn TRACE, which every run has, through the
per-game contract. What each cartridge can say is therefore a property of its
contract and nothing else, and these tests are written against the decoded
sample shape so they pin the JOIN rather than the addresses (those are measured
in v2-experiments/emerald_battle_probe.py against screenshots).

The three rules that are easy to get wrong, and each has a test:

  * the kind and the trainer come from the FIRST in-battle sample, because the
    intro is the one moment they are unambiguous;
  * the outcome comes from the sample AFTER the flag clears, because that is
    where the game writes it — taken during the fight it is 0 or, in the intro,
    the PREVIOUS fight's result. Gen 4 inverts this and the contract says so:
    it frees the battle heap at the close, so the sample after it reads another
    object (0x78 on Platinum) and the answer is the last non-zero value from
    INSIDE the fight;
  * a trainer NAME is FireRed's alone. Trainer 114 is a different person on
    every cartridge.
"""
from __future__ import annotations

import pytest

from src.app import route as R


def s(i, x, y, *, batt=False, kind=None, foe=None, lvl=None, out=None, tid=None, cls=None):
    return {"i": i, "input": "D", "map_group": 0, "map_num": 9, "map_id": None,
            "x": x, "y": y, "in_battle": batt, "battles_total": None,
            "foe_species": foe, "foe_level": lvl, "battle_kind": kind,
            "battle_outcome": out, "trainer_id": tid, "trainer_class": cls}


def g4(i, x, y, **kw):
    """A gen-4 sample: one map ID and no (group, number) pair. The tile key has
    a different SHAPE on this generation, so a card built from gen-3-shaped
    rows would pass a test that the real decoder's output fails."""
    row = s(i, x, y, **kw)
    row.update({"map_group": None, "map_num": None, "map_id": 343})
    return row


def unreadable(i):
    """What a Crystal sample taken on the wrong WRAM bank decodes to: every
    field None, in_battle included (src/referee/trace.py)."""
    return {"i": i, "input": "D", "map_group": None, "map_num": None, "map_id": None,
            "x": None, "y": None, "in_battle": None, "battles_total": None,
            "foe_species": None, "foe_level": None, "battle_kind": None,
            "battle_outcome": None, "trainer_id": None, "trainer_class": None}


def turns(*rows):
    return {t: {"samples": list(ss), "poll": None, "battle": None} for t, ss in rows}


def test_a_wild_battle_carries_its_foe_and_how_it_ended():
    per_turn = turns(
        (1, [s(0, 5, 5)]),
        (2, [s(0, 5, 5, batt=True, kind="wild", foe=286, lvl=3),
             s(1, 5, 5, batt=True, kind="wild", foe=286, lvl=3)]),
        (3, [s(0, 5, 5, out=1)]),                      # B_OUTCOME_WON
    )
    b, = R._battles_from_trace(per_turn, game="emerald-us")
    assert b["kind"] == "wild"
    assert b["foe"] == {"species": 286, "level": 3}     # Poochyena, Lv 3
    assert b["outcome"] == "won" and b["won"] is True
    assert (b["opened_turn"], b["closed_turn"], b["turns"]) == (2, 2, 1)
    assert b["tile"] == [0, 9, 5, 5], "the tile is where the grass was walked into"
    assert b["trainer_id"] is None and b["trainer"] is None


def test_a_trainer_battle_names_the_trainer_and_only_on_firered():
    per_turn = turns(
        (1, [s(0, 5, 5)]),
        (2, [s(0, 5, 5, batt=True, kind="trainer", foe=1, lvl=5, tid=327)]),
        (3, [s(0, 5, 5, out=1)]),
    )
    fr, = R._battles_from_trace(per_turn, game="firered-us")
    assert (fr["kind"], fr["trainer_id"]) == ("trainer", 327)
    assert fr["trainer"] == "Rival (Oak's Lab)", "FireRed's own roster names 327"

    em, = R._battles_from_trace(per_turn, game="emerald-us")
    assert (em["kind"], em["trainer_id"]) == ("trainer", 327)
    assert em["trainer"] is None, \
        "327 is a different person on Emerald — the browser labels it from that game's index"


def test_the_outcome_is_read_where_the_game_writes_it_not_during_the_fight():
    # The intro of a fight still holds the PREVIOUS fight's result: 35 of 146
    # FireRed mid-battle states (src/referee/battles.py). Reading the segment's
    # own samples would report the run's second battle as "lost" because its
    # first one was.
    per_turn = turns(
        (1, [s(0, 5, 5)]),
        (2, [s(0, 5, 5, batt=True, kind="wild", foe=290, out=2),   # stale "lost"
             s(1, 5, 5, batt=True, kind="wild", foe=290, out=0)]),
        (3, [s(0, 5, 5, out=7)]),                                  # B_OUTCOME_CAUGHT
    )
    b, = R._battles_from_trace(per_turn, game="emerald-us")
    assert b["outcome"] == "caught"
    assert b["won"] is True, "a catch is not a loss, whatever the intro byte said"


def test_ran_and_fled_are_neither_won_nor_lost():
    for byte, name in ((4, "ran"), (6, "mon_fled"), (3, "drew")):
        per_turn = turns((1, [s(0, 1, 1)]),
                         (2, [s(0, 1, 1, batt=True, kind="wild", foe=290)]),
                         (3, [s(0, 1, 1, out=byte)]))
        b, = R._battles_from_trace(per_turn, game="emerald-us")
        assert b["outcome"] == name
        assert b["won"] is None, f"{name} must not collapse to a loss"


def test_the_kind_survives_a_battle_whose_later_samples_say_nothing():
    # Menus and animations run mid-fight and a sample taken there can read the
    # flag without the type word. First writer wins, so the card keeps the kind.
    per_turn = turns(
        (1, [s(0, 2, 2)]),
        (2, [s(0, 2, 2, batt=True, kind="trainer", tid=603, foe=306, lvl=4),
             s(1, 2, 2, batt=True), s(2, 2, 2, batt=True, foe=306, lvl=4)]),
        (3, [s(0, 2, 2, out=2)]),
    )
    b, = R._battles_from_trace(per_turn, game="emerald-us")
    assert (b["kind"], b["trainer_id"]) == ("trainer", 603)
    assert b["outcome"] == "lost" and b["won"] is False


def test_a_cartridge_with_no_battle_fields_still_gets_its_segment_and_claims_nothing():
    # The degradation every run before 2026-09-20 takes, and every game whose
    # contract has only the flag. The segment is real; the detail is absent
    # rather than guessed.
    bare = lambda i, batt: {**s(i, 3, 3, batt=batt), "foe_species": None,
                            "battle_kind": None, "battle_outcome": None}
    per_turn = turns((1, [bare(0, False)]), (2, [bare(0, True)]), (3, [bare(0, False)]))
    b, = R._battles_from_trace(per_turn, game="crystal-us")
    assert b["kind"] is None and b["won"] is None
    assert "foe" not in b and "outcome" not in b
    assert (b["opened_turn"], b["turns"]) == (2, 1), "the segment is still measured"


def test_a_run_that_ends_mid_battle_has_no_outcome_to_read():
    per_turn = turns((1, [s(0, 4, 4)]),
                     (2, [s(0, 4, 4, batt=True, kind="wild", foe=288, lvl=7)]))
    b, = R._battles_from_trace(per_turn, game="emerald-us")
    assert b["closed_turn"] is None and "outcome" not in b
    assert b["foe"] == {"species": 288, "level": 7}, "what it DID see is still reported"


def test_the_referee_still_wins_where_it_ran():
    # place_battles falls back to the trace only when no referee poll exists.
    # A FireRed benchmark run has both, and the referee's segments are the ones
    # the board's numbers come from — two sources disagreeing on one card is
    # worse than one source saying less.
    per_turn = turns((1, [s(0, 5, 5)]), (2, [s(0, 5, 5, batt=True, kind="wild", foe=16)]))
    per_turn[2]["battle"] = {"turn": 2, "in_battle": True, "battles_total": 1,
                             "wild_battles": 1, "trainer_battles": 0}
    got = R.place_battles(per_turn, [[1, 0, 3, 0, 5, 5, 0]], frozenset(), "firered-us")
    assert got and all("battle_kind" not in b for b in got)


def test_an_invalid_map_is_not_a_battle_tile():
    # Crystal's phantom (0,0) — a failed bank-switched read. build_route passed
    # `invalid` to the tile decoder and NOT to place_battles until 2026-09-20,
    # so a fight could still be pinned to a map the cartridge does not have.
    per_turn = turns(
        (1, [{**s(0, 7, 7), "map_group": 0, "map_num": 0}]),
        (2, [s(0, 9, 9)]),
        (3, [s(0, 9, 9, batt=True, kind="wild", foe=290)]),
        (4, [s(0, 9, 9, out=4)]),
    )
    b, = R._battles_from_trace(per_turn, frozenset({(0, 0)}), "crystal-us")
    assert b["tile"] == [0, 9, 9, 9], "the phantom map must not become the ambush tile"


# --- Crystal (gen 2): one battle, a different enum, and a refusable read -------

def test_an_unreadable_sample_does_not_cut_a_battle_in_half():
    """`in_battle` has three values and the third is not False. A sample the
    contract REFUSED — Crystal's wrong WRAM bank, 17 of them inside one
    79-sample trainer battle — closes nothing: read as a clear flag it would
    close the segment, write an outcome out of a field that is None, and open a
    fresh card on the next press, so one fight becomes eighteen.

    The tile matters as much as the count: a refused sample carries no tile, so
    it must not become the ambush tile either."""
    per_turn = turns(
        (1, [s(0, 5, 5)]),
        (2, [s(0, 5, 5, batt=True, kind="trainer", foe=16, lvl=2, cls=22),
             unreadable(1),
             s(2, 5, 5, batt=True, kind="trainer", foe=19, lvl=4, cls=22)]),
        (3, [s(0, 5, 5, out=0)]),
    )
    got = R._battles_from_trace(per_turn, game="crystal-us")
    assert len(got) == 1, "one fight, not one per readable run of samples"
    b, = got
    assert (b["opened_turn"], b["closed_turn"], b["turns"]) == (2, 2, 1)
    assert b["tile"] == [0, 9, 5, 5] and b["outcome"] == "won"


def test_crystal_reads_its_outcome_byte_through_its_own_enum():
    """pokecrystal's wBattleResult is 0 win / 1 lose / 2 draw and gen 3's
    B_OUTCOME is 1 win / 2 lose / 3 draw. The two overlap at every value they
    share and agree at none of them, so the wrong table renames a loss "won"
    and an escape "lost" — every name legal, nothing to raise on.

    Emerald is the control: the SAME bytes through the SAME code must keep
    saying what they said yesterday."""
    def crystal(byte):
        per_turn = turns((1, [s(0, 1, 1)]),
                         (2, [s(0, 1, 1, batt=True, kind="wild", foe=41, lvl=3)]),
                         (3, [s(0, 1, 1, out=byte)]))
        return R._battles_from_trace(per_turn, game="crystal-us")[0]

    assert [(crystal(b)["outcome"], crystal(b)["won"]) for b in (0, 1, 2)] == [
        ("won", True), ("lost", False), ("ran", None)]

    def emerald(byte):
        per_turn = turns((1, [s(0, 1, 1)]),
                         (2, [s(0, 1, 1, batt=True, kind="wild", foe=290)]),
                         (3, [s(0, 1, 1, out=byte)]))
        return R._battles_from_trace(per_turn, game="emerald-us")[0]

    assert [emerald(b).get("outcome") for b in (1, 2, 4)] == ["won", "lost", "ran"]


def test_a_run_with_no_outcome_field_is_not_a_run_of_victories():
    """Crystal's 0 means WON, and `.get(x or 0)` cannot tell a byte that read 0
    from a field the run never had. Gen 3's table has no entry for 0, so the
    bug was invisible until a cartridge gave 0 a meaning: every battle in every
    Crystal run recorded before 2026-09-20 would have been declared a win."""
    per_turn = turns((1, [s(0, 2, 2)]),
                     (2, [s(0, 2, 2, batt=True)]),
                     (3, [s(0, 2, 2)]))                 # battle_outcome is None
    b, = R._battles_from_trace(per_turn, game="crystal-us")
    assert "outcome" not in b and b["won"] is None


def test_crystal_reports_the_trainer_class_and_leaves_the_id_alone():
    """Gen 2 names a trainer by a (class, index) PAIR and has no single id, so
    the class travels in its own key. Putting 36 in `trainer_id` would hand the
    browser's roster — which is keyed by FireRed-style ids — a number out of a
    different keyspace, which is the cross-cartridge collision `trainer` is
    already gated against.

    And a wild fight carries neither, whatever the stale byte said."""
    per_turn = turns((1, [s(0, 3, 3)]),
                     (2, [s(0, 3, 3, batt=True, kind="trainer", foe=10, lvl=3, cls=36)]),
                     (3, [s(0, 3, 3, out=0)]))
    b, = R._battles_from_trace(per_turn, game="crystal-us")
    assert b["trainer_class"] == 36 and b["trainer_id"] is None and b["trainer"] is None
    assert b["foe"] == {"species": 10, "level": 3}      # Caterpie, Lv 3

    wild = turns((1, [s(0, 3, 3)]),
                 (2, [s(0, 3, 3, batt=True, kind="wild", foe=41, lvl=3)]),
                 (3, [s(0, 3, 3, out=2)]))
    w, = R._battles_from_trace(wild, game="crystal-us")
    assert "trainer_class" not in w and w["outcome"] == "ran"


def test_a_gen_4_card_names_the_trainer_the_foe_and_how_it_ended():
    """Platinum's samples carry a kind, a trainer id, a foe AND an outcome.

    The trainer id is real and per cartridge: 852 is the rival on Platinum, 1
    is Youngster Tristan and 3 is Lass Natalie, each matched to the name the
    intro PRINTS. The NAME still does not travel — TRAINER_NAMES is FireRed's
    roster, and 852 is somebody else there — so `trainer` stays None exactly as
    it does for Emerald.

    The outcome arrives from INSIDE the fight, which is the gen-4 rule: the
    battle heap is freed when the overlay unloads, so the sample after the flag
    clears — where gen 2 and gen 3 keep the answer — is already a different
    object. Here the last in-battle sample says 1 and the one after says 0x78,
    which is what the freed block actually read on all five driven battles."""
    per_turn = turns(
        (1, [g4(0, 4, 4)]),
        (2, [g4(0, 4, 4, batt=True, kind="trainer", foe=396, lvl=5, tid=1, out=0),
             g4(1, 4, 4, batt=True, kind="trainer", foe=396, lvl=5, tid=1, out=1)]),
        (3, [g4(0, 4, 4, out=0x78)]),
    )
    b, = R._battles_from_trace(per_turn, game="platinum-us")
    assert b["kind"] == "trainer" and b["trainer_id"] == 1
    assert b["trainer"] is None, "the id travels between cartridges, the name does not"
    assert b["foe"] == {"species": 396, "level": 5}         # Starly, Lv 5
    assert b["outcome"] == "won" and b["won"] is True
    assert not any(k.startswith("_") for k in b), "the carrier key must not be published"
    # A gen-4 map id takes the first slot of the tile key and the second stays
    # a constant zero (tests/test_route_multigame.py), so the ambush tile is
    # (map 343, 0, x, y) and not a (group, number) pair.
    assert b["turns"] == 1 and b["tile"] == [343, 0, 4, 4]


def test_gen_4_takes_the_outcome_from_inside_the_fight_not_from_after_it():
    """The mutation this test exists to catch: read the sample AFTER the close,
    the way every other cartridge does, and Platinum reports nothing at all.

    0x78 is not invented. It is what the byte at the outcome's address read on
    the first clear-flag sample of every driven battle on 2026-09-21 — the
    allocator handed the 0x2494 block to something else the moment the battle
    overlay unloaded. Gen 4's enum has no 4th entry past 6, so the old rule
    degrades to "no outcome"; a WIDER table would have published a number."""
    per_turn = turns(
        (1, [g4(0, 4, 4)]),
        (2, [g4(0, 4, 4, batt=True, kind="wild", foe=401, lvl=3, out=0),
             g4(1, 4, 4, batt=True, kind="wild", foe=401, lvl=3, out=5)]),
        (3, [g4(0, 4, 4, out=0x78)]),
    )
    b, = R._battles_from_trace(per_turn, game="platinum-us")
    assert b["outcome"] == "ran", "5 is PLAYER_FLED on gen 4"
    assert b["won"] is None, "fleeing is neither winning nor losing"

    # and the control: gen 3, where the same shape MUST read the after-sample.
    gen3 = turns((1, [s(0, 3, 3)]),
                 (2, [s(0, 3, 3, batt=True, kind="wild", foe=19, lvl=3, out=0)]),
                 (3, [s(0, 3, 3, out=1)]))
    g, = R._battles_from_trace(gen3, game="firered-us")
    assert g["outcome"] == "won"


def test_a_zero_inside_a_gen_4_fight_cannot_erase_a_decision():
    """The outcome is written a few frames BEFORE the close and holds, so the
    last non-zero value is the fight's. A zero after it would be "not decided
    yet", which is never true once it has been decided — and taking the LAST
    value unconditionally would file a won battle as unknown."""
    per_turn = turns(
        (1, [g4(0, 4, 4)]),
        (2, [g4(0, 4, 4, batt=True, kind="wild", foe=399, lvl=2, out=2),
             g4(1, 4, 4, batt=True, kind="wild", foe=399, lvl=2, out=0)]),
        (3, [g4(0, 4, 4, out=0x78)]),
    )
    b, = R._battles_from_trace(per_turn, game="platinum-us")
    assert b["outcome"] == "lost" and b["won"] is False


def test_a_gen_4_flee_is_not_a_teleport_and_a_caught_mon_is_not_an_escape():
    """The enums overlap and disagree, which is Crystal's lesson at a new
    address. Gen 3's B_OUTCOME reads 4 as RAN and has no 5 but "teleported";
    gen 4 reads 4 as MON_CAUGHT and 5 as PLAYER_FLED. Published through the
    wrong table, a Platinum escape becomes a teleport and a capture becomes an
    escape — every value legal, both wrong."""
    def outcome_for(raw):
        per_turn = turns((1, [g4(0, 4, 4)]),
                         (2, [g4(0, 4, 4, batt=True, kind="wild", foe=399, lvl=2,
                                 out=raw)]),
                         (3, [g4(0, 4, 4, out=0x78)]))
        b, = R._battles_from_trace(per_turn, game="platinum-us")
        return b.get("outcome")
    assert outcome_for(4) == "caught" and R._TRACE_OUTCOMES[4] == "ran"
    assert outcome_for(5) == "ran" and R._TRACE_OUTCOMES[5] == "teleported"


def test_a_gen_4_fight_the_run_never_finished_still_claims_nothing():
    """A run that ends mid-battle has no outcome on any cartridge, and on gen 4
    the carrier key must not survive into route.json either."""
    per_turn = turns(
        (1, [g4(0, 4, 4)]),
        (2, [g4(0, 4, 4, batt=True, kind="wild", foe=399, lvl=2, out=0)]),
    )
    b, = R._battles_from_trace(per_turn, game="platinum-us")
    assert "outcome" not in b and b["won"] is None and b["closed_turn"] is None
    assert not any(k.startswith("_") for k in b)


def test_a_soulsilver_card_carries_a_levelled_foe_and_no_kind_at_all():
    """SoulSilver's contract declares `foe_level` and NOT `battle_kind`: its
    battleType is at a known address by the same structural argument Platinum's
    passed, but every battle the cartridge has ever produced is wild, and a
    wild-only corpus cannot settle a wild/trainer discriminator.

    So `kind` stays None and the card says "unknown". A caller filtering on
    `kind == "trainer"` finds none, which is the honest answer — commit f03dd4b
    is what the alternative looked like."""
    per_turn = turns(
        (1, [g4(0, 6, 6)]),
        (2, [g4(0, 6, 6, batt=True, foe=163, lvl=3)]),      # battle_kind is None
        (3, [g4(0, 6, 6)]),
    )
    b, = R._battles_from_trace(per_turn, game="soulsilver-us")
    assert b["kind"] is None and b["trainer_id"] is None
    assert b["foe"] == {"species": 163, "level": 3}         # Hoothoot, Lv 3
    assert "outcome" not in b and b["won"] is None


def test_the_kind_is_the_intros_and_a_later_sample_cannot_overwrite_it():
    """FIRST writer wins, and until this test nothing bit when it stopped.

    The existing "the kind survives later silence" test only feeds later
    samples whose kind is None, and None is already skipped by the `if` — so
    turning the rule into last-writer-wins left the whole suite green. The rule
    is only visible when a later sample carries a DIFFERENT kind, which is what
    this feeds.

    It is not hypothetical on gen 4. Five of the 542 in-battle samples in the
    Platinum replay corpus read the whole battle block as ZEROS — species 0,
    level 0, battleType 0 — and every one of them is the FIRST sample of a
    segment, the frame where the overlay is already up and the BattleContext is
    not yet filled. All five happen to belong to wild battles, so no card is
    wrong today; but a battleType of 0 decodes as "wild", so the ordering rule
    is the only thing standing between that frame and a trainer battle labelled
    wild for its whole length."""
    per_turn = turns(
        (1, [g4(0, 7, 7)]),
        (2, [g4(0, 7, 7, batt=True, kind="trainer", foe=396, lvl=5, tid=1),
             g4(1, 7, 7, batt=True, kind="wild", foe=396, lvl=5)]),
        (3, [g4(0, 7, 7)]),
    )
    b, = R._battles_from_trace(per_turn, game="platinum-us")
    assert b["kind"] == "trainer" and b["trainer_id"] == 1


def g5(i, x, y, **kw):
    """A gen-5 sample. Same single-map-id shape as gen 4, at Route 2's id —
    the map that carries BOTH a trainer battle and wild encounters in the
    corpus, which is the pairing that keeps these cards from being a test of
    the place instead of the kind."""
    row = s(i, x, y, **kw)
    row.update({"map_group": None, "map_num": None, "map_id": 319})
    return row


def test_a_gen_5_card_says_which_kind_and_a_levelled_foe_and_never_how_it_ended():
    """Black's samples carry a kind and a levelled foe and NO trainer and NO
    outcome, and each of those three is a different decision.

    The kind is real: the contract reads the battle proc's pointer to the
    opponent trainer's NAME buffer, NULL when there is no trainer, scored
    34/34 against what the screen said — "A wild Patrat appeared!" against "A
    Trainer catches another Trainer's eye".

    `trainer_id` stays None because the two integers beside that pointer cannot
    yet be told apart — one is the id and one the class — and `won` stays None
    because gen 5 frees the battle heap when the fight ends, so the sample
    after the flag clears is reading memory already handed back. None renders
    as "unknown", which is true; False would say the player lost."""
    trainer = turns(
        (1, [g5(0, 754, 636)]),
        (2, [g5(0, 754, 636, batt=True, kind="trainer", foe=504, lvl=7),
             g5(1, 754, 636, batt=True, kind="trainer", foe=504, lvl=7)]),
        (3, [g5(0, 754, 636)]),                             # battle_outcome is None
    )
    b, = R._battles_from_trace(trainer, game="black-us")
    assert b["kind"] == "trainer"
    assert b["foe"] == {"species": 504, "level": 7}          # the Youngster's Patrat
    assert b["trainer_id"] is None and b["trainer"] is None
    assert "trainer_class" not in b
    assert "outcome" not in b and b["won"] is None
    assert b["tile"] == [319, 0, 754, 636]

    wild = turns(
        (1, [g5(0, 751, 641)]),
        (2, [g5(0, 751, 641, batt=True, kind="wild", foe=506, lvl=4)]),
        (3, [g5(0, 751, 641)]),
    )
    w, = R._battles_from_trace(wild, game="black-us")
    assert w["kind"] == "wild" and w["foe"] == {"species": 506, "level": 4}
    assert w["trainer_id"] is None and w["won"] is None


def test_black_2s_one_reachable_battle_still_names_its_kind_and_its_foe():
    """Black 2 reaches exactly ONE battle — PKMN Trainer Hugh at the Aspertia
    lookout — because the game hangs in the Pokemon Center doorway the story
    walks the player into next. The card it produces is the whole of what that
    cartridge can show today, and it has to say "trainer" and carry Hugh's
    Oshawott: the contract's kind is the same offset of the same block as
    Black's, and on this cartridge it points at a buffer spelling "Hugh"."""
    per_turn = turns(
        (1, [s(0, 36, 715, batt=False)]),
        (2, [s(0, 36, 715, batt=True, kind="trainer", foe=501, lvl=5)]),
        (3, [s(0, 36, 715)]),
    )
    for t in per_turn.values():
        for row in t["samples"]:
            row.update({"map_group": None, "map_num": None, "map_id": 427})
    b, = R._battles_from_trace(per_turn, game="black2-us")
    assert b["kind"] == "trainer" and b["foe"] == {"species": 501, "level": 5}
    assert b["trainer_id"] is None and b["won"] is None
