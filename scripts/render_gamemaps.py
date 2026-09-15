"""Render the walk graph's maps to PNG from pret's tilesets.

    venv/bin/python scripts/render_gamemaps.py                  # fetch (cached) + render all
    venv/bin/python scripts/render_gamemaps.py --only PalletTown
    venv/bin/python scripts/render_gamemaps.py --offline        # cache only, no network

Output: ``src/dashboard/web/public/maps/<group>-<num>.png`` at the game's own
16 px per tile, plus ``index.json`` carrying the pret SHA and the walk-graph
version so a map image and the geometry drawn on it cannot silently disagree
(artifacts/game-map-render/plan.md M1-M3).

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

    for key, m in sorted(graph["maps"].items()):
        name = m["name"]
        if args.only and name not in args.only:
            continue
        mj = json.loads(fetch(f"data/maps/{name}/map.json", offline=args.offline, ref=ref))
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
        entry = {"name": name, "width": w, "height": h, "file": fname,
                 "bytes": (args.out / fname).stat().st_size}
        if isinstance(m.get("world"), list):
            entry["world"] = m["world"]
        index[key] = entry
        print(f"{name:42s} {w:3d}x{h:<3d} {entry['bytes'] // 1024:4d} KB")

    if not args.only:
        (args.out / "index.json").write_text(json.dumps({
            "version": 1,
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
