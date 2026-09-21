"""BLZ: the backwards LZ Nintendo compresses an NDS `arm9.bin` with.

Needed for exactly one thing, and it is the thing that unblocked SoulSilver: the
map-header table — which matrix places a map and which artwork set it uses —
lives in the arm9 and nowhere else, and the arm9 in the cartridge is compressed.
An earlier search for that table over the raw ROM found nothing, which was a
true result about the COMPRESSED bytes and said nothing about the table.

The format is Nintendo's, and the only unusual thing about it is that the
compressed stream runs BACKWARDS from the end of the file so the decompressor
can work in place. Footer, at the very end of the file:

    u32 inc_len        how much longer the decompressed file is (0 = not compressed)
    u8  hdr_len        bytes of that footer+header region to skip     (at -5)
    u24 enc_len        length of the compressed region, including hdr_len (at -8)

Everything before `len - enc_len` is stored plain and copied through.
"""
from __future__ import annotations


def is_compressed(data: bytes) -> bool:
    return len(data) >= 8 and int.from_bytes(data[-4:], "little") != 0


def decode(data: bytes) -> bytes:
    """The decompressed file, or `data` unchanged when it is not compressed."""
    if not is_compressed(data):
        return data
    pak_len = len(data)
    inc_len = int.from_bytes(data[-4:], "little")
    hdr_len = data[-5]
    enc_len = int.from_bytes(data[-8:-5], "little")
    dec_len = pak_len + inc_len
    plain = pak_len - enc_len           # the uncompressed prefix, copied as is
    enc_len -= hdr_len

    out = bytearray(dec_len)
    out[:plain] = data[:plain]

    stream = bytearray(data[plain:plain + enc_len])
    stream.reverse()                    # the stream is stored back to front
    produced = bytearray()
    need = dec_len - plain
    at, mask, flags = 0, 0, 0
    while len(produced) < need and at < len(stream):
        if mask == 0:
            flags, at, mask = stream[at], at + 1, 0x80
        if not flags & mask:
            produced.append(stream[at])
            at += 1
        else:
            if at + 1 >= len(stream):
                break
            word = (stream[at] << 8) | stream[at + 1]
            at += 2
            length = (word >> 12) + 3
            back = (word & 0xFFF) + 3
            if back > len(produced):
                raise ValueError("BLZ back-reference before the start of the stream")
            for _ in range(length):
                produced.append(produced[-back])
        mask >>= 1
    if len(produced) < need:
        raise ValueError(f"BLZ stream ran out: {len(produced)} of {need} bytes")
    produced.reverse()
    out[plain:] = produced[len(produced) - need:]
    return bytes(out)
