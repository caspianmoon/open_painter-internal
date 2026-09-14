"""Validate installed extension namespace using the supplied 2K artist project."""
from pathlib import Path
import hashlib, importlib, json, os, shutil, tomllib
import bpy, addon_utils
ROOT=Path(__file__).resolve().parents[2]
version=tomllib.loads((ROOT/'blender_manifest.toml').read_text())['version']
source=ROOT/'open painter test.blend'
original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
assert original_hash=='dc8d39b8faadf7e67764e7c6b2c6c2d47101ad23bd7440579226664fa3b6af79', 'Supplied acceptance project changed'
folder=ROOT/'.blender-test/project-native'
folder.mkdir(parents=True,exist_ok=True)
report_path=folder/'installed-project.json'
report_path.write_text(json.dumps({'result':'RUNNING','version':version})+'\n')
repo=bpy.context.preferences.extensions.repos.new(name='Project Package Validation',
    module='open_painter_project_test',custom_directory=str(folder/'installed-extension'))
archive=Path(os.environ.get('OPEN_PAINTER_TEST_ZIP',str(ROOT/'dist'/f'open_painter-{version}.zip')))
assert bpy.ops.extensions.package_install_files(filepath=str(archive),repo=repo.module,
                                                enable_on_install=True)=={'FINISHED'}
module='bl_ext.'+repo.module+'.open_painter'
assert addon_utils.check(module)[1]
assert importlib.import_module(module+'.constants').VERSION==version
runtime=importlib.import_module(module+'.evaluation.shader_preview')
materials=importlib.import_module(module+'.core.material_manager')
Metrics=importlib.import_module(module+'.evaluation.metrics').Metrics
copy=folder/'installed-project-check.blend'
shutil.copy2(source,copy)
bpy.ops.wm.open_mainfile(filepath=str(copy), use_scripts=False)
scene=bpy.context.scene
ts=scene.open_painter.texture_sets[scene.open_painter.active_texture_set]
assert ts.resolution=='2048' and len(ts.layers)==6
meshes=[obj for obj in scene.objects if obj.type=='MESH']
assert len(meshes)==10 and sum(len(obj.data.polygons) for obj in meshes)==315225
materials.repair(ts)
graph=runtime.flush(scene,ts)
assert runtime.current(scene,ts)
mask=next(layer for layer in ts.layers if layer.name=='Blue paint coat').masks[0]
original=mask.amount
revisions={out.channel:out.revision for out in ts.outputs}
with Metrics() as observed:
    mask.amount=.65
    assert runtime.current(scene,ts) and runtime.ensure(scene,ts) is graph
    assert runtime.diagnostics(scene,ts)['updated_bindings']==1
    assert observed.phases['image_write']['calls']==0
    assert observed.counts['shader_resource_requests']==0
    mask.amount=original
assert {out.channel:out.revision for out in ts.outputs}==revisions
assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
report={'result':'PASS','version':version,'archive':str(archive),'module':module,
        'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'source':str(source),'source_sha256':original_hash,'resolution':2048,
        'mesh_objects':10,'polygons':315225,'layers':len(ts.layers),
        'graph_nodes':graph.node_count,'native_edit_image_writes':0,'original_unchanged':True,
        'diagnostic':runtime.diagnostics(scene,ts)}
addon_utils.disable(module,default_set=True)
assert not hasattr(bpy.types.Scene,'open_painter')
report['registration_teardown']='PASS'
report['scope']='Installed namespace and native binding validation on the supplied project; visual held-slider evidence is separate.'
report_path.write_text(json.dumps(report,indent=2)+'\n')
print('INSTALLED_ACTUAL_PROJECT_PASS',version,str(report_path),flush=True)
