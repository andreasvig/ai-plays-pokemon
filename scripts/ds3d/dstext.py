"""The text archives of the DS Pokémon cartridges: the ciphers and the table.

Gen 4 (Diamond/Pearl/Platinum, HeartGold/SoulSilver) and Gen 5 (Black/White,
Black 2/White 2) both store their UI strings in NARC archives of "message
files", and both obfuscate them. Neither is UTF-16 on disk and neither can be
read with `strings`. This module is the whole of that: give it one message
file's bytes and it gives you the strings.

It sits beside `nitrofs.py` and `nitro.py` deliberately — the readers that get
you the bytes — and it is NOT map-specific. `scripts/extract_ds_mapnames.py` is
one caller; Platinum's trainer names (`msgdata/pl_msg.narc` file 618, which
this module decodes today: `Tristan`, `Logan`, `Natalie`, ...) and its class
names are another. Nothing here knows what a map is.

WHAT IS HERE, AND WHAT IS NOT
  - `gen4_text(buf)` / `gen5_text(buf)` -> `list[str]`, one per message.
  - `gen4_codes(buf)` -> the raw per-message code units, for a caller that
    wants the table's own numbers rather than characters.
  - `GEN4_CHARMAP` -> the code-unit table, and `gen4_render(codes)`.
  - `text_file(buf)` -> either, by sniffing the header.
  - `find_text_file(subfiles, must_contain)` -> which subfile of a NARC holds
    a named set of strings, refusing 0 or 2 matches rather than guessing.
  Not here: which archive in which ROM holds what. That is the caller's job,
  and it is the part that is actual research.

GEN 4: TWO XOR STREAMS AND A PRIVATE CHARACTER TABLE
The file is `u16 count`, `u16 seed`, then `count` pairs of `u32 offset`,
`u32 length` — and those pairs are enciphered too, which is why an offset table
read naively points nowhere:

    key  = (seed * (i + 1) * 0x2FD) & 0xFFFF      # i = message index
    key  = key | (key << 16)                      # the same halfword twice
    offset, length = raw_offset ^ key, raw_length ^ key    # length in u16s

Each message's code units then carry their own stream, seeded off the index
again and stepped by a constant, an LCG in all but name:

    k = (0x91BD3 * (i + 1)) & 0xFFFF
    for each unit:  unit ^= k;  k = (k + 0x493D) & 0xFFFF

What comes out is NOT Unicode. It is a game-private table; this module's
`GEN4_CHARMAP` was RECOVERED from the cartridge, not copied from anywhere, and
each block below says what pinned it:

    0x121-0x12A  '0'-'9'    Platinum's height file reads "  2'04"" for #001,
    0x12B-0x144  'A'-'Z'    and its weight file "  15.2 lbs." — Bulbasaur's
    0x145-0x15E  'a'-'z'    real numbers. Species #001 is `BULBASAUR`, whose
                            repeated B/U/A fix the offsets three times over.
    0x188 'é'  0x1AB '!'  0x1AC '?'  0x1AD ','  0x1AE '.'  0x1B3 '’'
    0x1B4 '“'  0x1B5 '”'  0x1BB '♂'  0x1BC '♀'  0x1BE '-'  0x1C3 '♪'
    0x1C4 ':'  0x1C5 ';'  0x1DE ' '  0x1E2 ' ' (the wide space)
                            Each read off a sentence that can only be one
                            thing: "Sand-Attack", "W-what?!", "Don't", the two
                            NIDORAN, "PLAYER: ", "Heads, ... right; tails".

  A code with no entry renders as `{1234}` rather than as a plausible wrong
  letter. That is on purpose: a silent substitution is exactly the failure this
  whole module is trying not to have.

  Two in-band controls are handled rather than mapped. `0xE000` is a line
  break. `0xFFFE` opens a variable — `0xFFFE, u16 id, u16 argc, argc x u16` —
  and renders as `{VAR:id}`, so a caller can see a substitution slot instead of
  silently losing the arguments as characters. `0x25BC`/`0x25BD` are the
  wait-for-input markers and render as a newline. `0xFFFF` ends the message.

  COMPRESSED MESSAGES. A message whose first unit is `0xF100` is bit-packed:
  the units after it carry FIFTEEN usable bits each (the top bit is always
  clear), concatenated low-bits-first into a stream of NINE-bit codes, ending
  at `0x1FF`. Platinum's trainer-name file is stored this way and is unreadable
  without it. The 15 is measured, not assumed — at 16 the very first name
  decodes as `T` then noise, at 15 it decodes as `Tristan`.

GEN 5: A ROTATING KEY AND ORDINARY UNICODE
Different container, different cipher, same job:

    u16 sections, u16 entries, u32 size (excluding this 16-byte header),
    u32 0, u32 section_offsets[sections]
    at a section: u32 section_size, then per entry
                  u32 offset (from the section), u16 units, u16 flags

    key = (0x7C89 + 0x2983 * i) & 0xFFFF          # i = entry index
    for each unit:  unit ^= key;  key = rotate_left_16(key, 3)

The constants were found by brute force over all 65536 starting keys on Black's
first message file and then read off the arithmetic: the first three entries
want 0x7C89, 0xA60C, 0xCF8F, which differ by 0x2983 exactly. Decrypted units
ARE Unicode code points for Latin text, so no table is needed; `0xFFFE` is a
line break, `0xFFFF` ends the message, and `0xF000`-range units are variable
markers left in place.
"""

