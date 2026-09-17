"""Material aging (0.10.0): tell Open Painter what a thing is, where it lived, how it was used
and how old it is, and it ages the material accordingly.

``materials`` and ``coatings`` describe substrates and protection layers by their physical
response; ``environments`` turns places, usage and maintenance into exposure parameters;
``model`` integrates the aging processes over the asset's history into a state; ``document``
turns a state into a layer recipe (the geometry-aware generators place every outcome) and
``engine`` keeps the resulting folder in step with the settings, nondestructively.
"""
