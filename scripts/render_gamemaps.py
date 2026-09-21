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
import os
import re
import sys
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, replace as dc_replace
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

#: gen 2's four palette sets, in the order `data/maps/environment_colors.asm`
#: lists them — the index IS `wTimeOfDayPal` (ram_constants.asm: MORN_F 0,
#: DAY_F 1, NITE_F 2, DARKNESS_F 3).
GEN2_DAYTIMES = ("morn", "day", "nite", "dark")


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
    #: 3 for a gen-3 decomp (2x2 metatiles, JSON map data), 2 for pokecrystal
    #: (4x4 blocks, assembly map data). It picks the SOURCE, not the emitter:
    #: everything after "here is a map and its pixels" is shared.
    gen: int = 3
    #: gen 3 only, from include/fieldmap.h at the pinned sha
    tiles_in_primary: int = 0
    metatiles_in_primary: int = 0
    pals_in_primary: int = 0
    pals_total: int = 0
    #: gen 2 only. A GBC recolours the whole world by the clock, so a render is
    #: of ONE time of day and has to say which: "morn", "day", "nite", "dark".
    time_of_day: str = "day"
    #: exactly one of these two says which maps to render
    graph: Optional[Path] = None      # a decomp walk graph (FireRed)
    observed: Optional[Path] = None   # the maps our runs entered (everyone else)
    #: world-frame origin, observed games only — the graph carries its own
    world_origin: Optional[str] = None

    def __post_init__(self) -> None:
        if self.gen == 3 and not all((self.tiles_in_primary, self.metatiles_in_primary,
                                      self.pals_in_primary, self.pals_total)):
            raise SystemExit(f"{self.key}: a gen-3 game must carry its fieldmap.h constants")
        if self.gen == 2 and self.time_of_day not in GEN2_DAYTIMES:
            raise SystemExit(f"{self.key}: time_of_day {self.time_of_day!r} is not one of {GEN2_DAYTIMES}")

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
    "crystal-us": Game(
        key="crystal-us",
        repo="pret/pokecrystal",
        sha="7a7881d0d62e0ddbd82dcf10e7116807487ac651",
        frame="johto",
        cache=CACHE_ROOT / "pret-cache-crystal",
        gen=2,
        # A GBC map is a different picture at every time of day — `nite` gray is
        # RGB 15,14,24 where `day` gray is 27,31,27 — so the atlas has to pick
        # one, and the right one is whatever the runs it is shown beside were
        # played at.
        #
        # This said `nite` until 2026-09-21, chosen when the only Crystal runs
        # were the two played on 2026-09-19/20 in the dark. It outlived its
        # reason: the 100-turn config-6.0 runs are daytime, and a purple Johto
        # under a green screenshot is the first thing anyone notices.
        #
        # Measured rather than eyeballed. For each candidate, the share of a
        # run's own screenshot pixels whose colour the render contains, over
        # frames from the back half of the run, textbox rows dropped:
        #
        #             morn    day   nite   dark
        #   09-20_00-28-46     0.0    0.0   97.6   14.0   (a night run)
        #   09-20_19-14-38    64.7   79.0    1.9    1.9
        #   09-20_21-18-34    70.3   74.4    2.6    2.6
        #
        # The night run is the control that proves the measure discriminates —
        # it picks `nite` at 97.6 and rejects `day` at 0.0, so a metric that
        # simply liked bright colours would have failed it. `morn` and `day`
        # differ in 17,440 of 276,480 pixels on ROUTE_29, so the two are a real
        # choice and not a tie; `day` wins on both daytime runs.
        #
        #   ./venv/bin/python scripts/render_gamemaps.py --game crystal-us \
        #       --time-of-day nite --out /tmp/nite      # to see the other one
        time_of_day="day",
        observed=REPO_ROOT / "artifacts" / "game-map-render" / "observed" / "crystal-us-observed.json",
        world_origin="NEW_BARK_TOWN",
    ),
}


# ---------------------------------------------------------------- fetching

def cache_write(path: Path, data: bytes) -> None:
    """Write one cached file so a reader never sees a half-written one.

    Two worktrees share ``local/pret-cache*`` (scripts/build_walkgraph.py fetches
    the same tree into the same directory), and a plain ``write_bytes`` is not
    atomic: a second process reading `tiles.png` while the first is a third of
    the way through it gets a truncated PNG and renders garbage that still looks
    like a map. tmp-then-``os.replace`` is atomic on the same filesystem, and the
    tmp name carries the pid so two writers cannot collide on it either.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


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
    cache_write(local, data)
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
    cache_write(cache, json.dumps(names).encode())
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
    """JASC-PAL -> (16, 3) uint8. Gen 3 only; gen 2 goes through gen2_pal.

    The bytes are taken as written and NOT re-quantised, which makes a gen-3
    render disagree with a SkyEmu frame in every single byte. That is correct
    and deliberate; it is written down because it looks exactly like a bug.

    A GBA colour is 5 bits per channel and there are two ways to widen it to 8.
    pret's .pal files carry the full-range spelling, `(v<<3)|(v>>2)`, so that
    pure white is 255 and not 248. SkyEmu writes frames with `v<<3`. Both are
    the same 5-bit value; they differ by 0-7 per channel, which is invisible
    and, where it differs at all, ours is the truer colour.

    So it stays. The consequence is that a BYTE-EXACT comparison against an
    emulator frame is meaningless for gen 3 — `verify_map_alignment.py` reduces
    both sides to the console's own 32 levels before comparing, and still
    prints the raw 8-bit agreement (0.0000 for FireRed and Emerald, 0.9896 for
    Crystal) so the asymmetry stays visible rather than being mistaken for
    alignment error. Crystal agrees byte for byte because `gen2_pal` has to
    match SkyEmu exactly for a different reason, measured there.

    Found 2026-09-21, when Emerald first scored 0.9854 on a colour-mapped
    comparison whose raw agreement was 0.0012 — the whole score was coming out
    of a lookup fitted on its own data.
    """
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


# ---------------------------------------------------------------- gen 2

"""pokecrystal, whose map data is assembly and whose blocks are 4x4, not 2x2.