from __future__ import annotations

import struct

# ---------------------------------------------------------------- gen 4

GEN4_TERMINATOR = 0xFFFF
GEN4_VARIABLE = 0xFFFE
GEN4_LINE_BREAK = 0xE000
GEN4_COMPRESSED = 0xF100
GEN4_PROMPTS = (0x25BC, 0x25BD)

_GEN4_OFFSET_MUL = 0x2FD
_GEN4_TEXT_SEED = 0x91BD3
_GEN4_TEXT_STEP = 0x493D


def _gen4_charmap() -> dict[int, str]:
    table: dict[int, str] = {}
    for n in range(10):
        table[0x121 + n] = chr(ord("0") + n)
    for n in range(26):
        table[0x12B + n] = chr(ord("A") + n)
        table[0x145 + n] = chr(ord("a") + n)
    table.update({
        0x188: "é",
        0x1AB: "!", 0x1AC: "?", 0x1AD: ",", 0x1AE: ".",
        0x1B3: "’", 0x1B4: "“", 0x1B5: "”",
        0x1BB: "♂", 0x1BC: "♀",
        0x1BE: "-", 0x1C3: "♪", 0x1C4: ":", 0x1C5: ";",
        0x1DE: " ", 0x1E2: " ",
    })
    return table


GEN4_CHARMAP = _gen4_charmap()


def is_gen4_text(buf: bytes) -> bool:
    """Cheap structural sniff: does the enciphered offset table land inside?"""
    if len(buf) < 4:
        return False
    count, seed = struct.unpack_from("<HH", buf, 0)
    if count == 0 or 4 + 8 * count > len(buf):
        return False
    for i in range(min(count, 8)):
        off, size = _gen4_entry(buf, i, seed)
        if off < 4 + 8 * count or size < 0 or off + 2 * size > len(buf):
            return False
    return True


def _gen4_entry(buf: bytes, i: int, seed: int) -> tuple[int, int]:
    key = (seed * (i + 1) * _GEN4_OFFSET_MUL) & 0xFFFF
    key |= key << 16
    off, size = struct.unpack_from("<II", buf, 4 + 8 * i)
    return off ^ key, size ^ key


def gen4_codes(buf: bytes) -> list[list[int]]:
    """Every message's code units, deciphered and uncompressed, terminator cut.

    Raises `ValueError` rather than returning junk when the file is not a
    Gen 4 message file — a wrong archive index has to be loud.
    """
    if len(buf) < 4:
        raise ValueError("too short to be a Gen 4 text file")
    count, seed = struct.unpack_from("<HH", buf, 0)
    out: list[list[int]] = []
    for i in range(count):
        off, size = _gen4_entry(buf, i, seed)
        if off < 4 + 8 * count or size < 0 or off + 2 * size > len(buf):
            raise ValueError(f"message {i}: offset {off} size {size} outside {len(buf)} bytes")
        raw = struct.unpack_from(f"<{size}H", buf, off)
        key = (_GEN4_TEXT_SEED * (i + 1)) & 0xFFFF
        units = []
        for unit in raw:
            units.append(unit ^ key)
            key = (key + _GEN4_TEXT_STEP) & 0xFFFF
        if units and units[0] == GEN4_COMPRESSED:
            units = _gen4_unpack(units)
        elif units and units[-1] == GEN4_TERMINATOR:
            units = units[:-1]
        out.append(units)
    return out


