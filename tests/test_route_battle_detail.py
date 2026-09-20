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
    the PREVIOUS fight's result;
  * a trainer NAME is FireRed's alone. Trainer 114 is a different person on
    every cartridge.
"""
from __future__ import annotations

import pytest

from src.app import route as R


def s(i, x, y, *, batt=False, kind=None, foe=None, lvl=None, out=None, tid=None):
    return {"i": i, "input": "D", "map_group": 0, "map_num": 9, "map_id": None,
            "x": x, "y": y, "in_battle": batt, "battles_total": None,
            "foe_species": foe, "foe_level": lvl, "battle_kind": kind,
            "battle_outcome": out, "trainer_id": tid}


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
