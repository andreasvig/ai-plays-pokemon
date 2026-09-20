"""Render a gen-3 game's maps to PNG from pret's tilesets.

    venv/bin/python scripts/render_gamemaps.py                      # FireRed, fetch (cached) + render all
    venv/bin/python scripts/render_gamemaps.py --game emerald-us    # Emerald
    venv/bin/python scripts/render_gamemaps.py --only PalletTown
    venv/bin/python scripts/render_gamemaps.py --offline            # cache only, no network

Output: ``src/dashboard/web/public/maps/<game>/<group>-<num>.png`` at the game's own
16 px per tile, plus ``index.json`` carrying the pret SHA and the walk-graph
version so a map image and the geometry drawn on it cannot silently disagree
(artifacts/game-map-render/plan.md M1-M3).

ONE SCRIPT, MANY GAMES (2026-09-20). Every gen-3 decomp ships the same file
shapes — ``data/layouts/layouts.json`` is field-for-field identical between
pokefirered and pokeemerald — so a second game is a DESCRIPTOR (``Game``
below), not a fork. What differs per game is four constants out of
``include/fieldmap.h``, verified at the pinned SHA of each repo rather than
assumed:

    ========================  =======  =======
    include/fieldmap.h        FireRed  Emerald
    ========================  =======  =======
    NUM_TILES_IN_PRIMARY          640      512
    NUM_METATILES_IN_PRIMARY      640      512
    NUM_PALS_IN_PRIMARY             7        6
    NUM_PALS_TOTAL                 13       13
    ========================  =======  =======

The palette count is the famous one (a wrong value recolours half the world),
but the two 640 -> 512 splits matter more: with FireRed's numbers every Emerald
metatile above 511 is looked up in the wrong table and the map renders as
garbage that still looks like a map.

WHAT A GAME WITHOUT A WALK GRAPH LOSES. FireRed has a decomp walk graph
(``data/firered-walkgraph.json``); Emerald has none, only the ground our runs
actually walked (``artifacts/game-map-render/observed/<game>-observed.json``).
Five fields depended on the graph, and each was decided on its own:

- the map manifest  -> the OBSERVED map list. Only maps a run entered.
- width / height    -> pret's ``layouts.json``, which is authoritative. The
                       graph cross-check is replaced by an assertion that the
                       OBSERVED bounds fit inside the layout: observed ground
                       can only ever be inside the real map, so an overrun
                       means the render is wrong. The check still bites.
- ``trim``          -> NOT EMITTED. Trimming needs the set of walkable tiles,
                       and the observed graph holds only tiles somebody stood
                       on, so it would clip real floor. Absent is a legal state
                       and an honest one; a wrong trim erases part of the map.
                       Measured, and it costs nothing: run ``empty_edges`` on
                       every Emerald interior with an EMPTY walkable set — the
                       flatness half of the rule alone, which is the largest
                       trim the rule could ever return — and all nine come back
                       {0, 0, 0, 0}. Emerald's rooms have no flat filler strip
                       at any edge. FireRed's black bar under the door is a
                       FireRed tileset fact, not a gen-3 one.
- ``doors``         -> emitted. The corridor rule has two halves and only the
                       first survives without a graph: "its exits lead to two
                       different maps" is pure ``warp_events`` and runs for any
                       game. ``_corridor_by_graph`` (the Route 2 east building
                       case) needs the graph and is skipped — so a corridor
                       whose two doors sit on ONE outdoor map would be marked
                       as a building on a graphless game. None exists in the
                       Emerald set; say so rather than pretend the rule ran.
- ``world``         -> emitted, from pret's own ``connections`` by the same BFS
                       ``build_walkgraph.py`` runs for FireRed, seeded at the
                       game's starting town. It is what makes the maps tile
                       into a region instead of stacking as loose rectangles.

``index.json`` also carries what the map viewer needs to place a building
(M10-M12), all of it static and all of it derived here rather than shipped to
the browser as a walk graph:

- ``type``      the map's own ``map_type``.
- ``building``  the name floors share: ``PewterCity_PokemonCenter_{1F,2F}``
                are one building, ordered by ``floors``.
- ``trim``      tile rows and columns at the EDGES that are one flat colour AND
                hold no walkable tile. Every FireRed interior ends in one such
                row — the black strip under the door — and drawn honestly it
                reads as an empty progress bar beneath the floor (Andreas,
                2026-09-15: "I still see this bar"). Both conditions are
                required: flatness alone would clip the doormat art bleeding
                into that row, and the walk-graph test alone would clip real
                scenery nobody can stand on. Note the strip is BLACK ART, not
                the transparent backdrop, which for the lab's tileset is cream
                — testing for the backdrop finds nothing. Tile coordinates are
                untouched; a viewer just draws a shorter image.
- ``doors``     on an outdoor map, the tiles a building is entered from —
                ``{x, y, to, building}``. A transition room gets none: it is a
                corridor, not a place, and it is one when EITHER of two things
                is true of the building (all its floors together):

                * its exits lead to two different maps — both Viridian Forest
                  gate houses (Route 2 and the forest) and the Route 22 gate
                  (Route 22 and Route 23, which we do not render); or
                * it is the only way through: close the building on the walk
                  graph and its own outside doorsteps stop reaching each other.
                  That is Route 2's east building, whose two doors are both on
                  Route 2 with a cliff between them — the first rule cannot see
                  it, and the Pewter museum, which also has two doors onto one
                  town, must not be caught by it.

The format, from ``include/fieldmap.h`` at the pinned SHA:

- ``map.bin``          one u16 per tile; ``& 0x03FF`` is the metatile id.
- ``metatiles.bin``    16 bytes per metatile: eight u16s, the first four the
                       bottom layer (2x2 of 8x8 tiles), the last four the top.
- each u16             ``& 0x03FF`` tile, ``>>10 & 1`` xflip, ``>>11 & 1``
                       yflip, ``>>12 & 0xF`` palette.
- ``tiles.png``        4bpp indexed, 128 px wide, 8x8 tiles row-major.
- primary/secondary    tiles and metatiles below the game's
                       ``NUM_{TILES,METATILES}_IN_PRIMARY`` are the primary
                       tileset's, the rest the secondary's; palettes below
                       ``NUM_PALS_IN_PRIMARY`` are the primary's and the rest
                       the secondary's. See the table above.

Colour index 0 is the GBA's transparent index: it shows the backdrop, which is
entry 0 of palette 0. Both layers honour it.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CACHE_ROOT = REPO_ROOT / "local"
MAPS_ROOT = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"

TILE_PX = 8
METATILE_TILES = 2          # 2x2 tiles per layer


@dataclass(frozen=True)
class Game:
    """Everything that differs between two gen-3 decomps.

    ``sha`` is HARDCODED and must be a full 40-hex commit. The cache marker it
    used to be read from is absent on a clean checkout, and the fallback was the
    string "master" — unverifiable provenance that ``tests/test_gamemaps.py``
    now refuses outright.
    """

    key: str                    # "firered-us" — the atlas directory and `game`
    repo: str                   # "pret/pokefirered"
    sha: str                    # pinned, 40 hex
    frame: str                  # the shared world frame's name: "kanto", "hoenn"
    cache: Path                 # one per repo: the two share file PATHS
    tiles_in_primary: int
    metatiles_in_primary: int
    pals_in_primary: int
    pals_total: int
    #: exactly one of these two says which maps to render
    graph: Optional[Path] = None      # a decomp walk graph (FireRed)
    observed: Optional[Path] = None   # the maps our runs entered (everyone else)
    #: world-frame origin, observed games only — the graph carries its own
    world_origin: Optional[str] = None

    @property
    def out(self) -> Path:
        return MAPS_ROOT / self.key


GAMES: dict[str, Game] = {
    "firered-us": Game(
        key="firered-us",
        repo="pret/pokefirered",
        sha="c75f352304d529f6ba92d4f74b9cf8b5c3810788",
        frame="kanto",
        # shared with scripts/build_walkgraph.py, which fetches the same tree
        cache=CACHE_ROOT / "pret-cache",
        tiles_in_primary=640,
        metatiles_in_primary=640,
        pals_in_primary=7,
        pals_total=13,
        graph=REPO_ROOT / "data" / "firered-walkgraph.json",
    ),
    "emerald-us": Game(
        key="emerald-us",
        repo="pret/pokeemerald",
        sha="5eff78649e7170a877b961ef0b3da13b81a16038",
        frame="hoenn",
        # NOT `pret-cache`: `data/layouts/layouts.json` exists in both repos and
        # one cache would serve FireRed's file for Emerald without a word.
        cache=CACHE_ROOT / "pret-cache-emerald",
        tiles_in_primary=512,
        metatiles_in_primary=512,
        pals_in_primary=6,
        pals_total=13,
        observed=REPO_ROOT / "artifacts" / "game-map-render" / "observed" / "emerald-us-observed.json",
        world_origin="LittlerootTown",
    ),
}


# ---------------------------------------------------------------- fetching

def fetch(path: str, *, offline: bool, game: Game) -> bytes:
    """Cache-first read of one pret file, pinned at ``game.sha``.

    Same cache and same reasoning as scripts/build_walkgraph.py: raw
    .githubusercontent.com, never the contents API, whose base64 mangled a
    binary file on 2026-09-09.
    """
    local = game.cache / path
    if local.exists():
        return local.read_bytes()
    if offline:
        raise SystemExit(f"--offline but {path} is not cached")
    req = urllib.request.Request(f"https://raw.githubusercontent.com/{game.repo}/{game.sha}/{path}",
                                 headers={"User-Agent": "pokebench-gamemaps/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"fetch {path} failed: {exc}") from exc
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return data


def listing(path: str, *, offline: bool, game: Game) -> list[str]:
    """Directory names under ``path``, cached as ``_listing.json``.

    The contents API is safe HERE and only here: a directory listing is names,
    not the base64 blob that corrupted a binary read.
    """
    cache = game.cache / path / "_listing.json"
    if cache.exists():
        return json.loads(cache.read_text())
    if offline:
        raise SystemExit(f"--offline but {path} is not listed in the cache")
    url = f"https://api.github.com/repos/{game.repo}/contents/{path}?ref={game.sha}"
    req = urllib.request.Request(url, headers={"User-Agent": "pokebench-gamemaps/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            entries = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise SystemExit(f"list {path} failed: {exc}") from exc
    names = sorted(d["name"] for d in entries if d.get("type") == "dir")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(names))
    return names


MAP_TYPE_INDOOR = "MAP_TYPE_INDOOR"
FLOOR_RE = re.compile(r"_(?:B?\d+F|Basement\d*|Roof)$")


def camel_to_const(name: str) -> str:
    parts = []
    for part in name.split("_"):
        parts.append(re.sub(r"(?<=[a-z])(?=[A-Z])", "_", part).upper())
    return "MAP_" + "_".join(parts)


def tileset_dir(kind: str, gname: str, *, offline: bool, game: Game) -> str:
    """'gTileset_GenericBuilding1' -> 'data/tilesets/secondary/generic_building_1'."""
    want = gname.removeprefix("gTileset_").lower()
    for d in listing(f"data/tilesets/{kind}", offline=offline, game=game):
        if d.replace("_", "") == want:
            return f"data/tilesets/{kind}/{d}"
    raise SystemExit(f"no {kind} tileset dir for {gname}")


# ---------------------------------------------------------------- buildings

# A COMPLEX: maps that open together behind one marker, in the order they stack
# in the popup -- top of the list is the top of the stack, which here is north.
#
# Andreas, 2026-09-16: "i would actually like viridian forest to be a separate
# room, such that when you click the gate openings you see the gate houses and
# the forest in a popup stacked on top of each other."
#
# Curated, not derived. Viridian Forest is a MAP_TYPE_ROUTE with no world
# position, reached only through two gate houses that are each corridors on
# their own -- no rule over the map data says those three are one place, and a
# rule invented to say it would be a rule about this one case wearing a
# general-looking coat. The list is the honest form of the same knowledge.
#
# Keyed by GAME, because the map names are: a complex declared for FireRed must
# not silently match a same-named map on another cartridge.
COMPLEXES_BY_GAME: dict[str, dict[str, list[str]]] = {
    "firered-us": {
        "ViridianForest": [
            "Route2_ViridianForest_NorthEntrance",
            "ViridianForest",
            "Route2_ViridianForest_SouthEntrance",
        ],
    },
}
#: Set by ``main`` before the building helpers run; a game with none declared
#: behaves exactly as it did before complexes existed.
COMPLEXES: dict[str, list[str]] = {}
COMPLEX_OF: dict[str, str] = {}


def set_complexes(game_key: str) -> None:
    """Point the two module-level maps at one game's declared complexes."""
    global COMPLEXES, COMPLEX_OF
    COMPLEXES = COMPLEXES_BY_GAME.get(game_key, {})
    COMPLEX_OF = {m: b for b, members in COMPLEXES.items() for m in members}


