"""Assigned world/UV area and sampled occupancy; original UVs stay unchanged."""
import numpy as np
from .snapshots import targets
from .uv_rasterizer import tiles


def measure(ts,sampling=128):
    if sampling not in {64,128,256}:
        raise ValueError('Choose bounded 64/128/256 occupancy sampling')
    occupied=np.zeros((sampling,sampling),np.bool_)
    world_area=uv_area=0.
    outside=0
    with targets(ts) as entries:
        for entry in entries:
            for tri in entry['triangles']:
                xyz=entry['positions'][list(tri.vertices)]
                uv=entry['uv'][list(tri.loops)]
                world_area+=np.linalg.norm(np.cross(xyz[1]-xyz[0],xyz[2]-xyz[0]))*.5
                a,b=uv[1]-uv[0],uv[2]-uv[0]
                uv_area+=abs(a[0]*b[1]-a[1]*b[0])*.5
                outside+=bool(np.any(uv<0) or np.any(uv>1))
                for region,inside,*_ in tiles(uv,sampling): occupied[region]|=inside
    return dict(world_area=float(world_area),uv_area_sum=float(uv_area),
        texels_per_world_unit=float(int(ts.resolution)*np.sqrt(uv_area/max(world_area,1e-12))),
        sampled_occupancy=float(occupied.mean()),sampling=sampling,outside_triangles=outside,
        note='UV area sum includes intentional overlaps; occupancy is a sampled estimate')
