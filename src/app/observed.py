"""The walk graph a run actually walked — built from the trace, not from a decomp.

``data/firered-walkgraph.json`` is the COMPLETE graph of FireRed: every passable
tile and every connection, built offline from pret's decompilation
(``scripts/build_walkgraph.py``). It exists for exactly one of the seven games
in ``configs/roms.yaml``, and for gen 4, gen 5 and any ROM hack there is no
decompilation to build the equivalent from.

This module builds the other kind of graph — the one the player demonstrated.
Its only input is the per-input position trace a run already records
(``turn_input_trace`` events, ``src/referee/trace.py``), so it works on any game
with a memory contract and needs nothing published about the cartridge.

What an edge means here, and why it is safe
-------------------------------------------
**An observed edge is a proof, and proofs do not expire.** The player stood on
tile A and then on the adjacent tile B; that move is possible, and no later
evidence can make it impossible. So the graph only ever grows, runs can be
unioned in any order, and a partial graph is never *wrong* — only incomplete.
That is the property the stitched-artwork approach did not have, where a wrong
majority stayed wrong however much data arrived.

**A non-move is NOT a proof, and is recorded as evidence only.** A direction
press that changed nothing might be a wall, or a textbox eating the press, or an
NPC standing there, or the first press of a turn-in-place. Telling those apart
needs an in-battle/textbox flag, which is located on FireRed alone
(``GameMemory.census_ok``). So blocks are counted per (tile, direction) under
``blocked`` and never promoted to walls. A wall inferred from a textbox would be
a permanent lie in a structure whose whole value is that it cannot lie.

Displacements that are not steps
--------------------------------
Two kinds, and both are kept out of the walk edges:

- **A map change** is a warp — a door, a staircase, a route seam. Recorded as a
  directed warp between the two tiles, which is what a door IS: one-way per
  direction, exactly as ``walkgraph.py`` treats them.
- **A jump within one map** is a scripted move or a relocation: Oak escorting
  the player, a blackout warping them to the last heal point. ``trace.py``
  already draws this line at :data:`~src.referee.trace.MAX_TILES_PER_INPUT`
  tiles for the same reason, and crediting the shortest path between the two
  ends would invent edges nobody walked.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

# Screen coordinates: +y is DOWN, matching every contract in
# src/referee/contracts.py and the walk graph's own convention.
DIRECTIONS = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}
PRESS_TO_DELTA = {"R": (1, 0), "L": (-1, 0), "D": (0, 1), "U": (0, -1),
                  "RIGHT": (1, 0), "LEFT": (-1, 0), "DOWN": (0, 1), "UP": (0, -1)}

Tile = tuple[tuple, int, int]  # (map_key, x, y); map_key is a tuple, gen-dependent


@dataclass
class ObservedGraph:
    """Tiles stood on, edges walked, warps taken — and blocks merely seen."""

    game: Optional[str] = None
    #: tile -> how many times the player was sampled standing on it
    visits: dict[Tile, int] = field(default_factory=lambda: defaultdict(int))
    #: (from, to) -> times traversed, for adjacent tiles on one map
    edges: dict[tuple[Tile, Tile], int] = field(default_factory=lambda: defaultdict(int))
    #: (from, to) -> times taken, for a map change or a jump
    warps: dict[tuple[Tile, Tile], int] = field(default_factory=lambda: defaultdict(int))
    #: (tile, direction) -> times a press that way moved nothing. EVIDENCE, not walls.
    blocked: dict[tuple[Tile, str], int] = field(default_factory=lambda: defaultdict(int))
    runs: list[str] = field(default_factory=list)
    inputs: int = 0
    unreadable: int = 0
    #: Samples dropped as mid-warp reads — see :func:`_drop_map_flicker`.
    flickers: int = 0
    #: True when this game has no entry in src/referee/contracts.py, so any
    #: coordinates in its runs were decoded with another cartridge's layout.
    contractless: bool = False
    #: Map keys the cartridge does not have, from the contract. A sample
    #: reading one is a failed read, not a place — see GameMemory.invalid_maps.
    invalid_maps: tuple = ()
    #: How many samples that dropped.
    impossible: int = 0
    #: Runs skipped because ANOTHER game's decoder wrote them — see
    #: GameMemory.wrote. Counted, not merged: their numbers are not positions.
    foreign_runs: int = 0

    def add_run(self, run_id: str, samples: Iterable[dict]) -> None:
        """Fold one run's per-input samples in. Order matters; runs do not."""
        self.runs.append(run_id)
        prev: Optional[Tile] = None
        for s in _drop_map_flicker(_drop_impossible_maps(list(samples), self), self):
            # Counted BEFORE the gap check: an input the run made is an input
            # the run made whether or not its position could be read. Counting
            # only survivors here while build()'s contractless branch counted
            # raw samples put two graphs in one summary on two denominators.
            self.inputs += 1
            if s is _GAP:
                # A dropped sample still consumed an INPUT, and the player's
                # position during it is exactly what we could not read. So the
                # samples either side are not consecutive observations and
                # nothing may be minted between them — the same rule an
                # unreadable sample already gets, for the same reason.
                prev = None
                continue
            tile = _tile_of(s)
            if tile is None:
                self.unreadable += 1
                prev = None  # a gap: the next tile has nothing to be adjacent to
                continue
            self.visits[tile] += 1
            if prev is not None and tile != prev:
                (pm, px, py), (m, x, y) = prev, tile
                dx, dy = x - px, y - py
                if pm == m and (dx, dy) in DIRECTIONS:
                    self.edges[(prev, tile)] += 1
                else:
                    self.warps[(prev, tile)] += 1
            elif prev is not None and tile == prev:
                delta = PRESS_TO_DELTA.get(str(s.get("input", "")).upper())
                if delta is not None:
                    self.blocked[(tile, DIRECTIONS[delta])] += 1
            prev = tile

    # --- views ---------------------------------------------------------------

    @property
    def maps(self) -> list[tuple]:
        return sorted({t[0] for t in self.visits}, key=repr)

    def tiles_of(self, map_key: tuple) -> list[Tile]:
        return [t for t in self.visits if t[0] == map_key]

    def bounds(self, map_key: tuple) -> tuple[int, int, int, int]:
        xs = [t[1] for t in self.visits if t[0] == map_key]
        ys = [t[2] for t in self.visits if t[0] == map_key]
        return min(xs), min(ys), max(xs), max(ys)

    def summary(self) -> dict[str, Any]:
        return {
            "game": self.game,
            "runs": self.runs,
            "inputs": self.inputs,
            "unreadable_inputs": self.unreadable,
            "maps": len(self.maps),
            "tiles": len(self.visits),
            "edges": len(self.edges),
            "warps": len(self.warps),
            # Named so nobody reads it as a wall count. Without a textbox flag a
            # block is a press that did nothing, and that is all it is.
            "blocked_observations": sum(self.blocked.values()),
            "midwarp_samples_dropped": self.flickers,
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-able. Keys become strings because JSON has no tuple keys; the
        tile spelling is ``map|x|y`` with the map key joined by ':'."""
        return {
            **self.summary(),
            "per_map": [
                {"map": list(m), "tiles": len(self.tiles_of(m)),
                 "bounds": list(self.bounds(m))}
                for m in self.maps
            ],
            "nodes": [_spell(t) for t in sorted(self.visits, key=repr)],
            "edges": [[_spell(a), _spell(b), n] for (a, b), n in sorted(self.edges.items(), key=repr)],
            "warps": [[_spell(a), _spell(b), n] for (a, b), n in sorted(self.warps.items(), key=repr)],
            "blocked": [[_spell(t), d, n] for (t, d), n in sorted(self.blocked.items(), key=repr)],
        }


def _drop_impossible_maps(samples: list[dict], g: "ObservedGraph") -> list[dict]:
    """Drop samples on a map key the cartridge does not have.

    Unlike :func:`_drop_map_flicker`, which needs no per-game knowledge, this
    one is ENTIRELY per-game knowledge — which is why the knowledge lives in
    the contract (``GameMemory.invalid_maps``) and only the application lives
    here. A failed read is not a short visit, so no run length makes it real
    and the flicker rule alone cannot see it: the Crystal corpus held six
    consecutive all-zero reads, which that rule reads as a stay.
    """
    if not g.invalid_maps:
        return samples
    keep = [_GAP if _map_of(s) in g.invalid_maps else s for s in samples]
    g.impossible += sum(1 for s in keep if s is _GAP)
    return keep


def _drop_map_flicker(samples: list[dict], g: "ObservedGraph") -> list[dict]:
    """Remove single samples whose map differs from BOTH temporal neighbours.

    A sample taken while the game is swapping maps reads the map bytes before
    the new ones land — on Crystal that is (0, 0), a group the cartridge does
    not have. Left in, every warp contributes a phantom tile and TWO phantom
    warp edges, so a long run ends up claiming a map it never entered and
    inflating the one figure that is supposed to count doors.

    The rule needs no per-game knowledge, which is the point: hardcoding "group
    0 is invalid on Crystal" is a fact about one cartridge, and the next game
    would need its own. A map entered and left inside ONE input, with different
    maps either side, is a read artifact on any cartridge — a real map takes at
    least a press to cross.

    It can only ever remove a lone sample: two consecutive samples on a map
    make it real to this filter, so a genuine one-tile corridor survives as
    soon as the player spends two inputs in it.

    A MISSING neighbour counts as a different map, so the rule reaches the
    first and last sample of a run too. That is not a special case, it is the
    same sentence: a map read that no second consecutive read confirms is not
    proof the player was ever there, and a run's edges are no more trustworthy
    than its middle. Leaving the ends out is what let a `0:0` tile back onto
    the Crystal sheet after this filter was written.
    """
    keep = []
    for i, s in enumerate(samples):
        m = _map_of(s)
        if m is not None:
            before = _map_of(samples[i - 1]) if i > 0 else None
            after = _map_of(samples[i + 1]) if i + 1 < len(samples) else None
            if m != before and m != after:
                g.flickers += 1
                keep.append(_GAP)
                continue
        keep.append(s)
    return keep


#: What a dropped sample leaves behind. It is not removed, because removing it
#: would make the samples either side look consecutive: the player spent an
#: input somewhere we could not read, so a move across the hole is one move for
#: all we know, or three. A fabricated warp between two maps no door connects
#: is worse than a missing one — the graph's whole claim is that every edge in
#: it is a proof.
_GAP: dict = {"__gap__": True}


def _map_of(s: dict) -> Optional[tuple]:
    if s is _GAP:
        return None
    if s.get("map_id") is not None:
        return (s["map_id"],)
    g, n = s.get("map_group"), s.get("map_num")
    return None if g is None or n is None else (g, n)


def _spell(t: Tile) -> str:
    m, x, y = t
    return f"{':'.join(str(v) for v in m)}|{x}|{y}"


def _tile_of(s: dict) -> Optional[Tile]:
    """One sample -> a tile, or None when the sample could not be read.

    The map key is whichever shape the game has: a (group, number) pair for gen
    2/3, a single id for gen 4/5. A sample with coordinates but NO map is
    refused rather than filed under a placeholder — two maps sharing a key is
    the silent-corruption failure ``walkgraph.py`` documents, and inventing the
    key here would reproduce it inside the observed graph.
    """
    x, y = s.get("x"), s.get("y")
    if x is None or y is None:
        return None
    if s.get("map_id") is not None:
        return ((s["map_id"],), x, y)
    g, n = s.get("map_group"), s.get("map_num")
    if g is None or n is None:
        return None
    return ((g, n), x, y)


# --- reading runs -------------------------------------------------------------

def run_samples(run_dir: Path) -> Iterator[dict]:
    """Every per-input sample a run recorded, in order."""
    events = run_dir / "events.jsonl"
    if not events.exists():
        return
    for line in events.read_text().splitlines():
        if '"turn_input_trace"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "turn_input_trace":
            continue
        yield from e.get("samples") or []


def run_game(run_dir: Path) -> Optional[str]:
    """The ``game:`` key a run was played on, from its own config.

    Read from the run rather than passed in: a graph keyed by tile carries
    nothing that says which cartridge it came from, so the one place the answer
    is reliable is the config the run actually used.
    """
    cfg = run_dir / "config.json"
    if not cfg.exists():
        return None
    try:
        data = json.loads(cfg.read_text())
    except ValueError:
        return None
    rom_path = (data.get("emulator") or {}).get("rom_path")
    from src.app.roms import rom_for_path

    rom = rom_for_path(rom_path) if rom_path else None
    return rom.game if rom else None


def build(run_dirs: Iterable[Path]) -> dict[str, ObservedGraph]:
    """One graph PER GAME. Never one graph over all runs.

    Emerald's map (3, 0) and FireRed's are the same key and different places
    (``walkgraph.py``), so merging two games into one structure produces a graph
    that answers confidently and wrongly — the exact failure the referee's graph
    loader refuses at load time.
    """
    from src.referee.contracts import contract_for

    out: dict[str, ObservedGraph] = {}
    for d in run_dirs:
        game = run_game(d)
        if game is None:
            continue
        samples = list(run_samples(d))
        if not samples:
            continue
        contract = contract_for(game)
        g = out.setdefault(game, ObservedGraph(game=game))
        if contract is not None:
            g.invalid_maps = tuple(contract.invalid_maps)
            if not contract.wrote(samples[0]):
                # A contract exists, but ANOTHER decoder wrote this run. The
                # spec is baked in at record time, so the numbers on disk are
                # whatever was live then — for the DS games that is FireRed's
                # layout applied to a DS, which yields map_group=1, map_num=112,
                # x=12320. Those are not positions and no re-read makes them into
                # positions. Same test as a contractless game — WHICH DECODER wrote
                # the sample — but the refusal is of the RUN, not the game. Setting
                # `contractless` here poisoned every game instead: it is never
                # cleared, so one pre-contract run blanked a sheet built from
                # fifteen good ones.
                g.foreign_runs += 1
                g.inputs += len(samples)
                continue
        if contract is None:
            # The samples are read back from the run's own events, decoded by
            # whatever code was live when it ran — and before 2026-09-19 that
            # was FireRed's layout applied to EVERY cartridge
            # (src/referee/contracts.py). So a game with no contract whose old
            # runs carry coordinates is not a game we can see the player in; it
            # is a game whose recorded coordinates came from dereferencing
            # 0x03005008 on a machine that keeps no pointer there. Two DS runs
            # from that afternoon each report exactly one tile, which is what
            # that looks like.
            #
            # The test is IDENTITY, not age: the question is which decoder
            # wrote the sample, and the contract registry answers it for every
            # run at once, including ones recorded later by a stale process.
            g.contractless = True
            g.inputs += len(samples)
            g.runs.append(d.name)
            continue
        g.add_run(d.name, samples)
    return out


__all__ = ["ObservedGraph", "build", "run_samples", "run_game", "DIRECTIONS"]
