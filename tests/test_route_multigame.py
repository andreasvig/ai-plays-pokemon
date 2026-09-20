"""The route on all seven cartridges (src/app/route.py, commit ca0b8aa).

``tests/test_route.py`` is the FireRed file: every fixture in it is a (group,
number) pair, every graph in it is the committed walk graph, and every battle in
it arrives as a ``referee_battle_state`` event. All three of those are FireRed
facts, and each one of them silently produced nothing on the other six games:

- ``_sample_tile`` required ``map_group``, so a gen 4/5 run — which names a map
  with a SINGLE id — built a route with ZERO visits. The trace was fine and the
  reader could not see it;
- ``load_route`` handed FireRed's walk graph to every cartridge. Foreign
  coordinates mostly failed to resolve in it, so every transition degraded to
  ``break`` and it read as harmless. It is not: (3, 0) is a real map in Emerald
  too and a different place, so a coordinate that DID resolve would have drawn a
  door between two maps of another game;
- ``place_battles`` replayed ``referee_battle_state``, which only the FireRed
  benchmark emits, so every SkyEmu run drew zero battles however many it fought.

And two defects found while fixing those, each with a test named after it below:
the trace fallback first walked ``visits`` and found 0 battles in an Emerald run
holding 214 in-battle samples (``visits`` collapses consecutive identical tiles,
and a battle is fought standing still), and the foreign-decoder guard was wrong
in both directions before it was right — poisoning a whole GAME from one bad
run, then rejecting every pre-contract sample and costing FireRed 69 real tiles
and 13 real runs.

Fixtures are the producer's shape, not an invented one: every sample dict below
carries the keys ``src.referee.trace.decode_samples`` emits, and
``test_the_fixtures_are_the_shape_each_decoder_emits`` is the live check on that
for gen 3, gen 4 and gen 5 alike. A fixture the decoder never emits would
witness nothing.
"""

from __future__ import annotations

import json
import struct

import pytest
from pathlib import Path

from src.app import observed, route
from src.app.roms import get_rom, load_roms
from src.referee import trace
from src.referee.contracts import (BLACK2, CONTRACTS, CRYSTAL, EMERALD, FIRERED, PLATINUM,
                                   SOULSILVER, contract_for)
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph


@pytest.fixture(scope="module")
def firered() -> WalkGraph:
    return WalkGraph.load(DEFAULT_GRAPH_PATH)


# --- fixtures in the producer's shape -----------------------------------------


def sample(i, inp, x, y, group=3, num=0, battle=False, total=None):
    """One gen 1-3 sample, keyed exactly as ``trace.decode_samples`` leaves it.

    ``battles_total`` defaults to None because only FireRed's contract declares
    the counter; Emerald's and Crystal's decoders leave it unset.
    """
    return {"i": i, "input": inp, "map_group": group, "map_num": num,
            "map_id": None, "x": x, "y": y,
            "in_battle": battle, "battles_total": total, "foe_species": None}


def ds_sample(i, inp, x, y, map_id, battle=False):
    """A gen 4/5 sample: ONE map id, and no (group, number) pair at all."""
    return {"i": i, "input": inp, "map_group": None, "map_num": None,
            "map_id": map_id, "x": x, "y": y,
            "in_battle": battle, "battles_total": None, "foe_species": None}


def pre_contract_sample(i, inp, x, y, group=3, num=0, battle=False, total=0):
    """A sample the PRE-2026-09-20 global decoder wrote.

    Identical to :func:`sample` except that the ``map_id`` KEY does not exist —
    that decoder had no notion of a single-id map, so it never emitted the key.
    Its absence is the whole fingerprint ``GameMemory.wrote`` reads.
    """
    s = sample(i, inp, x, y, group, num, battle, total)
    del s["map_id"]
    return s


def trace_event(turn, samples):
    return {"type": "turn_input_trace", "turn": turn, "samples": samples}


def poll_event(turn, g, m, x, y):
    return {"type": "referee_position", "turn": turn, "map_group": g, "map_num": m, "x": x, "y": y}


def battle_event(turn, *, in_battle, total, wild=0, trainer=0, opponent=None, new=()):
    return {"type": "referee_battle_state", "turn": turn, "in_battle": in_battle,
            "battles_total": total, "wild_battles": wild, "trainer_battles": trainer,
            "opponent": opponent, "trainers_new": list(new)}


