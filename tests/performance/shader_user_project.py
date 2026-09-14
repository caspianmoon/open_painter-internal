"""One authoring trace on the artist's actual 2K project; no synthetic mesh."""
from pathlib import Path
import sys, json, shutil, hashlib, time
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
import open_painter
from open_painter.core import material_manager, layer_stack
from open_painter.evaluation import shader_preview, cache
from open_painter.evaluation.metrics import Metrics

source=ROOT/'open painter test.blend'
folder=ROOT/'.blender-test/project-native'
folder.mkdir(parents=True,exist_ok=True)
original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
copy=folder/'authoring-trace.blend'
shutil.copy2(source,copy)
open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(copy))
scene=bpy.context.scene
ts=scene.open_painter.texture_sets[scene.open_painter.active_texture_set]
assert int(ts.resolution)>=2048, 'Project validation must retain at least the authored 2K resolution'
resolution=ts.resolution
meshes=[obj for obj in scene.objects if obj.type=='MESH']
geometry=[(o.name,len(o.data.vertices),len(o.data.polygons),[uv.name for uv in o.data.uv_layers]) for o in meshes]
assert len(meshes)==10 and sum(len(o.data.polygons) for o in meshes)==315225
layers={layer.name:layer for layer in ts.layers}
started=time.perf_counter()
material_manager.repair(ts)
build_ms=(time.perf_counter()-started)*1000
started=time.perf_counter()
graph=shader_preview.flush(scene,ts)
prepare_ms=(time.perf_counter()-started)*1000
assert shader_preview.current(scene,ts)
outputs={o.channel:(o.revision,o.dirty,o.image.as_pointer() if o.image else 0) for o in ts.outputs}
node_ids=graph.node_identities()

height=layer_stack.channel_for(layers['Blue paint coat'],'HEIGHT').effects[0]
controls=[
 ('Blue paint chips coverage',layers['Blue paint coat'].masks[0],'amount',.04,.88),
 ('Abrasion mask black level',layers['Scratched exposed steel'].masks[0],'black',.01,.54),
 ('Rust amount',layers['Oxide beneath paint'].masks[0],'amount',.03,.83),
 ('Recess dirt amount',layers['Recess dirt'].masks[0],'amount',.02,.85),
 ('Blue paint layer opacity',layers['Blue paint coat'],'opacity',.1,1.),
 ('Coating height output',height,'output_white',.52,.82),
 ('Steel roughness',layer_stack.channel_for(layers['Steel substrate'],'ROUGHNESS'),'value',.12,.91),
]
report={'source':str(source),'source_sha256':original_hash,'copy':str(copy),'blender':bpy.app.version_string,
        'resolution':int(resolution),'mesh_objects':len(meshes),'vertices':sum(len(o.data.vertices) for o in meshes),
        'polygons':sum(len(o.data.polygons) for o in meshes),'assigned_objects':[r.object.name for r in ts.assignments],
        'material':ts.name,'layers':[l.name for l in ts.layers],'graph_nodes':graph.node_count,
        'cold_build_ms':build_ms,'isolated_input_preparation_ms':prepare_ms,'controls':[],
        'measurement':'Synchronous RNA callback and normal scheduler work on the actual project. No viewport latency claim; held native slider screenshots are measured separately.'}
for label,owner,field,low,high in controls:
    original=float(getattr(owner,field))
    samples=[]
    with Metrics() as observed:
        for fraction in np.r_[np.linspace(0,1,17),np.linspace(1,0,17)[1:],np.linspace(0,1,17)[1:]]:
            started=time.perf_counter()
            setattr(owner,field,float(low+(high-low)*fraction))
            cache.tick()
            samples.append((time.perf_counter()-started)*1000)
            info=shader_preview.diagnostics(scene,ts)
            assert shader_preview.current(scene,ts),info
            assert not info['raster_boundaries_queued'] and not info['pending_resources'],info
            assert graph.node_identities()==node_ids
        setattr(owner,field,original)
        cache.tick()
    metrics=observed.report()
    for phase in ('source_generation','masking','filters','blending','image_write','geometry'):
        assert metrics['phases'][phase]['calls']==0,(label,metrics)
    for counter in ('shader_resource_requests','shader_resource_publications','live_channel_publications','shader_graph_builds'):
        assert observed.counts[counter]==0,(label,counter,observed.counts[counter])
    assert {o.channel:o.revision for o in ts.outputs}=={k:v[0] for k,v in outputs.items()}
    record={'label':label,'field':field,'changes':len(samples),'median_ms':float(np.median(samples)),
            'p95_ms':float(np.percentile(samples,95)),'maximum_ms':max(samples),
            'last_dependency_edit':shader_preview.diagnostics(scene,ts),'metrics':metrics}
    report['controls'].append(record)
    print('ACTUAL_PROJECT_CONTROL',label,record['median_ms'],record['p95_ms'],'ms; raster=0',flush=True)
# On the existing coating Levels, bypassed parameters remain editable. Invalid
# activation retains the valid graph, and restoring parameters recovers it.
original=(height.strength,height.black,height.white)
height.strength=0
height.black=.8
height.white=.2
assert shader_preview.current(scene,ts)
height.strength=1
assert shader_preview.diagnostics(scene,ts)['state']=='FAILED'
assert shader_preview.ensure(scene,ts) is graph
height.white=original[2]
height.black=original[1]
height.strength=original[0]
assert shader_preview.current(scene,ts)
assert ts.resolution==resolution
assert geometry==[(o.name,len(o.data.vertices),len(o.data.polygons),[uv.name for uv in o.data.uv_layers]) for o in meshes]
assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
report['bypassed_coating_levels_recovery']='PASS'
report['original_file_unchanged']=True
report['result']='PASS'
(folder/'authoring-trace.json').write_text(json.dumps(report,indent=2)+'\n')
print('ACTUAL_PROJECT_AUTHORING_TRACE_PASS',flush=True)
