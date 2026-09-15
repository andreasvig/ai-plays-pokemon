"""A run's route: every tile it stood on, in order, drawable on one pixel map.

Plan: ``artifacts/route-fidelity/plan.md`` (2026-09-15). The source of truth is
``events.jsonl`` (decision R1): the per-input ``turn_input_trace`` samples
(``src/referee/trace.py``, one tile after every button since 2026-09-14) and
the referee's per-turn ``referee_position`` polls. Nothing is written at run
time beyond those two events; this module reads them back and orders them.

Route dict (``ROUTE_VERSION`` 1):
- ``visits`` — ``[turn, i, map_group, map_num, x, y, in_battle]`` per tile the
  player stood on, in order; ``i`` is the input index the sample followed or
  ``POLL`` (−1) for the referee's poll after the turn's last input. Consecutive
  identical tiles are collapsed to the first.
- ``transitions`` — how consecutive visits connect on the walk graph: ``step``
  (same map, one tile), ``seam`` (one tile across an outdoor connection),
  ``warp`` (a door, stairs, a hole: one graph edge), ``jump`` (a shortest path
  of 2+ steps — a scripted walk between two samples, Oak's escort to the lab),
  ``teleport`` (a path longer than one input can walk,
  :data:`~src.referee.trace.MAX_TILES_PER_INPUT` — the game relocated the
  player, e.g. a blackout after losing every Pokemon: drawn as its two ends,
  never as a line), ``break`` (no path: an off-graph tile or an unexplained
  relocation).
- ``fills`` — ``{visit index: [[g, m, x, y], ...]}`` the shortest-path tiles
  between visit k and k+1 for every ``jump``, so the drawn line follows the
  ground, not a chord.
- ``tiles_moved`` — total graph steps along the route (jumps at their length).
- ``turns`` — ``{total, traced, blind}``: turns (> 0) with a poll or a trace,
  turns whose trace saw a tile, turns whose trace saw none. ``coverage`` =
  traced ÷ total; the rest are turns the referee polled but no trace reached.
- ``maps`` — ``{"g:m": {name, width, height, world}}`` for every map visited;
  ``world`` is the map's top-left in the shared outdoor frame (graph version 2)
  or None for an indoor map. ``tile_px`` gives the pixel size of one tile.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from src.referee.trace import MAX_TILES_PER_INPUT
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph

ROUTE_VERSION = 1
POLL = -1
Tile = tuple[int, int, int, int]


@lru_cache(maxsize=1)
def default_graph() -> Optional[WalkGraph]:
    """The committed FireRed walk graph, loaded once per process (None if absent)."""
    try:
        return WalkGraph.load(_repo_root() / DEFAULT_GRAPH_PATH)
    except (OSError, ValueError, KeyError):
        return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def iter_route_events(run_dir: Path) -> Iterable[dict]:
    """The two event kinds the route is built from, in file order."""
    path = Path(run_dir) / "events.jsonl"
    if not path.is_file():
        return
    with path.open() as fh:
        for line in fh:
            if '"turn_input_trace"' not in line and '"referee_position"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") in ("turn_input_trace", "referee_position"):
                yield e


def _sample_tile(s: dict) -> Optional[Tile]:
    if not isinstance(s, dict) or s.get("x") is None:
        return None
    try:
        return (int(s["map_group"]), int(s["map_num"]), int(s["x"]), int(s["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def shortest_path(graph: WalkGraph, a: int, b: int) -> Optional[list[int]]:
    """Node path from ``a`` to ``b`` along the directed graph (BFS), ends included."""
    if a == b:
        return [a]
    prev: dict[int, int] = {a: a}
    q: deque[int] = deque([a])
    while q:
        n = q.popleft()
        for u in graph.adj[n]:
            if u in prev:
                continue
            prev[u] = n
            if u == b:
                path = [b]
                while path[-1] != a:
                    path.append(prev[path[-1]])
                return path[::-1]
            q.append(u)
    return None


def build_route(events: Iterable[dict], graph: Optional[WalkGraph]) -> Optional[dict[str, Any]]:
    """Order the trace samples and polls into one route. None when the run has
    no per-input trace at all (runs before 2026-09-14): a poll-only route would
    be a 10-tile-a-turn sketch, not a route, and the board must not draw it."""
    per_turn: dict[int, dict[str, Any]] = {}
    for e in events:
        turn = e.get("turn")
        if not isinstance(turn, int):
            continue
        slot = per_turn.setdefault(turn, {"samples": None, "poll": None})
        if e.get("type") == "turn_input_trace":
            slot["samples"] = e.get("samples") if isinstance(e.get("samples"), list) else []
        else:
            slot["poll"] = e
    if not any(s["samples"] is not None for s in per_turn.values()):
        return None

    visits: list[list[int]] = []
    transitions: Counter[str] = Counter()
    fills: dict[str, list[list[int]]] = {}
    tiles_moved = 0
    traced = blind = 0
    in_battle = 0
    last: Optional[Tile] = None

    def visit(turn: int, i: int, tile: Tile) -> None:
        nonlocal last, tiles_moved
        if tile == last:
            return
        if last is not None:
            # The cap that separates a scripted walk from a game relocation is
            # a PER-INPUT bound, so it only applies when the two visits really
            # are one input apart. A blind turn leaves poll → poll, a whole
            # turn of walking, which may legitimately be far.
            one_input = not (visits and visits[-1][1] == POLL and i == POLL)
            kind, moved, fill = _classify(graph, last, tile, one_input=one_input)
            transitions[kind] += 1
            tiles_moved += moved
            if fill:
                fills[str(len(visits) - 1)] = [list(t) for t in fill]
        visits.append([turn, i, *tile, in_battle])
        last = tile

    for turn in sorted(per_turn):
        slot = per_turn[turn]
        samples, poll = slot["samples"], slot["poll"]
        if samples is not None:
            seen = False
            for s in samples:
                if isinstance(s, dict) and s.get("in_battle") is not None:
                    in_battle = 1 if s["in_battle"] else 0
                tile = _sample_tile(s)
                if tile is None:
                    continue
                seen = True
                visit(turn, int(s.get("i", 0)), tile)
            if seen:
                traced += 1
            else:
                blind += 1
        if poll is not None:
            tile = _sample_tile(poll)
            if tile is not None:
                visit(turn, POLL, tile)

    maps: dict[str, dict[str, Any]] = {}
    for v in visits:
        key = f"{v[2]}:{v[3]}"
        if key in maps:
            continue
        info = dict((graph.maps.get(key) if graph is not None else None) or {})
        maps[key] = {"name": info.get("name"), "width": info.get("width"), "height": info.get("height"),
                     "world": info.get("world")}
    # Turns that had inputs: the referee also polls at turn 0, before any button.
    total = sum(1 for t in per_turn if t > 0)
    return {
        "version": ROUTE_VERSION,
        "graph_source": graph.meta.get("source") if graph is not None else None,
        "tile_px": (graph.meta.get("tile_px") if graph is not None else None) or 16,
        "visits": visits,
        "transitions": dict(transitions),
        "fills": fills,
        "tiles_moved": tiles_moved,
        "turns": {"total": total, "traced": traced, "blind": blind},
        "coverage": (traced / total) if total else 0.0,
        "maps": maps,
    }


def _classify(graph: Optional[WalkGraph], a: Tile, b: Tile, *, one_input: bool = True
              ) -> tuple[str, int, Optional[list[Tile]]]:
    """(kind, graph steps, fill tiles between a and b for a jump)."""
    same_map = a[:2] == b[:2]
    if same_map and abs(a[2] - b[2]) + abs(a[3] - b[3]) == 1:
        return "step", 1, None
    if graph is None:
        return "break", 0, None
    na, nb = graph.node_id(*a), graph.node_id(*b)
    if na is None or nb is None:
        return "break", 0, None
    if nb in graph.adj[na]:
        if not same_map:
            wa, wb = graph.world_xy(*a), graph.world_xy(*b)
            if wa is not None and wb is not None and abs(wa[0] - wb[0]) + abs(wa[1] - wb[1]) == 1:
                return "seam", 1, None
        return "warp", 1, None
    path = shortest_path(graph, na, nb)
    if path is None:
        return "break", 0, None
    if one_input and len(path) - 1 > MAX_TILES_PER_INPUT:
        # The game moved the player (a blackout warp). One step, and NO fill:
        # drawing the shortest path would put a line across half the world the
        # run never walked.
        return "teleport", 1, None
    return "jump", len(path) - 1, [graph.coord(n) for n in path[1:-1]]


def load_route(run_dir: Path, graph: Optional[WalkGraph] = None) -> Optional[dict[str, Any]]:
    """The route of one run dir, or None when it has no per-input trace."""
    return build_route(iter_route_events(Path(run_dir)), default_graph() if graph is None else graph)


__all__ = ["ROUTE_VERSION", "POLL", "MAX_TILES_PER_INPUT", "build_route", "load_route", "iter_route_events", "shortest_path", "default_graph"]
