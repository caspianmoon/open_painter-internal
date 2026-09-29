# Effect and mask IR contracts — ABI 8

`op_ir_compile` accepts only typed, backward-referencing node indices. It never
accepts shader expressions or host pointers. `OP_IR_EFFECT` returns VECTOR RGB;
the caller retains the input alpha. Input 0 is the source VECTOR. The following
operation-specific inputs appear in the order below, followed by FLOAT strength
and FLOAT enabled. Inputs are FLOAT unless marked VECTOR. Unused slots in the
eight-element input array must still reference an earlier node.

| Operation | Inputs after source |
| --- | --- |
| LEVELS, ROUGHNESS_REMAP, METALLIC_REMAP, HEIGHT_REMAP | black, white, gamma, output_black, output_white |
| GAMMA | gamma |
| INVERT, NORMAL_INVERT_Y, DESATURATE | none |
| CLAMP | black, white |
| BRIGHTNESS_CONTRAST | amount, contrast |
| ROUGHNESS_CONTRAST | contrast |
| THRESHOLD | amount |
| HISTOGRAM_SCAN | amount, softness, contrast |
| HISTOGRAM_RANGE | black, white, softness, contrast |
| POSTERIZE, QUANTIZE | steps |
| AO_STRENGTH, NORMAL_STRENGTH, TEMPERATURE | amount |
| NORMAL_COMBINE | detail VECTOR, amount |
| HSL, HSV, HUE_SHIFT, SATURATION | hue, saturation, lightness |
| COLOR_BALANCE | color VECTOR |
| GRADIENT_MAP | color VECTOR, color2 VECTOR |
| COLORIZE, TINT | color VECTOR, amount |
| REPLACE_COLOR | color VECTOR, color2 VECTOR, softness |
| CHANNEL_MIXER | three row VECTORs, bias VECTOR |

C clamps the filtered RGB before applying effect opacity. Strength and enabled
remain bound uniforms, so changing them does not rebuild the operation. The
host validates active levels intervals and required normal-map resources before
submitting updated parameter values. Disabled effects may retain invalid levels
intervals; protected arithmetic keeps their unchanged source finite.

`OP_IR_MASK_FOLD` returns FLOAT. Its six FLOAT inputs are previous value, raw
source, black, white, invert and strength. `aux[0]` is 1 for the first entry and
0 otherwise. Operation is NORMAL, MULTIPLY, MAX, MIN or ADD. First entries blend
against 1. Levels and inversion precede composition. ADD clamps the result;
the other modes retain the reference behavior without an extra final clamp.

The C implementations live in `libopenpainter/src/shaders/effects.inc`. The host
field map in `evaluation/shader_effects.py` describes binding order; it contains
no native formulas. The original Python formulas remain independent reference
implementations. `native_effects.py` compares both executors and NumPy filters,
and verifies live RNA binding reuse in a real Blender material.
