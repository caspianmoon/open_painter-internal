"""Open Graph: a node view of a texture set's layer stack with real branching.

The graph is a projection of the authoring data (``Scene.open_painter``), never
a second document: nodes mirror layers, sockets mirror anchor producers and
consumers. Node additions, templates, grouping and links edit that same stack;
the compiled shader graph stays derived from it.
"""
