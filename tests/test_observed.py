"""The walk graph a run actually walked (src/app/observed.py).

The module's central claim is that an observed edge is a PROOF: the player stood
on A and then on the adjacent B, so that move is possible and nothing later can
make it impossible. Everything here is a consequence of that sentence, and the
tests are shaped to catch the ways it could quietly stop being true:

- a proof does not depend on when it arrived, so folding two runs in either
  order, or the same run twice, must land on the same graph;
- a proof is about ONE map, so a tile change across maps is a warp however
  adjacent the two coordinate pairs happen to look;
- a press that moved nothing proves nothing — it is counted under ``blocked``
  and never promoted to a wall, because a textbox, an NPC and a turn-in-place
  produce exactly the same non-move;
- a map read that no second consecutive read confirms is not proof either, and
  since 2026-09-19 that includes the first and last sample of a run, which is
  what let a phantom ``0:0`` map back onto the Crystal sheet.

Fixtures are the producer's shape, not an invented one: every sample dict below
has the keys ``trace.decode_samples`` emits, and
``test_the_fixtures_are_the_shape_the_decoder_emits`` is the live check on that.
"""

from __future__ import annotations

import json
import os
import struct
import time

import pytest
from pathlib import Path

from src.app import observed
from src.app.observed import DIRECTIONS, ObservedGraph, build, run_game, run_samples
from src.app.roms import get_rom, load_roms
from src.referee import trace
from src.referee.contracts import CRYSTAL, FIRERED, contract_for


def sample(i, inp, x, y, group=3, num=0, battle=False, total=0):
    """One Gen 2/3 sample, keyed exactly as ``trace.decode_samples`` leaves it."""
    return {"i": i, "input": inp, "map_group": group, "map_num": num,
            "map_id": None, "x": x, "y": y,
            "in_battle": battle, "battles_total": total, "foe_species": None, "foe_level": None, "battle_kind": None,
            "battle_outcome": None, "trainer_id": None, "trainer_class": None}


def ds_sample(i, inp, x, y, map_id):
    """A Gen 4/5 sample: one map id, no (group, number) pair."""
    return {"i": i, "input": inp, "map_group": None, "map_num": None,
            "map_id": map_id, "x": x, "y": y,
            "in_battle": False, "battles_total": None, "foe_species": None, "foe_level": None, "battle_kind": None,
            "battle_outcome": None, "trainer_id": None, "trainer_class": None}


def torn(i, inp, x, y):
    """The short read of :func:`tests.test_contracts.
    test_a_short_sample_loses_one_field_not_the_whole_turn`: the coordinates
    arrived and the map bytes did not."""
    return {"i": i, "input": inp, "map_group": None, "map_num": None,
            "map_id": None, "x": x, "y": y, "in_battle": False, "battles_total": None, "foe_species": None, "foe_level": None, "battle_kind": None,
            "battle_outcome": None, "trainer_id": None, "trainer_class": None}


def graph_of(*runs):
    g = ObservedGraph(game="firered-us")
    for i, samples in enumerate(runs):
        g.add_run(f"run-{i}", samples)
    return g


def state(g):
    """Everything a fold produced, as plain dicts — the defaultdicts would grow
    a key under a bare lookup and make a comparison lie."""
    return dict(g.edges), dict(g.visits), dict(g.warps), dict(g.blocked), g.inputs, g.flickers


# --- the fixtures are the producer's ------------------------------------------


def test_the_fixtures_are_the_shape_the_decoder_emits():
    """The one test that keeps every other test honest. A fixture shape the
    decoder never emits witnesses nothing — see the FireRed row in
    tests/test_contracts.py, which is where this row comes from."""
    row = ("D", [struct.pack("<hhBB", 6, 9, 3, 0), bytes([0]), struct.pack("<I", 0)])
    decoded = trace.decode_samples([row], 0, FIRERED)[0]
    assert decoded.keys() == sample(0, "D", 6, 9).keys()
    assert decoded == sample(0, "D", 6, 9)
    assert observed._tile_of(decoded) == ((3, 0), 6, 9)


def test_a_graph_can_be_folded_straight_off_the_decoder():
    """End to end on the other cartridge, and on the other field order: Crystal
    keeps one unsigned byte per axis and records (group, number, y, x)."""
    rows = [("D", [bytes([26, 3, 9, 5])]), ("D", [bytes([26, 3, 10, 5])])]
    g = graph_of(trace.decode_samples(rows, None, CRYSTAL))
    assert dict(g.edges) == {(((26, 3), 5, 9), ((26, 3), 5, 10)): 1}


# --- what an edge is ----------------------------------------------------------


def test_walking_one_tile_records_an_edge_and_two_visits():
    g = graph_of([sample(0, "D", 6, 9), sample(1, "D", 6, 10)])
    assert dict(g.edges) == {(((3, 0), 6, 9), ((3, 0), 6, 10)): 1}
    assert dict(g.visits) == {((3, 0), 6, 9): 1, ((3, 0), 6, 10): 1}
    assert dict(g.warps) == {} and dict(g.blocked) == {}
    assert g.inputs == 2 and g.unreadable == 0 and g.flickers == 0


def test_an_edge_is_directed_and_the_return_trip_is_its_own_edge():
    """Doors are one-way per direction (``walkgraph.py``), so the structure is
    directed throughout; a graph that folded the pair would claim a way back it
    never saw."""
    a, b = ((3, 0), 6, 9), ((3, 0), 6, 10)
    g = graph_of([sample(0, "D", 6, 9), sample(1, "D", 6, 10), sample(2, "U", 6, 9)])
    assert dict(g.edges) == {(a, b): 1, (b, a): 1}


def test_all_four_orthogonal_moves_are_edges_and_nothing_else_is():
    """DIRECTIONS is the whole vocabulary: +y is DOWN, matching every contract
    in src/referee/contracts.py."""
    assert DIRECTIONS == {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}
    steps = [(6, 9), (7, 9), (7, 10), (6, 10), (6, 9)]
    g = graph_of([sample(i, "D", x, y) for i, (x, y) in enumerate(steps)])
    assert len(g.edges) == 4 and dict(g.warps) == {}


def test_a_map_change_is_a_warp_even_when_the_coordinates_look_adjacent():
    """The load-bearing negative. A door lands the player one tile below where
    they stood, on a different map; an edge test that looked only at (dx, dy)
    would call that a walk and claim two maps are one continuous surface — the
    silent-corruption failure walkgraph.py documents, reproduced inside the
    observed graph."""
    samples = [sample(0, "D", 6, 9, 3, 0), sample(1, "D", 6, 9, 3, 0),
               sample(2, "D", 6, 10, 1, 4), sample(3, "D", 6, 11, 1, 4)]
    g = graph_of(samples)
    assert dict(g.warps) == {(((3, 0), 6, 9), ((1, 4), 6, 10)): 1}
    assert ((((3, 0), 6, 9), ((1, 4), 6, 10))) not in g.edges
    assert dict(g.edges) == {(((1, 4), 6, 10), ((1, 4), 6, 11)): 1}


def test_a_jump_within_one_map_is_a_warp_and_invents_no_tiles_between():
    """Oak escorting the player, or a blackout to the last heal point. Crediting
    the shortest path between the two ends would invent edges nobody walked, so
    the displacement is one warp and the tiles in between stay unvisited."""
    g = graph_of([sample(0, "B", 6, 9), sample(1, "B", 6, 20)])
    assert dict(g.warps) == {(((3, 0), 6, 9), ((3, 0), 6, 20)): 1}
    assert dict(g.edges) == {}
    assert set(g.visits) == {((3, 0), 6, 9), ((3, 0), 6, 20)}


def test_a_diagonal_displacement_is_not_an_edge():
    """Nothing in these games moves a player diagonally in one input, so a
    diagonal pair is two samples with something unobserved between them."""
    g = graph_of([sample(0, "D", 6, 9), sample(1, "R", 7, 10)])
    assert dict(g.edges) == {}
    assert dict(g.warps) == {(((3, 0), 6, 9), ((3, 0), 7, 10)): 1}


def test_a_gen_four_run_is_keyed_by_its_single_map_id():
    """Gen 4/5 identify a map by one id, not a (group, number) pair, and the
    tile key carries whichever shape the cartridge has."""
    g = graph_of([ds_sample(0, "D", 4, 5, 17), ds_sample(1, "D", 4, 6, 17)])
    assert dict(g.edges) == {(((17,), 4, 5), ((17,), 4, 6)): 1}
    assert g.maps == [(17,)]


# --- a non-move is evidence, not a wall ---------------------------------------


def test_a_press_that_moved_nothing_is_counted_as_evidence_not_as_a_wall():
    """Three samples on one tile, then a step. The two presses that changed
    nothing are counted — and counted as what they are. A textbox, an NPC and
    the first press of a turn-in-place all look exactly like this, and telling
    them apart needs a flag located on FireRed alone."""
    tile = ((3, 0), 6, 9)
    g = graph_of([sample(0, "D", 6, 9), sample(1, "D", 6, 9),
                  sample(2, "D", 6, 9), sample(3, "D", 6, 10)])
    assert dict(g.blocked) == {(tile, "down"): 2}
    assert g.visits[tile] == 3
    assert len(g.edges) == 1
    s = g.summary()
    assert s["blocked_observations"] == 2
    assert not [k for k in s if "wall" in k]  # nothing here may be read as a wall count


def test_a_block_is_counted_per_direction_in_the_direction_actually_pressed():
    """Four presses on one tile, one per direction, and the mapping from press
    to name has to survive: recording an ``up`` press as ``down`` would file the
    evidence against the wrong side of the tile."""
    tile = ((3, 0), 6, 9)
    presses = ["D", "U", "L", "R", "DOWN"]
    g = graph_of([sample(0, "A", 6, 9)] + [sample(i + 1, p, 6, 9) for i, p in enumerate(presses)])
    assert dict(g.blocked) == {(tile, "down"): 2, (tile, "up"): 1,
                               (tile, "left"): 1, (tile, "right"): 1}


def test_a_press_that_is_not_a_direction_records_no_block():
    """An A that opened a textbox moved nothing and says nothing about the tile's
    four sides, so it is not evidence about any of them."""
    g = graph_of([sample(0, "A", 6, 9), sample(1, "A", 6, 9), sample(2, "B", 6, 9)])
    assert dict(g.blocked) == {}
    assert g.visits[((3, 0), 6, 9)] == 3


# --- a gap is a gap -----------------------------------------------------------


def test_an_unreadable_sample_breaks_adjacency():
    """The two tiles either side of the gap ARE orthogonally adjacent, which is
    the point: joining them would be an edge inferred from a missing sample
    rather than proved by a pair of readings, and the player could have been
    relocated and walked back inside the gap."""
    samples = [sample(0, "D", 6, 9), sample(1, "D", 6, 10), torn(2, "D", 6, 10),
               sample(3, "D", 6, 11), sample(4, "D", 6, 12)]
    g = graph_of(samples)
    assert dict(g.edges) == {(((3, 0), 6, 9), ((3, 0), 6, 10)): 1,
                             (((3, 0), 6, 11), ((3, 0), 6, 12)): 1}
    assert (((3, 0), 6, 10), ((3, 0), 6, 11)) not in g.edges
    assert dict(g.warps) == {}
    assert g.unreadable == 1 and g.inputs == 5


def test_a_sample_with_coordinates_but_no_map_is_refused_not_filed_under_a_placeholder():
    """Two maps sharing a key is the silent-corruption failure; inventing a
    placeholder key here would reproduce it inside the observed graph."""
    assert observed._tile_of(torn(0, "D", 6, 9)) is None
    assert observed._tile_of(sample(0, "D", None, None)) is None
    g = graph_of([torn(0, "D", 6, 9), torn(1, "D", 6, 10)])
    assert dict(g.visits) == {} and g.unreadable == 2
    assert all(None not in t[0] for t in g.visits)


# --- the flicker filter, and the end-of-run extension -------------------------


def test_a_lone_map_read_at_the_start_of_a_run_is_not_proof():
    """The 2026-09-19 extension. A MISSING neighbour counts as a different map,
    so the rule reaches the first sample: one read of a map that no second
    consecutive read confirms is not proof the player was ever there."""
    samples = [sample(0, "D", 2, 2, 1, 4), sample(1, "D", 6, 9), sample(2, "D", 6, 10)]
    g = graph_of(samples)
    assert g.flickers == 1
    assert g.maps == [(3, 0)]
    assert dict(g.warps) == {}
    assert dict(g.edges) == {(((3, 0), 6, 9), ((3, 0), 6, 10)): 1}
    # `inputs` counts inputs the run MADE, dropped ones included — one
    # denominator for every graph in a summary, contractless ones too.
    assert g.inputs == len(samples)


def test_a_lone_map_read_at_the_end_of_a_run_is_not_proof():
    """The same sentence at the other end — and the end is where a run is cut
    off mid-warp, so it is the end that produced the phantom."""
    samples = [sample(0, "D", 6, 9), sample(1, "D", 6, 10), sample(2, "D", 2, 2, 1, 4)]
    g = graph_of(samples)
    assert g.flickers == 1
    assert g.maps == [(3, 0)]
    assert dict(g.warps) == {}
    assert dict(g.edges) == {(((3, 0), 6, 9), ((3, 0), 6, 10)): 1}


def test_two_consecutive_samples_on_a_map_survive_at_either_end_of_a_run():
    """The control for the two tests above, and the reason the rule is safe: the
    filter can only ever remove a LONE sample, so a genuine one-tile corridor
    entered at the very start of a run survives as soon as the player spends two
    inputs in it. Without this, the extension would be deleting real data."""
    samples = [sample(0, "D", 2, 2, 1, 4), sample(1, "D", 2, 3, 1, 4),
               sample(2, "D", 6, 9), sample(3, "D", 6, 10)]
    g = graph_of(samples)
    assert g.flickers == 0
    assert g.maps == [(1, 4), (3, 0)]
    assert dict(g.warps) == {(((1, 4), 2, 3), ((3, 0), 6, 9)): 1}
    assert len(g.edges) == 2
    assert g.inputs == 4


def test_the_phantom_crystal_zero_zero_map_never_reaches_the_sheet():
    """The case this filter exists for. A sample taken while Crystal is swapping
    maps reads the map bytes before the new ones land — group 0, number 0, a
    group the cartridge does not have. Left in, ONE such read costs a phantom
    tile, a phantom map on the sheet and TWO phantom warps, inflating the one
    figure that is supposed to count doors.

    The run below carries the phantom twice: once mid-warp between two real
    maps, and once as the last sample of the run, which is the copy that
    survived the filter's first version and put ``0:0`` back on the sheet."""
    samples = [sample(0, "D", 3, 4, 26, 3), sample(1, "D", 3, 5, 26, 3),
               sample(2, "D", 3, 5, 0, 0),           # mid-warp read
               sample(3, "D", 5, 5, 10, 1), sample(4, "D", 5, 6, 10, 1),
               sample(5, "D", 0, 0, 0, 0)]           # and the run is cut off in one
    g = graph_of(samples)
    assert (0, 0) not in g.maps and g.maps == [(10, 1), (26, 3)]
    assert not [t for t in g.visits if t[0] == (0, 0)]
    assert g.flickers == 2
    # And NO warp is minted across the hole. The dropped sample consumed an
    # input, so the reads either side are not consecutive observations: for all
    # the graph knows the player crossed one door there or three. Joining them
    # would put an edge in the graph that no door justifies, which is the one
    # thing an observed graph must never do. Measured on the real corpus, this
    # is 2 of Crystal's 22 distinct warps and 5 of its 622 edges.
    assert dict(g.warps) == {}, "a flicker must not be joined into a warp"
    assert g.summary()["midwarp_samples_dropped"] == 2
    assert g.summary()["maps"] == 2 and g.summary()["warps"] == 0


def test_a_single_sample_run_proves_nothing_at_all():
    """One sample has no neighbour on either side, so it confirms no map and
    leaves no tile. That is the shape the two contractless DS runs had — each
    reporting exactly one tile — and it is the honest answer to it."""
    g = graph_of([sample(0, "D", 6, 9)])
    assert dict(g.visits) == {} and g.flickers == 1 and g.inputs == 1
    assert g.runs == ["run-0"]  # the run is still on the record


def test_an_unreadable_sample_confirms_no_map_either():
    """Documented consequence rather than a separate rule: a torn read has no
    map, so a readable sample sitting alone between two torn ones is a map read
    nothing confirms, and goes the same way as any other lone read."""
    samples = [torn(0, "D", 6, 9), sample(1, "D", 6, 10), torn(2, "D", 6, 11)]
    g = graph_of(samples)
    assert g.flickers == 1
    assert dict(g.visits) == {} and g.unreadable == 2


# --- proofs do not expire: order, repetition, growth --------------------------


RUN_A = [sample(0, "D", 6, 9), sample(1, "D", 6, 10), sample(2, "D", 6, 10),
         sample(3, "D", 6, 11)]
RUN_B = [sample(0, "U", 6, 11), sample(1, "U", 6, 10), sample(2, "R", 7, 10),
         sample(3, "R", 7, 10), sample(4, "R", 7, 10, 1, 4), sample(5, "D", 7, 11, 1, 4)]


def test_two_runs_union_to_the_same_graph_in_either_order():
    """Runs can be unioned in ANY order — the property the stitched-artwork
    approach did not have, where a wrong majority stayed wrong however much data
    arrived. Every count here is a tally of proofs, and a tally does not care
    which proof arrived first."""
    forward, backward = graph_of(RUN_A, RUN_B), graph_of(RUN_B, RUN_A)
    assert state(forward) == state(backward)
    assert forward.summary() | {"runs": []} == backward.summary() | {"runs": []}
    assert sorted(forward.runs) == sorted(backward.runs)


def test_folding_the_same_run_twice_invents_no_edge():
    """Idempotent in STRUCTURE, additive in counts. A second fold of the same
    trace is the same evidence seen twice: it may not discover a tile, an edge,
    a warp or a block that one fold did not."""
    once, twice = graph_of(RUN_A), graph_of(RUN_A, RUN_A)
    assert set(twice.edges) == set(once.edges)
    assert set(twice.visits) == set(once.visits)
    assert set(twice.warps) == set(once.warps)
    assert set(twice.blocked) == set(once.blocked)
    assert all(twice.edges[k] == 2 * v for k, v in once.edges.items())
    assert twice.inputs == 2 * once.inputs


def test_a_partial_graph_is_incomplete_and_never_wrong():
    """The whole safety argument in one assertion: folding more data may only
    ADD keys and RAISE counts. Nothing a later run says can retract a tile, an
    edge or a warp an earlier run proved."""
    g = ObservedGraph(game="firered-us")
    g.add_run("a", RUN_A)
    before = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in (("edges", g.edges), ("visits", g.visits),
                           ("warps", g.warps), ("blocked", g.blocked))}
    g.add_run("b", RUN_B)
    for name, snapshot in before.items():
        after = getattr(g, name)
        assert set(snapshot) <= set(after), f"{name} lost a key"
        assert all(after[k] >= n for k, n in snapshot.items()), f"{name} lost a count"
    assert len(g.edges) > len(before["edges"])  # and the fold did add something


# --- views --------------------------------------------------------------------


def test_bounds_and_tiles_are_per_map():
    g = graph_of([sample(0, "D", 6, 9), sample(1, "D", 6, 10),
                  sample(2, "R", 7, 10), sample(3, "R", 7, 10, 1, 4),
                  sample(4, "D", 7, 11, 1, 4)])
    assert g.maps == [(1, 4), (3, 0)]
    assert sorted(g.tiles_of((3, 0))) == [((3, 0), 6, 9), ((3, 0), 6, 10), ((3, 0), 7, 10)]
    assert g.bounds((3, 0)) == (6, 9, 7, 10)
    assert g.bounds((1, 4)) == (7, 10, 7, 11)


def test_to_dict_is_json_able_and_spells_a_tile_as_map_x_y():
    g = graph_of([sample(0, "D", 6, 9), sample(1, "D", 6, 10), sample(2, "D", 6, 10)])
    d = g.to_dict()
    assert json.loads(json.dumps(d)) == d  # JSON has no tuple keys; nothing may leak one
    # Sorted by repr, so the order is lexicographic ("6|10" before "6|9") rather
    # than numeric. Deterministic is what the file needs — it is diffed — and
    # this pins which deterministic order it is.
    assert d["nodes"] == ["3:0|6|10", "3:0|6|9"]
    assert d["edges"] == [["3:0|6|9", "3:0|6|10", 1]]
    assert d["blocked"] == [["3:0|6|10", "down", 1]]
    assert d["per_map"] == [{"map": [3, 0], "tiles": 2, "bounds": [6, 9, 6, 10]}]
    assert observed._spell(((17,), 4, 5)) == "17|4|5"  # and a gen 4 key spells with no colon


