"""Portable native mask graphs, restricted to registered Open Painter node types."""
import bpy


def encode(tree, n):
    from ..core.layer_stack import snapshot
    from ..procedural_graph import is_native
    from ..procedural_graph.program import build
    if not is_native(tree):
        raise ValueError('Bake legacy Custom Graph masks before saving a material')
    build(tree)  # reject ambiguous outputs and cycles before writing assets
    base = {p.identifier for p in bpy.types.Node.bl_rna.properties}
    nodes = list(tree.nodes)
    records = []
    for node in nodes:
        if node.bl_idname == 'OPMGText':
            from ..evaluation.text_source import ensure
            ensure(node.text, n)
        settings = {key: value for key, value in snapshot(node).items() if key not in base}
        records.append({'type': node.bl_idname, 'name': node.name, 'location': list(node.location), 'settings': settings,
                        'inputs': {s.identifier: float(s.default_value) for s in node.inputs if hasattr(s, 'default_value')}})
    return {'version': 1, 'name': tree.name, 'nodes': records,
            'links': [[nodes.index(link.from_node), link.from_socket.identifier,
                       nodes.index(link.to_node), link.to_socket.identifier] for link in tree.links]}


def decode(data):
    from ..procedural_graph.program import KINDS, build
    from ..procedural_graph.tree import new_tree
    from ..core.layer_stack import restore
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unsupported Mask Graph document')
    nodes = data.get('nodes')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 256:
        raise ValueError('Invalid Mask Graph node count')
    if any(not isinstance(node, dict) or node.get('type') not in {*KINDS, 'OPMGOutput'} for node in nodes):
        raise ValueError('Unsupported Mask Graph node')
    links = data.get('links', [])
    if not isinstance(links, list) or len(links) > 2048:
        raise ValueError('Invalid Mask Graph links')
    tree = new_tree(str(data.get('name', 'Mask Graph'))[:128])
    try:
        created = []
        base = {p.identifier for p in bpy.types.Node.bl_rna.properties}
        for record in nodes:
            node = tree.nodes.new(record['type'])
            node.name = str(record.get('name', node.name))[:128]
            node.location = record.get('location', (0, 0))
            settings = record.get('settings', {})
            if not isinstance(settings, dict) or any(key in base or key not in node.bl_rna.properties for key in settings):
                raise ValueError('Invalid Mask Graph settings')
            restore(settings, node)
            for name, value in record.get('inputs', {}).items():
                node.inputs[name].default_value = value
            created.append(node)
        for a, output, b, input in links:
            if type(a) != int or type(b) != int or not 0 <= a < len(created) or not 0 <= b < len(created):
                raise ValueError('Invalid Mask Graph link')
            tree.links.new(created[a].outputs[output], created[b].inputs[input])
        build(tree)
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise
