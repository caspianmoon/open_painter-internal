"""Per-layer cost table (0.9.0): what each layer costs the preview, and why.

The table is runtime state keyed by scene and texture set. Three sources feed
it, none of them a GPU frame time (Blender does not expose per-node shader
execution):

- ``record_graph``: after every build or rehydration, each layer's compile
  milliseconds (the sum of its units' ``build_ms``; adopted units keep the
  figure of the build that compiled them), node count and unique samplers.
- ``record_boundary``: CPU milliseconds of isolated inputs (legacy noise
  fields, spatial filters, mask prefixes, strokes) attributed to the owning
  layer as the shader runtime steps them; cumulative until ``clear``.
- ``record_cpu``: the explicit Profile Layers pass — the CPU reference
  composer run once at a bounded resolution with a per-layer timer.
- ``record_gpu``: the engine's profiled evaluation (``shader_preview.profile_engine``,
  also run by Profile Layers): each layer's passes timed on the GPU at the set's
  resolution. For the live preview this is the figure that matters; the CPU
  reference is what export costs.

Cache refreshes (``record_refresh``) are attributed separately.
"""
from time import monotonic

from .resource_keys import key_for

_tables = {}


def _empty():
    return {'serial': -1, 'sampled_at': 0.0, 'resolution': 0, 'compile': {}, 'boundary': {}, 'refresh': {},
            'cpu': {}, 'gpu': {}, 'gpu_serial': -1}


def _table(scene, ts):
    return _tables.setdefault(key_for(scene, ts), _empty())


def table(scene, ts):
    """The raw table of a set (empty when nothing was recorded)."""
    return _tables.get(key_for(scene, ts), _empty())


def profiled(scene, ts):
    """True once the explicit Profile Layers pass ran for this set."""
    return bool(table(scene, ts)['cpu'])


def recorded(scene, ts):
    """Whether this set's per-layer compile table holds a record (``record_graph``)."""
    return bool(_tables.get(key_for(scene, ts), {}).get('compile'))


def record_graph(scene, ts, graph, layers=None):
    """Per-layer compile cost, node and sampler counts; ``layers``: only these changed (a patch)."""
    entry = _table(scene, ts)
    compile_ms, nodes, samplers = {}, {}, {}
    for key, unit in getattr(graph, 'units', {}).items():
        layer_id = key[1]
        if layers is not None and layer_id not in layers:
            continue
        compile_ms[layer_id] = compile_ms.get(layer_id, 0.0) + float(getattr(unit, 'build_ms', 0.0))
        nodes[layer_id] = nodes.get(layer_id, 0) + unit.node_count
        images = samplers.setdefault(layer_id, set())
        try:
            for node in unit.tree.nodes:
                if node.bl_idname == 'ShaderNodeTexImage' and node.image:
                    images.add(node.image.as_pointer())
        except (AttributeError, ReferenceError):
            pass
    recorded = {layer_id: {'compile_ms': compile_ms[layer_id], 'nodes': nodes[layer_id],
                           'samplers': len(samplers[layer_id])} for layer_id in compile_ms}
    if layers is None:
        entry['compile'] = recorded
        return
    table = entry.setdefault('compile', {})
    for layer_id in layers:
        table.pop(layer_id, None)
    table.update(recorded)


def record_boundary(scene, ts, timings):
    if not timings:
        return
    entry = _table(scene, ts)
    for layer_id, seconds in timings.items():
        entry['boundary'][layer_id] = entry['boundary'].get(layer_id, 0.0) + float(seconds) * 1000


def record_refresh(scene, ts, layer_id, ms):
    entry = _table(scene, ts)
    entry['refresh'][layer_id] = entry['refresh'].get(layer_id, 0.0) + float(ms)


def record_cpu(scene, ts, profiler_results, resolution, serial):
    """``profiler_results``: {layer_id: (exclusive_ms, inclusive_ms)} from the Profile Layers pass."""
    entry = _table(scene, ts)
    entry['cpu'] = {layer_id: {'cpu_ms': float(exclusive), 'inclusive_ms': float(inclusive)}
                    for layer_id, (exclusive, inclusive) in profiler_results.items()}
    entry['serial'] = serial
    entry['resolution'] = resolution
    entry['sampled_at'] = monotonic()


def record_gpu(scene, ts, units):
    """``units``: {unit key: GPU milliseconds} of one profiled engine evaluation."""
    entry = _table(scene, ts)
    per_layer = {}
    for key, ms in units.items():
        per_layer[key[1]] = per_layer.get(key[1], 0.0) + float(ms)
    entry['gpu'] = per_layer
    entry['gpu_serial'] = ts.edit_serial


def clear(scene, ts):
    _tables.pop(key_for(scene, ts), None)


def prune(live):
    for key in set(_tables) - set(live):
        _tables.pop(key, None)