# --- reading runs off disk ----------------------------------------------------


def a_contractless_rom() -> str:
    """A rom id from roms.yaml whose game has no contract yet.

    Chosen at test time rather than hardcoded. "platinum" was hardcoded here
    until 2026-09-20, when Platinum got a contract and two tests that had
    nothing to do with Platinum went red. The refusal these tests pin is about
    a game the registry does not cover, and which game that is changes as the
    work lands — so ask the registry, and say so out loud when the answer is
    "none left", which is the end state this project is walking toward.
    """
    for rom in load_roms():
        if contract_for(rom.game) is None:
            return rom.id
    pytest.skip("every rom in roms.yaml now has a contract — nothing left to refuse")


def write_run(tmp_path: Path, name: str, rom_id: str, turns: list[list[dict]]) -> Path:
    """A run directory shaped the way a finished run leaves one: a config.json
    carrying the ROM it was launched with, and events.jsonl carrying one
    ``turn_input_trace`` per turn."""
    run = tmp_path / name
    run.mkdir()
    rom = get_rom(rom_id)
    (run / "config.json").write_text(json.dumps(
        {"emulator": {"rom_path": rom.path}, "game_name": rom.game_name}))
    lines = [json.dumps({"type": "turn_input_trace", "turn": i + 1, "samples": s})
             for i, s in enumerate(turns)]
    (run / "events.jsonl").write_text("\n".join(lines) + "\n")
    return run


def test_run_samples_yields_every_sample_in_order(tmp_path):
    run = write_run(tmp_path, "r", "firered",
                    [[sample(0, "D", 6, 9), sample(1, "D", 6, 10)], [sample(0, "D", 6, 11)]])
    assert [(s["x"], s["y"]) for s in run_samples(run)] == [(6, 9), (6, 10), (6, 11)]


def test_run_samples_survives_a_torn_line_and_ignores_other_events(tmp_path):
    """A run killed mid-write leaves a half line, and the cheap ``in line``
    prefilter matches any event that merely MENTIONS the type. Neither may cost
    the samples that did arrive."""
    run = tmp_path / "r"
    run.mkdir()
    (run / "events.jsonl").write_text(
        '{"type": "referee_position", "turn": 1, "x": 6, "y": 9}\n'
        '{"type": "turn_input_trace", "turn": 1, "samples": [\n'          # torn
        '{"type": "note", "text": "turn_input_trace was empty"}\n'        # wrong type
        + json.dumps({"type": "turn_input_trace", "turn": 2,
                      "samples": [sample(0, "D", 6, 10)]}) + "\n"
        + json.dumps({"type": "turn_input_trace", "turn": 3, "samples": None}) + "\n")
    assert [(s["x"], s["y"]) for s in run_samples(run)] == [(6, 10)]


def test_run_samples_of_a_run_with_no_events_file_is_empty(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    assert list(run_samples(run)) == []


def test_run_game_reads_the_cartridge_off_the_runs_own_config(tmp_path):
    """A graph keyed by tile carries nothing that says which cartridge it came
    from, so the answer is read from the config the run actually used."""
    assert run_game(write_run(tmp_path, "a", "crystal", [[]])) == "crystal-us"
    assert run_game(write_run(tmp_path, "b", "platinum", [[]])) == "platinum-us"


def test_run_game_claims_nothing_for_a_config_it_cannot_join(tmp_path):
    missing = tmp_path / "no-config"
    missing.mkdir()
    assert run_game(missing) is None

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "config.json").write_text("{not json")
    assert run_game(broken) is None

    offreg = tmp_path / "offreg"
    offreg.mkdir()
    (offreg / "config.json").write_text(json.dumps(
        {"emulator": {"rom_path": "roms/some-hack-i-built.gba"}}))
    assert run_game(offreg) is None


# --- build(): one graph per game, and the contractless refusal ----------------


