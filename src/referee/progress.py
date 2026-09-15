"""Between-gate progress: how far along the *current* leg of the ladder the run is.

The referee latches gates (integers). This module turns the player position it
polls every turn into the fraction of the way to the *next* gate, measured in
walkable steps on the :class:`~src.referee.walkgraph.WalkGraph` — the metric
chosen in ``artifacts/granular-progress/plan.md`` §3A / §8.

Vocabulary
----------
- **Node** — one rung of the ladder (a :class:`Checkpoint` or a
  :class:`MultiGate`), in ladder order.
- **Leg k** — the stretch of the run that ends when node *k* is complete. The
  leg's *target set* is the node's locus resolved on the graph (for a
  multigate: the loci of its still-unstamped members).
- **Cursor** — the index of the current leg: the first incomplete node at or
  after the furthest *reached* node. It only ever moves forward; a rung it
  jumps over (skipped past its deadline, or out-of-order) gets a ``skipped``
  leg record so the list stays aligned with the ladder.
- **d_open (D)** — steps from the position where the leg opened to the target;
  **d_min** — the closest the run has ever been during the leg. A node with
  ``score_to: reached`` (the starter gate, 2026-09-14) re-bases D when the leg
  CLOSES: the shortest path from the opening position to the tile the gate was
  completed on, so a legitimate choice among several finishing tiles is not
  charged as a detour (d_min and distance_now become 0);
  ``fraction = clamp(1 - d_min / D, 0, 1)``, capped at
  :data:`OPEN_LEG_FRACTION_CAP` (0.95) while the leg is still open: standing
  on the target's tile is not the target (2026-09-11 — glm-5.3-flash(max) stood
  at Brock for its last leg and read as a 100 % clear next to two runs that
  actually won). Only a stamp closes the leg and makes it a whole gate.
- **progress** — ``gates_reached + fraction`` where ``gates_reached`` counts
  COMPLETE rungs (the same count the scorecard's done/auto statuses give).

Everything is a pure function of ``(positions, stamps)``: the tracker replays
positions against the stamps whenever the stamps change (a handful of times per
run) and extends incrementally otherwise, so a run resumed from a savepoint
(``load_state``) lands on exactly the numbers the live run had. No graph → the
tracker still records positions and legs, but every distance is ``None`` and
``progress`` is ``None``.

Off-graph positions (map not in the graph, tile not passable) never *lower* a
distance: the previous values stand and the leg's ``off_graph`` counter ticks.
A position that is on-graph but from which the target is unreachable is treated
the same way for distances (it still counts as a tile seen and toward steps).
"""

from __future__ import annotations

from typing import Any, Optional

# The most an UNFINISHED leg can contribute to ``progress``. A run at the target
# tile without the stamp has not done the gate: cap it below 1.0 so it never
# ties a run that did (Andreas, 2026-09-11, option A).
OPEN_LEG_FRACTION_CAP = 0.95

from src.referee.checkpoints import MultiGate, Node
from src.referee.walkgraph import WalkGraph

Position = tuple[int, int, int, int, int]  # (turn, map_group, map_num, x, y)


