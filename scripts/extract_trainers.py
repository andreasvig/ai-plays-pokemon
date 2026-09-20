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
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.render_gamemaps import GAMES, fetch  # noqa: E402
from src.referee.battles import TRAINER_NAMES  # noqa: E402

#: One directory per game, matching the map atlas: a trainer id means
#: nothing without the cartridge it was read from. The pret repo, its
#: pinned SHA and its cache all come from the SAME descriptor the map
#: renderer uses, so trainers and maps cannot be read from two trees.
TRAINERS_ROOT = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "trainers"


@dataclass(frozen=True)
class Roster:
    """Which trainers one cartridge's index carries, and what labels them.

    The pret FILE PATHS are the same in pokefirered and pokeemerald, so they
    stay module constants; what differs is the SELECTION and the label.

    FireRed's ladder names thirteen trainers (``src/referee/battles.py``
    TRAINER_NAMES) and every other surface shows those names, so its index is
    those thirteen and the referee's wording is the label. Emerald has no
    ladder, so there is no list to be selected BY — and a card that meets Lass
    Tiana must still be able to say "Lass Tiana". Its index is therefore the
    whole roster, labelled from the ROM's own class and name.

    Selecting by the referee where a referee exists is not an inconsistency: a
    label the rest of the app already uses beats one derived here, and where
    none exists the derivation is the honest fallback rather than a blank.
    """

    game: str
    referee_labels: bool
    #: Whether this cartridge SOURCES the shared species sprites. Exactly one
    #: does. ``public/pokemon/<id>.png`` is named by the gen-3 INTERNAL species
    #: index, which pokefirered and pokeemerald share — Mudkip is 283 in both —
    #: so one extraction serves every gen-3 game and a second would be the same
    #: pixels under the same names. It has to be declared rather than inferred:
    #: Emerald spells the symbol ``gMonStillFrontPic_`` (its fronts animate),
    #: so the FireRed reader finds nothing there and reports "0 sprites"
    #: cheerfully, which looks like a job that ran.
    mon_sprites: bool = False

    @property
    def out(self) -> Path:
        return TRAINERS_ROOT / self.game


ROSTERS = {
    "firered-us": Roster(game="firered-us", referee_labels=True, mon_sprites=True),
    "emerald-us": Roster(game="emerald-us", referee_labels=False),
}
#: Species sprites are NOT per game — a species id means the same mon on
#: every cartridge, so they sit beside the per-game trainer directories.
MON_DIR = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "pokemon"

SPECIES_IDS = "include/constants/species.h"
OPPONENTS = "include/constants/opponents.h"
TRAINERS = "src/data/trainers.h"
PARTIES = "src/data/trainer_parties.h"
PIC_TABLE = "src/data/trainer_graphics/front_pic_tables.h"
GFX = "src/data/graphics/trainers.h"
SPECIES_NAMES = "src/data/text/species_names.h"
MON_PIC_TABLE = "src/data/pokemon_graphics/front_pic_table.h"
MON_GFX = "src/data/graphics/pokemon.h"


def text(path: str, *, offline: bool, game) -> str:
    return fetch(path, offline=offline, game=game).decode("utf-8", "replace")


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


def _incbin(gfx: str, prefix: str) -> dict[str, str]:
    """``<symbol> -> the PNG it was included from``, in either pret spelling.

    pokefirered writes ``INCBIN_U32("graphics/.../hiker.4bpp.lz")`` and
    pokeemerald writes ``INCGFX_U32("graphics/.../hiker.png", ".4bpp.lz")`` —
    the same fact, the extension moved into a second argument. Matching only
    the first found zero Emerald sprites and raised nothing: every trainer
    simply came out with ``pic: null`` and the cards drew no portrait, which is
    what a silently generation-specific regex looks like.
    """
    out: dict[str, str] = {}
    for sym, path in re.findall(rf"({prefix}_\w+)\[\]\s*=\s*INC(?:BIN|GFX)_U32\(\"([^\"]+)\"", gfx):
        out[sym] = re.sub(r"\.4bpp\.lz$", ".png", path)
    return out


