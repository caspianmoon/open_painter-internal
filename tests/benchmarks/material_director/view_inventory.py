"""Frozen v3 read-only evidence poses. The studio rig is never rebuilt."""
PRIMARY = {
    "before": [("front", "00_before.png")],
    "after_create": [("front", "01_create_front.png"), ("back", "02_create_back.png"), ("close", "03_create_close.png")],
    "after_refine_1": [("front", "04_refine1_front.png"), ("back", "05_refine1_back.png"), ("close", "06_refine1_close.png")],
    "after_refine_2": [("front", "07_refine2_front.png"), ("back", "08_refine2_back.png"), ("close", "09_refine2_close.png")],
}
EXTRA_POSES = {
    "underside": {"location": [4, -6, -3], "center": [0, 0, 1], "ortho_scale": 3.2},
    "circumference_90": {"location": [6, 4, 3.4], "center": [0, 0, 1], "ortho_scale": 3.2},
    "circumference_270": {"location": [-6, -4, 3.4], "center": [0, 0, 1], "ortho_scale": 3.2},
}


def inventory(config=None):
    config = config or {}
    if config.get("protocol") == "two_weapon_v1":
        return {label: [(view, f"{label}_{view}.png")
                        for view in ("left", "right", "front", "rear", "close")]
                for label in ("before", "after_create", "after_refine_1", "after_refine_2",
                              "failure_create", "failure_refine_1", "failure_refine_2")}
    result = {name: list(views) for name, views in PRIMARY.items()}
    if config.get("protocol_version", 2) < 3:
        return result
    extras = ["underside"] if config.get("case_id") in {"T03", "T05"} else \
        ["circumference_90", "circumference_270"] if config.get("case_id") == "T04" else []
    for stage in ("create", "refine_1", "refine_2"):
        result["after_" + stage] += [(view, f"v3_{stage}_{view}.png") for view in extras]
    return result


def filenames(config=None):
    return [name for views in inventory(config).values() for _, name in views]