def _gen4_unpack(units: list[int]) -> list[int]:
    """The `0xF100` form: 9-bit codes inside 15-bit containers, low bits first."""
    out: list[int] = []
    bits = held = 0
    for unit in units[1:]:
        bits |= (unit & 0x7FFF) << held
        held += 15
        while held >= 9:
            code = bits & 0x1FF
            bits >>= 9
            held -= 9
            if code == 0x1FF:
                return out
            out.append(code)
    return out


def gen4_render(codes: list[int]) -> str:
    """Code units -> text. An unmapped code shows as `{n}`, never as a guess."""
    out: list[str] = []
    i = 0
    while i < len(codes):
        code = codes[i]
        i += 1
        if code == GEN4_TERMINATOR:
            break
        if code == GEN4_LINE_BREAK or code in GEN4_PROMPTS:
            out.append("\n")
            continue
        if code == GEN4_VARIABLE:
            if i + 1 < len(codes):
                var_id, argc = codes[i], codes[i + 1]
                i += 2 + argc
                out.append(f"{{VAR:{var_id}}}")
            else:
                out.append("{VAR}")
            continue
        out.append(GEN4_CHARMAP.get(code, "{%d}" % code))
    return "".join(out)


def gen4_text(buf: bytes) -> list[str]:
    """One Gen 4 message file -> its strings."""
    return [gen4_render(c) for c in gen4_codes(buf)]


# ---------------------------------------------------------------- gen 5

GEN5_KEY_BASE = 0x7C89
GEN5_KEY_ADVANCE = 0x2983
GEN5_TERMINATOR = 0xFFFF
GEN5_LINE_BREAK = 0xFFFE


def _rotate_left(value: int, n: int = 3) -> int:
    return ((value << n) | (value >> (16 - n))) & 0xFFFF


def is_gen5_text(buf: bytes) -> bool:
    if len(buf) < 16:
        return False
    sections, entries = struct.unpack_from("<HH", buf, 0)
    size, zero = struct.unpack_from("<II", buf, 4)
    return (1 <= sections <= 16 and entries >= 1 and zero == 0
            and size + 16 == len(buf) and 12 + 4 * sections <= len(buf))


def gen5_text(buf: bytes, section: int = 0) -> list[str]:
    """One Gen 5 message file's section -> its strings."""
    if not is_gen5_text(buf):
        raise ValueError("not a Gen 5 text file")
    sections, entries = struct.unpack_from("<HH", buf, 0)
    if not 0 <= section < sections:
        raise ValueError(f"section {section} of {sections}")
    base = struct.unpack_from("<I", buf, 12 + 4 * section)[0]
    out: list[str] = []
    for i in range(entries):
        off, units, _flags = struct.unpack_from("<IHH", buf, base + 4 + 8 * i)
        raw = struct.unpack_from(f"<{units}H", buf, base + off)
        key = (GEN5_KEY_BASE + GEN5_KEY_ADVANCE * i) & 0xFFFF
        chars: list[str] = []
        for unit in raw:
            value = unit ^ key
            key = _rotate_left(key)
            if value == GEN5_TERMINATOR:
                break
            chars.append("\n" if value == GEN5_LINE_BREAK else chr(value))
        out.append("".join(chars))
    return out


# ---------------------------------------------------------------- either

def text_file(buf: bytes) -> list[str]:
    """Decode a message file of either generation, sniffed from its header."""
    if is_gen5_text(buf):
        return gen5_text(buf)
    if is_gen4_text(buf):
        return gen4_text(buf)
    raise ValueError("not a Gen 4 or Gen 5 text file")


def find_text_file(subfiles: list[bytes], must_contain: set[str]) -> tuple[int, list[str]]:
    """The one subfile of a NARC whose strings include every named string.

    Refuses on none and on more than one. Locating an archive by a set of
    strings the caller already knows is the point: a hard-coded index is right
    for one cartridge and silently wrong for the next, and a table located by
    coincidence gives every map a real name from the right game and the wrong
    place.
    """
    hits: list[tuple[int, list[str]]] = []
    for i, blob in enumerate(subfiles):
        try:
            strings = text_file(blob)
        except Exception:
            continue
        if must_contain <= {s.strip() for s in strings}:
            hits.append((i, strings))
    if len(hits) != 1:
        raise SystemExit(
            f"looking for a text file containing {sorted(must_contain)}: "
            f"found {len(hits)} of them ({[i for i, _ in hits]}), refusing to guess"
        )
    return hits[0]