def test_build_keeps_one_graph_per_game_and_never_merges_two(tmp_path):
    """Emerald's map (3, 0) and FireRed's are the same key and different places.
    Both runs below stand on ((3, 0), 6, 9) — the collision is real and the only
    thing preventing it is that the graphs are separate."""
    walk = [[sample(0, "D", 6, 9), sample(1, "D", 6, 10)]]
    runs = [write_run(tmp_path, "fr", "firered", walk),
            write_run(tmp_path, "em", "emerald", walk)]
    graphs = build(runs)
    assert set(graphs) == {"firered-us", "emerald-us"}
    for game, g in graphs.items():
        assert g.game == game
        assert set(g.visits) == {((3, 0), 6, 9), ((3, 0), 6, 10)}
        assert g.visits[((3, 0), 6, 9)] == 1  # not 2: nothing was merged


def test_build_unions_two_runs_of_one_game_into_one_graph(tmp_path):
    walk = [[sample(0, "D", 6, 9), sample(1, "D", 6, 10)]]
    graphs = build([write_run(tmp_path, "a", "firered", walk),
                    write_run(tmp_path, "b", "firered", walk)])
    assert list(graphs) == ["firered-us"]
    g = graphs["firered-us"]
    assert sorted(g.runs) == ["a", "b"] and len(g.edges) == 1
    assert g.edges[(((3, 0), 6, 9), ((3, 0), 6, 10))] == 2


def test_build_skips_a_run_with_no_game_and_one_with_no_trace(tmp_path):
    """Neither is an error: a hand-rolled config is legitimate, and a run that
    recorded no trace has nothing to contribute. Both must produce no graph
    rather than an empty one, which would read as a game that walked nowhere."""
    nogame = tmp_path / "nogame"
    nogame.mkdir()
    (nogame / "config.json").write_text(json.dumps({"emulator": {"rom_path": "roms/x.gba"}}))
    (nogame / "events.jsonl").write_text(json.dumps(
        {"type": "turn_input_trace", "turn": 1, "samples": [sample(0, "D", 6, 9)]}) + "\n")
    assert build([nogame, write_run(tmp_path, "notrace", "firered", [])]) == {}


def test_build_refuses_a_contractless_game_by_identity_not_by_age(tmp_path):
    """A game with no entry in src/referee/contracts.py had its coordinates
    decoded with another cartridge's layout — before 2026-09-19 every run got
    FireRed's, which on a DS cartridge means dereferencing 0x03005008 on a
    machine that keeps no pointer there. Those numbers are not positions.

    The test the module applies is IDENTITY — which decoder wrote the sample —
    and the control for that is AGE: the contractless run below is stamped NOW
    and the contracted one a year ago, and the refusal follows the contract
    registry in both directions regardless."""
    walk = [[sample(0, "D", 6, 9), sample(1, "D", 6, 10)]]
    blind_rom = a_contractless_rom()
    ds = write_run(tmp_path, "ds-run", blind_rom, walk)
    gba = write_run(tmp_path, "gba-run", "firered", walk)
    now, year_ago = time.time(), time.time() - 365 * 86400
    for p in (ds, ds / "config.json", ds / "events.jsonl"):
        os.utime(p, (now, now))
    for p in (gba, gba / "config.json", gba / "events.jsonl"):
        os.utime(p, (year_ago, year_ago))

    blind_game = get_rom(blind_rom).game
    assert contract_for(blind_game) is None and contract_for("firered-us") is not None
    graphs = build([ds, gba])

    refused = graphs[blind_game]
    assert refused.contractless is True
    assert dict(refused.visits) == {} and dict(refused.edges) == {} and dict(refused.warps) == {}
    assert refused.inputs == 2 and refused.runs == ["ds-run"]

    kept = graphs["firered-us"]
    assert kept.contractless is False  # older, and accepted
    assert len(kept.edges) == 1


def test_a_contractless_game_stays_refused_however_recently_it_was_recorded(tmp_path):
    """The registry answers for every run of that game at once, including one
    recorded later by a stale process — which is exactly the run an age cutoff
    would wave through."""
    walk = [[sample(0, "D", 6, 9), sample(1, "D", 6, 10)]]
    blind_rom = a_contractless_rom()
    graphs = build([write_run(tmp_path, "old", blind_rom, walk),
                    write_run(tmp_path, "brand-new", blind_rom, walk)])
    g = graphs[get_rom(blind_rom).game]
    assert g.contractless is True
    assert sorted(g.runs) == ["brand-new", "old"]
    assert g.inputs == 4 and dict(g.visits) == {}
