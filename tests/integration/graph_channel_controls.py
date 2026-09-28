"""Graph redraws bind controls to live channels after raw collection changes."""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/integration'))
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.graph import mirror
from ui_layout_recorder import Recorder


class Bindings(Recorder):
    def _child(self, kind, **kw):
        self.items.append((kind, kw))
        return Bindings(self.items)

    def prop(self, owner, name, **kw):
        super().prop(owner, name, **kw)
        self.items[-1][1].update(pointer=owner.as_pointer(), field_value=getattr(owner, name),
                                 enabled=self.enabled)

    def template_ID(self, owner, name, **kw):
        super().template_ID(owner, name, **kw)
        self.items[-1][1]['pointer'] = owner.as_pointer()


open_painter.register()
ts = sets.create(bpy.context.scene, 'Graph channel controls', 512)
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'FILL', 'Live channels')
layer_id = layer.id
tree = mirror.ensure_tree(ts)
node = mirror.nodes_by_id(tree)[layer_id]


def check():
    # Deliberately derive the expected binding through the original public
    # single-channel resolver. Controls must preserve its first-match rule.
    before = stack.snapshot(layer)
    layout = Bindings()
    mirror.draw_node(node, bpy.context, layout)
    ordered = [stack.channel_for(layer, name) for name in CHANNELS]
    ordered = [prop for prop in ordered if prop is not None]
    assert [row['pointer'] for row in layout.props('enabled')] == [prop.as_pointer() for prop in ordered]
    enabled = [prop for prop in ordered if prop.enabled]
    assert [row['pointer'] for kind, row in layout.items if kind == 'template_ID'] == [prop.as_pointer() for prop in enabled]
    values = [row for row in layout.props() if row['name'] in {'color', 'value'}]
    assert [row['pointer'] for row in values] == [prop.as_pointer() for prop in enabled]
    textured = [prop for prop in enabled if prop.image or prop.detail_image or prop.procedural]
    assert [row['pointer'] for row in layout.props('fill_scale')] == ([textured[0].as_pointer()] if textured else [])
    selected = stack.channel_for(layer, bpy.context.scene.open_painter.properties_channel)
    inspector = layout.props('procedural_scale')
    if selected is not None and selected.procedural and not selected.anchor:
        assert len(inspector) == 1 and inspector[0]['pointer'] == selected.as_pointer()
        assert inspector[0]['enabled'] == selected.enabled
    else:
        assert not inspector
    assert stack.snapshot(layer) == before, 'drawing authored document state'
    return layout


check()
bpy.context.scene.open_painter.properties_channel = 'ROUGHNESS'
prop = stack.channel_for(layer, 'ROUGHNESS')
prop.procedural = 'CHECKER'
prop.projection = 'TRIPLANAR'
assert check().props('fill_triplanar_hardness')
prop.enabled = False
check()  # disabled inspector keeps its target while normal value controls hide
prop.enabled = True

# Collection reorder/removal and enum identifiers bypass update callbacks.
serial = ts.edit_serial
layer.channels.move(0, len(layer.channels) - 1)
assert ts.edit_serial == serial
check()
normal = next(i for i, item in enumerate(layer.channels) if item.channel == 'NORMAL')
layer.channels.remove(normal)
assert ts.edit_serial == serial
check()

# Imported/replayed data may temporarily contain duplicate channel identifiers.
# The first one is still authoritative, including after a reorder.
duplicate = layer.channels.add()
duplicate.channel = 'ROUGHNESS'
with stack.replay(ts):
    duplicate.value, duplicate.procedural = .12, 'NOISE'
    check()
    serial = ts.edit_serial
    layer.channels.move(len(layer.channels) - 1, 0)
    assert ts.edit_serial == serial
    first = check()
    assert abs(first.props('value')[0]['field_value'] - .12) < 1e-6
    layer.channels[0].channel = 'HEIGHT'
    bpy.context.scene.open_painter.properties_channel = 'HEIGHT'
    check()
    layer.channels.clear()
    check()

open_painter.unregister()
print('GRAPH_CHANNEL_CONTROLS_PASS', flush=True)
