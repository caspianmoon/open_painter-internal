"""Stable on-disk identifiers and ownership markers."""

VERSION = "0.15.0"
# Keep the first five enum positions stable for existing .blend projects.
BLEND_ITEMS = [
    (key, label, "")
    for key, label in (
        ("NORMAL", "Normal"),
        ("MULTIPLY", "Multiply"),
        ("ADD", "Linear Dodge (Add)"),
        ("SUBTRACT", "Subtract"),
        ("OVERLAY", "Overlay"),
        ("DIVIDE", "Divide"),
        ("DARKEN", "Darken (Min)"),
        ("LIGHTEN", "Lighten (Max)"),
        ("DIFFERENCE", "Difference"),
        ("EXCLUSION", "Exclusion"),
        ("SCREEN", "Screen"),
        ("LINEAR_BURN", "Linear Burn"),
        ("COLOR_BURN", "Color Burn"),
        ("COLOR_DODGE", "Color Dodge"),
        ("SOFT_LIGHT", "Soft Light"),
        ("HARD_LIGHT", "Hard Light"),
        ("VIVID_LIGHT", "Vivid Light"),
        ("LINEAR_LIGHT", "Linear Light"),
        ("PIN_LIGHT", "Pin Light"),
        ("DISABLE", "Disable"),
        ("HUE", "Hue"), ("SATURATION", "Saturation"), ("COLOR", "Color"), ("LUMINOSITY", "Luminosity"),
        ("NORMAL_COMBINE", "Normal Combine"), ("RNM", "Reoriented Normal Mapping"),
        ("NORMAL_ADD", "Additive Normal"), ("NORMAL_REPLACE", "Replace Normal"),
    )
]
OWNER = "open_painter_managed"
CHANNELS = ("BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO", "EMISSION", "OPACITY")
CHANNEL_LABELS = dict(
    zip(CHANNELS, ("BaseColor", "Roughness", "Metallic", "Normal", "Height", "AO", "Emission", "Opacity"))
)
CHANNEL_ITEMS = [(c, CHANNEL_LABELS[c], "") for c in CHANNELS]
RESOLUTIONS = [(str(n), str(n), f"{n} × {n} pixels") for n in (512, 1024, 2048, 4096, 8192)]
DEFAULTS = {
    "BASE_COLOR": (0.5, 0.5, 0.5, 1),
    "ROUGHNESS": (0.5, 0.5, 0.5, 1),
    "METALLIC": (0, 0, 0, 1),
    "NORMAL": (0.5, 0.5, 1, 1),
    "HEIGHT": (0.5, 0.5, 0.5, 1),
    "AO": (1, 1, 1, 1),
    "EMISSION": (0,0,0,1),
    "OPACITY": (1,1,1,1),
}
