"""Where the Open Painter engine spends its time, stage by stage, so each stage can be
measured (and optimised) on its own.

An update that reaches the images runs up to seven stages:

- **build**: units compiled into node trees (Python), only after a structural edit; the
  units whose structure did not change are adopted, so the figure says how many were rebuilt;
- **update**: numeric edits written as uniforms (no program changes);
- **CPU inputs**: rasters prepared on the CPU (legacy shader groups, filters, strokes),
  geometry textures and the padding table;
- **translate**: node trees turned into GLSL (Python);
- **compile**: GLSL compiled by the GPU driver;
- **render**: the units' passes. Without a profile this is the CPU time to submit them
  (the GPU runs asynchronously); ``Engine.profile`` times each pass on the GPU (five draws
  less one, each followed by a wait), per unit, with each Mask Graph a pass of its own;
- **publish**: results written into the set's images: a framebuffer pass (OpenGL, Metal),
  or a read-back into pixels (Vulkan, where Blender's image textures are not render targets;
  and for Cycles, which samples pixels on the CPU). Through pixels an evaluation publishes
  within a time budget and holds the other changed channels for the next tick.

``shader_preview`` accumulates build, update and CPU inputs between evaluations and
``record``s one row per evaluation; ``report`` renders the history and the last profile
as plain text a tester can paste.
"""
import platform
from collections import Counter, deque
from time import monotonic

HISTORY = 200
STAGES = ('build', 'update', 'inputs', 'translate', 'compile', 'render', 'publish')
TITLES = {'build': 'build', 'update': 'update', 'inputs': 'CPU inputs', 'translate': 'translate',
          'compile': 'compile', 'render': 'render', 'publish': 'publish'}


def begin():
    """The accumulator of what happens before an evaluation (``shader_preview`` fills it)."""
    return {'build_ms': 0.0, 'builds': 0, 'rebuilt': 0, 'reused': 0, 'update_ms': 0.0, 'updates': 0,
            'inputs_ms': 0.0}


def evaluation():
    """An evaluation's own stages (``Engine`` fills it)."""
    return {'inputs_ms': 0.0, 'translate_ms': 0.0, 'compile_ms': 0.0, 'compiles': 0, 'render_ms': Counter(),
            'passes': Counter(), 'publish_ms': 0.0, 'published': [], 'publish_path': '', 'units': {},
            'profiled': False}


def record(entry, rendered):
    """One row for an evaluation: the accumulated stages before it plus its own, in milliseconds."""
    rendered = rendered or evaluation()
    # A profile re-renders what is on screen: the edits before the next update stay pending for it.
    pending = begin() if rendered['profiled'] else (entry.pop('stage', None) or begin())
    row = {'at': monotonic(), 'build': pending['build_ms'], 'builds': pending['builds'],
           'rebuilt': pending['rebuilt'], 'reused': pending['reused'],
           'update': pending['update_ms'], 'updates': pending['updates'],
           'inputs': pending['inputs_ms'] + rendered['inputs_ms'], 'translate': rendered['translate_ms'],
           'compile': rendered['compile_ms'], 'compiles': rendered['compiles'],
           'render': sum(rendered['render_ms'].values()), 'passes': sum(rendered['passes'].values()),
           'render_by_kind': dict(rendered['render_ms']), 'passes_by_kind': dict(rendered['passes']),
           'publish': rendered['publish_ms'], 'published': list(rendered['published']),
           'publish_path': rendered['publish_path'], 'profiled': rendered['profiled'],
           'deferred': list(rendered.get('deferred', ()))}
    row['total'] = sum(row[stage] for stage in STAGES)
    entry.setdefault('history', deque(maxlen=HISTORY)).append(row)
    if rendered['profiled']:
        entry['profile'] = {'at': row['at'], 'units': dict(rendered['units']), 'row': row,
                            'wait_ms': rendered.get('wait_ms', 0.0)}
    return row


def _percentile(values, share):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def summary(history):
    """{stage: (mean, p95, max)} over the rows, plus the totals."""
    rows = [row for row in history if not row['profiled']]
    result = {}
    for stage in (*STAGES, 'total'):
        values = [row[stage] for row in rows]
        result[stage] = ((sum(values) / len(values)) if values else 0.0, _percentile(values, .95),
                         max(values, default=0.0))
    return result


def label(ts, key):
    """A unit as the artist knows it: the layer, and the channel, mask stack or Mask Graph."""
    layer = next((item for item in ts.layers if item.id == key[1]), None)
    name = f'"{layer.name}"' if layer is not None else key[1][:8]
    kind = key[0]
    if kind == 'CHANNEL':
        return f'{name} {key[2].replace("_", " ").lower()}'
    if kind == 'SRC':
        return f'{name} {key[2].replace("_", " ").lower()} content'
    if kind == 'FIELD':
        return f'{name} {key[2].replace("_", " ").lower()} detail'
    if kind == 'GRAPH':
        mask = next((item for item in layer.masks if item.id == key[2]), None) if layer is not None else None
        graph = mask.node_group.name if mask is not None and mask.node_group is not None else '?'
        return f'{name} Mask Graph "{graph}"'
    if kind == 'MASK':
        return f'{name} masks'
    return f'{name} {kind.lower()}'


