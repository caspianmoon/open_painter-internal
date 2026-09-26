"""Open Painter's own evaluation engine.

The layer stack is compiled into small per-unit GPU programs (``compiler`` +
``glsl``) and evaluated in texture space by ``runtime``; the result lands in the
texture set's channel images, which materials read through one Open Painter
Texture Set node. No Blender shader node tree is built for evaluation.
"""
