"""Data-only smart material profiles supplied by installed library modules.

The add-on ships the workflow; installed modules bring their own recipes and textures. Profiles
carry the same master-control vocabulary as catalog recipes and destination mesh
inputs, without executing code from a material archive.
"""
import json
from pathlib import Path
from uuid import UUID, uuid5
from zipfile import ZipFile, is_zipfile

from ..core.surface_settings import FIELDS as SURFACE_FIELDS

MAPS = {'NORMAL', 'AO', 'CURVATURE', 'WORLD_NORMAL', 'POSITION', 'OBJECT_NORMAL',
        'THICKNESS', 'BEVEL_NORMAL', 'BEVEL_MASK', 'MEAN_CURVATURE'}
FIELDS = {'opacity', 'visible', 'color', 'value', 'scale', 'amount', 'edge_size',
          'edge_width', 'noise_scale', 'noise_strength', 'detail_normal_strength',
          'detail_radius', 'output_white', 'output_black', 'anisotropy',
          'coat_weight', 'coat_roughness', 'height_distance'} | set(SURFACE_FIELDS)


def profile(folder):
    value = getattr(folder, 'master_controls', '')
    return parse(value) if value else None


def parse(value):
    if not isinstance(value, str) or len(value) > 32768:
        raise ValueError('Invalid smart material control profile.')
    data = json.loads(value)
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unsupported smart material control profile.')
    records = data.get('controls')
    if not isinstance(records, list) or not 1 <= len(records) <= 8:
        raise ValueError('A smart material needs one to eight master controls.')
    from .controls import Control, Member, slots
    controls = []
    labels = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Invalid master control.')
        try:
            control = Control(**dict(record, members=tuple(Member(**m) for m in record.get('members', ()))))
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid master control fields.') from exc
        if (not isinstance(control.label, str) or not control.label or len(control.label) > 80
                or control.label in labels or control.kind not in {'float', 'color', 'bool', 'factor', 'multiplier'}
                or control.scope not in {'layer', 'set'}
                or (control.computed and control.mode not in {'scale', 'coverage', 'invert', 'depth',
                                                             'levels_center', 'levels_width'})):
            raise ValueError('Invalid master control definition.')
        labels.add(control.label)
        targets = control.members if control.computed else (control,)
        if not targets or len(targets) > 32:
            raise ValueError('Invalid master control targets.')
        for target in targets:
            if (target.field not in FIELDS or not isinstance(target.layer, str)
                    or target.channel not in {'', 'BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'NORMAL',
                                              'HEIGHT', 'AO', 'OPACITY', 'EMISSION'}
                    or not isinstance(target.mask, int) or not -1 <= target.mask <= 255
                    or not isinstance(target.effect, int) or not -1 <= target.effect <= 255):
                raise ValueError('Invalid master control target.')
        if control.scope == 'set' and (control.computed or control.field not in SURFACE_FIELDS):
            raise ValueError('Invalid material surface control.')
        controls.append(control)
    slots(controls)
    required = data.get('required_maps', [])
    if not isinstance(required, list) or any(not isinstance(kind, str) or kind not in MAPS for kind in required):
        raise ValueError('Invalid required mesh maps.')
    return data, controls


