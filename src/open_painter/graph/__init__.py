"""Open Graph: a node view of a texture set's layer stack with real branching.

The graph is a projection of the authoring data (``Scene.open_painter``), never
a second document: nodes mirror layers, sockets mirror anchor producers and
consumers, and the only editable thing is a link, which is translated back into
an anchor reference. The compiled shader graph stays derived from the stack.
"""
