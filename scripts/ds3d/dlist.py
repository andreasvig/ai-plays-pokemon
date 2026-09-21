"""NDS GPU display-list interpreter -> triangle soup.

Decodes the packed command stream found in an NSBMD shape (piece) into
lists of triangles with position + texcoord. Only the subset of the geometry
engine that field models actually use is implemented.
"""
import struct

# opcode -> number of 32-bit parameter words
PARAMS = {
    0x00: 0,                                   # NOP
    0x10: 1, 0x11: 0, 0x12: 1, 0x13: 1, 0x14: 1, 0x15: 0,
    0x16: 16, 0x17: 12, 0x18: 16, 0x19: 12, 0x1A: 9,
    0x1B: 3, 0x1C: 3,
    0x20: 1, 0x21: 1, 0x22: 1,
    0x23: 2, 0x24: 1, 0x25: 1, 0x26: 1, 0x27: 1, 0x28: 1,
    0x29: 1, 0x2A: 1, 0x2B: 1,
    0x30: 1, 0x31: 1, 0x32: 1, 0x33: 1, 0x34: 32,
    0x40: 1, 0x41: 0,
    0x50: 1, 0x60: 1, 0x70: 3, 0x71: 2, 0x72: 1,
}


def _s10(v):
    return v - 1024 if v & 0x200 else v


def _s16(v):
    return v - 65536 if v & 0x8000 else v


def decode(dl, scale=1.0):
    """Return (tris, stats).

    tris: list of 3 x (x, y, z, s, t) tuples, in model space (fx-scaled float).
    stats: dict with command histogram.
    """
    tris = []
    stats = {}
    pos = [0.0, 0.0, 0.0]
    st = [0.0, 0.0]
    prim = None
    verts = []          # (x,y,z,s,t) accumulated inside BEGIN/END
    i = 0
    n = len(dl)

    def emit(v):
        verts.append(v)
        k = len(verts)
        if prim == 0:                      # separate triangles
            if k % 3 == 0:
                tris.append(tuple(verts[-3:]))
        elif prim == 1:                    # separate quads
            if k % 4 == 0:
                a, b, c, d = verts[-4:]
                tris.append((a, b, c))
                tris.append((a, c, d))
        elif prim == 2:                    # triangle strip
            if k >= 3:
                a, b, c = verts[-3:]
                if k % 2 == 1:
                    tris.append((a, b, c))
                else:
                    tris.append((b, a, c))
        elif prim == 3:                    # quad strip
            if k >= 4 and k % 2 == 0:
                a, b, c, d = verts[-4:]
                tris.append((a, b, d))
                tris.append((a, d, c))

    while i + 4 <= n:
        ops = dl[i:i + 4]
        i += 4
        for op in ops:
            if op == 0:
                continue
            np = PARAMS.get(op)
            if np is None:
                raise ValueError(f'unknown GPU opcode {op:#x} at {i}')
            args = struct.unpack_from('<%dI' % np, dl, i) if np else ()
            i += 4 * np
            stats[op] = stats.get(op, 0) + 1
            if op == 0x40:                 # BEGIN_VTXS
                prim = args[0] & 3
                verts = []
            elif op == 0x41:               # END_VTXS
                prim = None
            elif op == 0x22:               # TEXCOORD
                p = args[0]
                st = [_s16(p & 0xFFFF) / 16.0, _s16((p >> 16) & 0xFFFF) / 16.0]
            elif op == 0x23:               # VTX_16
                p0, p1 = args
                pos = [_s16(p0 & 0xFFFF) / 4096.0,
                       _s16((p0 >> 16) & 0xFFFF) / 4096.0,
                       _s16(p1 & 0xFFFF) / 4096.0]
                emit((pos[0] * scale, pos[1] * scale, pos[2] * scale, st[0], st[1]))
            elif op == 0x24:               # VTX_10
                p = args[0]
                pos = [_s10(p & 0x3FF) / 64.0,
                       _s10((p >> 10) & 0x3FF) / 64.0,
                       _s10((p >> 20) & 0x3FF) / 64.0]
                emit((pos[0] * scale, pos[1] * scale, pos[2] * scale, st[0], st[1]))
            elif op in (0x25, 0x26, 0x27):  # VTX_XY / VTX_XZ / VTX_YZ
                p = args[0]
                a = _s16(p & 0xFFFF) / 4096.0
                b = _s16((p >> 16) & 0xFFFF) / 4096.0
                if op == 0x25:
                    pos[0], pos[1] = a, b
                elif op == 0x26:
                    pos[0], pos[2] = a, b
                else:
                    pos[1], pos[2] = a, b
                emit((pos[0] * scale, pos[1] * scale, pos[2] * scale, st[0], st[1]))
            elif op == 0x28:               # VTX_DIFF
                p = args[0]
                pos = [pos[0] + _s10(p & 0x3FF) / 4096.0 / 8.0 * 8,
                       pos[1] + _s10((p >> 10) & 0x3FF) / 4096.0 / 8.0 * 8,
                       pos[2] + _s10((p >> 20) & 0x3FF) / 4096.0 / 8.0 * 8]
                emit((pos[0] * scale, pos[1] * scale, pos[2] * scale, st[0], st[1]))
            # colour / normal / matrix / attribute commands: ignored
    return tris, stats
