"""Evaluated .blend staging scene, final textures and a machine-readable manifest.

Generated instances are optionally realized in the staging scene only. Texture
exports cannot preserve their silhouettes; omission is recorded explicitly.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
from .exporter import export
from ..baking.bake_back import helpers_of
from ..core.image_manager import pack_image
from ..evaluation.evaluator import LayerEvaluator


def _pack_material_images(materials):
    # libraries.write stores a generated image as its packed bytes, not its pixel
    # buffer: strokes, boundary rasters and layer caches are packed by the save
    # handler only, so a bundle taken between saves must pack what the live
    # material references, through group trees.
    seen=set()
    def walk(tree):
        if tree is None or tree.as_pointer() in seen: return
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            if node.bl_idname=='ShaderNodeGroup': walk(node.node_tree)
            elif node.bl_idname=='ShaderNodeTexImage' and node.image and node.image.has_data: pack_image(node.image)
    for material in materials:
        if material and material.use_nodes: walk(material.node_tree)


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
        # Stitch and weld helpers hang off the extracted curve, not the source: resolve
        # ownership as Bake Back does so every effect kind reaches the bundle.
        helpers={obj for ts in texture_sets for obj in helpers_of(ts,context.scene)}
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
                files=export(context,ts,ts.export,directory=folder)   # the set's own settings stay as saved
                records.append(dict(id=ts.id,name=ts.name,resolution=int(ts.resolution),
                    udim_tile=ts.udim_tile or None,udim_group=ts.udim_group or None,
                    revision=ts.edit_serial,preset=ts.export.preset,
                    files=[str(path.relative_to(root)) for path in files]))
            for ts in texture_sets:
                for output in ts.outputs:
                    if output.image: pack_image(output.image)
            _pack_material_images(ts.material for ts in texture_sets)
            # Blender 5.2 crashes copying a never-synced view layer of a scene created after
            # a depsgraph update (0.9.7): sync the staging layer before it is written.
            scene.view_layers[0].update()
            bpy.data.libraries.write(str(root/'scene.blend'),{scene},fake_user=True,compress=True)
            (root/'manifest.json').write_text(json.dumps(dict(format_version=1,
                generated_geometry=geometry,triangles=triangles,texture_sets=records,
                limitations=['Texture maps alone do not preserve generated silhouette or parallax']),indent=2))
            # One transaction for the whole bundle: every file lands or none does.
            from .transaction import Transaction
            transaction=Transaction()
            for path in root.rglob('*'):
                if path.is_file():
                    transaction.add(destination/path.relative_to(root),path)
            destination.mkdir(parents=True,exist_ok=False)
            created.append(destination)
            outcome=transaction.commit(overwrite=False)
            if outcome.cleanup_pending:
                from ..utils.logging import logger
                logger.warning('Bundle %s: %s',destination,outcome.message)
        return destination/'manifest.json'
    except Exception:
        for path in reversed(created):
            if path.is_dir():
                import shutil
                shutil.rmtree(path,ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        raise
    finally:
        bpy.data.scenes.remove(scene)
        for obj in staged_objects:
            if obj.name in bpy.data.objects: bpy.data.objects.remove(obj,do_unlink=True)
        for mesh in meshes:
            if mesh.users==0: bpy.data.meshes.remove(mesh)
