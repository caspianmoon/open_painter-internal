"""Install the built ZIP using Blender's real extension installer in a temporary profile."""

from pathlib import Path
import tomllib
import os
import bpy, addon_utils, importlib

root = Path(__file__).resolve().parents[2]
version = tomllib.loads((root / "blender_manifest.toml").read_text())["version"]
repo = bpy.context.preferences.extensions.repos.new(
    name="Open Painter Test",
    module="open_painter_test",
    custom_directory=os.environ.get('OP_INSTALL_TEST_REPOSITORY', str(root / ".blender-test/extension-repository")),
)
print("INSTALL_REPO", repo.module, repo.directory)
result = bpy.ops.extensions.package_install_files(
    filepath=os.environ.get("OPEN_PAINTER_TEST_ZIP", str(root / "dist" / f"open_painter-{version}.zip")),
    repo=repo.module,
    enable_on_install=True,
)
assert result == {"FINISHED"}, result
module = "bl_ext.open_painter_test.open_painter"
assert addon_utils.check(module)[1]
assert importlib.import_module(module + ".constants").VERSION == version
composer = importlib.import_module(module + ".smart_materials.composer")
generated = importlib.import_module(module + ".smart_materials.generated_sources")
assert len(composer.CATALOG) == 50
for recipe in composer.CATALOG.values():
    composer.validate(recipe)
assert generated.ROOT.resolve().is_relative_to(Path(repo.directory).resolve())
for identifier in sorted(generated.SOURCE_IDS):
    image = generated.load(identifier)
    assert image.packed_file and image.colorspace_settings.name == "Non-Color"
    assert all(image.size)
    bpy.data.images.remove(image)
assert callable(importlib.import_module(module + ".export.surface_normal").combined)
print("INSTALLED_LIBRARY_PASS", len(composer.CATALOG), "recipes", len(generated.SOURCE_IDS), "generated images")
for _ in range(3):
    addon_utils.disable(module, default_set=True)
    assert not hasattr(bpy.types.Scene, "open_painter")
    addon_utils.enable(module, default_set=True)
    assert hasattr(bpy.types.Scene, "open_painter")
assert bpy.ops.open_painter.texture_set(action="ADD") == {"FINISHED"}
assert bpy.ops.open_painter.texture_set(action="ASSIGN") == {"FINISHED"}
assert bpy.ops.open_painter.layer(action="FILL") == {"FINISHED"}
ts = bpy.context.scene.open_painter.texture_sets[0]
ts.resolution = "512"
shader_preview = importlib.import_module(module + '.evaluation.shader_preview')
material_manager = importlib.import_module(module + '.core.material_manager')
stack = importlib.import_module(module + '.core.layer_stack')
Metrics = importlib.import_module(module + '.evaluation.metrics').Metrics
assert ts.preview_backend == 'LIVE'


def rendered(ts):
    """The engine renders an edit on its next ticks; True once the channel images are current."""
    for _ in range(300):
        shader_preview.tick(bpy.context.scene, ts)
        if shader_preview.current(bpy.context.scene, ts):
            return True
    return False


material_manager.repair(ts)
graph = shader_preview.ensure(bpy.context.scene, ts)
assert rendered(ts), shader_preview.diagnostics(bpy.context.scene, ts)
if os.environ.get('OP_EXPECT_NATIVE') == '1':
    # The bundled library and binding load from the installed extension, with no developer
    # override; the native scheduler runs on the host backends (Metal, or those opted in).
    import gpu
    assert 'OP_LIBOPENPAINTER_PATH' not in os.environ
    info = shader_preview.diagnostics(bpy.context.scene, ts)
    lifecycle = importlib.import_module(module + '.native_bridge.lifecycle')
    backend = gpu.platform.backend_type_get()
    expected = 'c17-host-' + backend.lower() if backend in lifecycle.host_backends() else 'legacy'
    assert info['engine']['execution_backend'] == expected, info
    loader = importlib.import_module(module + '.native_bridge.loader')
    assert loader.find_library().is_relative_to(Path(repo.directory).resolve())
    native = lifecycle.diagnostics()
    assert native['native_loaded'] and native['compiled_binding'], native
    assert lifecycle.cpu_library() is not None
    print('INSTALLED_NATIVE_PASS', expected, loader.find_library())