def write_run(tmp_path: Path, name: str, rom_id: str, events: list[dict]) -> Path:
    """A run directory shaped the way a finished run leaves one: a config.json
    naming the ROM it was launched with (the only place the cartridge is
    recorded — a route carries nothing that says which game it came from) and an
    events.jsonl of one JSON object per line."""
    run = tmp_path / name
    run.mkdir()
    rom = get_rom(rom_id)
    (run / "config.json").write_text(json.dumps(
        {"emulator": {"rom_path": rom.path}, "game_name": rom.game_name}))
    (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return run


def test_the_fixtures_are_the_shape_each_decoder_emits():
    """The test that keeps every other test in this file honest, run once per
    generation because the three shapes are what the file is ABOUT: a gen 3 pair,
    a gen 4 single id, and a gen 5 single id with a 16.16 fixed-point
    coordinate. If a helper here drifted from the decoder, every assertion below
    it would be measuring a dict the harness never produces."""
    gen3 = trace.decode_samples(
        [("D", [struct.pack("<hhBB", 6, 9, 0, 9), bytes([0])])], None, EMERALD)[0]
    assert gen3.keys() == sample(0, "D", 6, 9).keys()
    assert gen3 == sample(0, "D", 6, 9, group=0, num=9)

    # pret/pokeplatinum struct Location: mapHeaderID, warpId, x, z — the bedroom
    # state, which reads sPlayerStartLocation field for field. THREE ranges,
    # because Platinum's spec grew an overlay id and a species on 2026-09-20:
    # a row carrying only the first made the flag read None, which is the
    # fixture failing to speak the producer's contract rather than a defect.
    def platinum_row(overlay: int, species: int, loc=(415, -1, 4, 6)):
        return ("D", [struct.pack("<iiii", *loc),
                      struct.pack("<i", overlay), struct.pack("<H", species)])

    gen4 = trace.decode_samples([platinum_row(-1, 0)], None, PLATINUM)[0]
    assert gen4.keys() == ds_sample(0, "D", 4, 6, 415).keys()
    assert gen4 == ds_sample(0, "D", 4, 6, 415)

    # The overlay id is compared, not masked: FS_OVERLAY_ID_NONE is 0xffffffff,
    # which is non-zero under every mask, so a mask test would report a battle
    # in the overworld forever. And the species is GATED on the flag, because
    # outside a battle the field holds the PREVIOUS opponent.
    assert gen4["in_battle"] is False and gen4["foe_species"] is None
    fighting = trace.decode_samples([platinum_row(16, 396)], None, PLATINUM)[0]
    assert fighting["in_battle"] is True and fighting["foe_species"] == 396
    stale = trace.decode_samples([platinum_row(-1, 396)], None, PLATINUM)[0]
    assert stale["in_battle"] is False and stale["foe_species"] is None, (
        "a species read outside a battle is the last fight's, not this tile's")

    centre = struct.pack("<IiiI", 427, (47 << 16) | 0x8000, 1, (764 << 16) | 0x8000)
    gen5 = trace.decode_samples([("R", [centre])], None, BLACK2)[0]
    assert gen5 == ds_sample(0, "R", 47, 764, 427)

    # And the tile each one becomes, straight off the decoder.
    assert route._sample_tile(gen3) == (0, 9, 6, 9)
    assert route._sample_tile(gen4) == (415, 0, 4, 6)
    assert route._sample_tile(gen5) == (427, 0, 47, 764)


# --- 1. one map id in two numeric slots ---------------------------------------


def test_a_single_id_map_takes_the_first_slot_with_a_constant_zero_in_the_second():
    """The wire encoding. The map key reaches the browser as the string "a:b"
    and the atlas is keyed on it, so both shapes have to land in the same two
    numeric slots: 411 spells itself "411:0". The 0 is not a claim that Platinum
    has a map group — nothing reads the second slot for those games — and since
    no two games share a route file it cannot collide with anything."""
    assert route._sample_tile(ds_sample(0, "D", 4, 6, 411)) == (411, 0, 4, 6)
    assert route._sample_tile(ds_sample(1, "D", 12, 3, 0)) == (0, 0, 12, 3)


def test_a_group_and_number_map_still_fills_both_slots():
    """The control. Gen 1-3 name a map with a PAIR and both halves must survive,
    or every FireRed route in the repo would change shape."""
    assert route._sample_tile(sample(0, "D", 6, 9, 3, 0)) == (3, 0, 6, 9)
    assert route._sample_tile(sample(0, "D", 5, 9, 26, 3)) == (26, 3, 5, 9)


def test_a_sample_with_no_coordinate_is_no_tile_whichever_shape_it_has():
    """A blind read has to stay blind. Answering (411, 0, None, None) for a
    single-id sample whose coordinates never arrived would put a tile with no
    position into ``visits``."""
    assert route._sample_tile(ds_sample(0, "D", None, None, 411)) is None
    assert route._sample_tile(sample(0, "D", None, None)) is None
    assert route._sample_tile({"x": 1, "y": 2}) is None       # neither map shape
    assert route._sample_tile("not a sample") is None


def test_a_ds_run_builds_a_route_with_visits_at_all():
    """THE regression. Before 2026-09-20 ``_sample_tile`` required ``map_group``,
    so this exact route came back with an empty ``visits`` list, an empty
    ``maps`` dict and 0.0 coverage — on a trace that had read every tile
    perfectly. A route that draws nothing is indistinguishable from a run that
    walked nowhere, which is why this is the first test in the file."""
    events = [
        trace_event(1, [ds_sample(0, "D", 4, 6, 411), ds_sample(1, "D", 4, 7, 411)]),
        trace_event(2, [ds_sample(0, "D", 4, 8, 411)]),
    ]
    r = route.build_route(events, None)
    assert r["visits"] == [[1, 0, 411, 0, 4, 6, 0], [1, 1, 411, 0, 4, 7, 0],
                           [2, 0, 411, 0, 4, 8, 0]]
    assert list(r["maps"]) == ["411:0"]
    assert r["transitions"] == {"step": 2} and r["tiles_moved"] == 2
    assert r["turns"] == {"total": 2, "traced": 2, "blind": 0} and r["coverage"] == 1.0


def test_a_ds_run_read_off_disk_draws_its_route_with_no_graph(tmp_path):
    """The two halves together, end to end: a Platinum run resolves to a single
    map id AND to no walk graph, so the route is drawn from the samples alone.
    ``graph_source`` is None and ``tile_px`` falls back to 16 — a DS run must not
    borrow FireRed's metadata along with FireRed's graph."""
    run = write_run(tmp_path, "plat", "platinum", [
        trace_event(1, [ds_sample(0, "D", 4, 6, 411), ds_sample(1, "D", 4, 7, 411)]),
    ])
    r = route.load_route(run)
    assert [v[2:6] for v in r["visits"]] == [[411, 0, 4, 6], [411, 0, 4, 7]]
    assert r["graph_source"] is None and r["tile_px"] == 16
    assert r["maps"] == {"411:0": {"name": None, "width": None, "height": None, "world": None}}


# --- 2. the walk graph belongs to exactly one cartridge ------------------------


def test_only_firered_gets_the_committed_walk_graph(tmp_path):
    """``data/firered-walkgraph.json`` is pret's FireRed and its map keys are
    FireRed's. Enumerated over the WHOLE registry rather than spot-checked on
    one other game, so adding an eighth ROM cannot quietly inherit it."""
    assert route.GRAPH_GAME == "firered-us"
    walk = [trace_event(1, [sample(0, "D", 6, 9), sample(1, "D", 6, 10)])]
    got = {rom.game: route.graph_for_run(write_run(tmp_path, rom.id, rom.id, walk))
           for rom in load_roms()}
    assert {g: (v is not None) for g, v in got.items()} == {
        "firered-us": True, "emerald-us": False, "crystal-us": False,
        "platinum-us": False, "soulsilver-us": False, "black-us": False, "black2-us": False}
    assert got["firered-us"] is route.default_graph()


def test_the_collision_the_refusal_prevents_is_real(firered, tmp_path):
    """Why identity and not plausibility. The Emerald run below stands on map
    (3, 0) at a coordinate that RESOLVES in FireRed's graph — so handing it that
    graph does not fail loudly, it answers. Pallet Town is (3, 0) in FireRed and
    something else entirely in Emerald, and the transition drawn would be a door
    between two maps of a game this run never played."""
    assert firered.has_map(3, 0) and firered.node_id(3, 0, 6, 9) is not None
    emerald = write_run(tmp_path, "em", "emerald", [
        trace_event(1, [sample(0, "D", 6, 9, 3, 0), sample(1, "D", 6, 10, 3, 0)])])
    assert route.graph_for_run(emerald) is None
    assert route.load_route(emerald)["maps"]["3:0"]["name"] is None  # not "PalletTown"


def test_an_unknown_cartridge_is_two_situations_with_opposite_answers(tmp_path):
    """A run that names NO rom keeps the graph; a run that names one we do not
    know loses it.

    This test asserted "unknown is not FireRed" for all three cases until
    2026-09-20, and that cost the entire legacy corpus its graph — a run dir
    with no emulator block predates multi-ROM support, and in that era every
    run WAS FireRed. Two publish tests caught it. The distinction is whether
    the run made a positive statement about its cartridge: silence is the old
    world, a rom_path that does not join the registry is a ROM hack or a
    renamed dump, and a FireRed hack agreeing "mostly" with FireRed's graph is
    exactly how this class of defect hides.
    """
    bare = tmp_path / "bare"
    bare.mkdir()
    assert route.graph_for_run(bare) is not None, "a run naming no ROM is legacy FireRed"

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "config.json").write_text("{not json")
    assert route.graph_for_run(broken) is not None

    noemu = tmp_path / "noemu"
    noemu.mkdir()
    (noemu / "config.json").write_text(json.dumps({"_record": {"view": "both"}}))
    assert route.graph_for_run(noemu) is not None

    offreg = tmp_path / "offreg"
    offreg.mkdir()
    (offreg / "config.json").write_text(json.dumps(
        {"emulator": {"rom_path": "roms/some-hack-i-built.gba"}}))
    assert route.graph_for_run(offreg) is None, "a named ROM we cannot join is not FireRed"


# --- 3. the graph-free classifier ---------------------------------------------


def test_without_a_graph_a_map_change_between_consecutive_samples_is_a_warp():
    """Two CONSECUTIVE reads on different maps are a door: the player was here,
    then there, one input apart. Calling it a ``break`` — which is what six
    cartridges got until 2026-09-20 — throws the evidence away: Crystal's 125
    discarded breaks are 110 real doors."""
    assert route._classify(None, (3, 0, 6, 7), (4, 0, 4, 8)) == ("warp", 1, None)
    # It carries NO fill, because a fill is a shortest path and there is no graph
    # to walk one on, and it cannot be told from a seam for the same reason.
    events = [trace_event(1, [sample(0, "U", 6, 8, 26, 3), sample(1, "U", 4, 8, 10, 1)])]
    r = route.build_route(events, None)
    assert r["transitions"] == {"warp": 1} and r["fills"] == {} and r["tiles_moved"] == 1


def test_without_a_graph_a_step_is_a_step_and_an_unexplained_gap_is_a_break():
    """The other two arms of the same rule, and the reason the warp rule is safe
    to apply: same map and orthogonally adjacent is the one transition that needs
    no graph to prove, and same map but NOT adjacent stays a break, because
    without a graph there is no way to know whether a path exists."""
    assert route._classify(None, (3, 0, 6, 9), (3, 0, 6, 10)) == ("step", 1, None)
    assert route._classify(None, (3, 0, 6, 9), (3, 0, 7, 9)) == ("step", 1, None)
    assert route._classify(None, (3, 0, 6, 9), (3, 0, 7, 10)) == ("break", 0, None)
    assert route._classify(None, (3, 0, 6, 9), (3, 0, 6, 20)) == ("break", 0, None)


def test_a_graph_free_route_counts_its_steps_and_its_doors_separately():
    """End to end on Crystal's shape: three walked tiles, one door, one jump the
    samples cannot explain. Each lands in its own bucket and only the walked
    tiles and the door are credited as movement."""
    events = [trace_event(1, [
        sample(0, "D", 3, 4, 26, 3), sample(1, "D", 3, 5, 26, 3),   # step
        sample(2, "D", 3, 6, 26, 3),                                # step
        sample(3, "D", 5, 5, 10, 1),                                # warp: a door
        sample(4, "B", 9, 9, 10, 1),                                # break: no path known
    ])]
    r = route.build_route(events, None)
    assert r["transitions"] == {"step": 2, "warp": 1, "break": 1}
    assert r["tiles_moved"] == 3 and r["fills"] == {}


def test_the_graph_free_rule_does_not_leak_into_the_graphed_path(firered):
    """The control the graph-free branch needs. With a graph present the richer
    classification must survive intact — a seam is still a seam and not a warp,
    a scripted walk is still a jump WITH its fill, a relocation is still a
    teleport — and a map change the graph cannot resolve is still a ``break``,
    never the graph-free ``warp``. Without this test, making the no-graph case
    generous would quietly make the graphed case generous too, and every door in
    a FireRed route would stop being checked against the ground."""
    seam = route.build_route(
        [trace_event(50, [sample(0, "U", 12, 0, 3, 0), sample(1, "U", 12, 39, 3, 19)])], firered)
    assert seam["transitions"] == {"seam": 1}

    jump = route.build_route(
        [trace_event(24, [sample(0, "B", 12, 1, 3, 0), sample(1, "B", 11, 5, 3, 0)])], firered)
    assert jump["transitions"] == {"jump": 1} and jump["tiles_moved"] == 5
    assert len(jump["fills"]["0"]) == 4

    tele = route.build_route(
        [trace_event(99, [sample(0, "B", 43, 5, 1, 0), sample(1, "B", 8, 5, 4, 0)])], firered)
    assert tele["transitions"] == {"teleport": 1} and tele["fills"] == {}

    # A map change with a graph that cannot place either end: still a break.
    assert firered.node_id(9, 9, 1, 1) is None
    off = route.build_route(
        [trace_event(1, [sample(0, "U", 6, 8, 3, 0), sample(1, "U", 1, 1, 9, 9)])], firered)
    assert off["transitions"] == {"break": 1}


# --- 4. the fallback reads the SAMPLES, not `visits` ---------------------------

