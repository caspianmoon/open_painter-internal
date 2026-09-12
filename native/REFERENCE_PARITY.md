# Reference implementation status

Target: the user's five Substance Painter screenshots. This is an implementation checklist, not a claim of full parity.

The current application uses the custom AppKit shell in `shell/painter_shell.mm`: an EEVEE viewport with camera interaction. The previous Blender editor layout is superseded. The model capabilities below are retained and tested, but their old Python panels are not visible in the custom shell; replacement controls remain to be implemented.

| Reference | Implemented in current source | Remaining |
|---|---|---|
| Main window | Independent AppKit window with direct EEVEE texture presentation | Match exact proportions, narrow tool rail, four-column thumbnail library, integrated tool header and 2D rendered-channel display |
| Texture sets | Material slots select actual object materials; create a new assigned material | Resolution/channel settings, visibility, shader selection |
| Layers | Fill layers, folders, nested children, visibility, opacity, six blend modes, folder collapse, recursive deletion | Paint strokes, thumbnails, drag/drop, additional blend modes, per-channel controls, duplicate/reorder UI |
| Fill properties | Image input, UV mapping, filtering/wrap, tiling, rotation, offset, base color/roughness/metallic | Triplanar projection, aspect lock, normal/height/emission channels |
| Masks menu | White/black and bitmap masks and removal, including folders | Painting into masks, ID color selection, height combination |
| Effects menu | None yet | Generators, paint/fill effects, levels, compare mask, filters, color selection, anchor points |

Node properties update in place. Layer/image structure changes rebuild the material graph. No background CPU texture compositor or external frame transfer is used. Large stacks and real brush input have not yet been performance-tested.

The new editor space identifiers are specific to this fork. Standard Blender can retain scene/material data, but cannot display these custom editors. Cross-application file behavior requires explicit tests.
