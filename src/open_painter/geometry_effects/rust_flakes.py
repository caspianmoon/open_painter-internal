"""Small closed flake prototype in local coordinates, instanced by the manager."""


def mesh_data():
    vertices = [(x,y,z) for z in (0,.12) for y in (-.5,.5) for x in (-.5,.5)]
    faces = [(0,2,3,1),(4,5,7,6),(0,1,5,4),(2,6,7,3),(0,4,6,2),(1,3,7,5)]
    return vertices, faces
