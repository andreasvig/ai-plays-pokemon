"""A continued run's route carries the walk it inherited.

Andreas, 2026-09-20: "when you continue a run i wish that we keep the old graph
also, is that possible?"

It already is, and this is what says so. ``src/cli/runner.py``
``_copy_prior_run_artifacts`` PREPENDS the source run's whole ``events.jsonl``
to the continue's before the new logger appends, so `load_route` sees one
timeline and draws one map. Nothing in the route code knows a continue happened,
which is the reason it works — and also the reason it could be broken by a
change to the copier that nobody would connect to the map.

The second test is the one with teeth. A continue resumes from a SAVEPOINT, not
from the source run's end, so a run that reached turn 60 and is continued from
turn 50 replays turns 51-60 down a different path. Both versions are in the
file. The route must take the NEW one — that is the branch that was actually
played — and it must not draw both.
"""
from __future__ import annotations

from src.app import route as R


def trace(turn, *tiles, in_battle=False):
    return {"type": "turn_input_trace", "turn": turn,
            "samples": [{"i": i, "input": "D", "map_group": g, "map_num": m,
                         "map_id": None, "x": x, "y": y, "in_battle": in_battle,
                         "battles_total": None, "foe_species": None, "foe_level": None,
                         "battle_kind": None, "battle_outcome": None, "trainer_id": None}
                        for i, (g, m, x, y) in enumerate(tiles)]}


def tiles_of(route):
    return [(v[2], v[3], v[4], v[5]) for v in route["visits"]]


def test_the_inherited_walk_is_on_the_map_with_the_new_one():
    # What the file looks like after `_copy_prior_run_artifacts`: the source
    # run's turns, then the continue's.
    events = [trace(1, (0, 9, 5, 5)), trace(2, (0, 9, 5, 6)), trace(3, (0, 9, 5, 7)),
              trace(4, (0, 9, 6, 7)), trace(5, (0, 9, 7, 7))]
    got = R.build_route(events, None, game="emerald-us")
    assert tiles_of(got)[:3] == [(0, 9, 5, 5), (0, 9, 5, 6), (0, 9, 5, 7)], \
        "the first three turns are the source run's and must still be drawn"
    assert tiles_of(got)[-1] == (0, 9, 7, 7)
    assert got["turns"]["total"] == 5, "one run, one turn count"


def test_a_replayed_turn_draws_the_branch_that_was_played_not_both():
    # Continued from turn 2's savepoint: turns 3-4 exist twice, once as the
    # source run walked them and once as the continue did. Route keys `per_turn`
    # by turn number and the later write wins, so the abandoned branch drops
    # out — which is what makes the map show one walk rather than a fork.
    events = [trace(1, (0, 9, 5, 5)), trace(2, (0, 9, 5, 6)),
              trace(3, (0, 9, 5, 7)), trace(4, (0, 9, 5, 8)),      # the source run's tail
              trace(3, (0, 9, 6, 6)), trace(4, (0, 9, 7, 6))]      # the continue's
    got = R.build_route(events, None, game="emerald-us")
    walked = tiles_of(got)
    assert (0, 9, 7, 6) in walked, "the continue's own walk has to be there"
    assert (0, 9, 5, 8) not in walked, "the abandoned branch must not be drawn as well"
    assert got["turns"]["total"] == 4, "four turns happened, not six"


def test_a_fight_inherited_from_the_source_run_keeps_its_card():
    # The battles are read off the same samples, so they inherit for free — but
    # only if the segment logic sees the prepended turns as ordinary ones.
    events = [trace(1, (0, 16, 8, 14)),
              trace(2, (0, 16, 8, 14), in_battle=True),
              trace(3, (0, 16, 8, 14)),
              trace(4, (0, 16, 9, 14)),
              trace(5, (0, 16, 9, 14), in_battle=True),
              trace(6, (0, 16, 9, 14))]
    got = R.build_route(events, None, game="emerald-us")
    assert [b["opened_turn"] for b in got["battles"]] == [2, 5], \
        "both the inherited fight and the new one belong on the map"
