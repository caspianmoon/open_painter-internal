"""Bypassed invalid effects remain editable and validate on activation."""
from pathlib import Path
import sys
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def bypass_lifecycle():
    """Actual RNA rebuild, rejected activation, and non-destructive recovery."""
    import open_painter
    from open_painter.core import layer_stack as stack, texture_set_manager as sets
    from open_painter.evaluation import cache, shader_preview
    from open_painter.evaluation.metrics import Metrics
    from open_painter.utils.ids import new_id

    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    ts = sets.create(scene, "Bypassed Levels validation", 512)
    layer = stack.add(ts, "FILL")
    channel = stack.channel_for(layer, "ROUGHNESS")
    levels = channel.effects.add()
    levels.kind, levels.strength = "LEVELS", 0.
    levels.black, levels.white = .8, .2
    mask = layer.masks.add()
    mask.id, mask.source = new_id(), "FILTER"
    mask.effect.kind, mask.effect.enabled = "LEVELS", False
    mask.effect.black, mask.effect.white = .8, .2
    normal = stack.channel_for(layer, "NORMAL")
    normal.enabled = True
    combine = normal.effects.add()
    combine.kind, combine.strength = "NORMAL_COMBINE", 0.
    disabled_spatial = layer.masks.add()
    disabled_spatial.id, disabled_spatial.source = new_id(), "FILTER"
    disabled_spatial.effect.kind, disabled_spatial.effect.enabled = "GAUSSIAN_BLUR", False
    disabled_normal = layer.masks.add()
    disabled_normal.id, disabled_normal.source = new_id(), "FILTER"
    disabled_normal.effect.kind, disabled_normal.effect.enabled = "NORMAL_COMBINE", False
    with Metrics() as initial:
        graph = shader_preview.flush(scene, ts)
    assert initial.counts["shader_resource_requests"] == 0, initial.report()
    assert shader_preview.current(scene, ts)
    # A topology edit must recompile the same bypassed recipe successfully (in place).
    patches = shader_preview.diagnostics(scene, ts)["graph_patches"]
    layer.blend_mode = "MULTIPLY"
    rebuilt = shader_preview.flush(scene, ts)
    assert rebuilt is graph and shader_preview.current(scene, ts)
    assert shader_preview.diagnostics(scene, ts)["graph_patches"] == patches + 1
    identities = rebuilt.node_identities()
    with Metrics() as observed:
        levels.strength = 1.
        assert shader_preview.diagnostics(scene, ts)["state"] == "FAILED"
        assert shader_preview.ensure(scene, ts) is rebuilt
        assert rebuilt.node_identities() == identities
        levels.white = .9
        shader_preview.tick(scene, ts)   # the engine renders the edit
        assert shader_preview.current(scene, ts)
        assert shader_preview.ensure(scene, ts) is rebuilt
    assert observed.phases["image_write"]["calls"] == 0
    assert observed.counts["shader_resource_requests"] == 0
    # Missing Normal Combine detail is likewise a valid bypass, and activation
    # must reject before the old shader's strength socket can change.
    combine.strength = 1.
    assert shader_preview.diagnostics(scene, ts)["state"] == "FAILED"
    assert shader_preview.ensure(scene, ts) is rebuilt
    assert rebuilt.node_identities() == identities
    image = bpy.data.images.new("Recovered normal detail", width=8, height=8, float_buffer=True)
    image.generated_color = (.5, .5, 1., 1.)
    image.colorspace_settings.name = "Non-Color"
    combine.image = image
    shader_preview.flush(scene, ts)
    assert shader_preview.current(scene, ts)
    mask.effect.enabled = True
    shader_preview.tick(scene, ts)
    assert shader_preview.diagnostics(scene, ts)["state"] == "FAILED"
    mask.effect.white = .9
    shader_preview.flush(scene, ts)
    assert shader_preview.current(scene, ts)
    disabled_normal.effect.enabled = True
    shader_preview.tick(scene, ts)
    assert shader_preview.diagnostics(scene, ts)["state"] == "FAILED"
    disabled_normal.effect.enabled = False
    shader_preview.flush(scene, ts)
    assert shader_preview.current(scene, ts)
    # Hidden published sources remain dependencies; an unrelated hidden invalid
    # effect must not prevent compilation or later parameter updates.
    hidden = stack.add(ts, "FILL", "Hidden published source")
    hidden.visible = False
    hidden_prop = stack.channel_for(hidden, "ROUGHNESS")
    hidden_levels = hidden_prop.effects.add()
    hidden_levels.kind, hidden_levels.strength = "LEVELS", 0.
    hidden_levels.black, hidden_levels.white = .8, .2
    unrelated = stack.add(ts, "FILL", "Unreferenced hidden source")
    unrelated.visible = False
    ignored = stack.channel_for(unrelated, "ROUGHNESS").effects.add()
    ignored.kind, ignored.black, ignored.white = "LEVELS", .8, .2
    reference = layer.masks.add()
    reference.id, reference.source = new_id(), "REFERENCE"
    reference.reference_layer, reference.reference_channel = hidden.id, "ROUGHNESS"
    reference.reference_stage = "POST_FILTER"
    published = shader_preview.flush(scene, ts)
    assert shader_preview.current(scene, ts)
    hidden_levels.strength = 1.
    assert shader_preview.diagnostics(scene, ts)["state"] == "FAILED"
    assert shader_preview.ensure(scene, ts) is published
    hidden_levels.white = .9
    shader_preview.tick(scene, ts)
    assert shader_preview.current(scene, ts)
    ignored.gamma = .7
    shader_preview.tick(scene, ts)
    assert shader_preview.current(scene, ts)
    print("SHADER_BYPASS_LIFECYCLE_PASS", flush=True)
    open_painter.unregister()


bypass_lifecycle()
