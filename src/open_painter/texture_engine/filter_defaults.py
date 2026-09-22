"""Kind-specific defaults shared by manual and Director filter creation."""


def initial_amount(kind):
    if kind in {"BRIGHTNESS_CONTRAST", "TEMPERATURE"}:
        return 0.0
    if kind in {"NORMAL_STRENGTH", "NORMAL_COMBINE", "HEIGHT_TO_NORMAL", "AO_STRENGTH"}:
        return 1.0
    return 0.5
