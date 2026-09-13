"""Bounded low-poly moss clump prototype; no source subdivision."""
import math


def mesh_data():
    vertices = [(0,0,1)] + [(math.cos(i*math.tau/6)*.5, math.sin(i*math.tau/6)*.5,0) for i in range(6)]
    return vertices, [(0,i+1,(i+1)%6+1) for i in range(6)] + [tuple(range(6,0,-1))]
