"""Mask Graphs: Open Painter's own procedural node graphs.

A Custom Graph mask reads a Mask Graph, a node tree of Open Painter node types
(``tree``) that never enters Blender's shader editor. One definition of a graph
(``program``) feeds two evaluators kept apart so each can be measured and tuned
on its own: the engine emits it as its own GPU pass (``gpu``), the CPU reference
that export uses evaluates it with NumPy (``cpu``). ``edits`` turns node edits
into stack invalidations (numeric edits are uniforms, structural ones rebuild
only the graph's pass); ``templates`` builds starter graphs.

Masks that still point at a Blender shader group (files from 0.9.3–0.13) keep
the legacy path: ``evaluation.custom_graph`` bakes the group with Cycles.
"""


def is_native(tree):
    """A Mask Graph (as opposed to a legacy Blender shader group)."""
    from .tree import TREE_ID
    return tree is not None and getattr(tree, 'bl_idname', '') == TREE_ID
