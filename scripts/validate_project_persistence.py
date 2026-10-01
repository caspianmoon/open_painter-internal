"""Validate the artist's saved 2048 project through native save and real export.

Run in an isolated Blender process. This script creates no geometry or material
fixtures and never saves over the input project. Its temporary working copy and
exported maps are retained alongside the report for inspection.
"""
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import time
import traceback
from unittest.mock import patch
import zlib

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack, material_manager
from open_painter.evaluation import cache, live_preview, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
from open_painter.export import exporter

SOURCE = Path(os.environ.get('OP_PROJECT_SOURCE', str(ROOT / 'open painter test.blend')))
DIRECTORY = Path(os.environ.get('OP_PROJECT_PERSISTENCE_DIR',
                               str(ROOT / '.blender-test/project-native/persistence-export')))
REPORT = DIRECTORY / 'report.json'
EXPECTED_SET = '2dd556f037ba4bdf9f2161e4311b5916'
EXPECTED_LAYER = 'ccfa7f86147b4b5d9356fe2672be7881'
EXPECTED_MASK = '32ce1b3277f3431797a7df56a564cdfa'
record = {'result': 'RUNNING', 'source': str(SOURCE), 'blender': bpy.app.version_string,
          'build': bpy.app.build_hash.decode(), 'resolution': 2048, 'checks': []}


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest_array(value):
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def progress(stage, **values):
    record.update(stage=stage, **values)
    REPORT.write_text(json.dumps(record, indent=2, allow_nan=False) + '\n', encoding="utf-8")
    print('PROJECT_PERSISTENCE', stage, flush=True)


class Observe:
    """Passive observation of real raster calls; nothing is stubbed or blocked."""
    def __enter__(self):
        self.calls = Counter()
        self.context = ExitStack()
        for owner, name, label in ((LayerEvaluator, 'channel_steps', 'cpu_channel_steps'),
                                   (live_preview, 'steps', 'legacy_gpu_composite'),
                                   (images, 'write', 'image_write'),
                                   (images, 'pack_image', 'pack_image')):
            original = getattr(owner, name)

            def observe(*args, _original=original, _label=label, **kwargs):
                self.calls[_label] += 1
                return _original(*args, **kwargs)

            self.context.enter_context(patch.object(owner, name, observe))
        self.metrics = self.context.enter_context(Metrics())
        return self

    def __exit__(self, *error):
        return self.context.__exit__(*error)

    def report(self):
        return {'calls': dict(self.calls), **self.metrics.report()}

    def no_bake(self):
        for label in ('cpu_channel_steps', 'legacy_gpu_composite', 'image_write'):
            assert self.calls[label] == 0, self.report()
        assert self.metrics.counts['shader_resource_publications'] == 0, self.report()


def ready(scene, ts, timeout=300):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        cache.tick()
        detail = shader_preview.diagnostics(scene, ts)
        assert not detail['error'], detail
        if shader_preview.current(scene, ts):
            return detail
    raise TimeoutError(shader_preview.diagnostics(scene, ts))


def canonical(value):
    if isinstance(value, bpy.types.ID):
        return {'id_type': value.bl_rna.identifier, 'name': value.name_full}
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()
                if key not in {'dirty', 'revision', 'image_name', 'detail_image_name'}}
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in value]
    if isinstance(value, set):
        return sorted(value)
    return value


def recipe(ts):
    return {'id': ts.id, 'name': ts.name, 'resolution': ts.resolution,
            'backend': ts.preview_backend,
            'surface': {key: getattr(ts, key) for key in
                        ('height_distance', 'anisotropy', 'coat_weight', 'coat_roughness', 'preview_ao')},
            'assignments': [(ref.object.name, ref.slot_index) for ref in ts.assignments],
            'layers': [canonical(layer_stack.snapshot(layer)) for layer in ts.layers],
            'baked_maps': [canonical(layer_stack.snapshot(item)) for item in ts.baked_maps]}