with Metrics() as observed:
    stack.channel_for(ts.layers[0], 'ROUGHNESS').value = .37
    assert rendered(ts), shader_preview.diagnostics(bpy.context.scene, ts)
    assert shader_preview.ensure(bpy.context.scene, ts) is graph   # a numeric edit rebuilds nothing
    assert observed.counts['shader_resource_requests'] == 0
    import gpu
    if gpu.platform.backend_type_get() != 'VULKAN':
        assert observed.phases['image_write']['calls'] == 0   # published on the GPU, not through pixels
# The engine renders into the set's channel images, which the material reads through one node.
texture_set_node = importlib.import_module(module + '.core.texture_set_node')
assert ts.outputs and all(output.image is not None for output in ts.outputs)
assert [n.bl_idname for n in ts.material.node_tree.nodes].count(texture_set_node.NODE_ID) == 1
# A Custom Graph mask gets a Mask Graph (Open Painter's own nodes) and renders on the GPU.
procedural_graph = importlib.import_module(module + '.procedural_graph')
properties_panel = importlib.import_module(module + '.ui.properties_panel')
new_id = importlib.import_module(module + '.utils.ids').new_id
mask = ts.layers[0].masks.add()
mask.id, mask.source = new_id(), 'GRAPH'
mask.node_group = properties_panel.starter_graph('Installed Mask Graph')
assert procedural_graph.is_native(mask.node_group)
assert rendered(ts), shader_preview.diagnostics(bpy.context.scene, ts)
units = shader_preview.session_graph(bpy.context.scene, ts).units
assert any(key[0] == 'GRAPH' for key in units), sorted(units)
print('INSTALLED_NATIVE_SHADER_PASS', shader_preview.diagnostics(bpy.context.scene, ts))
assert importlib.import_module(module + '.smart_materials.format').VERSION == 5
mask.source = 'TEXT'
mask.text.content = 'Installed text'
assert rendered(ts), shader_preview.diagnostics(bpy.context.scene, ts)
assert mask.text.image is not None and mask.text.image.packed_file
brush_graph = importlib.import_module(module + '.painting.brush_graph')
tree = bpy.data.node_groups.new('Installed Brush Graph', 'OpenPainterGraph')
tree[brush_graph.MARKER] = True
inputs, output = tree.nodes.new('OPBrushInputs'), tree.nodes.new('OPBrushOutput')
tree.links.new(inputs.outputs['Pressure'], output.inputs['Flow'])
program = brush_graph.compile(tree)
assert abs(brush_graph.evaluate(program, dict(brush_graph.INPUTS, Pressure=.4), 1)['Flow'] - .4) < 1e-6
print('INSTALLED_AUTHORING_PASS')
director = importlib.import_module(module + '.ai.session')
document = importlib.import_module(module + '.authoring.document')
transactions = importlib.import_module(module + '.authoring.transactions')
assert director.preferences().as_pointer() == bpy.context.preferences.addons[module].preferences.as_pointer()
assert hasattr(bpy.types.WindowManager, 'open_painter_director')
assert not director.preferences().director_share_context
settings = bpy.context.window_manager.open_painter_director
assert settings.send_view and settings.send_maps and settings.generate_textures
assert hasattr(settings, 'reference_path')
settings.scope = 'SCENE'
assert settings.scope == 'SCENE'
context_api = importlib.import_module(module + '.authoring.context')
assert {'get_material_context', 'propose_scene_changes'} <= {tool['name'] for tool in context_api.tools(True)}
assert 'Coordinating a full scene' in context_api.INSTRUCTIONS
settings.scope = 'SET'
receipt = transactions.apply(ts, dict(version=1, summary='Installed Director', commands=[
    dict(op='set_channel', layer_id=ts.layers[0].id, channel='ROUGHNESS', values=dict(value=.62))]),
    operation_id='installed-director', expected_digest=document.digest(ts))
assert receipt['changes'] and abs(stack.channel_for(ts.layers[0], 'ROUGHNESS').value - .62) < 1e-6
assert rendered(ts), shader_preview.diagnostics(bpy.context.scene, ts)
print('INSTALLED_MATERIAL_DIRECTOR_PASS')
assert bpy.ops.open_painter.texture_set(action="REFRESH") == {"FINISHED"}
addon_utils.disable(module, default_set=True)
print(
    "INSTALL PASS: real ZIP installation, extension namespace, enable/disable, material evaluation"
)
