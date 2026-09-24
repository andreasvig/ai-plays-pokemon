"""Render the walk graph's maps to PNG from pret's tilesets.

    venv/bin/python scripts/render_gamemaps.py                  # fetch (cached) + render all
    venv/bin/python scripts/render_gamemaps.py --only PalletTown
    venv/bin/python scripts/render_gamemaps.py --offline        # cache only, no network

Output: ``src/dashboard/web/public/maps/<group>-<num>.png`` at the game's own
16 px per tile, plus ``index.json`` carrying the pret SHA and the walk-graph
version so a map image and the geometry drawn on it cannot silently disagree
(artifacts/game-map-render/plan.md M1-M3).

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
- primary/secondary    tiles < 640 and metatiles < 640 are the primary
                       tileset's; palettes 0-6 are the primary's, 7-12 the
                       secondary's (NUM_PALS_IN_PRIMARY = 7 — an Emerald value
                       of 6 here would recolour half the world).

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
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PRET = "pret/pokefirered"
CACHE = REPO_ROOT / "local" / "pret-cache"
GRAPH = REPO_ROOT / "data" / "firered-walkgraph.json"
OUT_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"

TILE_PX = 8
METATILE_TILES = 2          # 2x2 tiles per layer
NUM_TILES_IN_PRIMARY = 640
NUM_METATILES_IN_PRIMARY = 640
NUM_PALS_IN_PRIMARY = 7
NUM_PALS_TOTAL = 13


# ---------------------------------------------------------------- fetching

def pinned_sha() -> str:
    marker = CACHE / "SHA"
    return marker.read_text().strip() if marker.exists() else "master"


def fetch(path: str, *, offline: bool, ref: str) -> bytes:
    """Cache-first read of one pret file, pinned at ``ref``.

    Same cache and same reasoning as scripts/build_walkgraph.py: raw
    .githubusercontent.com, never the contents API, whose base64 mangled a
    binary file on 2026-09-09.
    """
    local = CACHE / path
    if local.exists():
        return local.read_bytes()
    if offline:
        raise SystemExit(f"--offline but {path} is not cached")
    req = urllib.request.Request(f"https://raw.githubusercontent.com/{PRET}/{ref}/{path}",
                                 headers={"User-Agent": "pokebench-gamemaps/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"fetch {path} failed: {exc}") from exc
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return data


MAP_TYPE_INDOOR = "MAP_TYPE_INDOOR"
FLOOR_RE = re.compile(r"_(?:B?\d+F|Basement\d*|Roof)$")


def camel_to_const(name: str) -> str:
    parts = []
    for part in name.split("_"):
        parts.append(re.sub(r"(?<=[a-z])(?=[A-Z])", "_", part).upper())
    return "MAP_" + "_".join(parts)


def tileset_dir(kind: str, gname: str) -> str:
    """'gTileset_GenericBuilding1' -> 'data/tilesets/secondary/generic_building_1'."""
    want = gname.removeprefix("gTileset_").lower()
    listing = json.loads((CACHE / f"data/tilesets/{kind}/_listing.json").read_text())
    for d in listing:
        if d.replace("_", "") == want:
            return f"data/tilesets/{kind}/{d}"
    raise SystemExit(f"no {kind} tileset dir for {gname}")


# ---------------------------------------------------------------- buildings

# A COMPLEX: maps that open together behind one marker, in the order they stack
# in the popup — top of the list is the top of the stack, which here is north.
#
# Andreas, 2026-09-16: "i would actually like viridian forest to be a separate
# room, such that when you click the gate openings you see the gate houses and
# the forest in a popup stacked on top of each other."
#
# Curated, not derived. Viridian Forest is a MAP_TYPE_ROUTE with no world
# position, reached only through two gate houses that are each corridors on
# their own — no rule over the map data says those three are one place, and a
# rule invented to say it would be a rule about this one case wearing a
# general-looking coat. The list is the honest form of the same knowledge.
COMPLEXES: dict[str, list[str]] = {
    "ViridianForest": [
        "Route2_ViridianForest_NorthEntrance",
        "ViridianForest",
        "Route2_ViridianForest_SouthEntrance",
    ],
}
COMPLEX_OF = {m: b for b, members in COMPLEXES.items() for m in members}


def building_of(name: str) -> str:
    """'PewterCity_PokemonCenter_2F' -> 'PewterCity_PokemonCenter' (1F stays 1F's own).

    A member of a COMPLEX answers with the complex instead, which is what makes
    its gates and its forest one marker and one popup.
    """
    if name in COMPLEX_OF:
        return COMPLEX_OF[name]
    return FLOOR_RE.sub("", name)


def _corridor_by_graph(graph: dict, building_maps: set[str], key_of: dict[str, str]) -> Optional[bool]:
    """True when closing this building cuts its own doorsteps off from each other.

    The doorstep is the OUTSIDE tile of each warp — on the walk graph that is a
    node outside the building with an edge into it. Returns None when the
    building has fewer than two of them (nothing to disconnect) or is not on
    the graph at all.
    """
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


def door_index(map_json: dict[str, dict], names: dict[str, str], graph: dict,
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
            # A gate house on its own is a corridor by both tests — that is what
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
            # One marker per building, at its first door — except a complex,
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
    same cluster — the forest to its gate houses — is NOT an exit: it is a
    stair inside the place you are already in. So the forest has none, and the
    two gate houses carry the cluster's only ways out, which is also where you
    would walk to.

    A FireRed doorway is three tiles wide and carries a warp on each, so the
    tiles are grouped into touching runs and the marker goes on the middle of
    each — one door, one marker, in the middle of the mat. Grouped, not merged
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

    def __init__(self, primary: str, secondary: str, *, offline: bool, ref: str) -> None:
        pdir, sdir = tileset_dir("primary", primary), tileset_dir("secondary", secondary)
        self.key = (primary, secondary)
        self.tiles_p = read_tiles(fetch(f"{pdir}/tiles.png", offline=offline, ref=ref))
        self.tiles_s = read_tiles(fetch(f"{sdir}/tiles.png", offline=offline, ref=ref))
        self.meta_p = np.frombuffer(fetch(f"{pdir}/metatiles.bin", offline=offline, ref=ref), dtype="<u2").reshape(-1, 8)
        self.meta_s = np.frombuffer(fetch(f"{sdir}/metatiles.bin", offline=offline, ref=ref), dtype="<u2").reshape(-1, 8)
        self.pals = np.zeros((NUM_PALS_TOTAL, 16, 3), dtype=np.uint8)
        for i in range(NUM_PALS_TOTAL):
            d = pdir if i < NUM_PALS_IN_PRIMARY else sdir
            self.pals[i] = read_pal(fetch(f"{d}/palettes/{i:02d}.pal", offline=offline, ref=ref))
        self._cache: dict[int, np.ndarray] = {}

    def backdrop(self) -> np.ndarray:
        return self.pals[0][0]

    def _entries(self, metatile: int) -> np.ndarray | None:
        if metatile < NUM_METATILES_IN_PRIMARY:
            table, idx = self.meta_p, metatile
        else:
            table, idx = self.meta_s, metatile - NUM_METATILES_IN_PRIMARY
        return table[idx] if idx < len(table) else None

    def _tile(self, tile: int) -> np.ndarray:
        if tile < NUM_TILES_IN_PRIMARY:
            table, idx = self.tiles_p, tile
        else:
            table, idx = self.tiles_s, tile - NUM_TILES_IN_PRIMARY
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
                colours = self.pals[min(pal, NUM_PALS_TOTAL - 1)][idx]
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", action="append", help="render just these map names")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    ref = pinned_sha()
    graph = json.loads(GRAPH.read_text())
    layouts = {l["id"]: l for l in json.loads(fetch("data/layouts/layouts.json", offline=args.offline, ref=ref))["layouts"] if "id" in l}

    args.out.mkdir(parents=True, exist_ok=True)
    tilesets: dict[tuple[str, str], Tileset] = {}
    index: dict[str, dict] = {}

    # every map's own json first: the door index is a property of the whole set
    map_json = {m["name"]: json.loads(fetch(f"data/maps/{m['name']}/map.json", offline=args.offline, ref=ref))
                for m in graph["maps"].values()}
    key_of = {m["name"]: k for k, m in graph["maps"].items()}
    floors: dict[str, list[str]] = {}
    for name in sorted(map_json):
        if map_json[name].get("map_type") == MAP_TYPE_INDOOR and name not in COMPLEX_OF:
            floors.setdefault(building_of(name), []).append(name)
    # A complex keeps the order it was DECLARED in — north to south, the way you
    # walk it — where a building's floors are sorted (1F, 2F). Sorting a complex
    # would stack the forest between its gates by alphabet, which is luck.
    for b, members in COMPLEXES.items():
        present = [m for m in members if m in map_json]
        if present:
            floors[b] = present
    # map_type for a destination outside the rendered set, fetched once and
    # cached like everything else; unknown counts as NOT indoor, which is the
    # conservative read (an unknown exit makes a room a corridor).
    groups = json.loads(fetch("data/maps/map_groups.json", offline=args.offline, ref=ref))
    all_names = {camel_to_const(m): m for g in groups["group_order"] for m in groups[g]}

    def is_indoor(const: str) -> bool:
        name = all_names.get(const)
        if name is None:
            return False
        try:
            mj = json.loads(fetch(f"data/maps/{name}/map.json", offline=args.offline, ref=ref))
        except SystemExit:
            return False
        return mj.get("map_type") == MAP_TYPE_INDOOR

    const_names = {camel_to_const(n): n for n in map_json}
    doors = door_index(map_json, const_names, graph, key_of, floors, is_indoor)
    exits = exit_index(map_json, const_names, key_of)

    for key, m in sorted(graph["maps"].items()):
        name = m["name"]
        if args.only and name not in args.only:
            continue
        mj = map_json[name]
        layout = layouts[mj["layout"]]
        tkey = (layout["primary_tileset"], layout["secondary_tileset"])
        if tkey not in tilesets:
            tilesets[tkey] = Tileset(*tkey, offline=args.offline, ref=ref)
        grid = fetch(layout["blockdata_filepath"], offline=args.offline, ref=ref)
        w, h = int(layout["width"]), int(layout["height"])
        if (w, h) != (m["width"], m["height"]):
            raise SystemExit(f"{name}: layout is {w}x{h} but the walk graph says {m['width']}x{m['height']}")
        img = render_map(grid, w, h, tilesets[tkey])
        fname = f"{key.replace(':', '-')}.png"
        img.save(args.out / fname, optimize=True)
        walkable = {(n[2], n[3]) for n in graph["nodes"] if f"{n[0]}:{n[1]}" == key}
        trim = empty_edges(img, walkable)
        entry = {"name": name, "width": w, "height": h, "file": fname,
                 "bytes": (args.out / fname).stat().st_size, "type": mj.get("map_type")}
        if any(trim.values()):
            entry["trim"] = {k: v for k, v in trim.items() if v}
        if isinstance(m.get("world"), list):
            entry["world"] = m["world"]
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
            # which was true until Viridian Forest — a ROUTE — moved indoors.
            entry["popup"] = True
            if b in COMPLEXES:
                # A complex is laid out GEOGRAPHICALLY — its members stack in
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
        (args.out / "index.json").write_text(json.dumps({
            "version": 2,
            "tile_px": 16,
            "graph_source": graph.get("source"),
            "graph_version": graph.get("version"),
            "pret_sha": ref,
            "maps": index,
        }, indent=1) + "\n")
    total = sum(e["bytes"] for e in index.values())
    print(f"{len(index)} maps, {total // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