# Littleroot Town is (0, 9) on Emerald — the map the first real Emerald run
# crossed into from the moving van.
EMERALD_MAP = (0, 9)


def standing_still_battle() -> list[dict]:
    """An Emerald run that walks into the grass and fights without moving.

    Turn 2 steps onto (8, 30) and the fight opens on the next button; turns 3
    and 4 are fought standing on that same tile; turn 4's last press ends it.
    """
    g, m = EMERALD_MAP
    return [
        trace_event(1, [sample(0, "U", 8, 32, g, m), sample(1, "U", 8, 31, g, m)]),
        trace_event(2, [sample(0, "U", 8, 30, g, m), sample(1, "A", 8, 30, g, m, battle=True)]),
        trace_event(3, [sample(i, "A", 8, 30, g, m, battle=True) for i in range(4)]),
        trace_event(4, [sample(0, "A", 8, 30, g, m, battle=True),
                        sample(1, "B", 8, 30, g, m)]),
    ]


def test_a_battle_fought_standing_still_is_found():
    """The defect this function was rewritten for. ``visits`` collapses
    consecutive identical tiles and a battle is fought STANDING STILL, so the
    first version of the fallback — which walked ``visits`` — found 0 battles in
    an Emerald run holding 214 in-battle samples. Every one of them had been
    collapsed into the tile the fight started on.

    The two assertions before the battle count are what make this bite: the run
    really does hold six in-battle samples, and not one of them survives into
    ``visits`` with its flag set. An implementation pointed back at ``visits``
    can therefore only answer 0."""
    events = standing_still_battle()
    assert sum(1 for e in events for s in e["samples"] if s["in_battle"]) == 6
    r = route.build_route(events, None)
    assert [v[2:6] for v in r["visits"]] == [[0, 9, 8, 32], [0, 9, 8, 31], [0, 9, 8, 30]]
    assert all(v[6] == 0 for v in r["visits"]), "the battle left no trace in `visits`"

    assert len(r["battles"]) == 1
    b = r["battles"][0]
    assert (b["opened_turn"], b["closed_turn"], b["turns"]) == (2, 4, 3)


def test_two_battles_separated_by_overworld_samples_are_two_segments():
    """A segment is a MAXIMAL run of samples with the flag set, so a press
    between two fights closes one and opens another — and the second is placed
    on its own tile, not on the first one's."""
    g, m = EMERALD_MAP
    events = [
        trace_event(1, [sample(0, "U", 8, 32, g, m), sample(1, "A", 8, 32, g, m, battle=True),
                        sample(2, "A", 8, 32, g, m)]),
        trace_event(2, [sample(0, "U", 8, 31, g, m), sample(1, "A", 8, 31, g, m, battle=True)]),
        trace_event(3, [sample(0, "B", 8, 31, g, m)]),
    ]
    r = route.build_route(events, None)
    assert [(b["opened_turn"], b["closed_turn"], b["tile"]) for b in r["battles"]] == [
        (1, 1, [0, 9, 8, 32]), (2, 2, [0, 9, 8, 31])]


# --- 5. where a battle is placed ----------------------------------------------


def test_the_battle_tile_is_the_last_sample_before_it_with_the_flag_clear():
    """The ambush tile — where the grass was walked into, not where the fight was
    won. The trace flags ``in_battle`` per button, so the sample before the flag
    first reads true is already the answer; the run below walks two tiles after
    the previous fight ended so that a naive "first tile of the turn" or "last
    tile of the run" rule would both give a different one."""
    g, m = EMERALD_MAP
    events = [
        trace_event(1, [sample(0, "U", 8, 34, g, m), sample(1, "U", 8, 33, g, m),
                        sample(2, "U", 8, 32, g, m), sample(3, "A", 8, 32, g, m, battle=True)]),
        trace_event(2, [sample(0, "B", 8, 32, g, m), sample(1, "U", 8, 31, g, m)]),
    ]
    r = route.build_route(events, None)
    assert [b["tile"] for b in r["battles"]] == [[0, 9, 8, 32]]


def test_a_battle_with_no_overworld_sample_before_it_carries_no_tile():
    """A run that resumes mid-fight has nothing to place the battle on, and the
    route says so rather than borrowing the first tile it sees AFTER the battle —
    which would draw the fight on a tile the player reached by fleeing it."""
    g, m = EMERALD_MAP
    events = [
        trace_event(1, [sample(0, "A", 8, 30, g, m, battle=True),
                        sample(1, "A", 8, 30, g, m, battle=True)]),
        trace_event(2, [sample(0, "B", 8, 29, g, m)]),
    ]
    r = route.build_route(events, None)
    assert len(r["battles"]) == 1 and r["battles"][0]["tile"] is None


