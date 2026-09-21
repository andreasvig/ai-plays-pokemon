"""NSBTX / TEX0 texture + palette reader -> RGBA numpy images."""
import struct
import numpy as np
from .nitro import read_container, read_dict, u8, u16, u32

FMT_NAMES = {0: 'none', 1: 'a3i5', 2: 'pal4', 3: 'pal16', 4: 'pal256',
             5: 'comp4x4', 6: 'a5i3', 7: 'direct'}


def _rgb555(v):
    r = (v & 31) * 255 // 31
    g = ((v >> 5) & 31) * 255 // 31
    b = ((v >> 10) & 31) * 255 // 31
    return r, g, b


class TextureSet:
    def __init__(self, data, base=0):
        c = read_container(data, base)
        t = c.blocks['TEX0'][0]
        self.data = data
        self.t = t
        self.tex_data = t + u32(data, t + 0x14)
        self.pal_data = t + u32(data, t + 0x38)
        self.comp_data = t + u32(data, t + 0x24)
        self.comp_info = t + u32(data, t + 0x28)
        self.textures = {n: item for n, _, item in read_dict(data, t + u16(data, t + 0x0E))}
        self.palettes = {n: item for n, _, item in read_dict(data, t + u32(data, t + 0x34))}

    def tex_params(self, name):
        return u32(self.textures[name], 0)

    def image(self, tex_name, pal_name):
        """Return (H, W, 4) uint8 RGBA."""
        d = self.data
        p = u32(self.textures[tex_name], 0)
        ofs = (p & 0xFFFF) << 3
        w = 8 << ((p >> 20) & 7)
        h = 8 << ((p >> 23) & 7)
        fmt = (p >> 26) & 7
        col0_transparent = (p >> 29) & 1
        pal_off = (u16(self.palettes[pal_name], 0) << 3) if pal_name in self.palettes else 0
        base = self.tex_data + ofs
        out = np.zeros((h, w, 4), np.uint8)

        def pal(i):
            # A palette is sized by its own entry count, which the texture does
            # not state: an a3i5 texture asks for 32 colours and a 4-colour
            # palette at the very end of the file has 4. Past the end is black
            # rather than an exception — those indices are never sampled, and
            # raising would drop a model that draws correctly.
            at = self.pal_data + pal_off + 2 * i
            return _rgb555(u16(d, at)) if at + 2 <= len(d) else (0, 0, 0)

        n = w * h
        if fmt == 3:      # 16-colour, 4bpp
            raw = np.frombuffer(d[base:base + n // 2], np.uint8)
            idx = np.empty(n, np.uint8)
            idx[0::2] = raw & 0xF
            idx[1::2] = raw >> 4
        elif fmt == 4:    # 256-colour, 8bpp
            idx = np.frombuffer(d[base:base + n], np.uint8).copy()
        elif fmt == 2:    # 4-colour, 2bpp
            raw = np.frombuffer(d[base:base + n // 4], np.uint8)
            idx = np.empty(n, np.uint8)
            for k in range(4):
                idx[k::4] = (raw >> (2 * k)) & 3
        elif fmt in (1, 6):  # a3i5 / a5i3
            # THE WIDENING IS THE WHOLE POINT. These are the two formats whose
            # alpha lives in the texel, and the scale to 0-255 is a multiply by
            # 255 — which overflows uint8 and wraps. Left in `raw`'s dtype,
            # EVERY a5i3 texel came out at 7 or 8 and every a3i5 one at 35 or
            # 36, whatever the cartridge said:
            #
            #   a5i3 index 23 -> (23*255) & 0xFF = 233, //31 = 7   (true 189)
            #   a5i3 index 31 -> (31*255) & 0xFF = 225, //31 = 7   (true 255)
            #   a3i5 index  6 -> ( 6*255) & 0xFF = 250, // 7 = 35  (true 218)
            #
            # A fully OPAQUE a5i3 texel therefore arrived at alpha 7, under
            # `raster.draw_tri`'s `> 8` test, and was thrown away: Lake Verity's
            # `l_lake` is a5i3 and its water did not draw at all, on either of
            # its maps. The 36 that this page's notes called "the pond's own
            # alpha" was this wrap, not the artwork.
            raw = np.frombuffer(d[base:base + n], np.uint8).astype(np.uint16)
            if fmt == 1:
                idx = (raw & 0x1F).astype(np.uint8)
                alpha = (((raw >> 5) & 7) * 255 // 7).astype(np.uint8)
            else:
                idx = (raw & 0x07).astype(np.uint8)
                alpha = (((raw >> 3) & 0x1F) * 255 // 31).astype(np.uint8)
            lut = np.array([pal(i) for i in range(32 if fmt == 1 else 8)], np.uint8)
            out[..., :3] = lut[idx].reshape(h, w, 3)
            out[..., 3] = alpha.reshape(h, w)
            return out
        elif fmt == 7:    # direct 16-bit
            raw = np.frombuffer(d[base:base + 2 * n], '<u2').copy()
            r = ((raw & 31) * 255 // 31).astype(np.uint8)
            g = (((raw >> 5) & 31) * 255 // 31).astype(np.uint8)
            b = (((raw >> 10) & 31) * 255 // 31).astype(np.uint8)
            a = np.where(raw >> 15, 255, 0).astype(np.uint8)
            out[..., 0] = r.reshape(h, w); out[..., 1] = g.reshape(h, w)
            out[..., 2] = b.reshape(h, w); out[..., 3] = a.reshape(h, w)
            return out
        elif fmt == 5:    # 4x4 block compressed
            return self._comp4x4(base, ofs, w, h, pal_off)
        else:
            out[..., 3] = 255
            return out

        ncol = int(idx.max()) + 1 if n else 1
        lut = np.array([pal(i) for i in range(max(ncol, 1))], np.uint8)
        out[..., :3] = lut[idx].reshape(h, w, 3)
        out[..., 3] = 255
        if col0_transparent:
            out[..., 3] = np.where(idx.reshape(h, w) == 0, 0, 255)
        return out

    def _comp4x4(self, base, ofs, w, h, pal_off):
        d = self.data
        out = np.zeros((h, w, 4), np.uint8)
        nblk = (w // 4) * (h // 4)
        # extra palette-index data lives in a parallel stream
        extra_base = self.comp_info + (ofs >> 1)
        for b in range(nblk):
            tile = u32(d, base + 4 * b)
            ex = u16(d, extra_base + 2 * b)
            pal_base = self.pal_data + pal_off + ((ex & 0x3FFF) << 2)
            mode = (ex >> 14) & 3
            cols = []
            for i in range(4):
                cols.append(_rgb555(u16(d, pal_base + 2 * i)) if pal_base + 2 * i + 1 < len(d) else (0, 0, 0))
            c0, c1 = cols[0], cols[1]
            if mode == 0:
                pal4 = [c0, c1, cols[2], (0, 0, 0)]
                alpha = [255, 255, 255, 0]
            elif mode == 1:
                mid = tuple((a + bb) // 2 for a, bb in zip(c0, c1))
                pal4 = [c0, c1, mid, (0, 0, 0)]
                alpha = [255, 255, 255, 0]
            elif mode == 2:
                pal4 = [c0, c1, cols[2], cols[3]]
                alpha = [255, 255, 255, 255]
            else:
                p2 = tuple((5 * a + 3 * bb) // 8 for a, bb in zip(c0, c1))
                p3 = tuple((3 * a + 5 * bb) // 8 for a, bb in zip(c0, c1))
                pal4 = [c0, c1, p2, p3]
                alpha = [255, 255, 255, 255]
            bx = (b % (w // 4)) * 4
            by = (b // (w // 4)) * 4
            for yy in range(4):
                for xx in range(4):
                    k = (tile >> (2 * (yy * 4 + xx))) & 3
                    out[by + yy, bx + xx, :3] = pal4[k]
                    out[by + yy, bx + xx, 3] = alpha[k]
        return out
