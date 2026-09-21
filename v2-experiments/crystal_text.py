"""Read Crystal's on-screen TEXT out of WRAM, so a battle's own words are an oracle.

Gen 2's text engine writes CHARACTER CODES straight into ``wTilemap`` (0xc4a0,
20x18) and the font tiles are laid out so the code IS the tile index. So the
screen's words are readable without OCR and without a picture — which matters
here because the question "was this a WILD battle or a TRAINER's" is answered by
a wording ("Wild ZUBAT appeared!" vs "<name> wants to battle!") and nothing else
on the screen says it.

The decoder is checked against pictures before it is trusted: three savepoints
whose text was read off the rendered PNG by eye must come back byte for byte.
"""
from __future__ import annotations

TILEMAP = 0xC4A0
W, H = 20, 18

_MAP = {0x7F: " ", 0x4E: "\n", 0x50: "", 0x55: "", 0x57: "", 0x58: "",
        0xE0: "'", 0xE1: "P", 0xE2: "M", 0xE3: "-", 0xE6: "?", 0xE7: "!",
        0xE8: ".", 0xE9: "&", 0xEA: "e", 0xEB: "#", 0xEC: "'", 0xF0: "$",
        0xF1: "x", 0xF2: ".", 0xF3: "/", 0xF4: ",", 0xF5: "@"}
for i in range(26):
    _MAP[0x80 + i] = chr(ord("A") + i)
    _MAP[0xA0 + i] = chr(ord("a") + i)
for i in range(10):
    _MAP[0xF6 + i] = chr(ord("0") + i)
_MAP.update({0x9A: "(", 0x9B: ")", 0x9C: ":", 0x9D: ";", 0x9E: "[", 0x9F: "]"})


def rows(blob: bytes) -> list[str]:
    """The 18 screen rows as text. ``blob`` is 360 bytes from ``TILEMAP``."""
    out = []
    for r in range(H):
        line = "".join(_MAP.get(b, "·") for b in blob[r * W:(r + 1) * W])
        out.append(line.rstrip())
    return out


def screen_text(blob: bytes) -> str:
    """One string, blank rows collapsed — what the screen says, for matching."""
    return " ".join(x.strip() for x in rows(blob) if x.strip())
