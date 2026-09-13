"""glTF 2 metallic-roughness inputs; textures only, not an inferred glTF material.
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
"""
def outputs():
    return [('BaseColorOpacity','BASE_COLOR_OPACITY'),('Normal','NORMAL'),
            ('OcclusionRoughnessMetallic',('AO','ROUGHNESS','METALLIC','ONE')),('Emissive','EMISSION')]
