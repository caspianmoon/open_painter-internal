def outputs(settings):
    return [("Custom", tuple(getattr(settings, "custom_" + c) for c in "rgba"))]
