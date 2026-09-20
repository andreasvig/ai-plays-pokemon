"""The route must drop a map the contract says cannot exist.

Found 2026-09-20. ``GameMemory.invalid_maps`` (src/referee/contracts.py:162) was
added for Crystal, whose ``(0, 0)`` is not a place: it is a FAILED READ. Crystal
keeps its map id in switchable GBC WRAM, so a sample taken while the wrong bank
is paged in returns four zero bytes. ``src/app/observed.py:_drop_impossible_maps``
applies it and the walk graph is clean.

``src/app/route.py`` never did — zero references in 471 lines — so the run
report's route, which is the surface Andreas actually looks at, still mints a
map entry ``"0:0"`` for it. Worse than the phantom map is the pair of phantom
WARPS either side of it: with no walk graph (six of seven games) ``_classify``
calls every map change a warp, so one flicker invents a door out of the real map
and a door back into it.

It draws nothing today only because Crystal has no artwork. The day it gets any,
the phantom draws.

The two assertions are deliberately separate. A fix that filtered the ``maps``
dict at the end would satisfy the first and leave the invented warps in the
transition counts and in the caption's "N doors" — the map and the warps are two
defects from one cause, and the warp half is the one that corrupts a number
already on screen.
"""

from __future__ import annotations

import pytest

from src.app import route
from src.referee.contracts import CRYSTAL, contract_for

from tests.test_route_multigame import sample, trace_event, write_run


def crystal_sample(i, inp, x, y, group, num):
    return sample(i, inp, x, y, group=group, num=num)


@pytest.fixture
def flickering_crystal_events():
    """A walk on Crystal map (26, 1) with one sample read mid bank-switch.

    Five steps east. The third sample reads (0, 0) — every byte zero — and the
    player is back on (26, 1) immediately after, two tiles further on, because
    the bank came back while he kept walking.
    """
    return [
        trace_event(1, [crystal_sample(0, "right", 10, 8, 26, 1),
                        crystal_sample(1, "right", 11, 8, 26, 1)]),
        trace_event(2, [crystal_sample(0, "right", 12, 8, 0, 0)]),      # the failed read
        trace_event(3, [crystal_sample(0, "right", 13, 8, 26, 1),
                        crystal_sample(1, "right", 14, 8, 26, 1)]),
    ]


def test_the_contract_is_what_declares_the_impossible_map():
    """Not route.py. The fact belongs to the cartridge, so the fix must read it
    from the contract rather than hardcoding (0, 0) in the route builder — every
    other game's (0, 0) is a real place."""
    assert CRYSTAL.invalid_maps == ((0, 0),)
    assert contract_for("crystal-us").invalid_maps == ((0, 0),)
    # The control: the same key is legitimate elsewhere, so a blanket filter
    # would delete a real map.
    assert contract_for("firered-us").invalid_maps == ()
    assert contract_for("emerald-us").invalid_maps == ()


def test_a_failed_read_is_not_a_map(tmp_path, flickering_crystal_events):
    run = write_run(tmp_path, "crystal-flicker", "crystal", flickering_crystal_events)
    r = route.load_route(run)
    assert r is not None
    assert "0:0" not in r["maps"], "a failed bank read was drawn as a place"
    assert set(r["maps"]) == {"26:1"}


def test_a_failed_read_does_not_invent_two_doors(tmp_path, flickering_crystal_events):
    """The half that corrupts a number already on screen.

    With no walk graph every map change classifies as ``warp``, so the flicker
    mints one warp out of (26, 1) and one back into it. The route caption prints
    that count as "N doors".
    """
    run = write_run(tmp_path, "crystal-flicker-warps", "crystal", flickering_crystal_events)
    r = route.load_route(run)
    assert r["transitions"].get("warp", 0) == 0, "a flicker was counted as walking through a door"


def test_the_tiles_either_side_of_the_dropped_sample_are_kept(tmp_path, flickering_crystal_events):
    """Dropping the sample must not drop the walk.

    The control against over-correcting: four real tiles were stood on and all
    four must survive, on their own map, with the gap between them reported
    honestly rather than smoothed over.
    """
    run = write_run(tmp_path, "crystal-flicker-tiles", "crystal", flickering_crystal_events)
    r = route.load_route(run)
    tiles = {(v[2], v[3], v[4], v[5]) for v in r["visits"]}
    assert tiles == {(26, 1, 10, 8), (26, 1, 11, 8), (26, 1, 13, 8), (26, 1, 14, 8)}
    # (11,8) -> (13,8) is two tiles apart on one map: an unexplained jump, which
    # is exactly what it is. It must not be silently filled in as a step.
    assert r["transitions"].get("break", 0) == 1


def test_a_game_with_no_invalid_maps_is_untouched(tmp_path):
    """Mutation control. If the filter were applied unconditionally, or keyed on
    something other than the contract, this Emerald run would lose its map."""
    events = [trace_event(1, [sample(0, "right", 5, 5, group=0, num=0),
                              sample(1, "right", 6, 5, group=0, num=0)])]
    run = write_run(tmp_path, "emerald-zero-map", "emerald", events)
    r = route.load_route(run)
    assert set(r["maps"]) == {"0:0"}, "Emerald's (0, 0) is Petalburg City, not a failed read"
    assert len(r["visits"]) == 2
