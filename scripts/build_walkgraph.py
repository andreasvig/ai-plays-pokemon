"""Build ``data/firered-walkgraph.json`` from pret's pokefirered map data.

    venv/bin/python scripts/build_walkgraph.py            # fetch (cached) + build + verify
    venv/bin/python scripts/build_walkgraph.py --offline  # cache only, no network

Sources (all public, fetched with ``gh api`` and cached under ``local/pret-cache/``):

- ``data/layouts/layouts.json`` + ``data/layouts/<Layout>/map.bin`` — the grid.
  One u16 per tile: bits 0-9 metatile id, bits 10-11 collision (0 = passable),
  bits 12-15 elevation.
- ``data/tilesets/{primary,secondary}/<name>/metatile_attributes.bin`` — one u32
  per metatile; bits 0-8 are the behaviour (ledges, water, doors …). Metatiles
  0..639 index the primary tileset, 640..1023 the secondary.
- ``data/maps/<Map>/map.json`` — warps (x, y → dest map + warp id), connections
  (direction + offset to a neighbouring outdoor map), and the map's MAP_ id.
- ``data/maps/map_groups.json`` — map name → (group, num), the same numbers the
  referee reads from SaveBlock1 (+0x4 / +0x5).

Scope: the outdoor maps of the first-badge route (Pallet → Pewter, plus the
adjacent Route 21 / 22 / 3 stubs) and EVERY indoor map reachable from them by
warp within the Pallet / Viridian / Pewter / Route 2 / Route 22 indoor groups,
plus Viridian Forest. A model that ducks into a Pokémon Center stays on-graph.

Rules (see artifacts/granular-progress/plan.md §5):
- passable = collision 0, behaviour not water; warp tiles are always nodes;
- ledges (MB_JUMP_*) are one-way: the tile before → the tile after, 1 step;
- MB_IMPASSABLE_<dir> blocks leaving the tile in that direction;
- elevation: 0 and 15 match anything, otherwise both sides must be equal;
- warps: node → the destination warp's tile (1 step); connections stitch edge
  rows/columns with the declared offset; NPCs are not barriers — EXCEPT cut
  trees, smashable rocks and boulders, which are object events (not tiles) and
  would otherwise open the Route 2 east-side bypass around Viridian Forest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import urllib.error
import urllib.request
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.referee.walkgraph import WalkGraph  # noqa: E402

GAME = "firered-us"  # joins to configs/roms.yaml
PRET = "pret/pokefirered"
CACHE = REPO_ROOT / "local" / "pret-cache"
OUT = REPO_ROOT / "data" / "firered-walkgraph.json"

# Outdoor seeds (TownsAndRoutes group) — connections are followed only among these.
SEED_MAPS = ["PalletTown", "Route1", "ViridianCity", "Route2", "PewterCity", "Route21_North", "Route22", "Route3"]
# Indoor groups whose maps are pulled in when a warp leads there.
INDOOR_GROUPS = {"gMapGroup_IndoorPallet", "gMapGroup_IndoorViridian", "gMapGroup_IndoorPewter",
                 "gMapGroup_IndoorRoute2", "gMapGroup_IndoorRoute22"}
# Dungeons are followed only for these.
DUNGEON_ALLOW = {"ViridianForest"}

NUM_METATILES_IN_PRIMARY = 640
BEHAVIOR_MASK = 0x1FF

MB_JUMP = {0x38: (1, 0), 0x39: (-1, 0), 0x3A: (0, -1), 0x3B: (0, 1)}          # east west north south
MB_IMPASSABLE = {0x30: {(1, 0)}, 0x31: {(-1, 0)}, 0x32: {(0, -1)}, 0x33: {(0, 1)},
                 0x34: {(1, 0), (0, -1)}, 0x35: {(-1, 0), (0, -1)}, 0x36: {(1, 0), (0, 1)}, 0x37: {(-1, 0), (0, 1)}}
MB_WATER = {0x10, 0x11, 0x12, 0x13, 0x15, 0x19, 0x1A, 0x1B}
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
# object_events whose graphics make the tile impassable without an HM
BARRIER_OBJECTS = ("CUT_TREE", "ROCK_SMASH_ROCK", "PUSHABLE_BOULDER", "STRENGTH")


# ---------------------------------------------------------------- fetching

def cache_write(path: Path, data: bytes) -> None:
    """tmp-then-``os.replace``: a reader never sees a half-written cached file.

    ``local/pret-cache`` is shared — scripts/render_gamemaps.py fetches the same
    tree into the same directory, and two worktrees run both — so a plain
    ``write_bytes`` lets a concurrent reader pick up a truncated ``map.bin`` and
    build a graph out of it without a word.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def fetch(path: str, *, offline: bool) -> bytes:
    local = CACHE / path
    if local.exists():
        return local.read_bytes()
    if offline:
        raise SystemExit(f"--offline but {path} is not cached")
    # raw.githubusercontent.com, not the contents API: the API's base64 for a
    # binary file came back re-encoded (map.bin 960 B → 1419 B of mojibake) on
    # 2026-09-09, which silently turned every grid into noise.
    #
    # AT THE PINNED SHA, not at `master`. This URL said `master` while the graph
    # it produced recorded `pret_sha()` beside it, so a file fetched today and a
    # provenance string written months ago could disagree and nothing would say
    # so — the one thing the provenance string exists to prevent.
    req = urllib.request.Request(
        f"https://raw.githubusercontent.com/{PRET}/{pret_sha(offline=offline)}/{path}",
        headers={"User-Agent": "pokebench-walkgraph/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"fetch {path} failed: {exc}") from exc
    cache_write(local, data)
    return data


def fetch_json(path: str, *, offline: bool) -> dict:
    return json.loads(fetch(path, offline=offline))


def pret_sha(*, offline: bool) -> str:
    """The commit this cache is of — a full 40-hex sha or nothing.

    It used to answer "unknown" when the marker was absent and the resolve
    failed, and that string went straight into the graph's ``source`` field.
    Unverifiable provenance is worse than no graph: it reads exactly like a real
    one. `tests/test_gamemaps.py` refuses a non-sha in the atlas for the same
    reason; this is the other half of it.
    """
    marker = CACHE / "SHA"
    if marker.exists():
        sha = marker.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise SystemExit(f"{marker} holds {sha!r}, which is not a 40-hex commit")
        return sha
    if offline:
        raise SystemExit(f"--offline and {marker} is absent: nothing says which commit this is")
    proc = subprocess.run(["gh", "api", f"repos/{PRET}/commits/master", "--jq", ".sha"],
                          capture_output=True, text=True)
    sha = proc.stdout.strip() if proc.returncode == 0 else ""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit(f"could not resolve {PRET}@master to a commit (got {sha!r})")
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_write(marker, sha.encode())
    return sha


def camel_to_const(name: str) -> str:
    """'Route2_ViridianForestSouthEntrance' → 'MAP_ROUTE2_VIRIDIAN_FOREST_SOUTH_ENTRANCE'."""
    parts = []
    for part in name.split("_"):
        # lowercase→Uppercase only: "PlayersHouse" → PLAYERS_HOUSE but "1F" stays 1F
        s = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", part)
        parts.append(s.upper())
    return "MAP_" + "_".join(parts)


def tileset_dir(kind: str, gname: str, listing: list[str]) -> str:
    """'gTileset_GenericBuilding1' → 'generic_building_1' by underscore-insensitive match."""
    want = gname.removeprefix("gTileset_").lower()
    for d in listing:
        if d.replace("_", "") == want:
            return f"data/tilesets/{kind}/{d}"
    raise SystemExit(f"no {kind} tileset dir for {gname}")


# ---------------------------------------------------------------- building

class MapData:
    def __init__(self, name: str, group: int, num: int, mj: dict, layout: dict, grid: bytes,
                 attrs_primary: bytes, attrs_secondary: bytes) -> None:
        self.name, self.group, self.num, self.json = name, group, num, mj
        self.width, self.height = int(layout["width"]), int(layout["height"])
        self.cells = struct.unpack(f"<{self.width * self.height}H", grid[: self.width * self.height * 2])
        self.ap, self.asx = attrs_primary, attrs_secondary
        self.warp_at: dict[tuple[int, int], dict] = {(int(w["x"]), int(w["y"])): w for w in mj.get("warp_events", [])}
        self.barriers: set[tuple[int, int]] = {
            (int(o["x"]), int(o["y"])) for o in mj.get("object_events", [])
            if any(k in str(o.get("graphics_id", "")) for k in BARRIER_OBJECTS)
        }

    def cell(self, x: int, y: int) -> tuple[int, int, int]:
        """(metatile, collision, elevation) or (-1, 3, 0) off-grid."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return -1, 3, 0
        v = self.cells[y * self.width + x]
        return v & 0x3FF, (v >> 10) & 3, v >> 12

    def behaviour(self, metatile: int) -> int:
        if metatile < 0:
            return 0
        if metatile < NUM_METATILES_IN_PRIMARY:
            off = metatile * 4
            blob = self.ap
        else:
            off = (metatile - NUM_METATILES_IN_PRIMARY) * 4
            blob = self.asx
        if off + 4 > len(blob):
            return 0
        return struct.unpack_from("<I", blob, off)[0] & BEHAVIOR_MASK

    def passable(self, x: int, y: int) -> bool:
        if (x, y) in self.warp_at:
            return True
        if (x, y) in self.barriers:
            return False
        mt, col, _el = self.cell(x, y)
        if mt < 0 or col != 0:
            return False
        return self.behaviour(mt) not in MB_WATER and self.behaviour(mt) not in MB_JUMP


def elevation_ok(a: int, b: int) -> bool:
    return a in (0, 15) or b in (0, 15) or a == b


def build(*, offline: bool) -> WalkGraph:
    groups = fetch_json("data/maps/map_groups.json", offline=offline)
    layouts = {l["id"]: l for l in fetch_json("data/layouts/layouts.json", offline=offline)["layouts"] if "id" in l}
    group_index = {name: i for i, name in enumerate(groups["group_order"])}
    map_gn: dict[str, tuple[int, int]] = {}
    map_group_name: dict[str, str] = {}
    for gname, gi in group_index.items():
        for ni, mname in enumerate(groups[gname]):
            map_gn[mname] = (gi, ni)
            map_group_name[mname] = gname
    const_to_name = {camel_to_const(m): m for m in map_gn}

    def listing(path: str) -> list[str]:
        cache = CACHE / path / "_listing.json"
        if cache.exists():
            return json.loads(cache.read_text())
        if offline:
            raise SystemExit(f"cannot list {path}")
        # ?ref=<sha>, for the same reason `fetch` pins: a directory listing taken
        # from master can name a tileset the pinned tree does not have. The call
        # also used to run BEFORE the cache was consulted, so every cached run
        # still paid for it.
        proc = subprocess.run(["gh", "api",
                               f"repos/{PRET}/contents/{path}?ref={pret_sha(offline=offline)}",
                               "--jq", ".[].name"], capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"cannot list {path}")
        names = proc.stdout.split()
        cache_write(cache, json.dumps(names).encode())
        return names

    primary_dirs = listing("data/tilesets/primary")
    secondary_dirs = listing("data/tilesets/secondary")
    attr_cache: dict[str, bytes] = {}

    def attrs(kind: str, gname: str) -> bytes:
        key = f"{kind}:{gname}"
        if key not in attr_cache:
            d = tileset_dir(kind, gname, primary_dirs if kind == "primary" else secondary_dirs)
            attr_cache[key] = fetch(f"{d}/metatile_attributes.bin", offline=offline)
        return attr_cache[key]

    def allowed(mname: str) -> bool:
        g = map_group_name.get(mname)
        if mname in SEED_MAPS or mname in DUNGEON_ALLOW:
            return True
        return g in INDOOR_GROUPS

    # -- collect maps: seeds + warp closure inside the allowed groups
    maps: dict[str, MapData] = {}
    queue = deque(SEED_MAPS)
    while queue:
        mname = queue.popleft()
        if mname in maps or not allowed(mname):
            continue
        mj = fetch_json(f"data/maps/{mname}/map.json", offline=offline)
        if mj.get("id") != camel_to_const(mname):
            raise SystemExit(f"{mname}: map.json id {mj.get('id')!r} != {camel_to_const(mname)!r}")
        layout = layouts[mj["layout"]]
        grid = fetch(layout["blockdata_filepath"], offline=offline)
        g, n = map_gn[mname]
        maps[mname] = MapData(mname, g, n, mj, layout, grid,
                              attrs("primary", layout["primary_tileset"]), attrs("secondary", layout["secondary_tileset"]))
        for w in mj.get("warp_events", []):
            dest = const_to_name.get(w["dest_map"])
            if dest and dest not in maps:
                queue.append(dest)
        # connections only among seeds (outdoor); indoor maps have none
        for c in mj.get("connections") or []:
            dest = const_to_name.get(c["map"])
            if dest in SEED_MAPS and dest not in maps:
                queue.append(dest)

    # -- nodes
    nodes: list[tuple[int, int, int, int]] = []
    index: dict[tuple[int, int, int, int], int] = {}
    for m in maps.values():
        for y in range(m.height):
            for x in range(m.width):
                if m.passable(x, y):
                    c = (m.group, m.num, x, y)
                    index[c] = len(nodes)
                    nodes.append(c)
    adj: list[set[int]] = [set() for _ in nodes]

    def link(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> None:
        ia, ib = index.get(a), index.get(b)
        if ia is not None and ib is not None and ia != ib:
            adj[ia].add(ib)

    # -- intra-map steps, ledges, directional blocks
    for m in maps.values():
        for y in range(m.height):
            for x in range(m.width):
                if (m.group, m.num, x, y) not in index:
                    continue
                mt, _col, el = m.cell(x, y)
                beh = m.behaviour(mt)
                blocked = MB_IMPASSABLE.get(beh, set())
                for dx, dy in DIRS:
                    if (dx, dy) in blocked:
                        continue
                    nx, ny = x + dx, y + dy
                    nmt, _ncol, nel = m.cell(nx, ny)
                    nbeh = m.behaviour(nmt)
                    if nbeh in MB_JUMP:
                        # a ledge: only crossable in its jump direction, landing beyond it
                        if MB_JUMP[nbeh] == (dx, dy):
                            lx, ly = nx + dx, ny + dy
                            link((m.group, m.num, x, y), (m.group, m.num, lx, ly))
                        continue
                    if (m.group, m.num, nx, ny) not in index:
                        continue
                    if not elevation_ok(el, nel):
                        continue
                    if (-dx, -dy) in MB_IMPASSABLE.get(nbeh, set()):
                        continue
                    link((m.group, m.num, x, y), (m.group, m.num, nx, ny))

    # -- warps
    for m in maps.values():
        for (x, y), w in m.warp_at.items():
            dest = const_to_name.get(w["dest_map"])
            dm = maps.get(dest) if dest else None
            if dm is None:
                continue
            try:
                dw = dm.json["warp_events"][int(w["dest_warp_id"])]
            except (IndexError, ValueError, KeyError):
                continue
            link((m.group, m.num, x, y), (dm.group, dm.num, int(dw["x"]), int(dw["y"])))

    # -- connections (outdoor seams)
    for m in maps.values():
        for c in m.json.get("connections") or []:
            dest = const_to_name.get(c["map"])
            dm = maps.get(dest) if dest else None
            if dm is None:
                continue
            off = int(c["offset"])
            d = c["direction"]
            pairs: list[tuple[tuple[int, int], tuple[int, int]]] = []
            if d == "up":
                pairs = [((x, 0), (x - off, dm.height - 1)) for x in range(m.width)]
            elif d == "down":
                pairs = [((x, m.height - 1), (x - off, 0)) for x in range(m.width)]
            elif d == "left":
                pairs = [((0, y), (dm.width - 1, y - off)) for y in range(m.height)]
            elif d == "right":
                pairs = [((m.width - 1, y), (0, y - off)) for y in range(m.height)]
            for (ax, ay), (bx, by) in pairs:
                a, b = (m.group, m.num, ax, ay), (dm.group, dm.num, bx, by)
                if a in index and b in index:
                    _mt, _c, ea = m.cell(ax, ay)
                    _mt2, _c2, eb = dm.cell(bx, by)
                    if elevation_ok(ea, eb):
                        link(a, b)
                        link(b, a)

    # -- world frame (version 2, 2026-09-15): every outdoor map connected to
    # Pallet Town gets the tile offset of its top-left corner in ONE shared
    # frame, Pallet Town at (0, 0), so a run's route can be drawn on a single
    # pixel map (tile = 16 px; artifacts/route-fidelity/plan.md R4). The offsets
    # follow pret's connection semantics exactly as the seam stitching above:
    # "up" with offset o puts the neighbour's column x at our column x + o.
    world: dict[str, tuple[int, int]] = {}
    conflicts: list[str] = []
    origin = next((m for m in maps.values() if m.name == "PalletTown"), None)
    if origin is not None:
        world[origin.name] = (0, 0)
        q: deque[str] = deque([origin.name])
        while q:
            m = maps[q.popleft()]
            wx, wy = world[m.name]
            for c in m.json.get("connections") or []:
                dest = const_to_name.get(c["map"])
                dm = maps.get(dest) if dest else None
                if dm is None:
                    continue
                off, d = int(c["offset"]), c["direction"]
                if d == "up":
                    w = (wx + off, wy - dm.height)
                elif d == "down":
                    w = (wx + off, wy + m.height)
                elif d == "left":
                    w = (wx - dm.width, wy + off)
                elif d == "right":
                    w = (wx + m.width, wy + off)
                else:
                    continue
                if dm.name in world:
                    if world[dm.name] != w:  # a cycle of connections must close
                        conflicts.append(f"{m.name} → {dm.name}: {w} vs {world[dm.name]}")
                    continue
                world[dm.name] = w
                q.append(dm.name)
    if conflicts:
        raise SystemExit("world frame does not close: " + "; ".join(conflicts))

    meta = {
        "version": 2,
        # Joins to `configs/roms.yaml` and to a ladder's `game:`. Without it a
        # graph is anonymous, and an anonymous graph loaded under another game
        # answers every query with confident nonsense (referee.py
        # _load_default_graph).
        "game": GAME,
        "source": f"{PRET}@{pret_sha(offline=offline)}",
        "built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "rules": "collision0+notwater; ledges one-way; MB_IMPASSABLE_*; elevation 0/15 wild; warps 1 step; connections stitched",
        "tile_px": 16,
        "world_origin": "PalletTown top-left = (0, 0); outdoor maps only",
    }
    mapsd = {}
    for m in maps.values():
        entry: dict = {"name": m.name, "width": m.width, "height": m.height}
        if m.name in world:
            entry["world"] = list(world[m.name])
        mapsd[f"{m.group}:{m.num}"] = entry
    return WalkGraph(nodes, [sorted(a) for a in adj], mapsd, meta)


# ---------------------------------------------------------------- verification

def verify(g: WalkGraph) -> list[str]:
    """Sanity checks that the graph tells the FireRed story. Returns problems."""
    problems: list[str] = []
    by_name = {m["name"]: tuple(int(p) for p in key.split(":")) for key, m in g.maps.items()}

    def entry(name: str) -> set[int]:
        gn = by_name.get(name)
        return g.map_entry_nodes(*gn) if gn else set()

    # the bed in the player's 2F bedroom — the canonical start tile is next to it
    bedroom = by_name.get("PalletTown_PlayersHouse_2F")
    start = None
    if bedroom:
        for c in ((5, 6), (4, 6), (5, 5), (6, 6)):
            start = g.node_id(*bedroom, *c)
            if start is not None:
                break
    if start is None:
        problems.append("no passable start tile in the bedroom")
        return problems

    route = ["PalletTown_PlayersHouse_1F", "PalletTown", "PalletTown_ProfessorOaksLab", "Route1", "ViridianCity",
             "ViridianCity_Mart", "Route2", "ViridianForest", "PewterCity", "PewterCity_Gym"]
    prev_d = -1
    for name in route:
        tgt = entry(name)
        if not tgt:
            problems.append(f"{name}: no entry tiles")
            continue
        d = g.distance_to(tgt, start)
        if d is None:
            problems.append(f"{name}: unreachable from the bedroom")
            continue
        print(f"  {name:38s} {d:5d} steps from the bed")
        if name in ("Route1", "ViridianCity", "Route2", "ViridianForest", "PewterCity") and d <= prev_d:
            problems.append(f"{name}: distance {d} not beyond the previous outdoor gate ({prev_d})")
        if name in ("Route1", "ViridianCity", "Route2", "ViridianForest", "PewterCity"):
            prev_d = d
    # the Viridian → Pewter path must go THROUGH the Forest: the Route 2 east side
    # is closed by cut trees, which are objects, not tiles (caught 2026-09-09)
    vir, pew, forest = by_name.get("ViridianCity"), by_name.get("PewterCity"), by_name.get("ViridianForest")
    if vir and pew and forest:
        field = g.distance_field(g.map_entry_nodes(*pew))
        n = next(iter(g.map_entry_nodes(*vir)))
        forest_tiles = 0
        while field[n] > 0:
            n = min(g.adj[n], key=lambda u: field[u] if field[u] >= 0 else 10**9)
            forest_tiles += g.coord(n)[:2] == forest
        print(f"  {'Viridian→Pewter path, Forest tiles':38s} {forest_tiles:5d}")
        if forest_tiles == 0:
            problems.append("Viridian → Pewter shortest path bypasses Viridian Forest")
    gym = by_name.get("PewterCity_Gym")
    if gym:
        brock = g.passable_neighbours(*gym, 6, 5)
        d = g.distance_to(brock, start) if brock else None
        print(f"  {'Brock (stand tile)':38s} {d if d is not None else '∞':>5} steps from the bed")
        if d is None:
            problems.append("Brock's stand tile unreachable")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use local/pret-cache only")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    g = build(offline=args.offline)
    print(f"maps {len(g.maps)}  nodes {len(g)}  edges {sum(len(a) for a in g.adj)}")
    problems = verify(g)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(g.to_dict(), separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