def validate(data):
    """Check data and all control targets before allocating Blender state."""
    from .modules import plain_id
    for folder in data['layers']:
        raw = folder.get('master_controls', '')
        if not raw:
            continue
        _, controls = parse(raw)
        module = data.get('module') or {}
        module_id = module.get('id') if module.get('root') == folder['id'] else folder.get('module_id')
        try:
            namespace = UUID(module_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('Smart material controls need a stable module identity.') from exc
        token = folder.get('module_instance', '')
        layers = {plain_id(item['id'], token): item for item in data['layers']}
        for control in controls:
            if control.scope == 'set':
                continue
            for target in control.members if control.computed else (control,):
                layer = layers.get(uuid5(namespace, 'layer:' + target.layer).hex)
                if layer is None:
                    # Saved instances may intentionally retain a deleted control target;
                    # their inspector reports the broken binding, like catalog materials.
                    if not module:
                        continue
                    raise ValueError('Master control references a missing layer: ' + target.layer)
                rows = layer.get('masks', [])
                if target.channel:
                    channel = next((c for c in layer.get('channels', []) if c['channel'] == target.channel), None)
                    if channel is None:
                        raise ValueError('Master control references a missing channel.')
                    rows = channel.get('effects', [])
                index = target.effect if target.channel else target.mask
                if index >= len(rows):
                    raise ValueError('Master control references a missing mask or filter.')
    for layer in data['layers']:
        for channel in layer.get('channels', []):
            if channel.get('mesh_map') and channel['mesh_map'] not in MAPS:
                raise ValueError('Unknown destination mesh map.')


def requirements(data):
    result = set()
    for layer in data['layers']:
        if layer.get('master_controls'):
            result.update(parse(layer['master_controls'])[0].get('required_maps', ()))
        result.update(c['mesh_map'] for c in layer.get('channels', ()) if c.get('mesh_map'))
    return result


def preflight(ts, data):
    validate(data)
    available = {item.kind for item in ts.baked_maps if item.image and all(item.image.size)}
    missing = requirements(data) - available
    if missing:
        from .composer import MissingMeshMaps
        raise MissingMeshMaps(data.get('name', 'Smart material'), missing)


def mesh_image(ts, kind):
    return next((item.image for item in ts.baked_maps if item.kind == kind and item.image), None)


def bind_mesh_maps(ts):
    from ..core.layer_stack import replay
    with replay(ts):
        for layer in ts.layers:
            for channel in layer.channels:
                if channel.mesh_map:
                    channel.image = mesh_image(ts, channel.mesh_map)


_cache = {}
_installed_stamp = None


def refresh_installed():
    """Pick up Connect installs/removals in an already open Painter workspace.

    A new scene or file needs only its local handles. Only a change of the installed files
    refreshes the shelves: Blender's library refresh rebuilds every listed asset, and one
    while the shelf loads its first previews (just after startup or a file load) can crash
    Blender, whose preview loader then writes into a preview it has freed."""
    import bpy

    from . import library
    global _installed_stamp
    try:
        paths = library.entries(bpy.context)
        stamp = (bpy.context.scene.as_pointer(), tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                                                     for p in paths))
        if stamp != _installed_stamp:
            from ..ui import native, shelf_library
            native.prepare_assets()
            if _installed_stamp is not None and stamp[1] != _installed_stamp[1]:
                shelf_library.refresh_shelves()
            _installed_stamp = stamp
    except (OSError, ReferenceError, AttributeError):
        pass  # A concurrent installation or scene switch will be retried next tick.
    return 5.0


def register():
    import bpy
    global _installed_stamp
    _installed_stamp = None
    if not bpy.app.background and not bpy.app.timers.is_registered(refresh_installed):
        bpy.app.timers.register(refresh_installed, first_interval=1.0, persistent=True)


def unregister():
    import bpy
    if bpy.app.timers.is_registered(refresh_installed):
        bpy.app.timers.unregister(refresh_installed)
    _cache.clear()


def header(path):
    path = Path(path)
    stamp = (path.stat().st_mtime_ns, path.stat().st_size)
    cached = _cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    if is_zipfile(path):
        with ZipFile(path) as archive:
            if archive.getinfo('material.json').file_size > 4 * 1024 * 1024:
                raise ValueError('Smart material metadata is too large.')
            data = json.loads(archive.read('material.json'))
    else:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Smart material metadata is too large.')
        data = json.loads(path.read_text())
    validate(data)
    _cache[str(path)] = (stamp, data)
    return data


def entries(context=None):
    """Only installed smart modules: empty on a clean free install."""
    import bpy

    from . import library
    newest = {}
    for path in library.entries(context or bpy.context):
        try:
            data = header(path)
            module = data.get('module', {})
            if not module or not any(layer.get('master_controls') for layer in data['layers']):
                continue
            item = (module['name'], 'LIBRARY_SMART', str(path), (.4, .4, .4), .3, '')
            if module['id'] not in newest or module['version'] > newest[module['id']][0]:
                newest[module['id']] = (module['version'], item)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted((value[1] for value in newest.values()), key=lambda item: item[0])