def building_of(name: str) -> str:
    """'PewterCity_PokemonCenter_2F' -> 'PewterCity_PokemonCenter' (1F stays 1F's own).

    A member of a COMPLEX answers with the complex instead, which is what makes
    its gates and its forest one marker and one popup.
    """
    if name in COMPLEX_OF:
        return COMPLEX_OF[name]
    return FLOOR_RE.sub("", name)


def _corridor_by_graph(graph: Optional[dict], building_maps: set[str],
                       key_of: dict[str, str]) -> Optional[bool]:
    """True when closing this building cuts its own doorsteps off from each other.

    The doorstep is the OUTSIDE tile of each warp — on the walk graph that is a
    node outside the building with an edge into it. Returns None when the
    building has fewer than two of them (nothing to disconnect), is not on the
    graph at all, or — for a game with no decomp walk graph — when there is no
    graph to close. On those games this half of the corridor rule does not run
    and the caller says so.
    """
    if graph is None:
        return None
    keys = {key_of[m] for m in building_maps if m in key_of}
    if not keys:
        return None
    nodes = [tuple(n) for n in graph["nodes"]]
    inside = {i for i, (g, m, _x, _y) in enumerate(nodes) if f"{g}:{m}" in keys}
    if not inside:
        return None
    steps = {j for i in inside for j in graph["adj"][i] if j not in inside}
    steps |= {i for i, adj in enumerate(graph["adj"]) if i not in inside and any(j in inside for j in adj)}
    if len(steps) < 2:
        return None
    start = min(steps)
    seen = {start}
    queue = [start]
    while queue:
        n = queue.pop()
        for u in graph["adj"][n]:
            if u in inside or u in seen:
                continue
            seen.add(u)
            queue.append(u)
    return not steps <= seen


