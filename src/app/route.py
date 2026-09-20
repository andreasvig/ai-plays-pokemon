"""A run's route: every tile it stood on, in order, drawable on one pixel map.

Plan: ``artifacts/route-fidelity/plan.md`` (2026-09-15). The source of truth is
``events.jsonl`` (decision R1): the per-input ``turn_input_trace`` samples
(``src/referee/trace.py``, one tile after every button since 2026-09-14) and
the referee's per-turn ``referee_position`` polls. Nothing is written at run
time beyond those two events; this module reads them back and orders them.

Route dict (``ROUTE_VERSION`` 2):
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
- ``battles`` — one entry per battle segment
  (:class:`~src.referee.battles.BattleTracker`, the same segments the board
  counts), placed on the map: ``{kind, opened_turn, closed_turn, turns,
  trainer_id, trainer, won, tile}``. ``tile`` is the last tile the player stood
  on OUTSIDE the battle, which is where it was ambushed — the trace flags
  ``in_battle`` per button, so the place a fight started is already recorded
  (M15). A battle we cannot place (no overworld sample before it) carries a
  null tile and is not drawn. ``outcome`` (won / lost / ran / caught / …) and
  ``foe`` (``{species, level, hp, max_hp}``) are present only on runs whose
  referee read them — from 2026-09-15 — and absent otherwise.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from src.referee.battles import BattleTracker, TRAINER_NAMES
from src.referee.trace import MAX_TILES_PER_INPUT
from src.referee.walkgraph import DEFAULT_GRAPH_PATH, WalkGraph

ROUTE_VERSION = 2
POLL = -1
Tile = tuple[int, int, int, int]
ROUTE_EVENT_TYPES = ("turn_input_trace", "referee_position", "referee_battle_state")


#: The one game the committed walk graph describes. `data/firered-walkgraph.json`
#: is pret's FireRed, and its map keys are FireRed's: (3, 0) is a real map in
#: Emerald too, and a different place. Handing it to another cartridge is the
#: mistake the referee's own graph loader already refuses at load time.
GRAPH_GAME = "firered-us"


@lru_cache(maxsize=1)
def default_graph() -> Optional[WalkGraph]:
    """The committed FireRed walk graph, loaded once per process (None if absent)."""
    try:
        return WalkGraph.load(_repo_root() / DEFAULT_GRAPH_PATH)
    except (OSError, ValueError, KeyError):
        return None


def graph_for_run(run_dir: Path) -> Optional[WalkGraph]:
    """The walk graph that describes THIS run's cartridge, or None.

    Only FireRed has one. Six games had it handed to them anyway until
    2026-09-20, which read as harmless because their coordinates mostly failed
    to resolve in it and every transition degraded to ``break`` — Emerald's last
    run scored 709 steps and 23 breaks, not one warp or seam. That is luck, not
    safety: a coordinate that DID resolve would have drawn a door between two
    maps of a different game. Refusing by identity is the same rule
    `src/app/observed.py` applies to a contractless run.
    """
    from src.app.observed import run_game

    try:
        game = run_game(Path(run_dir))
    except Exception:
        return None
    return default_graph() if game == GRAPH_GAME else None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def iter_route_events(run_dir: Path) -> Iterable[dict]:
    """The two event kinds the route is built from, in file order."""
    path = Path(run_dir) / "events.jsonl"
    if not path.is_file():
        return
    with path.open() as fh:
        for line in fh:
            if not any(f'"{t}"' in line for t in ROUTE_EVENT_TYPES):
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") in ROUTE_EVENT_TYPES:
                yield e


def _sample_tile(s: dict) -> Optional[Tile]:
    """One trace sample as ``(a, b, x, y)``.

    Gen 1-3 name a map with a (group, number) PAIR; gen 4 and 5 name it with a
    single id (``src/referee/contracts.py``). Both have to land in the same two
    numeric slots, because the map key travels to the browser as the string
    ``"a:b"`` and the atlas is keyed on it. A single-id game therefore spells
    itself ``"411:0"`` — the id in the first slot and a constant 0 in the
    second. That is a WIRE ENCODING, not a claim that Platinum has a map group;
    nothing reads the second slot for those games, and no two games share a
    route file, so the 0 cannot collide with anything.

    Before 2026-09-20 this required ``map_group``, so every DS run built a
    route with ZERO visits — the trace was fine and the reader could not see it.
    """
    if not isinstance(s, dict) or s.get("x") is None:
        return None
    try:
        if s.get("map_id") is not None:
            return (int(s["map_id"]), 0, int(s["x"]), int(s["y"]))
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
        slot = per_turn.setdefault(turn, {"samples": None, "poll": None, "battle": None})
        kind = e.get("type")
        if kind == "turn_input_trace":
            slot["samples"] = e.get("samples") if isinstance(e.get("samples"), list) else []
        elif kind == "referee_battle_state":
            slot["battle"] = e
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
        "battles": place_battles(per_turn, visits),
    }


def place_battles(per_turn: dict[int, dict[str, Any]], visits: list[list[int]]) -> list[dict[str, Any]]:
    """The run's battle segments, each put on the tile it opened on.

    The segments are :class:`~src.referee.battles.BattleTracker`'s, replayed
    from the same ``referee_battle_state`` events the board's numbers come from
    — so a fight drawn here is a fight counted there, with the same kind, turn
    count and outcome.

    The tile is the last one the player stood on with no battle running, at or
    before the opening turn. That is where the grass was walked into, not where
    the fight was won: the trace samples after every button, so the visit
    before ``in_battle`` first reads true is the ambush.
    """
    if not any(per_turn[t].get("battle") is not None for t in per_turn):
        return _battles_from_trace(per_turn)

    tracker = BattleTracker()
    defeated: set[int] = set()
    for turn in sorted(per_turn):
        b = per_turn[turn].get("battle")
        if b is None:
            continue
        defeated |= {int(i) for i in (b.get("trainers_new") or [])}
        tracker.record(turn, bool(b.get("in_battle")), int(b.get("battles_total") or 0),
                       int(b.get("wild_battles") or 0), int(b.get("trainer_battles") or 0),
                       sorted(defeated), b.get("opponent"),
                       outcome=b.get("outcome"), foe=b.get("foe"), own=b.get("own"))
    out: list[dict[str, Any]] = []
    for seg in tracker.segments():
        opened = seg.get("opened_turn")
        tile = None
        for v in visits:
            if v[0] > opened:
                break
            if not v[6]:                     # in_battle flag of the visit
                tile = v[2:6]
        tid = seg.get("trainer_id")
        entry = {"kind": seg.get("kind"), "opened_turn": opened, "closed_turn": seg.get("closed_turn"),
                 "turns": seg.get("turns"), "trainer_id": tid, "trainer": TRAINER_NAMES.get(tid) if tid else None,
                 "won": seg.get("won"), "uncounted": bool(seg.get("uncounted")), "tile": list(tile) if tile else None}
        # Only on runs that recorded them (2026-09-15 onwards): what was on the
        # other side, and how the fight ended. Absent means the run never read
        # them, which the card must say rather than call the outcome unknown.
        for key in ("outcome", "foe"):
            if seg.get(key) is not None:
                entry[key] = seg[key]
        out.append(entry)
    return out


def _battles_from_trace(per_turn: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Battle segments read off the TRACE SAMPLES, for a run with no referee.

    ``place_battles`` above replays ``referee_battle_state``, which only the
    FireRed benchmark emits — so every SkyEmu run, on all seven cartridges,
    drew zero battles no matter what its trace saw. The trace already carries
    ``in_battle`` per button (``src/referee/contracts.py`` gives six of seven
    games a flag), so a segment is a maximal run of SAMPLES with the flag set,
    and the tile is the last sample before it with the flag clear — the same
    ambush rule, from the same column.

    It reads the samples and NOT ``visits`` on purpose. ``visits`` collapses
    consecutive identical tiles, and a battle is fought standing still: the
    first version of this function walked ``visits`` and found 0 battles in an
    Emerald run holding 214 in-battle samples, because every one of them had
    been collapsed into the tile the fight started on.

    What this CANNOT know, and must not pretend: the outcome, the foe, the
    trainer, and whether the fight was wild or a trainer's. ``kind`` is None
    rather than a guess, so a card says "unknown" instead of inventing a
    category and a caller filtering on ``kind == "trainer"`` finds none.
    Crystal's flag is a MODE byte that does know wild from trainer, but the
    decoder reduces it to a bool before it reaches here, and events.jsonl
    stores the decoded sample — so recovering it needs a decoder change AND a
    new run.
    """
    out: list[dict[str, Any]] = []
    tile: Optional[Tile] = None
    seg: Optional[dict[str, Any]] = None
    for turn in sorted(per_turn):
        for s in per_turn[turn].get("samples") or []:
            if not isinstance(s, dict):
                continue
            here = _sample_tile(s)
            if s.get("in_battle"):
                if seg is None:
                    seg = {"kind": None, "opened_turn": turn, "closed_turn": turn,
                           "trainer_id": None, "trainer": None, "won": None,
                           "uncounted": False, "tile": list(tile) if tile else None}
                else:
                    seg["closed_turn"] = turn
            else:
                if here is not None:
                    tile = here
                if seg is not None:
                    seg["turns"] = seg["closed_turn"] - seg["opened_turn"] + 1
                    out.append(seg)
                    seg = None
    if seg is not None:                       # the run ended mid-battle
        seg["turns"] = seg["closed_turn"] - seg["opened_turn"] + 1
        seg["closed_turn"] = None
        out.append(seg)
    return out


