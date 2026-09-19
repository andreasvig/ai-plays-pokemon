"""The FireRed walk graph — every tile the player can stand on, and how they connect.

Built offline by ``scripts/build_walkgraph.py`` from pret's ``pokefirered``
decompilation (collision bits, metatile behaviours, warps, map connections) and
committed as ``data/firered-walkgraph.json``. Consumed by the referee's
:class:`~src.referee.progress.ProgressTracker` to turn a polled player position
into "how many steps from the next gate" — the between-gate granularity
proposed in ``artifacts/granular-progress/plan.md``.

The graph is DIRECTED: ledges are one-way, and a door warp's two directions are
two separate warp events. Distances are therefore computed on the reversed
graph when the question is "steps FROM here TO the target set".

Node = one passable tile, identified by ``(map_group, map_num, x, y)`` — the
same map-local coordinates the referee reads from SaveBlock1 — and indexed by
an int in ``nodes``. Everything here is pure data + BFS; no I/O beyond ``load``.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

Coord = tuple[int, int, int, int]  # (map_group, map_num, x, y)

# Repo-relative default; the referee resolves it against the repo root.
DEFAULT_GRAPH_PATH = Path("data") / "firered-walkgraph.json"

_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class WalkGraph:
    """Directed tile graph with cached multi-source distance fields."""

    def __init__(self, nodes: list[Coord], adj: list[list[int]], maps: dict[str, dict] | None = None,
                 meta: dict | None = None) -> None:
        if len(nodes) != len(adj):
            raise ValueError(f"nodes ({len(nodes)}) and adj ({len(adj)}) differ in length")
        self.nodes: list[Coord] = [tuple(n) for n in nodes]  # type: ignore[misc]
        self.adj: list[list[int]] = adj
        self.maps: dict[str, dict] = maps or {}
        self.meta: dict = meta or {}
        self._index: dict[Coord, int] = {c: i for i, c in enumerate(self.nodes)}
        self._rev: list[list[int]] | None = None
        self._fields: dict[frozenset[int], list[int]] = {}

    # --- construction ---------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "WalkGraph":
        return cls(
            nodes=[tuple(n) for n in data["nodes"]],
            adj=[list(a) for a in data["adj"]],
            maps=data.get("maps") or {},
            meta={k: v for k, v in data.items() if k not in ("nodes", "adj", "maps")},
        )

    @property
    def game(self) -> Optional[str]:
        """The `game:` key this graph was built for, joining to `configs/roms.yaml`.

        None for a graph built before the field existed. A node is keyed
        `(map_group, map_num, x, y)` with nothing in it that says which
        cartridge, so two games' graphs are indistinguishable once loaded —
        Emerald's map (3, 0) collides with FireRed's Pallet Town key. That makes
        the wrong graph WORSE than no graph: a missing one degrades to
        `progress: None` by design (`src/app/projection.py:457-482`), while a
        wrong one answers every distance query confidently and incorrectly.
        Whoever loads a graph is expected to check this.
        """
        value = self.meta.get("game")
        return value if isinstance(value, str) and value else None

    @classmethod
    def load(cls, path: Path | str) -> "WalkGraph":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return {**self.meta, "maps": self.maps, "nodes": [list(n) for n in self.nodes], "adj": self.adj}

    # --- lookups --------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.nodes)

    def node_id(self, map_group: int, map_num: int, x: int, y: int) -> Optional[int]:
        """Index of the tile, or None when it is not a passable tile of a known map."""
        return self._index.get((map_group, map_num, x, y))

    def coord(self, node: int) -> Coord:
        return self.nodes[node]

    def has_map(self, map_group: int, map_num: int) -> bool:
        return f"{map_group}:{map_num}" in self.maps

    def map_name(self, map_group: int, map_num: int) -> Optional[str]:
        m = self.maps.get(f"{map_group}:{map_num}")
        return m.get("name") if m else None

    def map_nodes(self, map_group: int, map_num: int) -> list[int]:
        return [i for i, (g, n, _x, _y) in enumerate(self.nodes) if g == map_group and n == map_num]

    def map_entry_nodes(self, map_group: int, map_num: int) -> set[int]:
        """Tiles of the map reachable directly from a tile OUTSIDE the map.

        The natural target for a ``map``-type gate: the first tile the player
        stands on after a warp or a connection seam into that map.
        """
        rev = self._reverse()
        out: set[int] = set()
        for i in self.map_nodes(map_group, map_num):
            for j in rev[i]:
                g, n, _x, _y = self.nodes[j]
                if (g, n) != (map_group, map_num):
                    out.add(i)
                    break
        return out

    def passable_neighbours(self, map_group: int, map_num: int, x: int, y: int) -> set[int]:
        """Passable tiles 4-adjacent to a (possibly impassable) tile — where the
        player stands to talk to the NPC / pick up the object on that tile."""
        out: set[int] = set()
        for dx, dy in _NEIGHBOURS:
            i = self.node_id(map_group, map_num, x + dx, y + dy)
            if i is not None:
                out.add(i)
        return out

    # Which way a direction press points, in tile coordinates.
    _STEP = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0),
             "UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}

    def passable_in(self, map_group: int, map_num: int, x: int, y: int, direction: str) -> Optional[bool]:
        """Can the player leave this tile in this direction? None = cannot say.

        True when the graph has an edge to the 4-adjacent tile that way, False
        when it does not, None when the tile is off-graph OR the node carries a
        CROSS-MAP edge. That last case is a door: its target lives on another
        map, so it is absent from the same-map neighbour set and a legitimate
        door approach would read as a wall. Control 2026-09-15: without the
        cross-map guard, 9 presses at PalletTown (16,13), Oak's lab exit, both
        Viridian Forest entrances and PewterCity (15,16) were charged as walls
        on tiles the player demonstrably walked off in that same direction;
        with it, 0 contradictions over both traced runs.
        """
        step = self._STEP.get(str(direction).upper())
        if step is None:
            return None
        node = self.node_id(map_group, map_num, x, y)
        if node is None:
            return None
        found = False
        for other in self.adj[node]:
            g, n, ox, oy = self.coord(other)
            if g != map_group or n != map_num:
                return None  # a warp leaves this tile; a door is not a wall
            dx, dy = ox - x, oy - y
            # Any edge ALONG this axis counts, not just the 4-adjacent tile: a
            # ledge hop is one input that moves two tiles (Route 1 (13,30) D
            # lands on (13,32)). Control 2026-09-15: the adjacent-only test
            # called 4 of 1467 observed moves walls, every one a ledge.
            if (dx, dy) == (0, 0):
                continue
            if step[0] == 0 and dx == 0 and dy * step[1] > 0:
                found = True
            elif step[1] == 0 and dy == 0 and dx * step[0] > 0:
                found = True
        return found

    def resolve_locus(self, checkpoint) -> set[int]:
        """The target tile set for a ladder gate.

        - an explicit ``locus`` ``{map_group, map_num, tiles?, near?}`` always
          wins — ``tiles`` are stand tiles themselves, ``near`` are object/NPC
          tiles whose passable neighbours are the stand tiles. Both may be given.
          A map gate uses it when the story event that fires the gate happens
          somewhere else (Oak's trigger on Pallet's north edge walks you into
          the lab; the lab door is 19 steps the other way — 2026-09-09).
        - ``map`` gates without one: the entry tiles of the signature map.
        - anything else without one: no spatial target (the leg is then
          unscored, never wrong) — an empty set.
        """
        locus = getattr(checkpoint, "locus", None)
        if not isinstance(locus, dict):
            if getattr(checkpoint, "type", None) == "map":
                sig = checkpoint.signature
                return self.map_entry_nodes(int(sig["map_group"]), int(sig["map_num"]))
            return set()
        try:
            g, n = int(locus["map_group"]), int(locus["map_num"])
        except (KeyError, TypeError, ValueError):
            return set()
        out: set[int] = set()
        for x, y in locus.get("tiles") or []:
            i = self.node_id(g, n, int(x), int(y))
            if i is not None:
                out.add(i)
        for x, y in locus.get("near") or []:
            out |= self.passable_neighbours(g, n, int(x), int(y))
        return out

    # --- distances ------------------------------------------------------------

    def _reverse(self) -> list[list[int]]:
        if self._rev is None:
            rev: list[list[int]] = [[] for _ in self.nodes]
            for a, outs in enumerate(self.adj):
                for b in outs:
                    rev[b].append(a)
            self._rev = rev
        return self._rev

    def distance_field(self, targets: Iterable[int]) -> list[int]:
        """Steps from every node TO the nearest target (-1 = unreachable). Cached."""
        key = frozenset(targets)
        field = self._fields.get(key)
        if field is None:
            rev = self._reverse()
            field = [-1] * len(self.nodes)
            q: deque[int] = deque()
            for t in key:
                if 0 <= t < len(field):
                    field[t] = 0
                    q.append(t)
            while q:
                v = q.popleft()
                d = field[v] + 1
                for u in rev[v]:
                    if field[u] < 0:
                        field[u] = d
                        q.append(u)
            self._fields[key] = field
        return field

    def distance_to(self, targets: Iterable[int], node: int) -> Optional[int]:
        """Steps from ``node`` to the nearest of ``targets``; None if unreachable."""
        d = self.distance_field(targets)[node]
        return None if d < 0 else d

    def world_xy(self, map_group: int, map_num: int, x: int, y: int) -> Optional[tuple[int, int]]:
        """Tile coordinates in the shared outdoor frame (graph version 2:
        ``maps[key]["world"]`` is the map's top-left, Pallet Town at (0, 0)).
        None for an indoor map or a graph built before the frame existed.
        Pixels are ``meta["tile_px"]`` (16) per tile."""
        w = (self.maps.get(f"{int(map_group)}:{int(map_num)}") or {}).get("world")
        if not w:
            return None
        return int(w[0]) + int(x), int(w[1]) + int(y)

    def steps_between(self, a: int, b: int) -> Optional[int]:
        """Shortest directed walk a → b; None if b is unreachable from a."""
        if a == b:
            return 0
        seen = {a}
        q: deque[tuple[int, int]] = deque([(a, 0)])
        while q:
            v, d = q.popleft()
            for u in self.adj[v]:
                if u == b:
                    return d + 1
                if u not in seen:
                    seen.add(u)
                    q.append((u, d + 1))
        return None


__all__ = ["WalkGraph", "Coord", "DEFAULT_GRAPH_PATH"]