def test_a_run_that_ends_mid_battle_leaves_the_segment_open():
    """The run was stopped during the fight, so nothing observed it close.
    ``closed_turn`` is None — a claim that it ended on the last turn recorded
    would be an outcome nobody saw — while ``turns`` still counts what was
    seen."""
    g, m = EMERALD_MAP
    events = [
        trace_event(1, [sample(0, "U", 8, 31, g, m), sample(1, "A", 8, 31, g, m, battle=True)]),
        trace_event(2, [sample(0, "A", 8, 31, g, m, battle=True)]),
    ]
    b = route.build_route(events, None)["battles"][0]
    assert b["closed_turn"] is None and b["opened_turn"] == 1 and b["turns"] == 2
    assert b["tile"] == [0, 9, 8, 31]


# --- 6. what the fallback must not invent --------------------------------------


def test_the_trace_fallback_invents_neither_kind_nor_trainer_nor_outcome():
    """The in_battle column knows a fight happened and nothing else. ``kind`` is
    None rather than a guess, so a card says "unknown" instead of inventing a
    category — and a caller filtering on ``kind == "trainer"`` finds NONE rather
    than a wrong answer. ``outcome`` and ``foe`` are absent entirely, which is
    the same distinction the referee path makes: absent means the run never read
    them, not that the fight ended unknown.

    Crystal's flag is a MODE byte that does know wild from trainer, but the
    decoder reduces it to a bool before it reaches here and events.jsonl stores
    the decoded sample — so filling ``kind`` in would need a decoder change AND
    a new run, not a cleverer reader."""
    r = route.build_route(standing_still_battle(), None)
    b = r["battles"][0]
    assert b["kind"] is None and b["trainer_id"] is None and b["trainer"] is None
    assert b["won"] is None and b["uncounted"] is False
    assert "outcome" not in b and "foe" not in b
    assert [x for x in r["battles"] if x["kind"] == "trainer"] == []
    assert [x for x in r["battles"] if x["won"]] == []


# --- 7. the referee path still wins when the referee spoke ---------------------


REFEREE_TURNS = [
    trace_event(1, [sample(0, "U", 14, 50, 3, 20)]),
    battle_event(1, in_battle=False, total=0),
    trace_event(2, [sample(0, "U", 14, 49, 3, 20, battle=True)]),
    battle_event(2, in_battle=True, total=1, trainer=1, opponent=104),
    # A press the trace reads as OUT of battle in the middle of the fight — a
    # sample taken between the two halves of a double battle screen. The trace
    # fallback would split here; the referee knows it is one fight.
    trace_event(3, [sample(0, "A", 14, 49, 3, 20),
                    sample(1, "A", 14, 49, 3, 20, battle=True)]),
    battle_event(3, in_battle=True, total=1, trainer=1, opponent=104),
    trace_event(4, [sample(0, "A", 14, 49, 3, 20)]),
    battle_event(4, in_battle=False, total=1, trainer=1, opponent=104, new=[104]),
]


def test_the_referee_path_still_wins_when_referee_battle_state_exists(firered):
    """Nothing about the fallback may change what a FireRed benchmark run draws.
    The referee's segments carry a kind, a named trainer and a verdict, and they
    are the same segments the board counts — so a fight on the map is a fight in
    the count. The trace's own column disagrees with them here (it would see
    two fights), and the referee's answer is the one that survives."""
    r = route.build_route(REFEREE_TURNS, firered)
    assert len(r["battles"]) == 1
    b = r["battles"][0]
    assert b["kind"] == "trainer" and b["trainer_id"] == 104
    assert b["trainer"] == "Bug Catcher Sammy" and b["won"] is True
    assert b["tile"] == [3, 20, 14, 50]


def test_the_fallback_runs_only_when_no_referee_event_exists(firered):
    """The control for the test above, and the reason the fallback exists: the
    SAME turns with the referee's events stripped out — which is every SkyEmu
    run on every cartridge — still draw battles, just without a kind or a name.
    Two rather than one, because the trace can only see what its flag says."""
    r = route.build_route([e for e in REFEREE_TURNS if e["type"] != "referee_battle_state"], firered)
    assert len(r["battles"]) == 2
    assert {b["kind"] for b in r["battles"]} == {None}
    assert [b["tile"] for b in r["battles"]] == [[3, 20, 14, 50], [3, 20, 14, 49]]