The one real difference from gen 3 is the INDIRECTION. A gen-3 map is a grid of
16x16 metatiles, which is also the unit the player walks in. A gen-2 map is a
grid of 32x32 BLOCKS, and the player walks in HALF a block — so a map that
`constants/map_constants.asm` calls 30x9 is 60x18 tiles to the referee, and the
observed bounds (Route 29 out to x=59) are what proves the factor of two is
real rather than assumed. Everything this module emits is therefore in TILES,
the same unit the gen-3 path and the walk graph already speak; only the render
loop thinks in blocks.

The chain, from `maps/Route29.blk` to pixels:

  .blk            one byte per block, row-major. A byte of 0 is NOT block 0 —
                  `LoadMetatiles` (home/map.asm) substitutes the map's own
                  border block for it, so the game can never draw block 0
                  inside a map.
  _metatiles.bin  16 bytes per block: a 4x4 of 8x8 tile ids, row-major.
  <tileset>.png   4-shade greyscale, 128 px wide, 8x8 tiles row-major, 192 of
                  them. The tile id indexes it directly.
  _palette_map    one nybble per tile (`gfx/tileset_palette_maps.asm`): low
                  nybble the even tile, high the odd — which is `srl a` +
                  carry in `_LoadOverworldAttrmapPals`. Bits 0-2 are the BG
                  palette, bit 3 the VRAM bank, which is a loading detail and
                  not a picture.

COLOUR IS NOT IN THE TILES. A gen-3 tile carries its own palette; a gen-2 tile
carries four shades and is coloured at runtime by where and WHEN you are:

  environment  TOWN/ROUTE take the outdoor palette row, INDOOR/GATE the indoor
               one, CAVE/DUNGEON the dungeon one
               (`data/maps/environment_colors.asm`).
  time of day  morn / day / nite / dark, each a different row of
               `gfx/tilesets/bg_tiles.pal`. An indoor map declares
               PALETTE_DAY and is the same at midnight; an outdoor one is
               PALETTE_AUTO and is not. `Game.time_of_day` says which one this
               render is OF, because there is no such thing as the picture.
  map group    the roof palette AND the roof tiles are replaced per group
               (`data/maps/roofs.asm`), so New Bark's roofs are not Violet's.
  tileset      six tilesets carry a palette file instead of the time-of-day set
               (`LoadSpecialMapPalette`). None of them is in the observed
               Crystal set, so that branch is written from the source and NOT
               exercised by the verification below — it is marked as such.

