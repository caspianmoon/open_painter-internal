"""Godot 4 ORMMaterial3D: linear R=AO/G=roughness/B=metallic; +Y normal.
https://docs.godotengine.org/en/stable/tutorials/3d/standard_material_3d.html
"""
def outputs():
    return [('BaseColorOpacity','BASE_COLOR_OPACITY'),('Normal','NORMAL'),
            ('ORM',('AO','ROUGHNESS','METALLIC','ONE')),('Emission','EMISSION')]