def test_a_run_with_neither_referee_events_nor_a_battle_flag_lists_no_battles():
    """Four of the seven contracts locate no battle flag at all, so their
    decoder writes ``in_battle`` False on every sample. That must produce an
    empty list — no battles OBSERVED — and not a crash or a phantom segment."""
    # Chosen, not hardcoded. This named PLATINUM until 2026-09-20, when
    # Platinum got a flag and a test about "a contract with no flag" went red
    # for a reason that had nothing to do with it — the same trap as
    # a_contractless_rom() in tests/test_observed.py.
    flagless = next((c for c in CONTRACTS.values() if c.battle_flag is None), None)
    if flagless is None:
        pytest.skip("every contract has a battle flag now — nothing left to be blind")
    assert flagless.census_ok is False
    r = route.build_route([trace_event(1, [ds_sample(0, "D", 4, 6, 411),
                                           ds_sample(1, "D", 4, 7, 411)])], None)
    assert r["battles"] == []


# --- 8. GameMemory.wrote: the decoder's fingerprint ----------------------------


def test_a_pre_contract_sample_is_firereds_and_no_one_elses():
    """The identity test, in both directions, on the sample shape that has no
    ``map_id`` key at all.

    Accepting it for FireRed is not leniency about age: the pre-2026-09-20
    global decoder WAS FireRed's spec, byte for byte (pinned by
    tests/test_contracts.py::test_firered_contract_reproduces_the_constant_it_
    replaced), so its output is a correct FireRed reading. Rejecting these
    outright cost FireRed 69 real tiles and 13 real runs — an age test wearing
    an identity test's clothes. Refusing it for the other six is the same
    sentence read the other way: that decoder was wrong on them."""
    old = pre_contract_sample(0, "D", 6, 9, 3, 0)
    assert "map_id" not in old
    assert FIRERED.wrote(old) is True
    assert {c.game: c.wrote(old) for c in CONTRACTS.values() if c is not FIRERED} == {
        "emerald-us": False, "crystal-us": False, "platinum-us": False,
        "soulsilver-us": False, "black2-us": False, "black-us": False}


def test_exactly_one_contract_claims_the_old_global_decoder():
    """``was_the_global_default`` is a historical fact about ONE cartridge. A
    second contract setting it would re-admit exactly the foreign runs the flag
    exists to refuse, and nothing else in the module would notice."""
    assert [c.game for c in CONTRACTS.values() if c.was_the_global_default] == ["firered-us"]


def test_a_current_sample_is_accepted_by_its_own_contract_and_refused_by_a_foreign_one():
    """The other half, and the one that catches a live mismatch rather than a
    historical one. The signature is a KEY and a None-ness, never plausibility:
    a gen 1-3 contract expects ``map_id`` unset and a gen 4/5 contract expects
    ``map_group`` unset, so the two shapes cannot be mistaken for one another
    however reasonable the numbers look."""
    gen3 = sample(0, "D", 6, 9, 3, 0)
    gen4 = ds_sample(0, "D", 4, 6, 411)
    assert [c.wrote(gen3) for c in (FIRERED, EMERALD, CRYSTAL)] == [True, True, True]
    assert [c.wrote(gen3) for c in (PLATINUM, SOULSILVER, BLACK2)] == [False, False, False]
    assert [c.wrote(gen4) for c in (PLATINUM, SOULSILVER, BLACK2)] == [True, True, True]
    assert [c.wrote(gen4) for c in (FIRERED, EMERALD, CRYSTAL)] == [False, False, False]


def test_a_torn_read_is_still_recognised_as_its_own_contracts():
    """Documented consequence of reading keys rather than values: a sample whose
    fields all came back None still carries the ``map_id`` KEY, so it is ours
    and is refused nothing. A value test would have thrown the turn away."""
    torn = {"i": 0, "input": "D", "map_group": None, "map_num": None, "map_id": None,
            "x": None, "y": None, "in_battle": None, "battles_total": None, "foe_species": None}
    assert FIRERED.wrote(torn) is True and PLATINUM.wrote(torn) is True
    assert route._sample_tile(torn) is None    # and it still contributes no tile


def test_wrote_refuses_anything_that_is_not_a_sample():
    assert FIRERED.wrote(None) is False and FIRERED.wrote("map_id") is False


# --- 9. a foreign run is refused; its GAME is not ------------------------------

# The real numbers, off a SoulSilver run recorded on 2026-09-20 before SoulSilver
# had a contract: FireRed's SaveBlock1 layout dereferenced on a DS, which returns
# values shaped exactly like coordinates. Drawn, that is a route through a map
# the run never entered — and its in_battle column produced 44 battles, 41 of
# them on invented tiles.
FOREIGN = [trace_event(1, [pre_contract_sample(0, "D", 12320, 7259, 1, 112, battle=True),
                           pre_contract_sample(1, "D", 12320, 7260, 1, 112, battle=True)])]

# The same cartridge, written by its own contract: New Bark Town's interiors,
# the only map ids SoulSilver's search ever observed.
GOOD_SS = [trace_event(1, [ds_sample(0, "D", 3, 4, 64), ds_sample(1, "D", 3, 5, 64),
                           ds_sample(2, "D", 3, 6, 64)])]