def _bypassed(ts, layer, by_id=None):
    seen, cursor = set(), layer
    if by_id is None:
        by_id = {item.id: item for item in ts.layers}
    while cursor is not None and cursor.id not in seen:
        if getattr(cursor, 'bypass', False):
            return True
        seen.add(cursor.id)
        cursor = by_id.get(cursor.parent_id) if cursor.parent_id else None
    return False


def state(scene, ts, layer, cache_states=None, by_id=None):
    """'BYPASSED' | 'LIVE' | 'CACHED' | 'FROZEN' | 'STALE' | 'PENDING' for the UI."""
    if _bypassed(ts, layer, by_id):
        return 'BYPASSED'
    mode = getattr(layer, 'cache_mode', 'LIVE')
    if mode == 'LIVE':
        return 'LIVE'
    from . import layer_cache
    if layer_cache.pending(scene, ts, layer.id):
        return 'PENDING'
    if cache_states is None:
        from . import shader_preview
        cache_states = shader_preview.diagnostics(scene, ts).get('cache_states', {})
    mine = [value for key, value in cache_states.items() if key.startswith(layer.id + ':')]
    if any(value != 'CURRENT' for value in mine):
        return 'STALE'
    return mode


def rows(scene, ts):
    """One dict per layer, in stack order, merging every recorded source."""
    entry = table(scene, ts)
    from . import shader_preview
    cache_states = shader_preview.diagnostics(scene, ts).get('cache_states', {}) if shader_preview.requested(ts) else {}
    # One id map for the whole table: the ancestor walk of every layer would
    # otherwise rebuild it, quadratic in the stack on each sidebar redraw.
    by_id = {item.id: item for item in ts.layers}
    result = []
    for layer in ts.layers:
        compile_info = entry['compile'].get(layer.id, {})
        cpu = entry['cpu'].get(layer.id, {})
        row = {'layer_id': layer.id, 'name': layer.name, 'type': layer.type,
               'state': state(scene, ts, layer, cache_states, by_id),
               'compile_ms': compile_info.get('compile_ms', 0.0), 'nodes': compile_info.get('nodes', 0),
               'samplers': compile_info.get('samplers', 0),
               'boundary_ms': entry['boundary'].get(layer.id, 0.0),
               'refresh_ms': entry['refresh'].get(layer.id, 0.0),
               'cpu_ms': cpu.get('cpu_ms', 0.0), 'inclusive_ms': cpu.get('inclusive_ms', 0.0),
               'gpu_ms': entry['gpu'].get(layer.id, 0.0), 'gpu_current': entry['gpu_serial'] == ts.edit_serial,
               'profiled': bool(cpu), 'current': entry['serial'] == ts.edit_serial}
        row['cost_ms'] = row['compile_ms'] + row['boundary_ms'] + row['cpu_ms']
        result.append(row)
    return result


def total(scene, ts):
    """Sum of every layer's cost (compile + boundary + exclusive CPU) in milliseconds."""
    return sum(row['cost_ms'] for row in rows(scene, ts))


def run(scene, ts, resolution=512):
    """The explicit Profile Layers pass: compose every touched channel on the CPU
    at a bounded resolution with a per-layer timer. Returns the recorded rows.

    Measures the authoritative stack (bypass and caches are preview state);
    the row's ``state`` says what the shader currently skips or samples.
    """
    from ..constants import CHANNELS, DEFAULTS
    from ..core.layer_stack import channel_for
    from .composite import Composer, LayerProfiler
    from .snapshots import capture
    n = max(16, min(int(ts.resolution), int(resolution)))
    scale = n / int(ts.resolution) if int(ts.resolution) else 1.0
    snapshot = capture(ts, scale).replaced(resolution=str(n))
    touched = [channel for channel in CHANNELS
               if any((prop := channel_for(layer, channel)) is not None and prop.enabled for layer in ts.layers)]
    profiler = LayerProfiler()
    for channel in touched:
        composer = Composer(snapshot, channel, n, {}, {}, profiler=profiler)
        components = composer.components
        for y0 in range(0, n, 16):
            y1 = min(n, y0 + 16)
            composer.compose('', y0, y1, DEFAULTS[channel][:components], set(), 1)
    results = {layer.id: (profiler.exclusive.get(layer.id, 0.0) * 1000, profiler.inclusive.get(layer.id, 0.0) * 1000)
               for layer in ts.layers}
    record_cpu(scene, ts, results, n, ts.edit_serial)
    from . import shader_preview
    if shader_preview.requested(ts):
        # The live preview's own cost: every pass of the engine timed on the GPU.
        try:
            shader_preview.profile_engine(scene, ts)
        except Exception:
            # The CPU figures stand; the set's engine status says why the GPU could not be profiled.
            from ..utils.logging import logger
            logger.debug('Engine profile skipped', exc_info=True)
    return rows(scene, ts)