def door_index(map_json: dict[str, dict], names: dict[str, str], graph: Optional[dict],
               key_of: dict[str, str], floors: dict[str, list[str]],
               is_indoor: Callable[[str], bool]) -> dict[str, list[dict]]:
    """Outdoor map name -> the door tiles of the buildings it holds.

    A warp's destination is a building's door when the destination is indoor
    AND is not a transition room. ``names`` maps a MAP_ constant to a map name;
    ``is_indoor`` answers for a destination OUTSIDE the rendered set, which is
    the difference between the Route 22 gate (its other side is Route 23, a
    route, so it is a corridor) and a Pokemon Center's 2F (its other sides are
    the Link rooms, which are indoor, so the Center stays a building).
    """
    indoor = {n for n, mj in map_json.items() if mj.get("map_type") == MAP_TYPE_INDOOR}
    # each interior's ways OUT: 2+ distinct ones means it is a corridor. A
    # destination we do not render (Route 23 through the Route 22 gate) is an
    # exit too — not knowing where it goes does not make it a room.
    reach: dict[str, set[tuple[str, str]]] = {}
    for n in indoor:
        out = set()
        for w in map_json[n].get("warp_events") or []:
            const = str(w.get("dest_map"))
            dest = names.get(const)
            inside = dest in indoor if dest is not None else is_indoor(const)
            if not inside:
                out.add((const, str(w.get("dest_warp_id"))))
        reach[n] = out
    corridor = {}
    for n in indoor:
        b = building_of(n)
        if b in corridor:
            continue
        if b in COMPLEXES:
            # A gate house on its own is a corridor by both tests -- that is what
            # a gate is. The complex it belongs to is a place, and the corridor
            # rule has nothing to say about it.
            corridor[b] = False
            continue
        maps = set(floors.get(b, [n]))
        two_maps = len({d for f in maps for d, _w in reach.get(f, ())}) >= 2
        corridor[b] = two_maps or bool(_corridor_by_graph(graph, maps, key_of))

    doors: dict[str, list[dict]] = {}
    for n, mj in map_json.items():
        if n in indoor:
            continue
        seen: set = set()
        for w in mj.get("warp_events") or []:
            dest = names.get(str(w.get("dest_map")))
            if dest is None or dest not in indoor:
                continue
            b = building_of(dest)
            if corridor[b]:
                continue
            # A door from inside a complex to another of its own members is not
            # a door: Viridian Forest is a ROUTE, so it reads as outdoor here
            # and was handing itself two markers that reopened the popup it is
            # already in.
            if COMPLEX_OF.get(n) == b:
                continue
            # One marker per building, at its first door -- except a complex,
            # which gets one per WAY IN: Route 2 meets Viridian Forest at two
            # gates a long way apart, and a marker on only one of them leaves
            # the other opening looking like scenery. Per way in, not per warp:
            # a gate is two tiles wide and has a warp on each.
            mark = (b, dest) if b in COMPLEXES else b
            if mark in seen:
                continue
            seen.add(mark)
            doors.setdefault(n, []).append({"x": int(w["x"]), "y": int(w["y"]), "to": dest, "building": b})
    return doors