class _Leg:
    """Mutable per-leg accumulator. Serialised by :meth:`ProgressTracker.summary`."""

    __slots__ = (
        "index", "node_id", "name", "status", "opened_turn", "closed_turn",
        "scored", "targets", "d_open", "d_min", "distance_now", "steps_walked",
        "tiles", "off_graph", "prev_node", "traced_turns", "bound_turns",
        "open_node", "score_to", "walls_hit",
    )

    def __init__(self, index: int, node_id: str, name: str, *, scored: bool,
                 targets: frozenset[int], steps_known: bool, score_to: str = "nearest") -> None:
        self.index = index
        self.node_id = node_id
        self.name = name
        self.status = "open"  # open | closed | skipped
        self.opened_turn: Optional[int] = None
        self.closed_turn: Optional[int] = None
        self.scored = scored
        self.targets = targets
        self.d_open: Optional[int] = None
        self.d_min: Optional[int] = None
        self.distance_now: Optional[int] = None
        self.steps_walked: Optional[int] = 0 if steps_known else None
        self.tiles: set[tuple[int, int, int, int]] = set()
        self.off_graph = 0
        self.prev_node: Optional[int] = None  # last on-graph node in this leg
        # How many of this leg's turn steps came from the per-input trace
        # (exact) vs the shortest-path bound between polls (2026-09-14).
        self.traced_turns = 0
        self.bound_turns = 0
        # Direction presses into a wall while already facing it, from the
        # per-input trace. Kept OUT of steps_walked so the pure-path number
        # survives a reweighting; efficiency charges it (plan W4,
        # artifacts/wasted-inputs/plan.md).
        self.walls_hit = 0
        # The first on-graph node of the leg (where D was measured from) and
        # the node's scoring rule — "reached" re-bases D at close (see module doc).
        self.open_node: Optional[int] = None
        self.score_to = score_to

    def fraction(self) -> Optional[float]:
        if not self.scored or self.d_open is None or self.d_min is None:
            return None
        if self.d_open <= 0:
            return 0.0
        frac = max(0.0, min(1.0, 1.0 - self.d_min / self.d_open))
        if self.status != "closed":
            frac = min(frac, OPEN_LEG_FRACTION_CAP)
        return frac

    def charged_steps(self) -> Optional[int]:
        """Steps efficiency is measured against: every tile actually walked plus
        one for every press into a wall. A bump burns the same frames as a step
        and buys nothing, so leaving it free let a run headbutt a ledge at no
        cost (live 2026-09-15: 577 of gemini-3.8-flash(high)'s presses)."""
        if self.steps_walked is None:
            return None
        return self.steps_walked + self.walls_hit

    def efficiency(self) -> Optional[float]:
        if self.status != "closed" or self.d_open is None or self.d_open <= 0:
            return None
        charged = self.charged_steps()
        if charged is None:
            return None
        return self.d_open / max(charged, self.d_open)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "node_id": self.node_id,
            "name": self.name,
            "status": self.status,
            "scored": self.scored,
            "opened_turn": self.opened_turn,
            "closed_turn": self.closed_turn,
            "d_open": self.d_open,
            "d_min": self.d_min,
            "distance_now": self.distance_now,
            "fraction": self.fraction(),
            "steps_walked": self.steps_walked,
            "walls_hit": self.walls_hit,
            "charged_steps": self.charged_steps(),
            "tiles_seen": len(self.tiles),
            "off_graph": self.off_graph,
            "efficiency": self.efficiency(),
            # "trace" = every stepped turn traced, "bound" = none, else "mixed".
            "steps_source": ("trace" if self.traced_turns and not self.bound_turns else
                             "bound" if self.bound_turns and not self.traced_turns else
                             "mixed" if self.traced_turns else None),
            "traced_turns": self.traced_turns,
        }


