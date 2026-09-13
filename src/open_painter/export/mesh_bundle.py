"""Evaluated .blend staging scene, final textures and a machine-readable manifest.

Generated instances are optionally realized in the staging scene only. Texture
exports cannot preserve their silhouettes; omission is recorded explicitly.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
from .exporter import export
from ..evaluation.evaluator import LayerEvaluator


def export_bundle(context,texture_sets,destination,geometry='ACTUAL'):
    if geometry not in {'ACTUAL','OMIT'}:
        raise ValueError('Choose actual generated geometry or explicitly omit it')
    destination=Path(destination)
    if destination.exists():
        raise ValueError('Bundle destination already exists')
    for ts in texture_sets:
        LayerEvaluator(ts.id_data).evaluate_texture_set(ts.id)
    scene=bpy.data.scenes.new('Open Painter Export')
    staged_objects=[]; meshes=[]; created=[]
    try:
        graph=context.evaluated_depsgraph_get()
        sources={ref.object for ts in texture_sets for ref in ts.assignments if ref.object}
        helpers={obj for obj in context.scene.objects if obj.get('open_painter_geometry_effect')=='INSTANCE_HELPER' and obj.parent in sources}
        triangles=0
        def stage(source,matrix,name):
            nonlocal triangles
            mesh=bpy.data.meshes.new_from_object(source,depsgraph=graph,preserve_all_data_layers=True)
            meshes.append(mesh)
            mesh.calc_loop_triangles(); triangles+=len(mesh.loop_triangles)
            if triangles>1000000:
                raise ValueError('Staged export exceeds 1,000,000 triangles; lower explicit geometry density')
            obj=bpy.data.objects.new(name,mesh); staged_objects.append(obj)
            scene.collection.objects.link(obj); obj.matrix_world=matrix
        for obj in sources:
            evaluated=obj.evaluated_get(graph)
            stage(evaluated,evaluated.matrix_world,obj.name)
        if geometry=='ACTUAL':
            for helper in helpers:
                evaluated=helper.evaluated_get(graph)
                stage(evaluated,evaluated.matrix_world,helper.name)
            for instance in graph.object_instances:
                if instance.is_instance and instance.parent and instance.parent.original in helpers:
                    stage(instance.object,instance.matrix_world,'OP Effect Instance')
        with TemporaryDirectory(prefix='op-bundle-') as temporary:
            root=Path(temporary)
            records=[]
            for index,ts in enumerate(texture_sets):
                folder=root/f'textures_{index:03d}'
                original=ts.export.directory
                try:
                    ts.export.directory=str(folder)
                    files=export(context,ts,ts.export)
                finally:
                    ts.export.directory=original
                records.append(dict(id=ts.id,name=ts.name,resolution=int(ts.resolution),
                    revision=ts.edit_serial,preset=ts.export.preset,
                    files=[str(path.relative_to(root)) for path in files]))
            from ..core.image_manager import pack_image
            for ts in texture_sets:
                for output in ts.outputs:
                    if output.image: pack_image(output.image)
            bpy.data.libraries.write(str(root/'scene.blend'),{scene},fake_user=True,compress=True)
            (root/'manifest.json').write_text(json.dumps(dict(format_version=1,
                generated_geometry=geometry,triangles=triangles,texture_sets=records,
                limitations=['Texture maps alone do not preserve generated silhouette or parallax']),indent=2))
            destination.mkdir(parents=True,exist_ok=False)
            for path in root.rglob('*'):
                if path.is_file():
                    target=destination/path.relative_to(root); target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open('xb') as stream:
                        created.append(target); stream.write(path.read_bytes())
        return destination/'manifest.json'
    except Exception:
        for path in reversed(created): path.unlink(missing_ok=True)
        raise
    finally:
        bpy.data.scenes.remove(scene)
        for obj in staged_objects:
            if obj.name in bpy.data.objects: bpy.data.objects.remove(obj,do_unlink=True)
        for mesh in meshes:
            if mesh.users==0: bpy.data.meshes.remove(mesh)
