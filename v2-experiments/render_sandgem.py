#!/usr/bin/env python3
"""The HERO image: one Platinum map under the game's own field camera.

The feasibility spike that proved gen-4 3D artwork was readable in pure Python
lives on as this one script. Everything it discovered is now in `scripts/ds3d/`
and shipped by `scripts/render_dsmaps.py`; what stays here is the camera the
map tier deliberately does NOT use.

Two views of the same scene:
  A) straight-down orthographic — what the atlas ships, 16 px per tile
  B) the game's own field camera: pitch -59.0515 deg, half-FOV 8.0914 deg,
     distance 666.92 (CAMERA_TYPE_DEFAULT, which MAP_HEADER_SANDGEM_TOWN uses)

B is a better picture and a worse map: it z-buffers correctly, so a building
hides the route behind it. That is why the atlas is A.

    ./venv/bin/python v2-experiments/render_sandgem.py [map_id]
"""
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import render_dsmaps as R                                    # noqa: E402
from ds3d import field, scene                                # noqa: E402

SS = 3


def build_scene(d, assets, map_id, with_props=True):
    """Every chunk of one map, in the map's own frame."""
    win = R.map_window(d, map_id)
    area = field.area_of(assets, d.meta[win.header]['areaDataArchiveID'])
    matrix = d.matrix_of(win.header)
    alts = matrix.get('altitudes')
    sc = scene.Scene()
    stats = []
    for col, row in sorted(win.cells):
        land = matrix['maps'][row][col]
        n = land.split('_')[-1]
        if not n.isdigit():
            continue
        raw = R.fetch(f'res/field/maps/data/map_data_{int(n):03d}.bin', offline=d.offline)
        st = field.add_chunk(sc, assets, raw, area, with_props=with_props,
                             origin=field.chunk_origin(col, row, win.c0, win.r0,
                                                       alts[row][col] if alts else 0))
        st.land = land
        stats.append(st)
    return win, sc, stats


def main(map_id=418):
    out_dir = os.path.join(ROOT, 'artifacts', 'ds-3d-spike')
    os.makedirs(out_dir, exist_ok=True)
    d = R.Decomp(offline=False)
    assets = field.Assets(lambda p: R.fetch(p, offline=d.offline),
                          lambda p: R.fetch_json(p, offline=d.offline))
    win, sc, stats = build_scene(d, assets, map_id)
    for st in stats:
        print(f'  {st.land}: {st.terrain_tris}/{st.declared_tris} terrain tris, '
              f'{st.props_drawn}/{st.props} props')

    ortho = field.ortho_pixels(sc, win.cw * R.CELL, win.ch * R.CELL, 16, SS)
    Image.fromarray(ortho, 'RGBA').save(os.path.join(out_dir, f'{win.name.lower()}-ortho.png'))

    # The field camera frames ONE chunk, so a multi-chunk map is centred on its
    # own middle rather than stretched to fit.
    w, h = 620, 560
    cx = win.cw * scene.SPAN / 2
    cz = win.ch * scene.SPAN / 2
    centred = scene.Scene()
    centred.tris = [(v - np.array([cx, 0.0, cz]), uv, t, wr) for v, uv, t, wr in sc.tris]
    fb = scene.render(centred, scene.game_cam_projector(w * SS, h * SS, SS), w * SS, h * SS,
                      bg=(126, 178, 118, 255))
    Image.fromarray(scene.downsample(fb, SS), 'RGBA').save(
        os.path.join(out_dir, f'{win.name.lower()}-gamecam.png'))
    print('wrote', out_dir)


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 418)
