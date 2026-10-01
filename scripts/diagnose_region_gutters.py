"""Replay an unchanged saved Director proposal for an isolated sampling diagnostic.

Run with Blender --background --factory-startup --python this_file -- ... .
This is an engine diagnostic, never a repaired benchmark result or provider input.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--product-root', type=Path, required=True)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--saved-candidate', type=Path)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path[:0] = [str(args.product_root.resolve()/'src'),str(ROOT/'tests/benchmarks/material_director')]
import open_painter
from open_painter.authoring import context as authoring, document, regions, scene as author_scene
from open_painter.core import layer_stack
from open_painter.evaluation import cache, shader_preview, field_jobs, mesh_masks
from open_painter.export.padding import ownership
from capture_evidence import config_for, render_views, scene_record, write_json

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

out = args.output.resolve()
out.mkdir(parents=True,exist_ok=False)
run = args.run.resolve()
trace_path = run/'provider_and_tool_trace.jsonl'
trace = [json.loads(line)['data'] for line in trace_path.read_text(encoding="utf-8").splitlines()]
previews = [event for event in trace if event.get('event')=='tool' and event.get('tool')=='preview_proposal']
event = previews[-1]
proposal_text = event['arguments']['proposal_json']
proposal = json.loads(proposal_text)
source = (args.saved_candidate or run/'before.blend').resolve()
source_hash = sha(source)
trace_hash = sha(trace_path)
report = dict(track='ISOLATED_REGION_SAMPLING_ENGINE_DIAGNOSTIC',provider_calls=0,
    quality='NOT_SCORED',reviewer='PENDING',excluded_from_live_benchmark_results=True,
    source=str(source),source_sha256=source_hash,trace_sha256=trace_hash,
    original_call_id=event['call_id'],proposal_sha256=hashlib.sha256(proposal_text.encode()).hexdigest(),
    product_root=str(args.product_root.resolve()),loaded_module=open_painter.__file__,
    implementation={name:sha(args.product_root/'src/open_painter/evaluation'/name)
                    for name in ('mesh_masks.py','shader_graph.py')})
(out/'unchanged_proposal.json').write_text(proposal_text+'\n', encoding="utf-8")
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
bpy.ops.wm.open_mainfile(filepath=str(source))
scene = bpy.context.scene
config = config_for(scene)
guard = scene_record(scene,config['target_set_ids'])
if not args.saved_candidate:
    registry = {}
    # Recreate only the original read-only spatial queries, not new authored choices.
    first = next(iter(scene.open_painter.texture_sets))
    objects = regions.bindings(authoring.resolve_bindings(authoring.bindings(bpy.context,first)),registry)
    for query in trace:
        if query.get('event')=='tool' and query.get('tool')=='query_region':
            spec = dict(query['arguments'])
            identifier = spec.pop('texture_set_id')
            ts = next(item for item in scene.open_painter.texture_sets if item.id==identifier)
            regions.query(ts,objects,spec,registry)
    author_scene.apply(scene,proposal,operation_id=event['request_id'],
        digests={ts.id:document.digest(ts) for ts in scene.open_painter.texture_sets},objects=objects,images={})
documents = {ts.id:document.canonical(document.snapshot(ts)) for ts in scene.open_painter.texture_sets}

started = time.monotonic()
while time.monotonic()-started < 120:
    field_jobs.tick(scene,budget=.05)
    complete = True
    for ts in scene.open_painter.texture_sets:
        shader_preview.tick(scene,ts)
        info = shader_preview.diagnostics(scene,ts)
        if info.get('error'):raise RuntimeError(info['error'])
        complete &= shader_preview.current(scene,ts)
    if complete:break
else:raise TimeoutError('Diagnostic preview did not settle')

report['views'] = render_views(bpy.context,out,'after_create',config)
if report['views']['status']!='CAPTURED':raise RuntimeError(report['views'])
report['masks'] = {}
for ts in scene.open_painter.texture_sets:
    n = int(ts.resolution)
    owned = ownership(ts,n).covered
    for layer in ts.layers:
        for mask in layer.masks:
            if mask.source!='MESH' or mask.mesh.kind!='REGION':continue
            raw = mesh_masks.raster(ts,mask,n)
            sampled = mesh_masks.evaluate(ts,mask,n,0,n,{})
            entry=dict(layer=layer.name,selector=document.encode(dict(mesh=layer_stack.snapshot(mask.mesh))),
                raw_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                evaluated_sha256=hashlib.sha256(sampled.tobytes()).hexdigest(),
                maximum_owned_delta=float(np.abs(raw[owned]-sampled[owned]).max()),
                changed_unowned_texels=int(np.count_nonzero((raw!=sampled)&~owned)))
            report['masks'][ts.id+':'+mask.id]=entry
report['documents_sha256'] = hashlib.sha256(json.dumps(documents,sort_keys=True).encode()).hexdigest()
report['protected_scene_unchanged'] = scene_record(scene,config['target_set_ids'])==guard
assert report['protected_scene_unchanged']
assert documents=={ts.id:document.canonical(document.snapshot(ts)) for ts in scene.open_painter.texture_sets}
assert sha(source)==source_hash and sha(trace_path)==trace_hash
report['source_unchanged'] = True
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(out/'candidate.blend'))
report['saved_candidate'] = dict(path=str(out/'candidate.blend'),sha256=sha(out/'candidate.blend'))
report['status'] = 'CAPTURED'
write_json(out/'diagnostic.json',report)
print('REGION_GUTTER_UNCHANGED_PROPOSAL_CAPTURED',str(out),flush=True)