def exit_index(map_json: dict, names: dict[str, str], key_of: dict[str, str]) -> dict[str, list[dict]]:
    """Popup map name -> the tiles that LEAVE its building or cluster.

    The door index read the other way round. A run's map used to open a
    building in a modal; since 2026-09-16 the map itself walks into it, so the
    way back has to be a thing you can click (Andreas: "the whole map should
    change to that sub-map with an arrow to go back, or the ability to just
    press on the door to get back out").

    A warp to another floor of the same building, or to another member of the
    same cluster -- the forest to its gate houses -- is NOT an exit: it is a
    stair inside the place you are already in. So the forest has none, and the
    two gate houses carry the cluster's only ways out, which is also where you
    would walk to.

    A FireRed doorway is three tiles wide and carries a warp on each, so the
    tiles are grouped into touching runs and the marker goes on the middle of
    each -- one door, one marker, in the middle of the mat. Grouped, not merged
    pairwise: comparing each warp only against the ones already KEPT let the
    third tile of a 3-wide door start a second door of its own. Two genuinely
    separate doors onto the same route stay two.
    """
    out: dict[str, list[dict]] = {}
    for n, mj in map_json.items():
        if mj.get("map_type") != MAP_TYPE_INDOOR and n not in COMPLEX_OF:
            continue
        b = building_of(n)
        by_dest: dict[str, list[tuple[int, int]]] = {}
        for w in mj.get("warp_events") or []:
            dest = names.get(str(w.get("dest_map")))
            if dest is None or dest not in key_of or building_of(dest) == b:
                continue
            by_dest.setdefault(dest, []).append((int(w["x"]), int(w["y"])))
        doors: list[dict] = []
        for dest, tiles in sorted(by_dest.items()):
            todo = sorted(tiles)
            while todo:
                group = [todo.pop(0)]
                grew = True
                while grew:
                    grew = False
                    for t in list(todo):
                        if any(abs(t[0] - g[0]) <= 1 and abs(t[1] - g[1]) <= 1 for g in group):
                            group.append(t)
                            todo.remove(t)
                            grew = True
                group.sort()
                mid = group[len(group) // 2]
                doors.append({"x": mid[0], "y": mid[1], "to": dest})
        if doors:
            out[n] = doors
    return out



def open_edges(mj: dict, w: int, h: int, dims_of) -> list[dict]:
    """The edges a neighbouring map continues across, as spans in tiles.

    A map's border block is what the game repeats OUTSIDE its bounds — but only
    where there is nothing else. On an edge with a connection the game draws the
    neighbour, and drawing the border there instead paints a wall across a road.
    Andreas, 2026-09-20, looking at Oldale Town: "the problem is that the woods
    you create cover possible roads, so it looks like there is no road there ...
    where you can both go up and to the right."

    The span is exact where the neighbour's size is known: a connection carries
    an ``offset`` along the shared edge and the neighbour covers its own width
    (or height) from there. Where it is NOT known the WHOLE edge is called open,
    which is the conservative direction — an edge left bare says "we have not
    drawn what is here", and trees say "there is no way through".
    """
    out: list[dict] = []
    for c in mj.get("connections") or []:
        d = c.get("direction")
        if d not in ("up", "down", "left", "right"):
            continue                      # dive/emerge are not edges
        along = w if d in ("up", "down") else h
        nd = dims_of(c.get("map"))
        if nd is None:
            lo, hi = 0, along
        else:
            off = int(c.get("offset", 0))
            n = nd[0] if d in ("up", "down") else nd[1]
            lo, hi = max(0, off), min(along, off + n)
        if hi > lo:
            out.append({"side": d, "from": lo, "to": hi})
    return out


# ---------------------------------------------------------------- tilesets

def read_pal(raw: bytes) -> np.ndarray:
    """JASC-PAL -> (16, 3) uint8."""
    lines = raw.decode("ascii", "replace").split()
    if lines[0] != "JASC-PAL":
        raise SystemExit("not a JASC-PAL file")
    n = int(lines[2])
    vals = [int(v) for v in lines[3: 3 + n * 3]]
    pal = np.zeros((16, 3), dtype=np.uint8)
    for i in range(min(n, 16)):
        pal[i] = vals[i * 3: i * 3 + 3]
    return pal


def read_tiles(raw: bytes) -> np.ndarray:
    """tiles.png -> (n_tiles, 8, 8) uint8 of palette INDICES (not colours)."""
    img = Image.open(io.BytesIO(raw))
    if img.mode != "P":
        raise SystemExit(f"tiles.png is {img.mode}, expected an indexed image")
    a = np.array(img, dtype=np.uint8)
    h, w = a.shape
    if w % TILE_PX or h % TILE_PX:
        raise SystemExit(f"tiles.png is {w}x{h}, not a whole number of 8x8 tiles")
    cols, rows = w // TILE_PX, h // TILE_PX
    return (a.reshape(rows, TILE_PX, cols, TILE_PX)
             .transpose(0, 2, 1, 3)
             .reshape(rows * cols, TILE_PX, TILE_PX))


class Tileset:
    """One primary+secondary pair: the tiles, the palettes and the metatiles."""

    def __init__(self, primary: str, secondary: str, *, offline: bool, game: Game) -> None:
        pdir = tileset_dir("primary", primary, offline=offline, game=game)
        sdir = tileset_dir("secondary", secondary, offline=offline, game=game)
        self.game = game
        self.key = (primary, secondary)
        self.tiles_p = read_tiles(fetch(f"{pdir}/tiles.png", offline=offline, game=game))
        self.tiles_s = read_tiles(fetch(f"{sdir}/tiles.png", offline=offline, game=game))
        self.meta_p = np.frombuffer(fetch(f"{pdir}/metatiles.bin", offline=offline, game=game), dtype="<u2").reshape(-1, 8)
        self.meta_s = np.frombuffer(fetch(f"{sdir}/metatiles.bin", offline=offline, game=game), dtype="<u2").reshape(-1, 8)
        self.pals = np.zeros((game.pals_total, 16, 3), dtype=np.uint8)
        for i in range(game.pals_total):
            d = pdir if i < game.pals_in_primary else sdir
            self.pals[i] = read_pal(fetch(f"{d}/palettes/{i:02d}.pal", offline=offline, game=game))
        self._cache: dict[int, np.ndarray] = {}

    def backdrop(self) -> np.ndarray:
        return self.pals[0][0]

    def _entries(self, metatile: int) -> np.ndarray | None:
        if metatile < self.game.metatiles_in_primary:
            table, idx = self.meta_p, metatile
        else:
            table, idx = self.meta_s, metatile - self.game.metatiles_in_primary
        return table[idx] if idx < len(table) else None

    def _tile(self, tile: int) -> np.ndarray:
        if tile < self.game.tiles_in_primary:
            table, idx = self.tiles_p, tile
        else:
            table, idx = self.tiles_s, tile - self.game.tiles_in_primary
        return table[idx] if idx < len(table) else np.zeros((TILE_PX, TILE_PX), dtype=np.uint8)

    def metatile_rgb(self, metatile: int) -> np.ndarray:
        """(16, 16, 3) uint8 — both layers composited over the backdrop."""
        if metatile in self._cache:
            return self._cache[metatile]
        out = np.tile(self.backdrop(), (16, 16, 1)).astype(np.uint8)
        entries = self._entries(metatile)
        if entries is not None:
            for slot, v in enumerate(entries):
                v = int(v)
                tile, xflip, yflip, pal = v & 0x03FF, (v >> 10) & 1, (v >> 11) & 1, (v >> 12) & 0xF
                idx = self._tile(tile)
                if xflip:
                    idx = idx[:, ::-1]
                if yflip:
                    idx = idx[::-1, :]
                ox = (slot % 2) * TILE_PX
                oy = ((slot % 4) // 2) * TILE_PX
                colours = self.pals[min(pal, self.game.pals_total - 1)][idx]
                mask = idx != 0                      # index 0 shows what is under it
                win = out[oy: oy + TILE_PX, ox: ox + TILE_PX]
                win[mask] = colours[mask]
        self._cache[metatile] = out
        return out


# ---------------------------------------------------------------- rendering

FLAT_SHARE = 0.9   # how much of a strip must be one colour to call it filler


def _flat(strip: np.ndarray) -> bool:
    px = strip.reshape(-1, 3)
    _, counts = np.unique(px, axis=0, return_counts=True)
    return bool(counts.max() / len(px) >= FLAT_SHARE)


def empty_edges(img: Image.Image, walkable: set[tuple[int, int]]) -> dict[str, int]:
    """Edge tile rows / columns that are one flat colour and unwalkable."""
    a = np.asarray(img)
    rows, cols = a.shape[0] // 16, a.shape[1] // 16
    used_rows = {ty for _tx, ty in walkable}
    used_cols = {tx for tx, _ty in walkable}
    row = lambda y: a[y * 16: (y + 1) * 16]          # noqa: E731
    col = lambda x: a[:, x * 16: (x + 1) * 16]       # noqa: E731

    def count(order, used, strip):
        n = 0
        for i in order:
            if i in used or not _flat(strip(i)):
                break
            n += 1
        return n

    top = count(range(rows - 1), used_rows, row)
    bottom = count(range(rows - 1, top, -1), used_rows, row)
    left = count(range(cols - 1), used_cols, col)
    right = count(range(cols - 1, left, -1), used_cols, col)
    return {"top": top, "left": left, "bottom": bottom, "right": right}


def render_map(grid: bytes, width: int, height: int, ts: Tileset) -> Image.Image:
    cells = np.frombuffer(grid, dtype="<u2")[: width * height].reshape(height, width)
    out = np.zeros((height * 16, width * 16, 3), dtype=np.uint8)
    for y in range(height):
        for x in range(width):
            out[y * 16: y * 16 + 16, x * 16: x * 16 + 16] = ts.metatile_rgb(int(cells[y, x]) & 0x03FF)
    return Image.fromarray(out, "RGB")


# ---------------------------------------------------------------- the map set

def observed_manifest(game: Game, groups: dict) -> tuple[dict[str, str], dict[str, list[int]]]:
    """``{"0:9": "LittlerootTown"}`` and its observed bounds, from a run scan.

    ``per_map[].map`` is a ``[group, num]`` pair — the same numbers the referee
    reads out of the save block — and ``map_groups.json`` turns the pair into a
    name: group N is ``group_order[N]``, map M is that group's Mth entry.
    """
    obs = json.loads(game.observed.read_text())
    if obs.get("game") != game.key:
        raise SystemExit(f"{game.observed} is for {obs.get('game')!r}, not {game.key!r}")
    order = groups["group_order"]
    sel: dict[str, str] = {}
    bounds: dict[str, list[int]] = {}
    for p in obs["per_map"]:
        g, n = int(p["map"][0]), int(p["map"][1])
        try:
            name = groups[order[g]][n]
        except (IndexError, KeyError) as exc:
            raise SystemExit(f"observed map {g}:{n} is not in {game.repo}'s map_groups.json") from exc
        sel[f"{g}:{n}"] = name
        bounds[f"{g}:{n}"] = [int(v) for v in p["bounds"]]
    return sel, bounds


def world_frame(game: Game, sel: dict[str, str], map_json: dict[str, dict],
                const_to_name: dict[str, str], dims: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Map name -> tile offset of its top-left corner in ONE shared frame.

    The same BFS ``build_walkgraph.py`` runs for FireRed (its "world frame"
    block), over pret's own ``connections``: "up" with offset o puts the
    neighbour's column x at our column x + o. The origin town sits at (0, 0)
    and everything else is measured from it, negatives included.

    Restricted to the rendered set: a neighbour we do not draw cannot be placed
    and cannot be walked THROUGH either, so a rendered map only lands in the
    frame if a chain of rendered maps reaches it. Anything left out simply has
    no ``world`` and the viewer insets it, which is the existing behaviour for
    FireRed's Viridian Forest.
    """
    origin = next((n for n in sel.values() if n == game.world_origin), None)
    if origin is None:
        return {}
    world: dict[str, tuple[int, int]] = {origin: (0, 0)}
    conflicts: list[str] = []
    rendered = set(sel.values())
    q: deque[str] = deque([origin])
    while q:
        name = q.popleft()
        wx, wy = world[name]
        mw, mh = dims[name]
        for c in map_json[name].get("connections") or []:
            dest = const_to_name.get(str(c.get("map")))
            if dest is None or dest not in rendered:
                continue
            dw, dh = dims[dest]
            off, d = int(c["offset"]), c["direction"]
            if d == "up":
                w = (wx + off, wy - dh)
            elif d == "down":
                w = (wx + off, wy + mh)
            elif d == "left":
                w = (wx - dw, wy + off)
            elif d == "right":
                w = (wx + mw, wy + off)
            else:
                continue
            if dest in world:
                if world[dest] != w:      # a cycle of connections must close
                    conflicts.append(f"{name} -> {dest}: {w} vs {world[dest]}")
                continue
            world[dest] = w
            q.append(dest)
    if conflicts:
        raise SystemExit("world frame does not close: " + "; ".join(conflicts))
    return world


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="firered-us", choices=sorted(GAMES))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", action="append", help="render just these map names")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    game = GAMES[args.game]
    set_complexes(game.key)
    out_dir = args.out or game.out
    ref = game.sha
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise SystemExit(f"{game.key}: sha {ref!r} is not a full 40-hex commit")
    graph = json.loads(game.graph.read_text()) if game.graph else None
    layouts = {l["id"]: l for l in json.loads(fetch("data/layouts/layouts.json", offline=args.offline, game=game))["layouts"] if "id" in l}
    groups = json.loads(fetch("data/maps/map_groups.json", offline=args.offline, game=game))

    # which maps: the decomp walk graph where there is one, the maps our runs
    # actually entered where there is not.
    bounds: dict[str, list[int]] = {}
    if graph is not None:
        sel = {k: m["name"] for k, m in graph["maps"].items()}
    else:
        sel, bounds = observed_manifest(game, groups)

    out_dir.mkdir(parents=True, exist_ok=True)
    tilesets: dict[tuple[str, str], Tileset] = {}
    index: dict[str, dict] = {}

    # every map's own json first: the door index is a property of the whole set
    map_json = {name: json.loads(fetch(f"data/maps/{name}/map.json", offline=args.offline, game=game))
                for name in sel.values()}
    key_of = {name: k for k, name in sel.items()}
    floors: dict[str, list[str]] = {}
    for name in sorted(map_json):
        if map_json[name].get("map_type") == MAP_TYPE_INDOOR and name not in COMPLEX_OF:
            floors.setdefault(building_of(name), []).append(name)
    # A complex keeps the order it was DECLARED in -- north to south, the way you
    # walk it -- where a building's floors are sorted (1F, 2F). Sorting a complex
    # would stack the forest between its gates by alphabet, which is luck.
    for b, members in COMPLEXES.items():
        present = [m for m in members if m in map_json]
        if present:
            floors[b] = present
    # map_type for a destination outside the rendered set, fetched once and
    # cached like everything else; unknown counts as NOT indoor, which is the
    # conservative read (an unknown exit makes a room a corridor).
    all_names = {camel_to_const(m): m for g in groups["group_order"] for m in groups[g]}

    def dims_of(const: str) -> Optional[tuple[int, int]]:
        """A neighbour's size in tiles, for the span its connection covers.

        Any map in the game, not only the rendered ones: a town's road out
        usually leads somewhere we have not drawn, and that is exactly the edge
        whose border must not be painted.
        """
        name = all_names.get(const)
        if name is None:
            return None
        try:
            nj = json.loads(fetch(f"data/maps/{name}/map.json", offline=args.offline, game=game))
            nl = layouts[nj["layout"]]
        except (SystemExit, KeyError):
            return None
        return int(nl["width"]), int(nl["height"])

    def is_indoor(const: str) -> bool:
        name = all_names.get(const)
        if name is None:
            return False
        try:
            mj = json.loads(fetch(f"data/maps/{name}/map.json", offline=args.offline, game=game))
        except SystemExit:
            return False
        return mj.get("map_type") == MAP_TYPE_INDOOR

    const_names = {camel_to_const(n): n for n in map_json}
    doors = door_index(map_json, const_names, graph, key_of, floors, is_indoor)
    exits = exit_index(map_json, const_names, key_of)

    dims = {name: (int(layouts[mj["layout"]]["width"]), int(layouts[mj["layout"]]["height"]))
            for name, mj in map_json.items()}
    # the world frame: the graph's for FireRed, pret's own connections otherwise
    world = {} if graph is not None else world_frame(game, sel, map_json, all_names, dims)

    for key, name in sorted(sel.items()):
        if args.only and name not in args.only:
            continue
        mj = map_json[name]
        layout = layouts[mj["layout"]]
        tkey = (layout["primary_tileset"], layout["secondary_tileset"])
        if tkey not in tilesets:
            tilesets[tkey] = Tileset(*tkey, offline=args.offline, game=game)
        grid = fetch(layout["blockdata_filepath"], offline=args.offline, game=game)
        w, h = int(layout["width"]), int(layout["height"])
        if graph is not None:
            m = graph["maps"][key]
            if (w, h) != (m["width"], m["height"]):
                raise SystemExit(f"{name}: layout is {w}x{h} but the walk graph says {m['width']}x{m['height']}")
        elif key in bounds:
            # No walk graph to cross-check against, so check the render against
            # the RUNS: observed ground can only ever lie inside the real map.
            # An overrun means we resolved the wrong layout — same bite as the
            # graph check, from the only other source that knows the answer.
            x0, y0, x1, y1 = bounds[key]
            if not (0 <= x0 <= x1 < w and 0 <= y0 <= y1 < h):
                raise SystemExit(f"{name}: layout is {w}x{h} but runs observed ground out to "
                                 f"({x1},{y1}) from ({x0},{y0}) — the layout is wrong")
        img = render_map(grid, w, h, tilesets[tkey])
        fname = f"{key.replace(':', '-')}.png"
        img.save(out_dir / fname, optimize=True)

        # The BORDER: the block the game repeats outside a map's own bounds.
        # Without it a town ends at a hard edge with nothing beyond, which is
        # what Andreas was looking at on 2026-09-20 — "there still is too much
        # missing map which would help indicate borders". It is 2x2 metatiles
        # (`border_width`/`border_height`), and it is emitted ONCE per map and
        # tiled by the viewer rather than baked into a fat margin on every PNG:
        # a 6-tile bleed on Littleroot would be 2.6x the pixels, for a picture
        # the browser can repeat from 32x32 bytes.
        bw, bh = int(layout.get("border_width", 2)), int(layout.get("border_height", 2))
        bpath = layout.get("border_filepath")
        if bpath and bw > 0 and bh > 0:
            try:
                bgrid = fetch(bpath, offline=args.offline, game=game)
                bimg = render_map(bgrid, bw, bh, tilesets[tkey])
                bname = f"{key.replace(':', '-')}-border.png"
                bimg.save(out_dir / bname, optimize=True)
                border = {"file": bname, "w": bw, "h": bh,
                          "bytes": (out_dir / bname).stat().st_size}
            except SystemExit:
                raise
            except Exception:
                # A map with no usable border simply has none; the viewer
                # already draws nothing there rather than guessing a fill.
                border = None
        else:
            border = None
        entry = {"name": name, "width": w, "height": h, "file": fname,
                 "bytes": (out_dir / fname).stat().st_size, "type": mj.get("map_type"),
                 # NORMALISED, because MAP_TYPE_INDOOR is a pret gen-3 constant
                 # and the viewer must not branch on a string only this
                 # generation emits. `type` stays as raw provenance.
                 "indoor": mj.get("map_type") == MAP_TYPE_INDOOR}
        if border:
            entry["border"] = border
            # ...and the edges it must NOT be drawn across.
            opens = open_edges(mj, w, h, dims_of)
            if opens:
                entry["open"] = opens
        if graph is not None:
            # `trim` needs the set of tiles something can STAND on. Only a
            # decomp walk graph has it; the observed graph holds the tiles
            # somebody did stand on, which is a subset, and trimming to a subset
            # cuts real floor off the picture. No trim at all is better.
            walkable = {(n[2], n[3]) for n in graph["nodes"] if f"{n[0]}:{n[1]}" == key}
            trim = empty_edges(img, walkable)
            if any(trim.values()):
                entry["trim"] = {k: v for k, v in trim.items() if v}
        place = graph["maps"][key].get("world") if graph is not None else list(world.get(name, ())) or None
        if isinstance(place, list):
            entry["world"] = place
            entry["frame"] = game.frame
        if mj.get("map_type") == MAP_TYPE_INDOOR or name in COMPLEX_OF:
            b = building_of(name)
            entry["building"] = b
            # the other floors of this building, as map keys, 1F first; a floor
            # outside the rendered set is dropped rather than left dangling. A
            # complex is already in its own order and is not re-sorted.
            order = floors.get(b, []) if b in COMPLEXES else sorted(floors.get(b, []))
            entry["floors"] = [key_of[f] for f in order if f in key_of]
            # The one flag the world frame reads: this map is drawn in a popup,
            # not on the world. It used to be spelled `type == MAP_TYPE_INDOOR`,
            # which was true until Viridian Forest -- a ROUTE -- moved indoors.
            entry["popup"] = True
            if b in COMPLEXES:
                # A complex is laid out GEOGRAPHICALLY -- its members stack in
                # the order declared, north at the top. A building's floors are
                # laid out side by side, 1F first, because stacking 1F above 2F
                # would put the building upside down.
                entry["complex"] = True
        if name in doors:
            entry["doors"] = [{**d, "to": key_of[d["to"]]} for d in doors[name] if d["to"] in key_of]
        if name in exits:
            entry["exits"] = [{**d, "to": key_of[d["to"]]} for d in exits[name] if d["to"] in key_of]
        index[key] = entry
        print(f"{name:42s} {w:3d}x{h:<3d} {entry['bytes'] // 1024:4d} KB")

    if not args.only:
        # Schema 2 (2026-09-20): the atlas names its own game, because there is
        # now one per game and a route picks its atlas by that key.
        meta = {
            "schema": 2,
            "game": game.key,
            "key_shape": "pair",
            "tile_px": 16,
            "camera": "topdown",
            "source": {"kind": "decomp", "repo": game.repo, "sha": ref},
            # A game with no decomp walk graph says so with nulls rather than
            # borrowing another game's version — the atlas/graph agreement test
            # is what these two fields exist for, and there is nothing to agree
            # with. `map_set` records what the map list came from instead.
            "walkgraph": {"version": graph.get("version"), "source": graph.get("source")} if graph
                         else {"version": None, "source": None},
            "bytes": sum(e["bytes"] for e in index.values()),
            "maps": index,
        }
        if graph is None:
            meta["map_set"] = {
                "kind": "observed",
                "source": str(game.observed.relative_to(REPO_ROOT)),
                "note": "maps our runs entered; trim omitted (no walkable-tile set)",
            }
            meta = {k: meta[k] for k in ("schema", "game", "key_shape", "tile_px", "camera",
                                         "source", "walkgraph", "map_set", "bytes", "maps")}
        (out_dir / "index.json").write_text(json.dumps(meta, indent=1) + "\n")
    total = sum(e["bytes"] for e in index.values())
    print(f"{len(index)} maps, {total // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