A GBC colour is 5 bits per channel and SkyEmu writes it out as `v << 3`
(measured, not assumed: a frame's commonest colour is 120,112,192 and nite grey
is RGB 15,14,24 — 15*8, 14*8, 24*8. The `(v<<3)|(v>>2)` spelling would have
given 123,113,198 and every pixel would have missed).
"""

GEN2_BLOCK_TILES = 4                 # 4x4 8x8 tiles per block
GEN2_BLOCK_PX = GEN2_BLOCK_TILES * TILE_PX
GEN2_TILES_PER_BLOCK = 2             # ...which is 2x2 of the 16 px tiles we count in
GEN2_ROOF_TILE = 0x0A                # LoadMapGroupRoof: `ld de, vTiles2 tile $0a`
GEN2_ROOF_LENGTH = 9                 # constants/tileset_constants.asm
#: `LoadMetatiles` computes the block's offset in 8 bits and wraps: block 128
#: draws block 0. Faithful, and documented in pokecrystal's own
#: docs/bugs_and_glitches.md. No tileset in the observed set has more than 128
#: blocks, so it changes nothing here — it is in so that one that does cannot
#: silently render something the game never shows.
GEN2_BLOCKS_PER_TILESET = 128
GEN2_DIRECTIONS = {"north": "up", "south": "down", "west": "left", "east": "right"}
#: gen-2 environments that are a room you open in a popup rather than a place on
#: the world. GATE is included for the same reason FireRed's gate houses are
#: MAP_TYPE_INDOOR: the corridor rule, not this flag, is what demotes them.
GEN2_INDOOR_ENVIRONMENTS = {"INDOOR", "GATE"}
GEN2_OUTDOOR_ENVIRONMENTS = {"TOWN", "ROUTE"}   # the two that get a roof palette
#: `LoadSpecialMapPalette` (engine/tilesets/tileset_palettes.asm). UNEXERCISED:
#: no map in the observed Crystal set uses one of these tilesets, so nothing
#: below checks this table. Kept because the alternative is a silently wrong
#: colour on the first map that does.
GEN2_SPECIAL_PALETTES = {
    "TILESET_POKECOM_CENTER": "gfx/tilesets/pokecom_center.pal",
    "TILESET_BATTLE_TOWER_INSIDE": "gfx/tilesets/battle_tower_inside.pal",
    "TILESET_HOUSE": "gfx/tilesets/house.pal",
    "TILESET_RADIO_TOWER": "gfx/tilesets/radio_tower.pal",
    "TILESET_MANSION": "gfx/tilesets/mansion_1.pal",
    # TILESET_ICE_PATH takes ice_path.pal UNLESS the environment is INDOOR (the
    # Hall of Fame shares the tileset), which is why it is handled in the caller
    # rather than here.
}


def _gen2_int(tok: str) -> int:
    """`$05`, `-5`, `10` -> int."""
    tok = tok.strip()
    if tok.startswith("$"):
        return int(tok[1:], 16)
    return int(tok, 10)


def _gen2_body(raw: bytes) -> list[str]:
    """An rgbasm file's real lines: comments gone, MACRO bodies gone.

    The macro bodies matter. `data/maps/attributes.asm` opens by DEFINING
    `connection`, and its definition contains the line `connection \\1, \\2, \\3,
    (\\4) - (\\5)` — read as data that is a connection from every map to every
    other one.
    """
    out: list[str] = []
    depth = 0
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.split(";")[0].strip()
        if not line:
            continue
        if line.startswith("MACRO ") or line.endswith(": MACRO"):
            depth += 1
            continue
        if line == "ENDM":
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        out.append(line)
    return out


def gen2_map_groups(raw: bytes) -> tuple[dict, dict[str, tuple[int, int]]]:
    """`constants/map_constants.asm` in the shape `observed_manifest` reads.

    Gen-2 groups and map numbers are BOTH 1-based (the referee reads them
    straight out of wMapGroup/wMapNumber), so index 0 of each list is a spacer
    rather than a map — that is what makes `groups[order[g]][n]` land on the
    right map without a second indexing convention.
    """
    order = ["MAPGROUP_NONE"]
    groups: dict[str, list[str]] = {"MAPGROUP_NONE": [""]}
    sizes: dict[str, tuple[int, int]] = {}
    cur: Optional[str] = None
    for line in _gen2_body(raw):
        if line.startswith("newgroup "):
            cur = line.split(None, 1)[1].strip()
            order.append(cur)
            groups[cur] = [""]
        elif line.startswith("map_const ") and cur:
            parts = [p.strip() for p in line[len("map_const "):].split(",")]
            groups[cur].append(parts[0])
            # WIDTH, HEIGHT are in blocks; every caller here wants tiles.
            sizes[parts[0]] = (int(parts[1]) * GEN2_TILES_PER_BLOCK,
                               int(parts[2]) * GEN2_TILES_PER_BLOCK)
    groups["group_order"] = order
    return groups, sizes


def gen2_map_table(raw: bytes, order: list[str]) -> dict[str, dict]:
    """`data/maps/maps.asm`: map constant -> its CamelCase name, tileset, environment.

    The join is POSITIONAL — the Nth `map` line of the Nth group table is the
    Nth `map_const` — because maps.asm names a map `Route29` where the constant
    is `ROUTE_29` and no rule turns one into the other (`PlayersHouse1F` vs
    `PLAYERS_HOUSE_1F` is where a rule would break). `MapGroupPointers` gives
    the table order so it is pret's own ordering and not the file's.
    """
    lines = _gen2_body(raw)
    pointers: list[str] = []
    sections: dict[str, list[list[str]]] = {}
    cur: Optional[str] = None
    for line in lines:
        if line.startswith("MapGroupPointers"):
            cur = None
            continue
        if line.startswith("dw MapGroup_") and cur is None:
            pointers.append(line.split()[1])
            continue
        if line.startswith("MapGroup_") and line.endswith(":"):
            cur = line.rstrip(":")
            sections[cur] = []
            continue
        if line.startswith("map ") and cur:
            sections[cur].append([p.strip() for p in line[len("map "):].split(",")])
    if len(pointers) != len(order) - 1:
        raise SystemExit(f"maps.asm lists {len(pointers)} groups, map_constants.asm {len(order) - 1}")
    return {"pointers": pointers, "sections": sections}


def gen2_attributes(raw: bytes) -> dict[str, dict]:
    """`data/maps/attributes.asm`: map constant -> border block and connections.

    A connection's offset is in BLOCKS and means the same thing gen 3's does —
    the neighbour's own origin sits at ours plus the offset along the shared
    edge — so doubling it is the whole translation.
    """
    out: dict[str, dict] = {}
    cur: Optional[str] = None
    for line in _gen2_body(raw):
        if line.startswith("map_attributes "):
            parts = [p.strip() for p in line[len("map_attributes "):].split(",")]
            cur = parts[1]
            out[cur] = {"camel": parts[0], "border_block": _gen2_int(parts[2]), "connections": []}
        elif line.startswith("connection ") and cur:
            parts = [p.strip() for p in line[len("connection "):].split(",")]
            if len(parts) < 4 or parts[0] not in GEN2_DIRECTIONS:
                continue
            out[cur]["connections"].append({
                "direction": GEN2_DIRECTIONS[parts[0]],
                "map": parts[2],
                "offset": _gen2_int(parts[3]) * GEN2_TILES_PER_BLOCK,
            })
    return out


def gen2_warps(raw: bytes) -> list[dict]:
    """A map's `warp_event x, y, MAP_CONST, warp_id` lines, in TILE coordinates."""
    out: list[dict] = []
    for line in _gen2_body(raw):
        if not line.startswith("warp_event"):
            continue
        parts = [p.strip() for p in line[len("warp_event"):].split(",")]
        if len(parts) < 4:
            continue
        try:
            x, y = int(parts[0]), int(parts[1])
        except ValueError:
            continue                      # a warp placed by a constant: not ours to guess
        out.append({"x": x, "y": y, "dest_map": parts[2], "dest_warp_id": parts[3]})
    return out