class ProgressTracker:
    """Per-poll position → per-leg distance stats → ``progress`` float.

    ``graph`` may be ``None`` (file missing, build not done): positions are
    still recorded and legs still open/close on stamps, but no distance is
    ever computed and ``summary()["progress"]`` is ``None``.
    """

    def __init__(self, graph: Optional[WalkGraph], nodes: list[Node]) -> None:
        self.graph = graph
        self.nodes: list[Node] = list(nodes)
        self.positions: list[Position] = []
        self.stamps: dict[str, int] = {}
        # Exact overworld steps per turn from the per-input trace
        # (src/referee/trace.py); replaces the between-poll bound for that turn.
        self.traced_steps: dict[int, int] = {}
        # The last tile the trace sampled on that turn. A door warp or the
        # auto-step out of a door outlasts the input's gap, so the sample shows
        # the old tile and the poll a second later the settled one; the steps
        # between them belong to the turn (2026-09-15, plan R3).
        self.traced_end: dict[int, tuple[int, int, int, int]] = {}
        self.traced_walls: dict[int, int] = {}
        self._steps_cache: dict[tuple[int, int], Optional[int]] = {}
        # Per-member resolved loci, computed once: node index -> gate id -> tiles.
        self._member_targets: list[dict[str, frozenset[int]]] = []
        for node in self.nodes:
            members = node.gates if isinstance(node, MultiGate) else [node]
            self._member_targets.append({
                g.id: (frozenset(graph.resolve_locus(g)) if graph is not None else frozenset())
                for g in members
            })
        self._reset_derived()

    # --- public API -----------------------------------------------------------

    def record(self, turn: int, map_group: int, map_num: int, x: int, y: int) -> dict[str, Any]:
        """Append one polled position and fold it into the current leg.

        Returns what the referee's ``referee_position`` event needs:
        ``{"on_graph", "distance", "next_gate"}`` — ``distance`` is this
        position's steps to the current target (None off-graph / unreachable /
        unscored), ``next_gate`` the current leg's node id (None once the ladder
        is complete).
        """
        pos: Position = (int(turn), int(map_group), int(map_num), int(x), int(y))
        self.positions.append(pos)
        # Stamps at this turn are not known yet (the referee stamps after it
        # records), so every pending transition is strictly earlier: apply them
        # with an unknown opening position, then fold the position in.
        self._apply_transitions(before_turn=pos[0], opening=None)
        node, d = self._fold(pos)
        leg = self._current_leg()
        return {
            "on_graph": node is not None,
            "distance": d,
            "next_gate": leg.node_id if leg is not None else None,
        }

    def observe_stamps(self, stamps: dict[str, int], furthest_node_idx: Optional[int] = None) -> None:
        """Tell the tracker the referee's latch. Replays positions when it changed.

        ``furthest_node_idx`` is accepted for call-site symmetry with the
        referee but not needed: the furthest reached rung is derived from the
        stamps themselves, identically to ``Referee._furthest_reached_node_idx``.
        """
        known = {k: int(v) for k, v in stamps.items() if k in self._gate_ids()}
        if known == self.stamps:
            return
        self.stamps = known
        self._rebuild()

    def record_traced_steps(self, turn: int, steps: int,
                            end_tile: Optional[tuple[int, int, int, int]] = None,
                            walls: int = 0) -> None:
        """Exact overworld steps for ``turn`` — call BEFORE that turn's ``record``.
        ``end_tile`` is the trace's last sampled tile; the poll's tile may lie a
        warp or a door step further, which ``_fold`` adds to the turn.
        ``walls`` is the turn's presses into a wall, folded into the leg's
        ``walls_hit`` and charged by :meth:`_Leg.efficiency`."""
        self.traced_steps[int(turn)] = max(int(steps), 0)
        if walls:
            self.traced_walls[int(turn)] = max(int(walls), 0)
        else:
            self.traced_walls.pop(int(turn), None)
        if end_tile is not None and len(end_tile) == 4:
            self.traced_end[int(turn)] = tuple(int(v) for v in end_tile)  # type: ignore[assignment]
        else:
            self.traced_end.pop(int(turn), None)

    def export_state(self) -> dict[str, Any]:
        out: dict[str, Any] = {"positions": [list(p) for p in self.positions]}
        if self.traced_steps:  # only runs with a per-input trace carry the key
            out["traced_steps"] = {str(t): s for t, s in sorted(self.traced_steps.items())}
        if self.traced_end:
            out["traced_end"] = {str(t): list(c) for t, c in sorted(self.traced_end.items())}
        if self.traced_walls:
            out["traced_walls"] = {str(t): w for t, w in sorted(self.traced_walls.items())}
        return out

    def load_state(self, data: Any) -> None:
        """Restore ``positions`` (everything else is recomputed). Tolerant of junk."""
        positions: list[Position] = []
        raw = data.get("positions") if isinstance(data, dict) else None
        for entry in raw or []:
            try:
                if len(entry) != 5:
                    continue
                positions.append(tuple(int(v) for v in entry))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        self.positions = positions
        traced: dict[int, int] = {}
        raw_t = data.get("traced_steps") if isinstance(data, dict) else None
        for t, s in (raw_t or {}).items():
            try:
                traced[int(t)] = int(s)
            except (TypeError, ValueError):
                continue
        self.traced_steps = traced
        ends: dict[int, tuple[int, int, int, int]] = {}
        for t, c in ((data.get("traced_end") if isinstance(data, dict) else None) or {}).items():
            try:
                if len(c) == 4:
                    ends[int(t)] = tuple(int(v) for v in c)  # type: ignore[assignment]
            except (TypeError, ValueError):
                continue
        self.traced_end = ends
        walls: dict[int, int] = {}
        for t, w in ((data.get("traced_walls") if isinstance(data, dict) else None) or {}).items():
            try:
                walls[int(t)] = int(w)
            except (TypeError, ValueError):
                continue
        self.traced_walls = walls
        self._rebuild()

    def summary(self) -> dict[str, Any]:
        legs = [leg.to_dict() for leg in self._legs]
        current = self._current_leg()
        gates_reached = sum(1 for k in range(len(self.nodes)) if self._complete_turn(k) is not None)
        progress: Optional[float]
        if self.graph is None:
            progress = None
        else:
            frac = current.fraction() if current is not None else None
            progress = float(gates_reached) + (frac or 0.0)
        return {
            "progress": progress,
            "gates_reached": gates_reached,
            "total_gates": len(self.nodes),
            "current_leg": current.to_dict() if current is not None else None,
            "legs": legs,
            "graph": {
                "loaded": self.graph is not None,
                "source": (self.graph.meta.get("source") if self.graph is not None else None),
                "nodes": len(self.graph) if self.graph is not None else 0,
            },
            "positions_recorded": len(self.positions),
        }

    # --- stamps → node timing -------------------------------------------------

    def _gate_ids(self) -> set[str]:
        return {gid for per_node in self._member_targets for gid in per_node}

    def _members(self, k: int) -> list:
        node = self.nodes[k]
        return node.gates if isinstance(node, MultiGate) else [node]

    def _complete_turn(self, k: int, upto: Optional[int] = None) -> Optional[int]:
        """Turn node k became complete (all members stamped), or None."""
        turns = []
        for g in self._members(k):
            t = self.stamps.get(g.id)
            if t is None or (upto is not None and t > upto):
                return None
            turns.append(t)
        return max(turns) if turns else None

    def _reach_turn(self, k: int, upto: Optional[int] = None) -> Optional[int]:
        """Turn node k was first reached (any member stamped), or None."""
        turns = [
            t for g in self._members(k)
            if (t := self.stamps.get(g.id)) is not None and (upto is None or t <= upto)
        ]
        return min(turns) if turns else None

    def _cursor_at(self, upto: Optional[int]) -> int:
        """Current leg index given stamps with turn <= ``upto`` (None = all)."""
        furthest = -1
        for k in range(len(self.nodes)):
            if self._reach_turn(k, upto) is not None:
                furthest = k
        for k in range(max(furthest, 0), len(self.nodes)):
            if self._complete_turn(k, upto) is None:
                return k
        return len(self.nodes)

    def _targets_at(self, k: int, upto: Optional[int]) -> frozenset[int]:
        """Target tiles of leg k given stamps <= ``upto``: the unstamped members' loci."""
        out: set[int] = set()
        for g in self._members(k):
            t = self.stamps.get(g.id)
            if t is not None and (upto is None or t <= upto):
                continue
            out |= self._member_targets[k][g.id]
        return frozenset(out)

    def _transitions(self) -> list[tuple[int, int, frozenset[int]]]:
        """Every stamp turn at which the cursor moves or the current leg retargets.

        Returns ``[(turn, cursor_after, targets_of_that_leg_after)]`` in turn
        order, including only turns where something changed.
        """
        out: list[tuple[int, int, frozenset[int]]] = []
        cursor = self._cursor_at(-1)
        targets = self._targets_at(cursor, -1) if cursor < len(self.nodes) else frozenset()
        for t in sorted(set(self.stamps.values())):
            c = self._cursor_at(t)
            tg = self._targets_at(c, t) if c < len(self.nodes) else frozenset()
            if c != cursor or tg != targets:
                out.append((t, c, tg))
                cursor, targets = c, tg
        return out

    # --- derived state --------------------------------------------------------

    def _reset_derived(self) -> None:
        self._legs: list[_Leg] = []
        self._pending = self._transitions()
        self._cursor = self._cursor_at(-1)
        if self._cursor < len(self.nodes):
            self._legs.append(self._new_leg(self._cursor, self._targets_at(self._cursor, -1)))

    def _new_leg(self, k: int, targets: frozenset[int]) -> _Leg:
        node = self.nodes[k]
        return _Leg(
            k, node.id, node.name,
            scored=self.graph is not None and bool(targets),
            targets=targets,
            steps_known=self.graph is not None,
            score_to=getattr(node, "score_to", "nearest"),
        )

    def _current_leg(self) -> Optional[_Leg]:
        if self._legs and self._legs[-1].status == "open":
            return self._legs[-1]
        return None

    def _rebuild(self) -> None:
        self._reset_derived()
        for pos in self.positions:
            self._apply_transitions(before_turn=pos[0], opening=None)
            self._fold(pos)
            self._apply_transitions(before_turn=pos[0] + 1, opening=pos)
        self._apply_transitions(before_turn=None, opening=None)

    def _apply_transitions(self, *, before_turn: Optional[int], opening: Optional[Position]) -> None:
        """Consume pending transitions with turn < ``before_turn`` (all if None).

        ``opening`` is the position the new leg opens at — the position polled
        on the transition's own turn — or None when no position was recorded
        for that turn (legacy state), in which case the leg's D falls back to
        its first on-graph position.
        """
        while self._pending and (before_turn is None or self._pending[0][0] < before_turn):
            turn, new_cursor, new_targets = self._pending.pop(0)
            current = self._current_leg()
            if new_cursor == self._cursor:
                # Same leg, different target set (a multigate member stamped):
                # restart the distance stats against what is left, keep the
                # walking/coverage totals.
                if current is not None:
                    current.targets = new_targets
                    current.scored = self.graph is not None and bool(new_targets)
                    current.d_open = current.d_min = current.distance_now = None
                    if opening is not None:
                        self._seed_distance(current, opening)
                continue
            # Close the current leg and every rung the cursor jumped over. A
            # rung that completed at this turn is "closed"; one the run moved
            # past without completing (skipped past its deadline, or an
            # out-of-order later gate) is "skipped" — its stats stay for the
            # report but it earns no efficiency and no gate.
            for k in range(self._cursor, new_cursor):
                if k == self._cursor and current is not None:
                    leg = current
                else:
                    leg = self._new_leg(k, frozenset())
                    leg.opened_turn = turn
                    self._legs.append(leg)
                leg.closed_turn = turn
                leg.status = "closed" if self._complete_turn(k, turn) is not None else "skipped"
                if leg.status == "closed":
                    self._rebase_to_reached(leg)
            self._cursor = new_cursor
            if new_cursor < len(self.nodes):
                leg = self._new_leg(new_cursor, new_targets)
                leg.opened_turn = turn
                self._legs.append(leg)
                if opening is not None:
                    node = self._node_of(opening)
                    leg.prev_node = node
                    leg.tiles.add(opening[1:])
                    self._seed_distance(leg, opening)

    def _node_of(self, pos: Position) -> Optional[int]:
        if self.graph is None:
            return None
        return self.graph.node_id(pos[1], pos[2], pos[3], pos[4])

    def _distance(self, leg: _Leg, node: int) -> Optional[int]:
        if self.graph is None or not leg.scored:
            return None
        return self.graph.distance_to(leg.targets, node)

    def _seed_distance(self, leg: _Leg, pos: Position) -> None:
        """Set d_open (and d_min / distance_now) from the leg's opening position."""
        node = self._node_of(pos)
        if node is None:
            return
        d = self._distance(leg, node)
        if d is not None:
            leg.d_open = d
            leg.d_min = d
            leg.distance_now = d
            leg.open_node = node

    def _rebase_to_reached(self, leg: _Leg) -> None:
        """``score_to: reached`` — at close, D becomes the shortest path from
        the leg's opening node to the node the gate was completed on (the last
        on-graph position folded into the leg: the stamp turn's poll, folded
        before the stamp closed it). Leaves the leg alone when either end is
        unknown or unreachable, so a legacy leg keeps its nearest-tile D."""
        if leg.score_to != "reached" or self.graph is None or not leg.scored:
            return
        if leg.open_node is None or leg.prev_node is None:
            return
        d = self._steps(leg.open_node, leg.prev_node)
        if d is None:
            return
        leg.d_open = d
        leg.d_min = 0
        leg.distance_now = 0

    def _steps(self, a: int, b: int) -> Optional[int]:
        key = (a, b)
        if key not in self._steps_cache:
            self._steps_cache[key] = self.graph.steps_between(a, b)  # type: ignore[union-attr]
        return self._steps_cache[key]

    def _fold(self, pos: Position) -> tuple[Optional[int], Optional[int]]:
        """Fold one position into the current leg. Returns (node, distance)."""
        leg = self._current_leg()
        node = self._node_of(pos)
        if leg is None:
            return node, None
        if leg.opened_turn is None:
            leg.opened_turn = pos[0]  # leg 0 opens at the first recorded position
        leg.tiles.add(pos[1:])
        if node is None:
            leg.off_graph += 1
            return None, None
        if leg.prev_node is not None and leg.steps_walked is not None:
            traced = self.traced_steps.get(pos[0])
            if traced is not None:
                leg.steps_walked += traced
                leg.traced_turns += 1
                leg.walls_hit += self.traced_walls.get(pos[0], 0)
                # Movement the poll saw after the last sample (a warp that was
                # still fading, the auto-step out of a door) — plan R3.
                end = self.traced_end.get(pos[0])
                end_node = self._node_of((pos[0], *end)) if end is not None else None
                if end_node is not None and end_node != node:
                    extra = self._steps(end_node, node)
                    if extra:
                        leg.steps_walked += extra
            else:
                step = self._steps(leg.prev_node, node)
                if step is not None:
                    leg.steps_walked += step
                    leg.bound_turns += 1
        leg.prev_node = node
        d = self._distance(leg, node)
        if d is not None:
            if leg.d_open is None:
                leg.d_open = d  # opening position unknown/off-graph: first on-graph one stands in
                leg.open_node = node
            leg.distance_now = d
            leg.d_min = d if leg.d_min is None else min(leg.d_min, d)
        return node, d


__all__ = ["ProgressTracker", "Position"]