def _classify(graph: Optional[WalkGraph], a: Tile, b: Tile, *, one_input: bool = True
              ) -> tuple[str, int, Optional[list[Tile]]]:
    """(kind, graph steps, fill tiles between a and b for a jump)."""
    same_map = a[:2] == b[:2]
    if same_map and abs(a[2] - b[2]) + abs(a[3] - b[3]) == 1:
        return "step", 1, None
    if graph is None:
        # No published graph for this cartridge, so the only evidence is the two
        # samples themselves — which is exactly what `src/app/observed.py`
        # works from. Two CONSECUTIVE reads on different maps are a door: the
        # player was here, then there, one input apart. It cannot be told from
        # a seam, and it carries no fill, because both of those need a graph.
        # Same map and not adjacent stays a break: without a graph there is no
        # way to know whether a path exists.
        return ("warp", 1, None) if not same_map else ("break", 0, None)
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
    """The route of one run dir, or None when it has no per-input trace.

    ``graph`` defaults to the one that describes THIS run's cartridge, which for
    six of the seven games is none at all — see :func:`graph_for_run`.
    """
    run_dir = Path(run_dir)
    if not wrote_by_its_own_contract(run_dir):
        return None
    return build_route(iter_route_events(run_dir),
                       graph_for_run(run_dir) if graph is None else graph)


def wrote_by_its_own_contract(run_dir: Path) -> bool:
    """False when this run's samples were written by a decoder for another game.

    A run's trace is decoded AT RECORD TIME and stored decoded, so the spec is
    baked into events.jsonl and no later fix can re-read it. Before each DS game
    got a contract on 2026-09-20 its runs were decoded with FireRed's layout,
    which on a DS returns numbers shaped exactly like coordinates — one
    SoulSilver run holds map_group=1, map_num=112, x=12320, y=7259. Drawn, that
    is a route through a map the run never entered, and its in_battle column
    produced 44 battles with 41 of them placed on invented tiles.

    A run with no contract at all is left alone: it has nothing to disagree
    with, `build_route` already returns None when no trace decodes, and a
    FireRed-era run predating the whole trace system must keep working.
    """
    from src.app.observed import run_game
    from src.referee.contracts import contract_for

    try:
        contract = contract_for(run_game(Path(run_dir)))
    except Exception:
        return True
    if contract is None:
        return True
    for e in iter_route_events(Path(run_dir)):
        for s in (e.get("samples") or []):
            if isinstance(s, dict):
                return contract.wrote(s)
    return True


__all__ = ["ROUTE_VERSION", "POLL", "MAX_TILES_PER_INPUT", "build_route", "load_route", "iter_route_events",
           "shortest_path", "default_graph", "graph_for_run", "GRAPH_GAME", "wrote_by_its_own_contract", "place_battles"]