def test_load_route_refuses_a_run_a_foreign_decoder_wrote(tmp_path):
    """A run's trace is decoded AT RECORD TIME and stored decoded, so the spec is
    baked into events.jsonl and no later fix can re-read it. These numbers are
    not positions and nothing makes them into positions — the only correct answer
    is no route at all."""
    assert contract_for("soulsilver-us") is SOULSILVER
    bad = write_run(tmp_path, "foreign", "soulsilver", FOREIGN)
    assert route.wrote_by_its_own_contract(bad) is False
    assert route.load_route(bad) is None


def test_a_soulsilver_run_written_by_soulsilvers_own_contract_is_read(tmp_path):
    """The control, and the half that makes the test above about IDENTITY rather
    than about the cartridge: the same game, the same directory shape, refused or
    read purely on which decoder wrote the samples."""
    good = write_run(tmp_path, "good", "soulsilver", GOOD_SS)
    assert route.wrote_by_its_own_contract(good) is True
    assert [v[2:6] for v in route.load_route(good)["visits"]] == [
        [64, 0, 3, 4], [64, 0, 3, 5], [64, 0, 3, 6]]


def test_a_pre_contract_firered_run_is_still_read(tmp_path):
    """The 69 tiles and 13 runs. A FireRed run recorded before contracts existed
    carries no ``map_id`` key, and it is still a correct FireRed reading — the
    guard must let it through, or the game the whole system was built on loses
    its history to a rule written for the other six."""
    old = write_run(tmp_path, "old-firered", "firered",
                    [trace_event(1, [pre_contract_sample(0, "D", 6, 9),
                                     pre_contract_sample(1, "D", 6, 10)])])
    assert route.wrote_by_its_own_contract(old) is True
    r = route.load_route(old)
    assert [v[2:6] for v in r["visits"]] == [[3, 0, 6, 9], [3, 0, 6, 10]]
    assert r["maps"]["3:0"]["name"] == "PalletTown"   # and it still gets its graph


def test_a_run_with_no_trace_at_all_is_left_alone(tmp_path):
    """A run that predates the trace system has nothing to disagree with, so the
    guard says nothing about it and ``build_route`` returns None for its own
    reason — no per-input trace. Refusing it HERE instead would put two
    different failures behind one answer."""
    poll_only = write_run(tmp_path, "poll-only", "firered", [poll_event(1, 3, 0, 6, 9)])
    assert route.wrote_by_its_own_contract(poll_only) is True
    assert route.load_route(poll_only) is None


def test_build_skips_the_foreign_run_and_still_builds_the_games_graph(tmp_path):
    """The defect the guard had before it was right. Setting ``contractless``
    here poisoned the whole GAME rather than the run — the flag is never
    cleared, so one pre-contract run blanked a sheet built from fifteen good
    ones. The refusal is of the RUN: it is counted in ``foreign_runs``, its
    inputs still count toward the denominator, and every good run of the same
    cartridge is folded in as usual.

    The foreign run is listed FIRST so that a flag set while reading it would
    have to survive the two good runs that follow — which is exactly how the
    real sheet was blanked."""
    runs = [write_run(tmp_path, "foreign", "soulsilver", FOREIGN),
            write_run(tmp_path, "good-a", "soulsilver", GOOD_SS),
            write_run(tmp_path, "good-b", "soulsilver", GOOD_SS)]
    g = observed.build(runs)["soulsilver-us"]

    assert g.foreign_runs == 1
    assert g.contractless is False, "one bad run must not blank the game"
    assert sorted(g.runs) == ["good-a", "good-b"]
    assert g.edges[((((64,), 3, 4)), (((64,), 3, 5)))] == 2
    assert set(g.visits) == {((64,), 3, 4), ((64,), 3, 5), ((64,), 3, 6)}
    # The foreign run's inputs are counted, and none of its numbers became tiles.
    assert g.inputs == 2 + 3 + 3
    assert not [t for t in g.visits if t[1] > 1000 or t[0] == (1, 112)]


def test_a_game_whose_only_run_is_foreign_gets_an_empty_graph_not_a_blanked_one(tmp_path):
    """The edge of the same rule: with nothing good to fold, the graph is empty
    and the refusal is still recorded as a foreign RUN. ``contractless`` stays
    False because SoulSilver HAS a contract — that flag answers a different
    question, and conflating the two is what made one run speak for a game."""
    g = observed.build([write_run(tmp_path, "foreign", "soulsilver", FOREIGN)])["soulsilver-us"]
    assert g.foreign_runs == 1 and g.contractless is False
    assert dict(g.visits) == {} and dict(g.edges) == {} and g.runs == []
