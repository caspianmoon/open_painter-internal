"""Assigned world/UV area and sampled occupancy; original UVs stay unchanged."""
import numpy as np
from .snapshots import targets
from .uv_rasterizer import rasterize


def measure(ts,sampling=128):
    if sampling not in {64,128,256}:
        raise ValueError('Choose bounded 64/128/256 occupancy sampling')
    occupied=np.zeros((sampling,sampling),np.bool_)
    world_area=uv_area=0.
    outside=0
    with targets(ts) as entries:
        for entry in entries:
            # Array form throughout: a per-triangle RNA walk stalled the Measure and
            # Atlas actions for tens of seconds on subdivided assets.
            xyz=entry['positions'][entry['tri_vertices']].astype(np.float64)
            uv=entry['uv'][entry['tri_loops']]
            world_area+=float(np.linalg.norm(np.cross(xyz[:,1]-xyz[:,0],xyz[:,2]-xyz[:,0]),axis=1).sum()*.5)
            a,b=(uv[:,1]-uv[:,0]).astype(np.float64),(uv[:,2]-uv[:,0]).astype(np.float64)
            uv_area+=float(np.abs(a[:,0]*b[:,1]-a[:,1]*b[:,0]).sum()*.5)
            outside+=int(((uv<0)|(uv>1)).any(axis=(1,2)).sum())
            rows,cols,*_=rasterize(uv,sampling,unique=False)
            occupied[rows,cols]=True
    return dict(world_area=float(world_area),uv_area_sum=float(uv_area),
        texels_per_world_unit=float(int(ts.resolution)*np.sqrt(uv_area/max(world_area,1e-12))),
        sampled_occupancy=float(occupied.mean()),sampling=sampling,outside_triangles=outside,
        note='UV area sum includes intentional overlaps; occupancy is a sampled estimate')
