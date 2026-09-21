"""NSBMD (BMD0/MDL0) model reader."""
import struct
from .nitro import read_container, read_dict, u8, u16, u32, s16


class Model:
    def __init__(self, data, moff, name):
        self.data, self.off, self.name = data, moff, name
        (self.size, self.render_off, self.mat_off,
         self.piece_off, self.inv_off) = struct.unpack_from('<5I', data, moff)
        self.num_objects = u8(data, moff + 23)
        self.num_materials = u8(data, moff + 24)
        self.num_pieces = u8(data, moff + 25)
        self.up_scale = struct.unpack_from('<i', data, moff + 28)[0] / 4096.0
        self.down_scale = struct.unpack_from('<i', data, moff + 32)[0] / 4096.0
        self.num_verts, self.num_surfs, self.num_tris, self.num_quads = \
            struct.unpack_from('<4H', data, moff + 36)
        bb = struct.unpack_from('<6h', data, moff + 44)
        self.bbox = [v / 4096.0 for v in bb]
        self.objects = read_dict(data, moff + 64)
        self.materials = read_dict(data, moff + self.mat_off + 4)
        self.pieces = read_dict(data, moff + self.piece_off)
        self._tex_pair = None
        self._pal_pair = None

    # -- material <-> texture name join -------------------------------------
    def texture_pairs(self):
        """material index -> (texture name, palette name)."""
        d, mo = self.data, self.off + self.mat_off
        tex = read_dict(d, mo + u16(d, mo))
        pal = read_dict(d, mo + u16(d, mo + 2))
        out = {}
        for name, _, item in tex:
            rel, cnt = u16(item, 0), u8(item, 2)
            for k in range(cnt):
                out.setdefault(u8(d, mo + rel + k), [None, None])[0] = name
        for name, _, item in pal:
            rel, cnt = u16(item, 0), u8(item, 2)
            for k in range(cnt):
                out.setdefault(u8(d, mo + rel + k), [None, None])[1] = name
        return out

    # -- render commands ----------------------------------------------------
    # A render command's identity is its low five bits; 0x20/0x40/0x80 are
    # FLAGS that add parameters. `wk_sp1`, the signpost outside New Bark Town,
    # binds its material with 0x24 and 0x44 rather than 0x04 because it has
    # three bones — and a scan that matched the bare opcode drew none of it and
    # said nothing.
    CMD = 0x1F
    BIND, DRAW = 0x04, 0x05

    def bind_draw(self):
        """[(material_index, piece_index)] in draw order."""
        d = self.data
        seg = d[self.off + self.render_off: self.off + self.mat_off]
        pairs, i = [], 0
        while i + 3 < len(seg):
            if (seg[i] & self.CMD) == self.BIND and (seg[i + 2] & self.CMD) == self.DRAW \
               and seg[i + 1] < self.num_materials and seg[i + 3] < self.num_pieces:
                pairs.append((seg[i + 1], seg[i + 3]))
                i += 4
            else:
                i += 1
        return pairs

    def piece_dl(self, idx):
        d = self.data
        _, _, item = self.pieces[idx]
        po = self.off + self.piece_off + u32(item, 0)
        _f0, _f1, dlo, dll = struct.unpack_from('<4I', d, po)
        return d[po + dlo: po + dlo + dll]

    def _material_at(self, idx):
        _, _, item = self.materials[idx]
        # material struct: u32 dummy, u32 size, u32 diffuse_ambient,
        # u32 specular_emission, u32 polygon_attr, u32 polygon_attr_mask,
        # u32 texture_params, ...
        return self.off + self.mat_off + 4 + u32(item, 0)

    def material_teximage(self, idx):
        return struct.unpack_from('<I', self.data, self._material_at(idx) + 24)[0]

    def material_diffuse(self, idx):
        """The material's own diffuse colour, as RGB 0-255.

        What an UNTEXTURED material is drawn with. Gen 4 interiors use them for
        flat surfaces — a lab floor, the shading under a staircase — and a
        renderer that only knows how to draw textures drops those polygons and
        leaves a black hole in the middle of the room.
        """
        v = struct.unpack_from('<I', self.data, self._material_at(idx) + 8)[0] & 0x7FFF
        return ((v & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31, ((v >> 10) & 31) * 255 // 31)


def load_models(data, base=0):
    c = read_container(data, base)
    off, _ = c.blocks['MDL0']
    out = []
    for name, _, item in read_dict(data, off + 8):
        out.append(Model(data, off + u32(item, 0), name))
    return out, c
