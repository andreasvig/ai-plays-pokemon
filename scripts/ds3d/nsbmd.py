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

    # -- the material struct ------------------------------------------------
    #
    # Offsets into `NNSG3dResMtl`, and the first two are ONE word between them:
    #
    #   0x00 u16 dummy            always 0
    #   0x02 u16 size             the struct's own length: 0x2C, or more when
    #                             it carries a texture matrix
    #   0x04 u32 diffuse_ambient  <- the flat colour an untextured face draws
    #   0x08 u32 specular_emission
    #   0x0C u32 polygon_attr
    #   0x10 u32 polygon_attr_mask
    #   0x14 u32 texture_params   <- the repeat/flip bits, and ONLY those
    #   0x18 u32 texture_params_mask
    #   0x1C u16 palette_base, u16 flags
    #   0x20 u16 orig_width, u16 orig_height
    #
    # Reading `dummy` and `size` as two WORDS rather than two halfwords put
    # every field four bytes late, and a second four came from anchoring the
    # struct at the name dictionary rather than at the material section — so
    # the diffuse colour was really the polygon attribute and the texture
    # parameters were really the palette base. Neither raises: a wrong colour
    # is a colour and a wrong parameter word is still 32 bits.
    #
    # The oracle that settles it is `orig_width`/`orig_height`: the material
    # states the size of the texture it binds, and the NSBTX states it too.
    # Over every model the four rendered gen-4 games reach, 514 of 514 agree at
    # this base and 0 of 514 agree four bytes later (`tests/test_dsmaps.py`).
    MAT_DIFFUSE = 0x04
    MAT_POLY_ATTR = 0x0C
    MAT_TEXPARAMS = 0x14
    MAT_ORIG_SIZE = 0x20

    def _material_at(self, idx):
        _, _, item = self.materials[idx]
        return self.off + self.mat_off + u32(item, 0)

    def material_texparams(self, idx):
        """The material's own `TEXIMAGE_PARAM`, which carries the WRAP MODE.

        A texture's NSBTX entry declares where it is, how big it is and what
        format it is in; whether it REPEATS is a property of the material that
        binds it, and the two live in different files. Read from the NSBTX the
        repeat bits are always clear, every UV outside the first tile clamps to
        the edge texel, and a tree row whose canopy tiles across a whole chunk
        becomes one flat ribbon of the darkest colour in its texture, running
        edge to edge. 495 of the 514 materials above ask for repeat in both
        directions; ten genuinely want the clamp.
        """
        return struct.unpack_from('<I', self.data,
                                  self._material_at(idx) + self.MAT_TEXPARAMS)[0]

    def material_alpha(self, idx) -> int:
        """`POLYGON_ATTR` bits 16-20: 31 opaque, 1-30 translucent, 0 wireframe.

        A material can be see-through with a texture that is not: a building's
        drop shadow on Gen 4 is an OPAQUE black texture drawn at alpha 9/31,
        and a renderer that reads only the texels paints a black slab beside
        every house. 134 of the 630 materials the four rendered gen-4 maps
        touch are translucent this way, and none of them is translucent in its
        texture.
        """
        pa = struct.unpack_from('<I', self.data,
                                self._material_at(idx) + self.MAT_POLY_ATTR)[0]
        return (pa >> 16) & 0x1F

    def material_orig_size(self, idx):
        """`(width, height)` of the texture this material expects to bind."""
        return struct.unpack_from('<2H', self.data,
                                  self._material_at(idx) + self.MAT_ORIG_SIZE)

    def material_diffuse(self, idx):
        """The material's own diffuse colour, as RGB 0-255.

        What an UNTEXTURED material is drawn with. Gen 4 interiors use them for
        flat surfaces — a lab floor, the shading under a staircase — and a
        renderer that only knows how to draw textures drops those polygons and
        leaves a black hole in the middle of the room.
        """
        v = struct.unpack_from('<I', self.data,
                               self._material_at(idx) + self.MAT_DIFFUSE)[0] & 0x7FFF
        return ((v & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31, ((v >> 10) & 31) * 255 // 31)


def load_models(data, base=0):
    c = read_container(data, base)
    off, _ = c.blocks['MDL0']
    out = []
    for name, _, item in read_dict(data, off + 8):
        out.append(Model(data, off + u32(item, 0), name))
    return out, c
