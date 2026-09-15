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
    custom_directory=str(root / ".blender-test/extension-repository"),
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
assert ts.preview_backend == 'LIVE' and not ts.outputs
material_manager.repair(ts)
graph = shader_preview.ensure(bpy.context.scene, ts)
with Metrics() as observed:
    stack.channel_for(ts.layers[0], 'ROUGHNESS').value = .37
    assert shader_preview.current(bpy.context.scene, ts)
    assert shader_preview.ensure(bpy.context.scene, ts) is graph
    assert observed.phases['image_write']['calls'] == 0
    assert observed.counts['shader_resource_requests'] == 0
assert not ts.outputs
print('INSTALLED_NATIVE_SHADER_PASS', shader_preview.diagnostics(bpy.context.scene, ts))
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
assert shader_preview.current(bpy.context.scene, ts)
print('INSTALLED_MATERIAL_DIRECTOR_PASS')
assert bpy.ops.open_painter.texture_set(action="REFRESH") == {"FINISHED"}
addon_utils.disable(module, default_set=True)
print(
    "INSTALL PASS: real ZIP installation, extension namespace, enable/disable, material evaluation"
)