def geometry(scene):
    result = {}
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        h = hashlib.sha256()
        for collection, prop, width, dtype in (
                (mesh.vertices, 'co', 3, np.float32),
                (mesh.loops, 'vertex_index', 1, np.int32),
                (mesh.polygons, 'loop_start', 1, np.int32),
                (mesh.polygons, 'loop_total', 1, np.int32),
                (mesh.polygons, 'material_index', 1, np.int32)):
            values = np.empty(len(collection) * width, dtype)
            collection.foreach_get(prop, values)
            h.update(values.tobytes())
        for uv in mesh.uv_layers:
            h.update(uv.name.encode())
            values = np.empty(len(uv.data) * 2, np.float32)
            uv.data.foreach_get('uv', values)
            h.update(values.tobytes())
        result[obj.name] = {'vertices': len(mesh.vertices), 'polygons': len(mesh.polygons),
                            'loops': len(mesh.loops), 'mesh_and_uv_sha256': h.hexdigest(),
                            'transform': [list(row) for row in obj.matrix_world],
                            'materials': [slot.material.name if slot.material else None
                                          for slot in obj.material_slots]}
    return result


def source_images(ts):
    found = {}
    def visit(value, path):
        if isinstance(value, bpy.types.Image):
            found.setdefault(value.name, {'image': value, 'uses': []})['uses'].append(path)
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(item, path + '.' + key)
        elif isinstance(value, (tuple, list)):
            for index, item in enumerate(value):
                visit(item, path + f'[{index}]')
    for layer in ts.layers:
        visit(layer_stack.snapshot(layer), layer.name)
    for item in ts.baked_maps:
        visit(item.image, 'baked.' + item.kind)
    result = {}
    for name, entry in sorted(found.items()):
        image = entry['image']
        result[name] = {'size': list(image.size), 'colorspace': image.colorspace_settings.name,
                        'pixel_float32_sha256': digest_array(images.pixels(image)),
                        'packed': bool(image.packed_file),
                        'packed_sha256': hashlib.sha256(image.packed_file.data).hexdigest()
                        if image.packed_file else None,
                        'uses': entry['uses']}
    return result


def same_sources(before, after):
    assert set(before) == set(after)
    for name in before:
        for key in ('size', 'colorspace', 'pixel_float32_sha256', 'uses'):
            assert before[name][key] == after[name][key], (name, key)


def outputs(ts):
    return {out.channel: {'revision': out.revision, 'dirty': out.dirty,
                          'size': list(out.image.size),
                          'pixel_float32_sha256': digest_array(images.pixels(out.image))}
            for out in ts.outputs}


def read_png16(path):
    """Independently decode the production writer's lossless RGBA PNG stream."""
    raw = Path(path).read_bytes()
    assert raw[:8] == b'\x89PNG\r\n\x1a\n'
    offset, payload = 8, []
    while offset < len(raw):
        length, = struct.unpack_from('>I', raw, offset)
        kind, body = raw[offset + 4:offset + 8], raw[offset + 8:offset + 8 + length]
        if kind == b'IHDR':
            width, height, bits, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            assert (bits, color, compression, filtering, interlace) == (16, 6, 0, 0, 0)
        elif kind == b'IDAT':
            payload.append(body)
        offset += length + 12
    data = np.frombuffer(zlib.decompress(b''.join(payload)), np.uint8).reshape(height, width * 8 + 1)
    assert not data[:, 0].any(), 'Unexpected PNG row filter'
    return np.ascontiguousarray(data[:, 1:]).view('>u2').reshape(height, width, 4)[::-1].astype(np.uint16)


def quantized(value):
    return np.rint(np.clip(value, 0, 1) * 65535).astype(np.uint16)


