"""The walk graph must refuse to be loaded under a game it was not built for.

A graph node is keyed ``(map_group, map_num, x, y)`` and carries nothing that
says which cartridge produced it. So loading FireRed's graph under Emerald does
not raise and does not come back empty — Emerald's map (3, 0) hits the key
``"3:0"`` that FireRed calls Pallet Town, and the referee reports
``on_graph: true`` with distances that are confidently wrong.

That asymmetry is the whole point of this file. A MISSING graph is safe: the
tracker degrades to positions-only and ``src/app/projection.py:457-482`` leaves
``progress`` at None so the board falls back to counting gates. A WRONG graph is
not safe, and nothing downstream can tell the difference.

Found by the cross-game audit on 2026-09-19, before the first non-FireRed run
rather than after.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.referee.referee import _load_default_graph
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph

REPO = Path(__file__).resolve().parents[1]
GRAPH = REPO / DEFAULT_GRAPH_PATH

pytestmark = pytest.mark.skipif(not GRAPH.is_file(), reason="no committed walk graph")


def test_the_committed_graph_declares_the_game_it_is_for():
    """Without this field every check below is vacuous."""
    assert WalkGraph.load(GRAPH).game == "firered-us"


def test_it_loads_for_the_game_it_declares():
    graph = _load_default_graph(game="firered-us")
    assert graph is not None
    assert len(graph) > 1000, "a graph that loaded but is empty proves nothing"


def test_it_refuses_to_load_for_another_game():
    assert _load_default_graph(game="emerald-us") is None


def test_the_collision_it_guards_against_is_real(tmp_path):
    """The refusal is not hypothetical: the key really is occupied.

    Emerald's Petalburg City is map (3, 0). If the graph were loaded anyway,
    that coordinate would resolve — to FireRed's Pallet Town. This asserts the
    node is present, so the guard is protecting against something rather than
    against nothing.
    """
    graph = WalkGraph.load(GRAPH)
    assert any(n[0] == 3 and n[1] == 0 for n in graph.nodes), (
        "map (3,0) is not in the graph, so this test no longer witnesses the collision")


def test_without_the_game_argument_it_still_loads(tmp_path):
    """The mutation control: the refusal comes from the check, not from the file.

    If this also returned None the tests above would pass for the wrong reason —
    a graph that fails to load at all refuses every game equally.
    """
    assert _load_default_graph() is not None


def test_a_graph_that_declares_no_game_is_refused(tmp_path):
    """An anonymous graph is exactly the state this guard exists to end.

    Accepting one would reopen the hole for every graph built before the field
    existed, which is the population most likely to be lying about its game.
    """
    data = json.loads(GRAPH.read_text())
    data.pop("game", None)
    anonymous = tmp_path / "anonymous-walkgraph.json"
    anonymous.write_text(json.dumps(data))
    assert WalkGraph.load(anonymous).game is None
    assert _load_default_graph(anonymous, game="firered-us") is None
    # ... and it is the GAME check doing it, not a broken file.
    assert _load_default_graph(anonymous) is not None
