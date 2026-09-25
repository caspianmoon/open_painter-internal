"""Target renderers: the conventions an engine expects, as one project-level choice.

A target names the normal map convention, the gloss channel, how the engine uses
AO, the channel packing (an export preset), file naming, resolution limits and
the mesh export it pairs with. Selecting a target configures every texture
set's export settings that follow it; Engine Preview reconfigures the display
material to approximate what the engine will compute from the exported files;
Export → <Target> writes textures, mesh and a manifest in one step.
"""
from dataclasses import dataclass, field

ITEMS = (('BLENDER', 'Blender', 'Principled BSDF, OpenGL normals, separate maps'),
         ('UNREAL', 'Unreal Engine 5', 'DirectX normals, ORM packing, T_ naming, FBX'),
         ('UNITY_HDRP', 'Unity HDRP', 'OpenGL normals, Mask Map (metal/AO/detail/smoothness), FBX'),
         ('UNITY_URP', 'Unity URP', 'OpenGL normals, MetallicGloss (metal, smoothness in alpha), FBX'),
         ('GODOT', 'Godot 4', 'OpenGL normals, ORM, glTF'),
         ('CUSTOM', 'Custom', 'Keep each set\'s own export settings'))


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    normal: str = 'OPENGL'            # 'OPENGL' (+Y) or 'DIRECTX' (-Y, green flipped)
    gloss: str = 'ROUGHNESS'          # 'ROUGHNESS' or 'SMOOTHNESS' (1 - roughness in the packed map)
    ao_usage: str = 'MULTIPLY'        # 'MULTIPLY' (viewport-style) or 'INDIRECT' (engines: indirect light only)
    preset: str = 'BLENDER'           # export preset key (channel packing)
    naming: str = '{set}_{map}'       # file name template
    labels: dict = field(default_factory=dict)   # preset map label → engine name
    color_spaces: dict = field(default_factory=dict)   # map label → 'sRGB' | 'Non-Color'
    compression: dict = field(default_factory=dict)    # map label → 'BC1' | 'BC5' | 'BC7' | 'NONE'
    default_resolution: int = 2048
    max_resolution: int = 8192
    file_format: str = 'PNG'
    bit_depth: str = '8'
    mesh_format: str = ''             # '', 'FBX' or 'GLTF'
    mesh_options: dict = field(default_factory=dict)
    specular: float = .5              # Principled "Specular IOR Level": F0 = 0.04 in every engine
    view_transform: str = 'Standard'
    notes: str = ''


