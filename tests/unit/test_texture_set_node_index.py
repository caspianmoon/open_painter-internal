"""The texture set node's sync index answers as scans of the tree would.

``socket.links`` walks every link of a tree, so the sync looks nodes and links up in an index
read once per sync. It must still find the first node of a role, keep one link into a single
input (a new source replaces the old, as ``links.new`` does), allow several into a multi-input
socket, and forget what ``_drop`` removed. Runs in Blender; skipped without bpy.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

try:
    import bpy
except ImportError:
    bpy = None


def links_into(socket):
    return sorted((link.from_node.get('role'), link.from_socket.identifier) for link in socket.id_data.links
                  if link.to_socket == socket)


@unittest.skipIf(bpy is None, 'needs Blender')
class SyncIndexTests(unittest.TestCase):
    def setUp(self):
        from open_painter.core import texture_set_node
        self.module = texture_set_node
        self.tree = bpy.data.node_groups.new('Sync index probe', 'ShaderNodeTree')

    def tearDown(self):
        bpy.data.node_groups.remove(self.tree)

    def test_a_role_resolves_to_its_first_node_and_is_created_once(self):
        first = self.tree.nodes.new('ShaderNodeMath')
        first['role'] = 'Mix'
        second = self.tree.nodes.new('ShaderNodeMath')
        second['role'] = 'Mix'
        index = self.module._Index(self.tree)
        self.assertEqual(self.module._node(index, 'Mix', 'ShaderNodeMath', (0, 0)), first)
        created = self.module._node(index, 'Other', 'ShaderNodeMath', (10, 0))
        self.assertEqual(self.module._node(index, 'Other', 'ShaderNodeMath', (10, 0)), created)
        self.assertEqual(sum(node.get('role') == 'Other' for node in self.tree.nodes), 1)
        self.assertEqual(tuple(created.location), (10.0, 0.0))

    def test_connect_is_idempotent_and_a_new_source_replaces_the_old(self):
        index = self.module._Index(self.tree)
        a = self.module._node(index, 'A', 'ShaderNodeValue', (0, 0))
        b = self.module._node(index, 'B', 'ShaderNodeValue', (0, 100))
        target = self.module._node(index, 'T', 'ShaderNodeMath', (200, 0))
        for _ in range(3):
            self.module._connect(index, a.outputs[0], target.inputs[0])
        self.assertEqual(len(self.tree.links), 1)
        self.module._connect(index, b.outputs[0], target.inputs[0])
        self.assertEqual(links_into(target.inputs[0]), [('B', 'Value')])
        # The old source comes back: the index must not still believe it is linked.
        self.module._connect(index, a.outputs[0], target.inputs[0])
        self.assertEqual(links_into(target.inputs[0]), [('A', 'Value')])
        # A fresh index (the next sync) reads the same state.
        again = self.module._Index(self.tree)
        self.module._connect(again, a.outputs[0], target.inputs[0])
        self.assertEqual(len(self.tree.links), 1)

    def test_drop_forgets_the_removed_nodes_and_their_links(self):
        index = self.module._Index(self.tree)
        a = self.module._node(index, 'A', 'ShaderNodeValue', (0, 0))
        target = self.module._node(index, 'T', 'ShaderNodeMath', (200, 0))
        self.module._connect(index, a.outputs[0], target.inputs[0])
        self.module._drop(index, lambda node: node.get('role') == 'A')
        self.assertNotIn(('A', 'ShaderNodeValue'), index.nodes)
        recreated = self.module._node(index, 'A', 'ShaderNodeValue', (0, 0))
        self.module._connect(index, recreated.outputs[0], target.inputs[0])
        self.assertEqual(links_into(target.inputs[0]), [('A', 'Value')])


if __name__ == '__main__':
    unittest.main()