def gen2_pal_names(raw: bytes) -> dict[str, int]:
    """`PAL_BG_GRAY` -> 0 ... `PAL_BG_TEXT` -> 7, from tileset_constants.asm."""
    names: dict[str, int] = {}
    for line in _gen2_body(raw):
        if line.startswith("const PAL_BG_"):
            names[line.split()[1][len("PAL_BG_"):]] = len(names)
    return names


def gen2_palette_map(raw: bytes, pal_names: dict[str, int]) -> np.ndarray:
    """One palette index per tile of a tileset, from `*_palette_map.asm`."""
    out: list[int] = []
    for line in _gen2_body(raw):
        if not line.startswith("tilepal"):
            continue
        parts = [p.strip() for p in line[len("tilepal"):].split(",")]
        for name in parts[1:]:
            if name not in pal_names:
                raise SystemExit(f"palette map names {name!r}, which is not a PAL_BG_ constant")
            out.append(pal_names[name])
    return np.array(out, dtype=np.uint8)


def gen2_colours(raw: bytes) -> np.ndarray:
    """Every `RGB r,g,b` in a .pal file, as (n, 3) uint8 at the console's output.

    5 bits per channel, written out as ``v << 3``. Measured against a real
    SkyEmu frame rather than assumed — see the module note.
    """
    vals: list[list[int]] = []
    for line in _gen2_body(raw):
        if not line.startswith("RGB"):
            continue
        nums = [int(v) for v in re.findall(r"\d+", line[3:])]
        for i in range(0, len(nums) - 2, 3):
            vals.append(nums[i:i + 3])
    return (np.array(vals, dtype=np.uint8) << 3)


def gen2_environment_colours(raw: bytes) -> dict[str, list[list[int]]]:
    """environment -> four rows of eight `bg_tiles.pal` indices (morn/day/nite/dark).

    Read WITH the comments: the pointer table's only statement of which
    environment each block belongs to is the comment beside it.
    """
    table: dict[str, str] = {}
    blocks: dict[str, list[list[int]]] = {}
    cur: Optional[str] = None
    for line in raw.decode("utf-8", "replace").splitlines():
        code, _, comment = line.partition(";")
        code, comment = code.strip(), comment.strip()
        if code.startswith("dw .") and comment and comment != "unused":
            table[comment.split()[0]] = code[len("dw ."):].strip()
        elif code.startswith(".") and code.endswith(":"):
            cur = code[1:-1]
            blocks[cur] = []
        elif code.startswith("db ") and cur:
            blocks[cur].append([_gen2_int(v) for v in code[len("db "):].split(",")])
    return {env: blocks[label] for env, label in table.items() if label in blocks}


def gen2_map_group_roofs(raw: bytes) -> list[Optional[str]]:
    """`data/maps/roofs.asm`: one roof name (or None) per map group, 0-based."""
    out: list[Optional[str]] = []
    cur = None
    for line in _gen2_body(raw):
        if line.startswith("MapGroupRoofs"):
            cur = "table"
            continue
        if cur == "table" and line.startswith("db "):
            v = line[len("db "):].strip()
            out.append(None if v.startswith("-") else v[len("ROOF_"):].lower())
            continue
        if cur == "table" and line.startswith("assert_table_length"):
            break
    return out


def gen2_block_files(raw: bytes) -> dict[str, str]:
    """map name -> the .blk it is drawn from, out of `data/maps/blocks.asm`.

    NOT `maps/<Name>.blk`. Sixteen Pokemon Centers are one label stack over one
    INCBIN — `CherrygrovePokecenter1F_Blocks:` has no file of its own and the
    guessed path 404s, which is how this was found. A run of labels shares the
    INCBIN that follows it.
    """
    out: dict[str, str] = {}
    pending: list[str] = []
    for line in _gen2_body(raw):
        if line.endswith("_Blocks:"):
            pending.append(line[: -len("_Blocks:")])
        elif line.startswith("INCBIN "):
            path = line[len("INCBIN "):].strip().strip('"')
            for name in pending:
                out[name] = path
            pending = []
        elif line.startswith("SECTION"):
            pending = []
    return out


def gen2_read_tiles(raw: bytes) -> np.ndarray:
    """A pokecrystal tileset PNG -> (n, 8, 8) uint8 of 2bpp colour indices."""
    img = Image.open(io.BytesIO(raw)).convert("L")
    a = np.asarray(img, dtype=np.int16)
    extra = set(np.unique(a).tolist()) - {0, 85, 170, 255}
    if extra:
        raise SystemExit(f"a tileset PNG holds shades {sorted(extra)}, which is not 2bpp")
    idx = ((255 - a) // 85).astype(np.uint8)     # white -> 0 ... black -> 3
    h, w = idx.shape
    if w % TILE_PX or h % TILE_PX:
        raise SystemExit(f"a tileset PNG is {w}x{h}, not a whole number of 8x8 tiles")
    cols, rows = w // TILE_PX, h // TILE_PX
    return (idx.reshape(rows, TILE_PX, cols, TILE_PX)
               .transpose(0, 2, 1, 3)
               .reshape(rows * cols, TILE_PX, TILE_PX))


class Gen2Tileset:
    """One gen-2 tileset already coloured for one map's palette set."""

    def __init__(self, tiles: np.ndarray, blocks: np.ndarray, pal_of_tile: np.ndarray,
                 pals: np.ndarray) -> None:
        self.tiles = tiles
        self.blocks = blocks
        self.pal_of_tile = pal_of_tile
        self.pals = pals                       # (8, 4, 3) uint8
        self._cache: dict[int, np.ndarray] = {}

    def block_rgb(self, block: int) -> np.ndarray:
        """(32, 32, 3) uint8 — one block, all sixteen of its tiles."""
        block %= GEN2_BLOCKS_PER_TILESET
        if block in self._cache:
            return self._cache[block]
        out = np.zeros((GEN2_BLOCK_PX, GEN2_BLOCK_PX, 3), dtype=np.uint8)
        if block < len(self.blocks):
            for slot, tile in enumerate(self.blocks[block]):
                tile = int(tile)
                if tile >= len(self.tiles):
                    continue
                idx = self.tiles[tile]
                pal = self.pals[int(self.pal_of_tile[tile]) & 7]
                oy = (slot // GEN2_BLOCK_TILES) * TILE_PX
                ox = (slot % GEN2_BLOCK_TILES) * TILE_PX
                out[oy: oy + TILE_PX, ox: ox + TILE_PX] = pal[idx]
        self._cache[block] = out
        return out


def gen2_render_map(grid: bytes, w_blocks: int, h_blocks: int, ts: Gen2Tileset,
                    border_block: int) -> Image.Image:
    cells = np.frombuffer(grid, dtype=np.uint8)[: w_blocks * h_blocks]
    if len(cells) < w_blocks * h_blocks:
        raise SystemExit(f"a .blk holds {len(cells)} blocks, not the {w_blocks * h_blocks} declared")
    cells = cells.reshape(h_blocks, w_blocks)
    out = np.zeros((h_blocks * GEN2_BLOCK_PX, w_blocks * GEN2_BLOCK_PX, 3), dtype=np.uint8)
    for y in range(h_blocks):
        for x in range(w_blocks):
            b = int(cells[y, x])
            if b == 0:
                b = border_block          # LoadMetatiles: 0 is "draw the border block"
            out[y * GEN2_BLOCK_PX: (y + 1) * GEN2_BLOCK_PX,
                x * GEN2_BLOCK_PX: (x + 1) * GEN2_BLOCK_PX] = ts.block_rgb(b)
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


def map_keys(groups: dict) -> dict[str, str]:
    """Every map in the game, name -> ``"group:num"``.

    The same indexing ``observed_manifest`` reads backwards, and the reason it
    is the same function for both generations: a gen-2 group list carries a
    spacer at index 0 because its groups and map numbers are 1-based.
    """
    out: dict[str, str] = {}
    for g, gname in enumerate(groups["group_order"]):
        for n, name in enumerate(groups[gname]):
            if name:
                out[name] = f"{g}:{n}"
    return out


def sibling_floors(sel: dict[str, str], keys: dict[str, str],
                   is_indoor: Callable[[str], bool]) -> list[str]:
    """Add every OTHER floor of every building a run entered. Returns what it added.

    Andreas, 2026-09-20, looking at Oldale Town's Pokemon Center on the rendered
    world: "the pokecenter (and other 2 story/multiroom) rooms needs to show the
    multi rooms in their sub world". The popup drew one panel labelled 1F,
    because the atlas held one floor and only one: an observed manifest is the
    maps a RUN entered, and no run has ever walked upstairs in a Centre.

    This widens a BUILDING from the floors walked to all of its floors and does
    nothing else — the scope rule is untouched, and a building no run entered is
    still not rendered. The floor that is added has artwork and a panel with no
    route drawn on it, which is exactly what FireRed already looks like: its
    manifest comes from the decomp walk graph rather than from runs, which is
    why ``ViridianCity_PokemonCenter_2F`` has been in its atlas all along and
    Emerald's Oldale equivalent was not.

    The grouping is ``building_of`` — the same rule the popup already uses, so a
    floor cannot be added into one building and drawn in another. Only an INDOOR
    map expands, and only into indoor maps: without that guard the rule reads a
    map's own name as a floor suffix and starts pulling in its neighbours.
    """
    added: list[str] = []
    chosen = set(sel.values())
    for name in sorted(chosen):
        if not is_indoor(name):
            continue
        b = building_of(name)
        for other, key in sorted(keys.items()):
            if other in chosen or other in added or building_of(other) != b:
                continue
            if not is_indoor(other):
                continue
            sel[key] = other
            added.append(other)
    return added


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


# ---------------------------------------------------------------- the source

# What differs between a gen-3 decomp and pokecrystal is where a map's shape,
# its warps and its pixels are READ from — not what is done with them. These two
# classes are that boundary: everything below `main` (the door index, the exit
# index, the world frame, the border, the atlas) sees one interface and cannot
# tell the generations apart.
#
# Every length either side of this boundary is in TILES — the 16 px unit the
# player walks in and the referee reports. Gen 3 already counts in it; gen 2
# counts in 32 px blocks and doubles on the way out.


class Gen3Source:
    """pokefirered / pokeemerald, via pret's own JSON."""

    def __init__(self, game: Game, *, offline: bool) -> None:
        self.game = game
        self.offline = offline
        self.layouts = {l["id"]: l for l in json.loads(
            fetch("data/layouts/layouts.json", offline=offline, game=game))["layouts"] if "id" in l}
        self.groups = json.loads(fetch("data/maps/map_groups.json", offline=offline, game=game))
        #: every map in the game, not only the rendered ones: a town's road out
        #: usually leads somewhere we have not drawn, and that is exactly the
        #: edge whose border must not be painted.
        self.all_names = {camel_to_const(m): m for g in self.groups["group_order"]
                          for m in self.groups[g]}
        self._json: dict[str, Optional[dict]] = {}
        self._tilesets: dict[tuple[str, str], Tileset] = {}

    def map_json(self, name: str) -> Optional[dict]:
        if name not in self._json:
            try:
                self._json[name] = json.loads(
                    fetch(f"data/maps/{name}/map.json", offline=self.offline, game=self.game))
            except SystemExit:
                self._json[name] = None
        return self._json[name]

    def const_of(self, name: str) -> str:
        return camel_to_const(name)

    def _layout(self, name: str) -> Optional[dict]:
        mj = self.map_json(name)
        if mj is None:
            return None
        return self.layouts.get(mj.get("layout"))

    def dims(self, name: str) -> Optional[tuple[int, int]]:
        l = self._layout(name)
        return None if l is None else (int(l["width"]), int(l["height"]))

    def is_indoor(self, name: str) -> bool:
        mj = self.map_json(name)
        return bool(mj) and mj.get("map_type") == MAP_TYPE_INDOOR

    def _tileset(self, layout: dict) -> Tileset:
        tkey = (layout["primary_tileset"], layout["secondary_tileset"])
        if tkey not in self._tilesets:
            self._tilesets[tkey] = Tileset(*tkey, offline=self.offline, game=self.game)
        return self._tilesets[tkey]

    def render(self, name: str) -> Image.Image:
        l = self._layout(name)
        ts = self._tileset(l)
        grid = fetch(l["blockdata_filepath"], offline=self.offline, game=self.game)
        return render_map(grid, int(l["width"]), int(l["height"]), ts)

    def border(self, name: str) -> Optional[tuple[Image.Image, int, int]]:
        """The block the game repeats outside a map's bounds, and its size in tiles."""
        l = self._layout(name)
        bw, bh = int(l.get("border_width", 2)), int(l.get("border_height", 2))
        bpath = l.get("border_filepath")
        if not (bpath and bw > 0 and bh > 0):
            return None
        bgrid = fetch(bpath, offline=self.offline, game=self.game)
        return render_map(bgrid, bw, bh, self._tileset(l)), bw, bh


class Gen2Source:
    """pokecrystal, via assembly.

    Four files carry what one gen-3 `map.json` does, and they are joined
    POSITIONALLY rather than by name, because pret spells the same map
    `Route29` in one and `ROUTE_29` in another and `PlayersHouse1F` against
    `PLAYERS_HOUSE_1F` is where a name rule would break. The map's NAME here is
    therefore its own pret constant: it is the identifier every one of the four
    files agrees on, and `FLOOR_RE` reads `_1F` off it exactly as it reads
    FireRed's.
    """

    def __init__(self, game: Game, *, offline: bool) -> None:
        self.game = game
        self.offline = offline
        read = lambda p: fetch(p, offline=offline, game=game)      # noqa: E731
        self.groups, self._sizes = gen2_map_groups(read("constants/map_constants.asm"))
        order = self.groups["group_order"]
        table = gen2_map_table(read("data/maps/maps.asm"), order)
        self._map: dict[str, dict] = {}
        for gi, label in enumerate(table["pointers"], start=1):
            consts = self.groups[order[gi]][1:]
            rows = table["sections"].get(label, [])
            if len(rows) != len(consts):
                raise SystemExit(f"{label} holds {len(rows)} maps, map_constants.asm {len(consts)}")
            for const, row in zip(consts, rows):
                self._map[const] = {"camel": row[0], "tileset": row[1],
                                    "environment": row[2], "palette": row[6], "group": gi}
        self._attrs = gen2_attributes(read("data/maps/attributes.asm"))
        self._blocks = gen2_block_files(read("data/maps/blocks.asm"))
        #: a gen-2 map's name IS its constant, so this is the identity — kept so
        #: the caller does not have to know that.
        self.all_names = {c: c for c in self._map}
        self._pal_names = gen2_pal_names(read("constants/tileset_constants.asm"))
        self._bg = gen2_colours(read("gfx/tilesets/bg_tiles.pal")).reshape(-1, 4, 3)
        self._roof_colours = gen2_colours(read("gfx/tilesets/roofs.pal"))
        self._roof_of_group = gen2_map_group_roofs(read("data/maps/roofs.asm"))
        self._env_colours = gen2_environment_colours(read("data/maps/environment_colors.asm"))
        self._json: dict[str, dict] = {}
        self._tilesets: dict[tuple, Gen2Tileset] = {}

    # -- what the map IS -------------------------------------------------

    def map_json(self, name: str) -> Optional[dict]:
        """A gen-2 map in the gen-3 shape the rest of this script reads."""
        if name in self._json:
            return self._json[name]
        m, a = self._map.get(name), self._attrs.get(name)
        if m is None or a is None:
            return None
        env = m["environment"]
        mj = {
            # NORMALISED into gen 3's vocabulary, because the whole emitter
            # branches on MAP_TYPE_INDOOR. GATE is indoor for the same reason
            # FireRed's gate houses are: it is the corridor rule, not this flag,
            # that decides a gate is not a place.
            "map_type": MAP_TYPE_INDOOR if env in GEN2_INDOOR_ENVIRONMENTS else f"MAP_TYPE_{env}",
            "environment": env,
            "warp_events": gen2_warps(fetch(f"maps/{m['camel']}.asm",
                                            offline=self.offline, game=self.game)),
            "connections": a["connections"],
        }
        self._json[name] = mj
        return mj

    def const_of(self, name: str) -> str:
        return name

    def dims(self, name: str) -> Optional[tuple[int, int]]:
        return self._sizes.get(name)

    def is_indoor(self, name: str) -> bool:
        m = self._map.get(name)
        return bool(m) and m["environment"] in GEN2_INDOOR_ENVIRONMENTS

    # -- what the map LOOKS like ------------------------------------------

    def _daytime(self, name: str) -> int:
        """Which row of `bg_tiles.pal` this map is drawn from, 0-3.

        `PALETTE_AUTO` follows the clock — which is the render's own
        `time_of_day` — and every other value pins the map to one row whatever
        the clock says (`ReplaceTimeOfDayPals.BrightnessLevels`). That is why an
        interior looks the same at midnight and a route does not.
        """
        declared = self._map[name]["palette"]
        if declared == "PALETTE_AUTO":
            return GEN2_DAYTIMES.index(self.game.time_of_day)
        want = declared[len("PALETTE_"):].lower()
        return GEN2_DAYTIMES.index("dark" if want == "dark" else want)

    def _palettes(self, name: str) -> np.ndarray:
        """The eight BG palettes this map is coloured with, (8, 4, 3) uint8."""
        m = self._map[name]
        env, tileset, group = m["environment"], m["tileset"], m["group"]
        special = GEN2_SPECIAL_PALETTES.get(tileset)
        if tileset == "TILESET_ICE_PATH" and env != "INDOOR":
            special = "gfx/tilesets/ice_path.pal"       # INDOOR here is the Hall of Fame
        if special:
            return gen2_colours(fetch(special, offline=self.offline, game=self.game)).reshape(8, 4, 3)
        tod = self._daytime(name)
        row = self._env_colours[env][tod]
        pals = self._bg[row].copy()
        if env in GEN2_OUTDOOR_ENVIRONMENTS:
            # Colours 1 and 2 of the roof palette are the map GROUP's, not the
            # tileset's — New Bark's roofs are not Violet's, out of one tileset.
            roof = self._roof_colours[group * 4: group * 4 + 4]
            pals[self._pal_names["ROOF"], 1:3] = roof[0:2] if tod < 2 else roof[2:4]
        return pals

    def _roof_tiles(self, name: str) -> Optional[np.ndarray]:
        """The nine roof tiles that overwrite tiles $0a..$12, or None."""
        m = self._map[name]
        if m["tileset"] not in ("TILESET_JOHTO", "TILESET_JOHTO_MODERN",
                                "TILESET_BATTLE_TOWER_OUTSIDE"):
            return None                       # LoadTilesetGFX loads a roof for these three
        group = m["group"]
        roof = self._roof_of_group[group] if group < len(self._roof_of_group) else None
        if roof is None:
            return None
        tiles = gen2_read_tiles(fetch(f"gfx/tilesets/roofs/{roof}.png",
                                      offline=self.offline, game=self.game))
        return tiles[:GEN2_ROOF_LENGTH]

    def _tileset(self, name: str) -> Gen2Tileset:
        m = self._map[name]
        pals = self._palettes(name)
        roof = self._roof_tiles(name)
        key = (m["tileset"], pals.tobytes(), None if roof is None else roof.tobytes())
        if key not in self._tilesets:
            short = m["tileset"][len("TILESET_"):].lower()
            tiles = gen2_read_tiles(fetch(f"gfx/tilesets/{short}.png",
                                          offline=self.offline, game=self.game))
            if roof is not None:
                tiles = tiles.copy()
                tiles[GEN2_ROOF_TILE: GEN2_ROOF_TILE + len(roof)] = roof
            blocks = np.frombuffer(fetch(f"data/tilesets/{short}_metatiles.bin",
                                         offline=self.offline, game=self.game),
                                   dtype=np.uint8).reshape(-1, GEN2_BLOCK_TILES ** 2)
            pal_of_tile = gen2_palette_map(fetch(f"gfx/tilesets/{short}_palette_map.asm",
                                                 offline=self.offline, game=self.game),
                                           self._pal_names)
            self._tilesets[key] = Gen2Tileset(tiles, blocks, pal_of_tile, pals)
        return self._tilesets[key]

    def render(self, name: str) -> Image.Image:
        m, a = self._map[name], self._attrs[name]
        wt, ht = self._sizes[name]
        blk = self._blocks.get(m["camel"])
        if blk is None:
            raise SystemExit(f"{name}: data/maps/blocks.asm has no .blk for {m['camel']}")
        grid = fetch(blk, offline=self.offline, game=self.game)
        return gen2_render_map(grid, wt // GEN2_TILES_PER_BLOCK, ht // GEN2_TILES_PER_BLOCK,
                               self._tileset(name), a["border_block"])

    def border(self, name: str) -> Optional[tuple[Image.Image, int, int]]:
        """Gen 2's border is ONE block — 2x2 tiles — not gen 3's 2x2 metatiles."""
        a = self._attrs[name]
        img = gen2_render_map(bytes([a["border_block"]]), 1, 1,
                              self._tileset(name), a["border_block"])
        return img, GEN2_TILES_PER_BLOCK, GEN2_TILES_PER_BLOCK


def source_for(game: Game, *, offline: bool):
    return (Gen2Source if game.gen == 2 else Gen3Source)(game, offline=offline)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="firered-us", choices=sorted(GAMES))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", action="append", help="render just these map names")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--time-of-day", choices=GEN2_DAYTIMES, default=None,
                    help="gen 2 only: which of the four palette rows to render. "
                         "Defaults to the game descriptor's own value. A GBC map "
                         "is a different picture at every time of day, so this "
                         "is how you render the one your runs were played at.")
    args = ap.parse_args()

    game = GAMES[args.game]
    if args.time_of_day:
        if game.gen != 2:
            raise SystemExit(f"--time-of-day is gen 2 only; {game.key} is gen {game.gen}")
        game = dc_replace(game, time_of_day=args.time_of_day)
    set_complexes(game.key)
    out_dir = args.out or game.out
    ref = game.sha
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise SystemExit(f"{game.key}: sha {ref!r} is not a full 40-hex commit")
    graph = json.loads(game.graph.read_text()) if game.graph else None
    src = source_for(game, offline=args.offline)
    groups = src.groups

    # which maps: the decomp walk graph where there is one, the maps our runs
    # actually entered where there is not.
    bounds: dict[str, list[int]] = {}
    if graph is not None:
        sel = {k: m["name"] for k, m in graph["maps"].items()}
    else:
        sel, bounds = observed_manifest(game, groups)
        # ...and every other floor of every building those runs entered, so a
        # Pokemon Center's popup is not one panel labelled 1F.
        grown = sibling_floors(sel, map_keys(groups), src.is_indoor)
        if grown:
            print(f"+{len(grown)} unvisited floors of visited buildings: {', '.join(grown)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}

    # every map's own description first: the door index is a property of the
    # whole set, not of one map
    map_json = {}
    for name in sel.values():
        mj = src.map_json(name)
        if mj is None:
            raise SystemExit(f"{name}: {game.repo} has no map data for it")
        map_json[name] = mj
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
    all_names = src.all_names

    def dims_of(const: str) -> Optional[tuple[int, int]]:
        """A neighbour's size in tiles, for the span its connection covers.

        Any map in the game, not only the rendered ones: a town's road out
        usually leads somewhere we have not drawn, and that is exactly the edge
        whose border must not be painted.
        """
        name = all_names.get(const)
        return None if name is None else src.dims(name)

    def is_indoor(const: str) -> bool:
        name = all_names.get(const)
        return False if name is None else src.is_indoor(name)

    const_names = {src.const_of(n): n for n in map_json}
    doors = door_index(map_json, const_names, graph, key_of, floors, is_indoor)
    exits = exit_index(map_json, const_names, key_of)

    dims = {}
    for name in map_json:
        d = src.dims(name)
        if d is None:
            raise SystemExit(f"{name}: {game.repo} gives it no size")
        dims[name] = d
    # the world frame: the graph's for FireRed, pret's own connections otherwise
    world = {} if graph is not None else world_frame(game, sel, map_json, all_names, dims)

    for key, name in sorted(sel.items()):
        if args.only and name not in args.only:
            continue
        mj = map_json[name]
        w, h = dims[name]
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
        img = src.render(name)
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
        try:
            drawn = src.border(name)
        except SystemExit:
            raise
        except Exception:
            # A map with no usable border simply has none; the viewer
            # already draws nothing there rather than guessing a fill.
            drawn = None
        if drawn is not None:
            bimg, bw, bh = drawn
            bname = f"{key.replace(':', '-')}-border.png"
            bimg.save(out_dir / bname, optimize=True)
            border = {"file": bname, "w": bw, "h": bh,
                      "bytes": (out_dir / bname).stat().st_size}
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