def pic_paths(pic_table: str, gfx: str) -> dict[str, str]:
    """TRAINER_PIC_<NAME> -> the front-pic PNG in the pret tree."""
    symbol_path = _incbin(gfx, "gTrainerFrontPic")
    out = {}
    for pic, symbol in re.findall(r"TRAINER_SPRITE\((\w+),\s*(gTrainerFrontPic_\w+)", pic_table):
        path = symbol_path.get(symbol)
        if path:
            out[pic] = re.sub(r"\.4bpp\.lz$", ".png", path)
    return out


def mon_pic_paths(pic_table: str, gfx: str) -> dict[str, str]:
    """SPECIES_<NAME> -> its front-pic PNG in the pret tree.

    Same two-hop shape as the trainer pics: the table names a symbol, the
    graphics header says which file that symbol was INCBIN'd from.
    """
    symbol_path = _incbin(gfx, "gMonFrontPic")
    out = {}
    for name, symbol in re.findall(r"SPECIES_SPRITE\((\w+),\s*(gMonFrontPic_\w+)", pic_table):
        path = symbol_path.get(symbol)
        if path:
            out[f"SPECIES_{name}"] = re.sub(r"\.4bpp\.lz$", ".png", path)
    return out


def mon_sprite(raw: bytes, out: Path) -> None:
    """Save a mon's front pic KEEPING pret's 4bpp palette, index 0 transparent.

    Not `sprite()`'s RGBA: a mon pic is 16 colours, and flattening 410 of them
    to RGBA tripled the set to 1.6 MB for no visible difference. A PNG
    `transparency` chunk pointing at index 0 says the same thing in 610 bytes.
    """
    img = Image.open(__import__("io").BytesIO(raw))
    if img.mode != "P":
        img.convert("RGBA").save(out, optimize=True)
        return
    img.save(out, optimize=True, transparency=0)


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
    ap.add_argument("--game", default="firered-us", choices=sorted(ROSTERS))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    roster = ROSTERS[args.game]
    pret = GAMES[roster.game]
    out_dir = args.out or roster.out
    ref = pret.sha
    rd = lambda p: text(p, offline=args.offline, game=pret)  # noqa: E731

    sp_ids = species_ids(rd(SPECIES_IDS))
    ids = trainer_ids(rd(OPPONENTS))
    by_key = trainers(rd(TRAINERS))
    party_of = parties(rd(PARTIES))
    species = species_names(rd(SPECIES_NAMES))
    pics = pic_paths(rd(PIC_TABLE), rd(GFX))
    key_of_id = {v: k for k, v in ids.items()}

    out_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    wanted_pics: set[str] = set()
    missing: list[int] = []
    # The referee's thirteen where there is a referee; the whole ROM otherwise.
    # A missing id is an ERROR in the first case (the ladder names somebody the
    # ROM does not) and impossible in the second, which is why the check below
    # stays rather than being softened to cover both.
    wanted = sorted(TRAINER_NAMES) if roster.referee_labels else sorted(key_of_id)
    for tid in wanted:
        key = key_of_id.get(tid)
        t = by_key.get(key) if key else None
        if t is None:
            missing.append(tid)
            continue
        party = [{"species": species.get(m["species"], m["species"].removeprefix("SPECIES_").title()),
                   "level": m["level"],
                   # the id too, because the card draws the mon's own sprite and
                   # the file is named by id — the name would need a second map
                   "id": sp_ids.get(m["species"])}
                  for m in party_of.get(t["party"], [])]
        pic = (t["pic"] or "").lower()
        if t["pic"] in pics:
            wanted_pics.add(t["pic"])
        cls = (t["class"] or "").replace("_", " ").title()
        given = t["name"].title()
        index[str(tid)] = {
            # The referee's own label where one exists — it is what every other
            # surface shows, so a card that said something else would look like
            # a different fight. Otherwise the ROM's own class and given name,
            # which is how "Lass Tiana" gets onto an Emerald card at all.
            "label": TRAINER_NAMES[tid] if roster.referee_labels else f"{cls} {given}".strip(),
            "name": given,
            "class": cls,
            "pic": f"{pic}.png" if t["pic"] in pics else None,
            "party": party,
        }

    for pic in sorted(wanted_pics):
        raw = fetch(pics[pic], offline=args.offline, game=pret)
        sprite(raw).save(out_dir / f"{pic.lower()}.png", optimize=True)

    by_id = {str(i): species[key] for key, i in sp_ids.items() if key in species and i > 0}

    # Every mon's front pic, named by species id (Andreas, 2026-09-16: "for the
    # battle I would like Pokemon sprites on the hover, for both trainer Pokemon
    # and wild Pokemon"). All of them rather than only the ones a first-badge
    # run can meet: it is 250 KB in the repo, a viewer downloads only the one
    # they hover, and a sprite that is missing only for an unusual encounter is
    # the kind of gap nobody finds until it is on the public site.
    #
    # NOT per game, unlike the trainers beside it: a species id is the National
    # Dex number on every cartridge we run, so one set serves all seven and a
    # per-game copy would be the same pixels seven times.
    mon_dir = MON_DIR
    if not roster.mon_sprites:
        print(f"species sprites: not from {roster.game} — {MON_DIR.name}/ is the shared "
              f"gen-3 set, sourced from firered-us (see Roster.mon_sprites)")
        have = len(list(mon_dir.glob("*.png"))) if mon_dir.is_dir() else 0
        if not have:
            raise SystemExit(
                f"{mon_dir} is empty — run this with --game firered-us first, or the "
                "cards draw broken images for every Pokemon on every cartridge")
        print(f"{have} already there")
        (out_dir / "index.json").write_text(json.dumps(
            {"version": 3, "game": roster.game, "pret_sha": ref,
             "trainers": index, "species": by_id}, indent=1) + "\n")
        print(f"{roster.game}: {len(index)} trainers, {len(wanted_pics)} sprites, "
              f"{(out_dir / 'index.json').stat().st_size // 1024} KB index")
        return 0
    mon_dir.mkdir(parents=True, exist_ok=True)
    mon_pics = mon_pic_paths(rd(MON_PIC_TABLE), rd(MON_GFX))
    drawn = 0
    no_pic: list[str] = []
    for key, i in sorted(sp_ids.items(), key=lambda kv: kv[1]):
        path = mon_pics.get(key)
        if not path or i <= 0 or str(i) not in by_id:
            continue
        out = mon_dir / f"{i}.png"
        if not out.exists():
            try:
                raw = fetch(path, offline=args.offline, game=pret)
            except SystemExit:
                # Castform is the one species whose .4bpp.lz is BUILT from
                # several PNGs, one per weather form, so the symbol's path has
                # no file of its own — `castform/front.png` does not exist and
                # `castform/normal/front.png` does. Worth chasing rather than
                # tolerating: an Emerald trainer carries a Castform, and the
                # card would have drawn a broken image for it.
                alt = re.sub(r"/([^/]+)\.png$", r"/normal/\1.png", path)
                try:
                    raw = fetch(alt, offline=args.offline, game=pret)
                except SystemExit:
                    # Named rather than silently dropped: a missing sprite
                    # should be a line here, not a broken image on the site.
                    no_pic.append(key.removeprefix("SPECIES_").title())
                    continue
            mon_sprite(raw, out)
        drawn += 1
    if no_pic:
        print(f"no front pic at the expected path for {len(no_pic)}: {', '.join(no_pic)}")

    (out_dir / "index.json").write_text(json.dumps(
        {"version": 3, "game": roster.game, "pret_sha": ref,
         "trainers": index, "species": by_id}, indent=1) + "\n")
    print(f"{drawn} Pokemon sprites in {mon_dir}")
    if missing:
        raise SystemExit(f"no pret entry for trainer ids {missing} — the referee names them but the ROM does not")
    print(f"{roster.game}: {len(index)} trainers, {len(wanted_pics)} sprites, "
          f"{(out_dir / 'index.json').stat().st_size // 1024} KB index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