REGISTRY = {
    'BLENDER': Target('BLENDER', 'Blender', notes='What the viewport shows: Principled BSDF, AO multiplied into base colour'),
    'UNREAL': Target('UNREAL', 'Unreal Engine 5', normal='DIRECTX', gloss='ROUGHNESS', ao_usage='INDIRECT',
                     preset='UNREAL', naming='T_{set}_{map}',
                     labels={'BaseColor': 'BC', 'Normal': 'N', 'ORM': 'ORM'},
                     color_spaces={'BaseColor': 'sRGB', 'Normal': 'Non-Color', 'ORM': 'Non-Color'},
                     compression={'BaseColor': 'BC1', 'Normal': 'BC5', 'ORM': 'BC1'},
                     mesh_format='FBX',
                     mesh_options=dict(axis_forward='-Y', axis_up='Z', global_scale=1.0, apply_unit_scale=True,
                                       apply_scale_options='FBX_SCALE_NONE', bake_space_transform=True,
                                       mesh_smooth_type='FACE', use_tspace=True, path_mode='COPY'),
                     notes='Normal map with Flip Green Channel off (DirectX), ORM sampled as Linear Color masks, '
                           'BaseColor sRGB. Unreal\'s ACES tone mapping is approximated by the Standard view.'),
    'UNITY_HDRP': Target('UNITY_HDRP', 'Unity HDRP', gloss='SMOOTHNESS', ao_usage='INDIRECT', preset='UNITY_HDRP',
                         naming='{set}_{map}',
                         color_spaces={'BaseColorMap': 'sRGB', 'NormalMap': 'Non-Color', 'MaskMap': 'Non-Color',
                                       'EmissiveColorMap': 'sRGB'},
                         compression={'BaseColorMap': 'BC7', 'NormalMap': 'BC5', 'MaskMap': 'BC7', 'EmissiveColorMap': 'BC7'},
                         mesh_format='FBX',
                         mesh_options=dict(apply_scale_options='FBX_SCALE_ALL', mesh_smooth_type='FACE', use_tspace=True,
                                           path_mode='COPY'),
                         notes='Mask Map R=Metallic G=AO B=Detail A=Smoothness (sRGB off); Normal texture type Normal map'),
    'UNITY_URP': Target('UNITY_URP', 'Unity URP', gloss='SMOOTHNESS', ao_usage='INDIRECT', preset='UNITY_URP',
                        naming='{set}_{map}',
                        color_spaces={'BaseMap': 'sRGB', 'Normal': 'Non-Color', 'MetallicGlossMap': 'Non-Color',
                                      'OcclusionMap': 'Non-Color', 'EmissionMap': 'sRGB'},
                        compression={'BaseMap': 'BC7', 'Normal': 'BC5', 'MetallicGlossMap': 'BC7',
                                     'OcclusionMap': 'BC7', 'EmissionMap': 'BC7'},
                        mesh_format='FBX',
                        mesh_options=dict(apply_scale_options='FBX_SCALE_ALL', mesh_smooth_type='FACE', use_tspace=True,
                                          path_mode='COPY'),
                        notes='Metallic in R, smoothness in A; occlusion sampled from G'),
    'GODOT': Target('GODOT', 'Godot 4', ao_usage='INDIRECT', preset='GODOT', naming='{set}_{map}',
                    labels={'BaseColorOpacity': 'albedo', 'Normal': 'normal', 'ORM': 'orm', 'Emission': 'emission'},
                    color_spaces={'BaseColorOpacity': 'sRGB', 'Normal': 'Non-Color', 'ORM': 'Non-Color', 'Emission': 'sRGB'},
                    compression={'BaseColorOpacity': 'BC7', 'Normal': 'BC5', 'ORM': 'BC7', 'Emission': 'BC7'},
                    max_resolution=16384, mesh_format='GLTF',
                    mesh_options=dict(export_format='GLB', export_apply=True, export_materials='NONE'),
                    notes='ORMMaterial3D: albedo sRGB, ORM and normal linear, +Y normals'),
    'CUSTOM': Target('CUSTOM', 'Custom', preset='', notes='Each texture set keeps its own export settings'),
}


def get(key):
    return REGISTRY.get(key, REGISTRY['BLENDER'])


def apply(target, settings):
    """Configure a set's export settings from a target; CUSTOM leaves them alone."""
    if target.key == 'CUSTOM' or not target.preset:
        return False
    changed = False
    for name, value in (('preset', target.preset), ('file_format', target.file_format), ('bit_depth', target.bit_depth)):
        if getattr(settings, name) != value:
            setattr(settings, name, value)
            changed = True
    return changed


def map_name(target, label):
    return target.labels.get(label, label)


def safe_name(value):
    import re
    return re.sub(r"[^\w.\-]+", "_", value, flags=re.UNICODE).strip("._") or "TextureSet"


def filename(target, set_name, label, extension, prefix='', suffix=''):
    name = target.naming.format(set=safe_name(prefix + set_name), map=safe_name(map_name(target, label) + suffix))
    return f'{name}.{extension}'


def resolution(target, ts, settings):
    n = int(settings.resolution) if settings.resolution != 'SET' else int(ts.resolution)
    return min(n, target.max_resolution)


def manifest(target, ts, files):
    """What an importer needs to know about the exported files."""
    return {
        'target': target.key, 'label': target.label, 'texture_set': ts.name,
        'udim_tile': getattr(ts, 'udim_tile', 0) or None,
        'udim_group': getattr(ts, 'udim_group', '') or None,
        'normal_map': {'convention': target.normal, 'green': 'down (-Y)' if target.normal == 'DIRECTX' else 'up (+Y)',
                       'tangent_space': True},
        'gloss': target.gloss, 'ambient_occlusion': target.ao_usage.lower(),
        'specular_f0': .04, 'view_transform': target.view_transform,
        'files': [{'path': path.name, 'map': label, 'color_space': target.color_spaces.get(label, 'Non-Color'),
                   'compression': target.compression.get(label, 'NONE')} for label, path in files],
        'notes': target.notes,
    }
