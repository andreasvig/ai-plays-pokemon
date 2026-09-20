"""Extract the trainers a first-badge run can meet from pret, for the battle icons.

    venv/bin/python scripts/extract_trainers.py            # fetch (cached) + write
    venv/bin/python scripts/extract_trainers.py --offline  # cache only, no network

Output under ``src/dashboard/web/public/trainers/``: one RGBA PNG per trainer
picture and ``index.json`` — the trainer ids the referee already records mapped
to ``{name, class, pic, party: [{species, level}]}``, plus ``species``, the
game's own name for every species id, which is how a wild battle's icon names
what the run walked into (the referee reads the id, not the name).

Why this is static (artifacts/game-map-render/plan.md M16): the party a trainer
carries is a constant of the ROM, and the referee has stored the trainer id of
every fight since 2026-09-14 — so the roster, the levels and the sprite are
recoverable for every run already published, with no new memory read. It is the
ROSTER, not what was actually sent out: the game may not use the whole party,
and we do not know which mon appeared.

Sources, all at the same pinned SHA as the walk graph:

- ``include/constants/opponents.h``            TRAINER_<NAME> -> id
- ``src/data/trainers.h``                      id -> class, name, pic, party label
- ``src/data/trainer_parties.h``               party label -> [{lvl, species}]
- ``src/data/trainer_graphics/front_pic_tables.h``  pic -> graphics symbol
- ``src/data/graphics/trainers.h``             graphics symbol -> a PNG path
- ``src/data/text/species_names.h``            SPECIES_<NAME> -> the game's name
- ``include/constants/species.h``              SPECIES_<NAME> -> its id
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.render_gamemaps import CACHE, fetch, pinned_sha  # noqa: E402
from src.referee.battles import TRAINER_NAMES  # noqa: E402

#: One directory per game, matching the map atlas: a trainer id means
#: nothing without the cartridge it was read from.
GAME = "firered-us"
OUT_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "trainers" / GAME

SPECIES_IDS = "include/constants/species.h"
OPPONENTS = "include/constants/opponents.h"
TRAINERS = "src/data/trainers.h"
PARTIES = "src/data/trainer_parties.h"
PIC_TABLE = "src/data/trainer_graphics/front_pic_tables.h"
GFX = "src/data/graphics/trainers.h"
SPECIES_NAMES = "src/data/text/species_names.h"


def text(path: str, *, offline: bool, ref: str) -> str:
    return fetch(path, offline=offline, ref=ref).decode("utf-8", "replace")


def trainer_ids(src: str) -> dict[str, int]:
    return {m[1]: int(m[2]) for m in re.finditer(r"#define\s+(TRAINER_[A-Z0-9_]+)\s+(\d+)", src)}


def species_ids(src: str) -> dict[str, int]:
    return {m[1]: int(m[2]) for m in re.finditer(r"#define\s+(SPECIES_[A-Z0-9_]+)\s+(\d+)", src)}


def species_names(src: str) -> dict[str, str]:
    # _("BULBASAUR") -> "Bulbasaur". NIDORAN's symbols carry the gender glyph,
    # which the game draws from the text encoding; spell it out instead.
    out = {}
    for key, name in re.findall(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*_\(\"([^\"]*)\"\)", src):
        pretty = name.title().replace("♂", " (m)").replace("♀", " (f)")
        out[key] = pretty
    return out


def parties(src: str) -> dict[str, list[dict]]:
    """``sParty_X[] = { {.lvl = 9, .species = SPECIES_WEEDLE}, ... }``.

    Split on the declarations rather than matching a brace-balanced body: one
    non-greedy body pattern that mis-terminates swallows the NEXT declaration
    whole, which is how Bug Catcher Anthony came out with an empty party on the
    first run of this script.
    """
    out: dict[str, list[dict]] = {}
    decls = list(re.finditer(r"\b(sParty_\w+)\[\]\s*=\s*\{", src))
    for i, m in enumerate(decls):
        end = decls[i + 1].start() if i + 1 < len(decls) else len(src)
        body = src[m.end(): end]
        mons = []
        for entry in re.findall(r"\{(.*?)\}", body, re.S):
            lvl = re.search(r"\.lvl\s*=\s*(\d+)", entry)
            sp = re.search(r"\.species\s*=\s*(SPECIES_[A-Z0-9_]+)", entry)
            if lvl and sp:
                mons.append({"species": sp.group(1), "level": int(lvl.group(1))})
        out[m.group(1)] = mons
    return out


def trainers(src: str) -> dict[str, dict]:
    """``[TRAINER_X] = { .trainerClass = …, .trainerName = _("SAMMY"), … }``."""
    out: dict[str, dict] = {}
    for key, body in re.findall(r"\[(TRAINER_[A-Z0-9_]+)\]\s*=\s*\{(.*?)\n    \},", src, re.S):
        party = re.search(r"\.party\s*=\s*\w+\((sParty_\w+)\)", body)
        out[key] = {
            "class": (re.search(r"\.trainerClass\s*=\s*TRAINER_CLASS_([A-Z0-9_]+)", body) or [None, None])[1],
            "name": (re.search(r'\.trainerName\s*=\s*_\("([^"]*)"\)', body) or [None, ""])[1],
            "pic": (re.search(r"\.trainerPic\s*=\s*TRAINER_PIC_([A-Z0-9_]+)", body) or [None, None])[1],
            "party": party.group(1) if party else None,
        }
    return out


def pic_paths(pic_table: str, gfx: str) -> dict[str, str]:
    """TRAINER_PIC_<NAME> -> the front-pic PNG in the pret tree."""
    symbol_path = dict(re.findall(r"(gTrainerFrontPic_\w+)\[\]\s*=\s*INCBIN_U32\(\"([^\"]+)\"\)", gfx))
    out = {}
    for pic, symbol in re.findall(r"TRAINER_SPRITE\((\w+),\s*(gTrainerFrontPic_\w+)", pic_table):
        path = symbol_path.get(symbol)
        if path:
            out[pic] = re.sub(r"\.4bpp\.lz$", ".png", path)
    return out


def sprite(raw: bytes) -> Image.Image:
    """The front pic as RGBA, colour index 0 — the GBA's transparent one — cut out."""
    img = Image.open_bytes = Image.open(__import__("io").BytesIO(raw))
    if img.mode != "P":
        return img.convert("RGBA")
    a = img.convert("RGBA")
    idx = img.load()
    px = a.load()
    for y in range(img.height):
        for x in range(img.width):
            if idx[x, y] == 0:
                px[x, y] = (0, 0, 0, 0)
    return a


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    ref = pinned_sha()
    rd = lambda p: text(p, offline=args.offline, ref=ref)  # noqa: E731

    ids = trainer_ids(rd(OPPONENTS))
    by_key = trainers(rd(TRAINERS))
    party_of = parties(rd(PARTIES))
    species = species_names(rd(SPECIES_NAMES))
    pics = pic_paths(rd(PIC_TABLE), rd(GFX))
    key_of_id = {v: k for k, v in ids.items()}

    args.out.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    wanted_pics: set[str] = set()
    missing: list[int] = []
    for tid in sorted(TRAINER_NAMES):
        key = key_of_id.get(tid)
        t = by_key.get(key) if key else None
        if t is None:
            missing.append(tid)
            continue
        roster = [{"species": species.get(m["species"], m["species"].removeprefix("SPECIES_").title()),
                   "level": m["level"]} for m in party_of.get(t["party"], [])]
        pic = (t["pic"] or "").lower()
        if t["pic"] in pics:
            wanted_pics.add(t["pic"])
        index[str(tid)] = {
            # The referee's own label is what every other surface shows, so it
            # is the one here too; the ROM's class and given name sit beside it.
            "label": TRAINER_NAMES[tid],
            "name": t["name"].title(),
            "class": (t["class"] or "").replace("_", " ").title(),
            "pic": f"{pic}.png" if t["pic"] in pics else None,
            "party": roster,
        }

    for pic in sorted(wanted_pics):
        raw = fetch(pics[pic], offline=args.offline, ref=ref)
        sprite(raw).save(args.out / f"{pic.lower()}.png", optimize=True)
        print(f"{pic.lower():28s} {(args.out / f'{pic.lower()}.png').stat().st_size // 1024:3d} KB")

    ids = species_ids(rd(SPECIES_IDS))
    by_id = {str(i): species[key] for key, i in ids.items() if key in species and i > 0}
    (args.out / "index.json").write_text(json.dumps(
        {"version": 2, "pret_sha": ref, "trainers": index, "species": by_id}, indent=1) + "\n")
    if missing:
        raise SystemExit(f"no pret entry for trainer ids {missing} — the referee names them but the ROM does not")
    print(f"{len(index)} trainers, {len(wanted_pics)} sprites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