def machine():
    """Blender, the OS and the GPU the engine runs on (one line)."""
    import bpy
    parts = [f'Blender {bpy.app.version_string}', f'{platform.system()} {platform.release()}']
    try:
        import gpu
        parts += [gpu.platform.backend_type_get(), gpu.platform.vendor_get(), gpu.platform.renderer_get(),
                  gpu.platform.version_get()]
        parts.append(f'{int(gpu.capabilities.max_textures_frag_get())} textures per pass')
    except Exception as error:
        parts.append(f'GPU unavailable ({error})')
    return ' · '.join(str(part) for part in parts if part)


def _stage_line(row):
    kinds = ' · '.join(f'{kind} {count}' for kind, count in sorted(row['passes_by_kind'].items()))
    held = row.get('deferred') or ()
    publish = (f"{row['publish']:.1f} ({len(row['published'])} channel{'s' if len(row['published']) != 1 else ''}"
               f"{', ' + row['publish_path'] if row['publish_path'] else ''}"
               f"{f', {len(held)} held for the next tick' if held else ''})")
    build = f"{row['build']:.1f}"
    if row['builds']:
        build += f" ({row['rebuilt']} unit{'s' if row['rebuilt'] != 1 else ''} rebuilt, {row['reused']} kept)"
    return (f"build {build} · update {row['update']:.1f} · CPU inputs {row['inputs']:.1f} · "
            f"translate {row['translate']:.1f} · compile {row['compile']:.1f} ({row['compiles']} programs) · "
            f"render {row['render']:.1f} ({row['passes']} passes{': ' + kinds if kinds else ''}) · "
            f"publish {publish}")


def report(scene, ts):
    """Plain text: the machine, the set, the last evaluation, recent statistics and the last profile."""
    from .. import shader_preview
    entry = shader_preview._sessions.get(shader_preview.key_for(scene, ts), {})
    graph, engine = entry.get('graph'), entry.get('engine')
    lines = ['Open Painter engine report', machine()]
    if graph is None or engine is None:
        lines.append(f'Set "{ts.name}": the engine has not built this set '
                     f'({shader_preview.status(scene, ts)})')
        return '\n'.join(lines)
    kinds = Counter(key[0] for key in graph.units)
    from .runtime import device
    lines.append(f'Set "{ts.name}" · {graph.n} px · {len(graph.units)} units ('
                 + ' · '.join(f'{kind} {count}' for kind, count in sorted(kinds.items()))
                 + f') · {len(device().shaders)} programs · {"full" if engine.precise else "display"} precision'
                 + f' · {len(engine.inline)} units compiled into their consumer')
    history = [row for row in entry.get('history', ()) if not row['profiled']]
    if history:
        last = history[-1]
        lines.append(f"Last update ({monotonic() - last['at']:.1f} s ago): {last['total']:.1f} ms")
        lines.append('  ' + _stage_line(last))
        stats = summary(history)
        lines.append(f"Last {len(history)} updates: mean {stats['total'][0]:.1f} ms · p95 {stats['total'][1]:.1f}"
                     f" · max {stats['total'][2]:.1f}")
        lines.append('  stage        mean     p95      max   (ms)')
        for stage in STAGES:
            mean, p95, most = stats[stage]
            lines.append(f'  {TITLES[stage]:<11}{mean:>7.1f}{p95:>8.1f}{most:>9.1f}')
        builds = [row for row in history if row['builds']]
        if builds:
            lines.append(f"Structural rebuilds: {len(builds)} (mean {sum(r['build'] for r in builds) / len(builds):.1f} ms;"
                         f" the last rebuilt {builds[-1]['rebuilt']} of {builds[-1]['rebuilt'] + builds[-1]['reused']} units)")
        lines.append('  render is CPU submission time: the GPU runs asynchronously. Profile Engine measures it.')
        if last['publish_path'].startswith('pixels'):
            lines.append('  publish goes through the CPU: on Vulkan Blender\'s image textures cannot be render targets,'
                         ' so each channel is read back, written into the image and re-uploaded by Blender'
                         ' (the OpenGL backend writes them on the GPU).')
    else:
        lines.append('No update recorded yet.')
    profile = entry.get('profile')
    if profile:
        row, units = profile['row'], profile['units']
        lines.append(f"Profile ({monotonic() - profile['at']:.0f} s ago; each pass timed on the GPU as five draws "
                     f"less one, publishing less one {profile['wait_ms']:.2f} ms wait per channel):"
                     f" {row['render']:.2f} ms in {row['passes']} passes, publish {row['publish']:.2f} ms")
        by_kind = Counter()
        by_layer = Counter()
        names = {layer.id: layer.name for layer in ts.layers}
        for key, ms in units.items():
            by_kind[key[0]] += ms
            by_layer[key[1]] += ms
        lines.append('  by kind: ' + ' · '.join(f'{kind} {ms:.1f}' for kind, ms in by_kind.most_common()))
        lines.append('  heaviest passes:')
        for key, ms in sorted(units.items(), key=lambda item: -item[1])[:12]:
            lines.append(f'    {ms:7.2f} ms  {key[0]:<8}{label(ts, key)}')
        lines.append('  by layer:')
        for layer_id, ms in by_layer.most_common(12):
            lines.append(f'    {ms:7.2f} ms  "{names.get(layer_id, layer_id[:8])}"')
    else:
        lines.append('No profile yet: Performance ▸ Profile Engine measures every pass on the GPU.')
    return '\n'.join(lines)
