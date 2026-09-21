"""National-Dex-keyed species sprites and names, for the four DS cartridges.

    venv/bin/python scripts/extract_dex_sprites.py            # fetch (cached) + write
    venv/bin/python scripts/extract_dex_sprites.py --offline  # cache only, no network

Output under ``src/dashboard/web/public/pokemon/dex/``: one PNG per NATIONAL DEX
number 1..649, plus ``index.json`` carrying the same numbers mapped to the
species' English names.

Why a SECOND set rather than a mapping onto the one beside it
-------------------------------------------------------------
``public/pokemon/<id>.png`` is keyed by the GEN-3 INTERNAL species index — 411
files, of which 252..276 are twenty-five copies of one "?" placeholder, which is
exactly the unused band gen 3 leaves between Celebi and Treecko. Three of the
seven cartridges number their species that way and four do not: Platinum,
SoulSilver, Black and Black 2 all report a NATIONAL DEX number. The card was
feeding a dex number to a gen-3-internal filename, so Platinum's rival Chimchar
(dex 390) drew gen-3 internal 390, which is Anorith.

Mapping dex -> gen-3 internal at the point of use would fix nothing that matters:
every species those four cartridges have actually been recorded fighting —
390 Chimchar, 396 Starly, 399 Bidoof, 401 Kricketot, 403 Shinx, 495 Snivy,
501 Oshawott, 504 Patrat — is a gen-4 or gen-5 native with NO gen-3 sprite to
map onto. Nine of nine observed DS battles would turn from a wrong picture into
a missing one. So the dex keyspace needs its own pixels.

Where the pixels come from
--------------------------
1..493 — Platinum's own ``/poketool/pokegra/pl_pokegra.narc``, the cartridge
this repo already runs. Six NARC entries per species in dex order (back female,
back male, front female, front male, palette, shiny palette), the character data
XOR-encrypted with a 16-bit LCG keyed on its own first halfword — forwards on
Platinum and HGSS, backwards on Diamond/Pearl. Each front is a 160x80 4bpp
LINEAR (not tiled) image holding two animation frames side by side; the left one
is the pose the battle draws first.

494..649 — Unova's own species, which exist on no cartridge older than gen 5.
Black stores these as ANIMATION PARTS: `/a/0/0/4` entry 20n+9 is a 96x96 tile
sheet whose NCER names twenty OAM cells called `head`, `body`, `hand_R`,
`tail_A` and so on, assembled by a multi-cell animation. There is no flat frame
in the ROM to extract, so these 156 come pre-assembled from PokeAPI/sprites at a
pinned commit — the same art, out of the same ROM, composed by somebody else.

Names come from PokeAPI's own data CSV at a pinned commit, and the script
REFUSES to write them unless all 386 gen-3 species agree with the names pret
already gives this repo (see :func:`check_names_against_pret`). National Dex
names do not change between generations — Rattata is Rattata on all seven
cartridges — so one table keyed by dex number serves every game that numbers its
species that way, and the per-game gen-3 tables in ``trainers/<game>/index.json``
keep serving the two that do not.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ds3d.nitrofs import narc_entries, read_rom  # noqa: E402

OUT_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "pokemon" / "dex"
#: The gen-3 set this one sits beside, and the source of the placeholder.
GEN3_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "pokemon"
CACHE = REPO_ROOT / "local" / "dex-sprite-cache"

#: Highest National Dex number any cartridge here can name. Black and Black 2
#: are gen 5, and gen 5 ends at Genesect.
MAX_DEX = 649
#: Highest number Platinum's own archive covers — Arceus, the end of gen 4.
GEN4_MAX_DEX = 493

#: `configs/roms.yaml` game key -> the NARC inside that cartridge.
PLATINUM_GAME = "platinum-us"
POKEGRA = "/poketool/pokegra/pl_pokegra.narc"
#: back_f, back_m, front_f, front_m, palette, shiny palette.
POKEGRA_STRIDE = 6
POKEGRA_FRONT_M = 3
POKEGRA_FRONT_F = 2
POKEGRA_PAL = 4

#: PokeAPI/sprites, pinned. Only the 156 Unova species are taken from here.
SPRITES_REPO = "PokeAPI/sprites"
SPRITES_SHA = "0b133a62e914976d3d7ea33aaa1ac676ca248c30"
SPRITES_PATH = "sprites/pokemon/versions/generation-v/black-white/{dex}.png"

#: PokeAPI/pokeapi, pinned. One CSV, every species name, every language.
NAMES_REPO = "PokeAPI/pokeapi"
NAMES_SHA = "575291cdb197a7e3a320297be276c9de4ef8401a"
NAMES_PATH = "data/v2/csv/pokemon_species_names.csv"
#: `local_language_id` for English in that CSV.
ENGLISH = 9

#: pret/pokefirered, at the SHA the map renderer and the trainer extractor pin.
#: Used ONLY to audit the name table — see check_names_against_pret.
PRET_REPO = "pret/pokefirered"
PRET_SHA = "c75f352304d529f6ba92d4f74b9cf8b5c3810788"


# ---------------------------------------------------------------- fetching

def fetch(repo: str, sha: str, path: str, *, offline: bool) -> bytes:
    """Cache-first read of one file from a pinned GitHub tree.

    Same shape and same reasoning as scripts/render_gamemaps.py's fetch: raw
    .githubusercontent.com, never the contents API, whose base64 mangles a
    binary file.
    """
    local = CACHE / repo.replace("/", "_") / sha[:8] / path
    if local.exists():
        return local.read_bytes()
    if offline:
        raise SystemExit(f"--offline but {repo}:{path} is not cached")
    req = urllib.request.Request(
        f"https://raw.githubusercontent.com/{repo}/{sha}/{path}",
        headers={"User-Agent": "pokebench-dexsprites/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"fetch {repo}:{path} failed: {exc}") from exc
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(f".{local.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(local)
    return data


def rom_path(game: str) -> Path:
    """The ROM `configs/roms.yaml` names for this game, sha1 checked.

    The sha1 is in that file for every cartridge, so an extraction can say it
    read the SAME bytes the runs were played on rather than some other dump
    that happens to sit at the path.
    """
    import yaml
    spec = yaml.safe_load((REPO_ROOT / "configs" / "roms.yaml").read_text())
    roms = spec["roms"] if isinstance(spec, dict) and "roms" in spec else spec
    for entry in roms:
        if entry.get("game") == game:
            path = REPO_ROOT / entry["path"]
            if not path.exists():
                raise SystemExit(f"{path} is missing; roms/ is gitignored")
            want = entry.get("sha1")
            got = hashlib.sha1(path.read_bytes()).hexdigest()
            if want and got != want:
                raise SystemExit(
                    f"{path} is sha1 {got}, roms.yaml says {want} — a different dump")
            return path
    raise SystemExit(f"no roms.yaml entry for game {game!r}")


# ------------------------------------------------------------- gen 4 pixels

def decrypt_pokegra(buf: bytes) -> bytes:
    """Undo Platinum's character-data XOR.

    A 16-bit LCG seeded with the stream's own FIRST halfword, stepping forward.
    Diamond and Pearl seed with the LAST and step backwards; Platinum and HGSS
    do it this way round. Getting it the wrong way round does not raise — it
    produces a full-size image of noise, which is why the caller renders one
    known species and looks at it.
    """
    n = len(buf) // 2
    words = list(struct.unpack(f"<{n}H", buf[:n * 2]))
    key = words[0]
    out = []
    for w in words:
        out.append(w ^ key)
        key = (key * 0x4E6D + 0x6073) & 0xFFFF
    return struct.pack(f"<{n}H", *out)


def ncgr_image(ncgr: bytes, nclr: bytes) -> Optional[Image.Image]:
    """One decrypted NCGR + NCLR as a paletted image, index 0 transparent."""
    if len(ncgr) < 0x30 or ncgr[:4] != b"RGCN":
        return None
    hs = struct.unpack_from("<H", ncgr, 12)[0]
    if ncgr[hs:hs + 4] != b"RAHC":
        return None
    tiles_h, tiles_w, fmt, _part, _tiled, dsz, doff = struct.unpack_from("<HHIIIII", ncgr, hs + 8)
    if fmt != 3:                       # 3 = 4bpp; gen 4 mon pics are all 16 colour
        return None
    body = ncgr[hs + 8 + doff:hs + 8 + doff + dsz]
    body = decrypt_pokegra(body)
    width, height = tiles_w * 8, tiles_h * 8
    px = bytearray(width * height)
    for i, byte in enumerate(body):
        if 2 * i + 1 >= len(px):
            break
        px[2 * i] = byte & 0xF
        px[2 * i + 1] = byte >> 4
    palette = []
    raw = nclr[0x28:0x28 + 32]
    for i in range(16):
        v = struct.unpack_from("<H", raw, 2 * i)[0]
        r, g, b = (v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3
        palette += [r | r >> 5, g | g >> 5, b | b >> 5]
    img = Image.new("P", (width, height))
    img.putdata(bytes(px))
    img.putpalette(palette)
    return img


def gen4_front(entries: list[bytes], dex: int) -> Optional[Image.Image]:
    """The left 80x80 of this species' male front pic, or the female one.

    A female-only species (Nidoran-f's line, Jynx, Chansey…) still has a male
    entry in the archive, and on the cartridges checked it holds the same pixels
    — but an empty one would decode to a blank rather than raise, so the
    fallback is here and a blank is caught by the caller's pixel count.
    """
    base = POKEGRA_STRIDE * dex
    if base + POKEGRA_PAL >= len(entries):
        return None
    nclr = entries[base + POKEGRA_PAL]
    for slot in (POKEGRA_FRONT_M, POKEGRA_FRONT_F):
        img = ncgr_image(entries[base + slot], nclr)
        if img is None:
            continue
        frame = img.crop((0, 0, img.width // 2, img.height))
        if any(v != 0 for v in frame.tobytes()):
            return frame
    return None


# ------------------------------------------------------------------- names

def dex_names(*, offline: bool) -> dict[int, str]:
    """National Dex number -> English name, 1..MAX_DEX.

    The gender glyphs become the same ``(m)``/``(f)`` spelling the gen-3 table
    beside this one uses, so a card cannot show two spellings of Nidoran
    depending on which cartridge it is drawing.
    """
    raw = fetch(NAMES_REPO, NAMES_SHA, NAMES_PATH, offline=offline).decode("utf-8")
    out: dict[int, str] = {}
    for row in csv.DictReader(io.StringIO(raw)):
        if int(row["local_language_id"]) != ENGLISH:
            continue
        dex = int(row["pokemon_species_id"])
        if 1 <= dex <= MAX_DEX:
            out[dex] = row["name"].replace("♀", " (f)").replace("♂", " (m)")
    return out


def _norm(name: str) -> str:
    """A name reduced to what two sources can be expected to agree on."""
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("’", "'"))


def check_names_against_pret(names: dict[int, str], *, offline: bool) -> int:
    """Audit the dex names against pret, and raise on any disagreement.

    The join is pret's own: ``include/constants/species.h`` gives the gen-3
    INTERNAL index of a SPECIES_ symbol and ``include/constants/pokedex.h`` gives
    the same symbol's National Dex number as an enum position, so the 386 real
    gen-3 species are 386 independent chances for an external table to be wrong
    about which number is which mon. It is also the only check in this script
    that can catch an off-by-one in the dex numbering itself.
    """
    dex_h = fetch(PRET_REPO, PRET_SHA, "include/constants/pokedex.h", offline=offline).decode()
    names_h = fetch(PRET_REPO, PRET_SHA, "src/data/text/species_names.h", offline=offline).decode()
    # Stop at the enum's own closing brace. Three `#define`s follow it
    # (KANTO_DEX_COUNT = NATIONAL_DEX_MEW and friends) and sweeping those in
    # made Mew the 412th member, which this audit then reported as a name
    # disagreement — the check catching its own reader, which is the point of
    # having one.
    body = dex_h[dex_h.index("NATIONAL_DEX_NONE"):]
    body = body[:body.index("};")]
    order = re.findall(r"NATIONAL_DEX_([A-Z0-9_]+)", body)
    pret_dex = {name: i for i, name in enumerate(order)}      # NONE is 0
    pret_name = {}
    for key, text in re.findall(r"\[SPECIES_([A-Z0-9_]+)\]\s*=\s*_\(\"([^\"]*)\"\)", names_h):
        pret_name[key] = text.title().replace("♂", " (m)").replace("♀", " (f)")
    bad = []
    checked = 0
    for key, num in pret_dex.items():
        if num == 0 or num > GEN4_MAX_DEX or key.startswith("OLD_UNOWN"):
            continue
        mine, theirs = names.get(num), pret_name.get(key)
        if theirs is None:
            continue
        checked += 1
        if mine is None or _norm(mine) != _norm(theirs):
            bad.append(f"dex {num}: pret says {theirs!r}, the table says {mine!r}")
    if bad:
        raise SystemExit("the dex name table disagrees with pret:\n  " + "\n  ".join(bad[:20]))
    return checked


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out_dir = args.out or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    names = dex_names(offline=args.offline)
    missing_names = [n for n in range(1, MAX_DEX + 1) if n not in names]
    if missing_names:
        raise SystemExit(f"no English name for dex {missing_names[:10]}")
    checked = check_names_against_pret(names, offline=args.offline)
    print(f"names: {len(names)}, {checked} of them audited against pret and agreeing")

    path = rom_path(PLATINUM_GAME)
    data, _title, code, fat, fs = read_rom(str(path))
    start, end = fat[fs[POKEGRA]]
    entries = narc_entries(data[start:end])
    if len(entries) % POKEGRA_STRIDE:
        raise SystemExit(f"{POKEGRA} holds {len(entries)} entries, not a multiple of "
                         f"{POKEGRA_STRIDE} — the layout assumption is wrong")
    print(f"{code}: {POKEGRA} has {len(entries) // POKEGRA_STRIDE} species slots")

    from_rom = blank = 0
    for dex in range(1, GEN4_MAX_DEX + 1):
        img = gen4_front(entries, dex)
        if img is None:
            blank += 1
            continue
        img.save(out_dir / f"{dex}.png", optimize=True, transparency=0)
        from_rom += 1
    print(f"{from_rom} sprites from {path.name}" + (f", {blank} blank" if blank else ""))

    from_web = 0
    for dex in range(GEN4_MAX_DEX + 1, MAX_DEX + 1):
        raw = fetch(SPRITES_REPO, SPRITES_SHA, SPRITES_PATH.format(dex=dex),
                    offline=args.offline)
        img = Image.open(io.BytesIO(raw))
        img.save(out_dir / f"{dex}.png", optimize=True)
        from_web += 1
    print(f"{from_web} sprites from {SPRITES_REPO}@{SPRITES_SHA[:8]}")

    # The species a card draws when it has no sprite for the number, and the
    # ROM's OWN answer to that question: gen-3 internal 252 is the first of the
    # twenty-five identical "?" pics in the band gen 3 leaves unused, which is
    # what FireRed itself shows for a species that is not one.
    placeholder = GEN3_DIR / "unknown.png"
    source = GEN3_DIR / "252.png"
    if source.exists():
        placeholder.write_bytes(source.read_bytes())
        print(f"placeholder: {placeholder.relative_to(REPO_ROOT)} (from the gen-3 '?' pic)")
    else:
        raise SystemExit(f"{source} is missing — run scripts/extract_trainers.py first")

    have = sorted(int(p.stem) for p in out_dir.glob("*.png"))
    if have != list(range(1, MAX_DEX + 1)):
        raise SystemExit(f"{out_dir} holds {len(have)} sprites, not 1..{MAX_DEX}")
    (out_dir / "index.json").write_text(json.dumps({
        "version": 1,
        "keyspace": "national-dex",
        "max_dex": MAX_DEX,
        "sources": {
            f"1-{GEN4_MAX_DEX}": f"{path.name} {POKEGRA}",
            f"{GEN4_MAX_DEX + 1}-{MAX_DEX}": f"{SPRITES_REPO}@{SPRITES_SHA}",
            "names": f"{NAMES_REPO}@{NAMES_SHA} {NAMES_PATH}",
        },
        "species": {str(k): names[k] for k in sorted(names)},
    }, indent=1, ensure_ascii=False) + "\n")
    size = sum(p.stat().st_size for p in out_dir.glob("*.png")) // 1024
    print(f"{len(have)} sprites and {len(names)} names in {out_dir.relative_to(REPO_ROOT)}"
          f" ({size} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
