"""Versioned material coordinates. Legacy texture/world controls are unchanged.

Meters are established only by an explicit declaration with provenance. Scene
unit display settings and a normalized bounding box are not size measurements.
"""
import math

VERSION = 1


def capture(ts):
    status = getattr(ts, "physical_space_status", "UNKNOWN")
    value = float(getattr(ts, "meters_per_world_unit", 1.0))
    provenance = getattr(ts, "physical_space_provenance", "").strip()
    known = status == "KNOWN" and bool(provenance) and math.isfinite(value) and value > 0
    return dict(version=VERSION, status="KNOWN" if known else "UNKNOWN",
                meters_per_world_unit=value if known else None,
                provenance=provenance or "No verified physical size has been declared",
                scene_unit_scale_applied=False,
                legacy_mapping="unchanged",
                note="One conversion from current world coordinates to metres; no extra object or scene scale.")


def require(ts):
    from .schema import AuthoringError
    info = capture(ts)
    if info["status"] != "KNOWN":
        raise AuthoringError("Physical size is unknown. An artist must declare meters per current world unit "
                             "with measurement/source provenance before requesting millimeter controls. "
                             "Use explicitly relative detail otherwise.")
    return info["meters_per_world_unit"]


def declare(ts, meters_per_world_unit, provenance):
    """Explicit artist/fixture preparation API; never inferred from a file name."""
    value = float(meters_per_world_unit)
    if not math.isfinite(value) or value <= 0 or not str(provenance).strip():
        raise ValueError("A positive conversion and explicit source/measurement provenance are required")
    ts.meters_per_world_unit = value
    ts.physical_space_provenance = str(provenance)
    ts.physical_space_status = "KNOWN"
    return capture(ts)


def field_values(ts, current, values):
    """Convert explicit metres once; evaluated native world fields remain truth."""
    from .schema import AuthoringError
    result = dict(values)
    metric = {key for key in ("distance_m", "bounds_m") if key in result}
    if metric:
        if any(key[:-2] in result for key in metric):
            raise AuthoringError("Choose metres or world units for each field control, not both")
        mpu = require(ts)
        result.update(physical_mapping_version=1, physical_meters_per_unit=mpu,
                      physical_provenance=capture(ts)["provenance"])
        for key in metric:
            value = result.pop(key)
            result[key[:-2]] = [v / mpu for v in value] if key == "bounds_m" else value / mpu
    if "bounds" in result and result["bounds"][0] >= result["bounds"][1]:
        raise AuthoringError("Field bounds must be strictly increasing")
    if "distance" in result and result["distance"] < .00001:
        raise AuthoringError("Requested physical reach is below the native field minimum at this world scale")
    return result


def mask_values(ts, current, values):
    """Metric procedural masks use the same native frame as surface channels."""
    from .schema import AuthoringError
    result = dict(values)
    radius = result.pop("chip_radius_m", None)
    period = result.pop("period_m", None)
    legacy_transform = any(key in result for key in ("scale", "noise_scale", "rotation", "offset"))
    if current.get("material_mapping_version") and legacy_transform:
        raise AuthoringError("Metric procedural masks use their explicit material frame, not legacy UV transforms")
    if radius is None and period is None:
        return result
    if radius is not None and period is not None:
        raise AuthoringError("Choose chip radius or field period, not both")
    if current["source"] != "PROCEDURAL":
        raise AuthoringError("Metric pattern controls require a procedural mask")
    if radius is not None and result.get("procedural", current["procedural"]) != "DOTS":
        raise AuthoringError("Chip radius is the 50% boundary of the native circular DOTS primitive")
    if legacy_transform:
        raise AuthoringError("Metric procedural masks use their explicit material frame, not legacy UV transforms")
    result.update(material_mapping_version=1, material_meters_per_unit=require(ts),
                  material_period_m=radius / .28 if radius is not None else period,
                  material_provenance=capture(ts)["provenance"],
                  material_source_id=current.get("material_source_id") or current["id"] + ".structure",
                  projection="TRIPLANAR")
    return result


def resolution_diagnostic(ts, period_m):
    """A sampling diagnostic, not proof of material quality or local UV density."""
    from ..evaluation.physical import density
    scale = capture(ts)
    tile = density(ts)
    if scale["status"] != "KNOWN" or tile is None:
        return dict(status="UNKNOWN", reason="Physical scale or valid UV area unavailable")
    texel_m = tile * scale["meters_per_world_unit"] / int(ts.resolution)
    samples = float(period_m) / max(texel_m, 1e-15)
    return dict(status="UNDER_RESOLVED" if samples < 4 else "ESTIMATED_RESOLVABLE",
                mean_samples_per_period=samples, mean_texel_size_m=texel_m,
                note="Area-average estimate; stretched UVs can require more resolution. No automatic upscaling.")
