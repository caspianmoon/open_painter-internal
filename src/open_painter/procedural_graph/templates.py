"""Starter Mask Graphs: what a new Custom Graph mask and the graph templates begin with."""
import bpy

from .tree import new_tree

KINDS = ('NOISE_RAMP', 'CELLULAR_RAMP', 'LAYERED_NOISE')


def _node(tree, idname, location, **values):
    node = tree.nodes.new(idname)
    node.location = location
    for name, value in values.items():
        setattr(node, name, value)
    return node


def _ramp(node, low, high):
    node.stops[0].position, node.stops[1].position = low, high


def build(kind, name):
    """A new Mask Graph: Noise → Ramp (the default), Cells → Ramp, or two noises multiplied → Ramp."""
    from ..constants import OWNER
    tree = new_tree(name)
    try:
        tree[OWNER] = True
        coordinates = _node(tree, 'OPMGCoordinates', (-700, 0))
        if kind == 'CELLULAR_RAMP':
            source = _node(tree, 'OPMGCells', (-450, 0), kind='WORLEY', scale=8.0)
        else:
            source = _node(tree, 'OPMGNoise', (-450, 0), kind='FBM', scale=8.0)
        tree.links.new(coordinates.outputs['UV'], source.inputs['Vector'])
        signal = source.outputs['Value']
        if kind == 'LAYERED_NOISE':
            detail = _node(tree, 'OPMGNoise', (-450, -260), kind='FBM', scale=36.0, seed=7)
            tree.links.new(coordinates.outputs['UV'], detail.inputs['Vector'])
            multiply = _node(tree, 'OPMGMath', (-200, 0), operation='MULTIPLY')
            tree.links.new(signal, multiply.inputs['A'])
            tree.links.new(detail.outputs['Value'], multiply.inputs['B'])
            signal = multiply.outputs['Value']
        ramp = _node(tree, 'OPMGRamp', (40, 0))
        _ramp(ramp, *((.1, .45) if kind == 'LAYERED_NOISE' else (.25, .75)))
        tree.links.new(signal, ramp.inputs['Fac'])
        output = _node(tree, 'OPMGOutput', (300, 0))
        tree.links.new(ramp.outputs['Value'], output.inputs['Coverage'])
        tree.use_fake_user = True
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise
