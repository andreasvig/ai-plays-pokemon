"""Minimal Nitro (NSBMD/NSBTX) container + 3D info-list ("dict") reader.

Pure struct parsing, no dependencies. Feasibility spike only.
"""
import struct
from dataclasses import dataclass, field


def u8(b, o):  return b[o]
def u16(b, o): return struct.unpack_from('<H', b, o)[0]
def s16(b, o): return struct.unpack_from('<h', b, o)[0]
def u32(b, o): return struct.unpack_from('<I', b, o)[0]
def s32(b, o): return struct.unpack_from('<i', b, o)[0]


@dataclass
class Container:
    stamp: str
    version: int
    blocks: dict          # stamp -> (offset, size)
    data: bytes


def read_container(data, base=0):
    stamp = data[base:base + 4].decode('ascii')
    bom = u16(data, base + 4)
    assert bom == 0xFEFF, f'bad BOM {bom:#x}'
    version = u16(data, base + 6)
    filesize = u32(data, base + 8)
    header_size = u16(data, base + 12)
    num_blocks = u16(data, base + 14)
    blocks = {}
    for i in range(num_blocks):
        off = base + u32(data, base + 16 + 4 * i)
        bstamp = data[off:off + 4].decode('ascii', 'replace')
        bsize = u32(data, off + 4)
        blocks[bstamp] = (off, bsize)
    return Container(stamp, version, blocks, data)


def read_dict(data, base, item_size=None):
    """Nitro 3D info list. Returns list of (name, item_offset, item_bytes)."""
    _dummy = u8(data, base)
    n = u8(data, base + 1)
    _section_size = u16(data, base + 2)
    # unknown block
    ub = base + 4
    _header_size = u16(data, ub)          # 8
    _ub_section_size = u16(data, ub + 2)
    _constant = u32(data, ub + 4)         # 0x17F
    unk_end = ub + 8 + 4 * n
    # info block
    ib = unk_end
    isize = u16(data, ib)
    _ib_section = u16(data, ib + 2)
    items_off = ib + 4
    if item_size is None:
        item_size = isize
    names_off = items_off + n * isize
    out = []
    for i in range(n):
        name = data[names_off + 16 * i: names_off + 16 * i + 16]
        name = name.split(b'\0')[0].decode('ascii', 'replace').rstrip()
        io = items_off + i * isize
        out.append((name, io, data[io:io + isize]))
    return out