def main():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    original_sha = digest_file(SOURCE)
    inventory = json.loads((ROOT / '.blender-test/project-native/inventory.json').read_text(encoding="utf-8"))
    assert original_sha == inventory['sha256'], 'Input changed since the actual project inventory'
    record['source_sha256_before'] = original_sha
    working = DIRECTORY / 'project-working-copy.blend'
    assert not working.exists(), 'Choose a new acceptance directory; previous evidence exists'
    shutil.copy2(SOURCE, working)
    open_painter.register()
    bpy.ops.wm.open_mainfile(filepath=str(working))
    scene = bpy.context.scene
    ts = next(item for item in scene.open_painter.texture_sets if item.id == EXPECTED_SET)
    assert int(ts.resolution) == 2048 and ts.preview_backend == 'LIVE'
    original_recipe = recipe(ts)
    original_geometry = geometry(scene)
    assert len(original_geometry) == 10
    original_sources = source_images(ts)
    original_outputs = outputs(ts)
    record.update(texture_set=ts.id, texture_set_name=ts.name, geometry=original_geometry,
                  authored_sources_before=original_sources, recipe_before=original_recipe,
                  outputs_before=original_outputs, working_copy=str(working))
    progress('original-project-loaded')

    material_manager.repair(ts)
    initial = ready(scene, ts)
    assert outputs(ts) == original_outputs, 'Building native preview rewrote saved outputs'
    progress('native-graph-current', initial_native=initial)
    coat = next(layer for layer in ts.layers if layer.id == EXPECTED_LAYER)
    chips = next(mask for mask in coat.masks if mask.id == EXPECTED_MASK)
    assert coat.name == 'Blue paint coat' and chips.name == 'Chips'
    original_amount = chips.amount
    with Observe() as edits:
        chips.amount = min(.95, original_amount + .19)
        changed = shader_preview.diagnostics(scene, ts)
        assert shader_preview.current(scene, ts) and changed['updated_bindings'] > 0, changed
        assert changed['graph_builds'] == initial['graph_builds']
        cache.tick()
        chips.amount = original_amount
        restored = shader_preview.diagnostics(scene, ts)
        assert shader_preview.current(scene, ts)
        assert restored['graph_builds'] == initial['graph_builds']
        edits.no_bake()
    assert recipe(ts) == original_recipe
    dirty_outputs = outputs(ts)
    assert ts.dirty and any(out['dirty'] for out in dirty_outputs.values())
    for channel, before in original_outputs.items():
        assert dirty_outputs[channel]['revision'] == before['revision']
        assert dirty_outputs[channel]['pixel_float32_sha256'] == before['pixel_float32_sha256']
    progress('actual-chips-edit-restored', chips_edit={'original_amount': original_amount,
             'changed': changed, 'restored': restored, 'observation': edits.report()})

    with Observe() as saved:
        bpy.ops.wm.save_as_mainfile(filepath=str(working))
        saved.no_bake()
    assert outputs(ts) == dirty_outputs, 'Save automatically updated dirty outputs'
    assert shader_preview.current(scene, ts)
    sources_saved = source_images(ts)
    same_sources(original_sources, sources_saved)
    assert all(value['packed'] for value in sources_saved.values())
    progress('native-save-no-bake', save_observation=saved.report(),
             copy_sha256=digest_file(working), authored_sources_saved=sources_saved)

    bpy.ops.wm.open_mainfile(filepath=str(working))
    scene = bpy.context.scene
    ts = next(item for item in scene.open_painter.texture_sets if item.id == EXPECTED_SET)
    assert recipe(ts) == original_recipe, 'Saved recipe changed on reopen'
    assert geometry(scene) == original_geometry, 'Real project meshes changed on reopen'
    reloaded_sources = source_images(ts)
    same_sources(original_sources, reloaded_sources)
    assert all(value['packed'] for value in reloaded_sources.values())
    reloaded_outputs = outputs(ts)
    for channel, before in dirty_outputs.items():
        assert reloaded_outputs[channel]['revision'] == before['revision']
        assert reloaded_outputs[channel]['pixel_float32_sha256'] == before['pixel_float32_sha256']
    assert ts.dirty and all(out.dirty for out in ts.outputs)
    material_manager.repair(ts)
    reloaded_native = ready(scene, ts)
    progress('actual-project-reopened', reloaded_native=reloaded_native,
             authored_sources_reopened=reloaded_sources, outputs_after_reopen=reloaded_outputs)

    settings = ts.export
    settings.directory = str(DIRECTORY / 'exports')
    settings.preset, settings.resolution = 'BLENDER', 'SET'
    settings.file_format, settings.bit_depth = 'PNG', '16'
    settings.prefix, settings.suffix = '', ''
    normal_evidence = {}
    real_normal = exporter.surface_normal

    def observe_normal(target, authored_normal, height):
        value = real_normal(target, authored_normal, height)
        normal_evidence.update(
            raw_normal_input_sha256=digest_array(authored_normal),
            height_input_sha256=digest_array(height),
            exported_uint16_sha256=digest_array(quantized(value)),
            xy_rms=float(np.sqrt(np.mean((value[..., :2] * 2 - 1) ** 2))),
            max_unit_length_error=float(np.max(abs(np.linalg.norm(value[..., :3] * 2 - 1, axis=2) - 1))))
        return value

    progress('explicit-export-started')
    with Observe() as exported, patch.object(exporter, 'surface_normal', observe_normal):
        paths = exporter.export(bpy.context, ts, settings)
    assert len(paths) == 8
    assert exported.calls['cpu_channel_steps'] > 0 and exported.calls['image_write'] > 0
    assert not ts.dirty and all(not out.dirty for out in ts.outputs)
    assert shader_preview.current(scene, ts), 'Export detached the native authoring material'
    current_outputs = {out.channel: out for out in ts.outputs}
    expected = {}
    for channel in ('ROUGHNESS', 'HEIGHT'):
        value = images.pixels(current_outputs[channel].image)
        expected[channel] = digest_array(quantized(value))
    expected['NORMAL'] = normal_evidence['exported_uint16_sha256']
    assert normal_evidence['raw_normal_input_sha256'] == digest_array(images.pixels(current_outputs['NORMAL'].image))
    assert normal_evidence['height_input_sha256'] == digest_array(images.pixels(current_outputs['HEIGHT'].image))
    assert normal_evidence['xy_rms'] > 1e-5 and normal_evidence['max_unit_length_error'] < 1e-5
    files = []
    for path in paths:
        decoded = read_png16(path)
        assert decoded.shape == (2048, 2048, 4)
        label = path.stem.rsplit('_', 1)[-1].upper()
        if label in expected:
            assert digest_array(decoded) == expected[label], f'{label} did not encode the current output'
        files.append({'path': str(path), 'sha256': digest_file(path), 'size': list(decoded.shape[:2]),
                      'bit_depth': 16, 'current_data_verified': label in expected})
    same_sources(original_sources, source_images(ts))
    assert geometry(scene) == original_geometry
    record['checks'] = ['real-ten-mesh-project-at-saved-2048', 'native-current-and-attached',
                        'actual-blue-coat-chips-edit-and-restoration-without-bake',
                        'native-save-preserves-dirty-output-pixels', 'saved-sources-packed-and-pixel-identical',
                        'reopen-preserves-complete-recipe-and-real-geometry',
                        'explicit-export-produces-eight-2048-png16-files',
                        'roughness-height-and-combined-normal-encode-current-output',
                        'export-reattaches-native-material', 'original-project-byte-identical']
    progress('complete', result='PASS', export_observation=exported.report(),
             exported_files=files, combined_normal=normal_evidence,
             source_sha256_after=digest_file(SOURCE))
    assert record['source_sha256_after'] == original_sha
    open_painter.unregister()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        record.update(result='FAIL', error=traceback.format_exc(),
                      source_sha256_after=digest_file(SOURCE))
        REPORT.write_text(json.dumps(record, indent=2, allow_nan=False) + '\n', encoding="utf-8")
        traceback.print_exc()
        raise
    print('ACTUAL_PROJECT_PERSISTENCE_EXPORT_PASS', REPORT, flush=True)
