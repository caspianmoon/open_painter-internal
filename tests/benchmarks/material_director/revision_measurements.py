"""Evaluator-only, deterministic geometry-weighted effect measurements.

No semantic label or measurement in this module is sent to Material Director.
Invalid UV area stays in the denominator and is explicitly reported.
"""
from copy import deepcopy
import json
import math
from pathlib import Path


def measurement_policy(config=None):
    """Use the pinned run policy, falling back only to this frozen protocol file."""
    config = config or {}
    policy = config.get("measurement_policy")
    if policy is None:
        policy = json.loads(Path(__file__).with_name("test_cases_v3.json").read_text(encoding="utf-8"))["measurements"]
    policy = deepcopy(policy)
    required = ("mask_threshold", "mask_absolute_tolerance", "invalid_uv_area_max_fraction",
                "minimum_initial_moss_area_fraction", "moss_area_ratio", "moss_area_ratio_tolerance",
                "new_moss_support_max_fraction", "band_height_tolerance")
    for key in required:
        value = policy.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid frozen measurement policy: " + key)
    return policy


def effect_control(native, layer_mask):
    """Separate a typed paint-loss opacity from its inverse coating mask.

    The sole native inverted root has C=1-s*M. This read-only inverse recovers
    the source M for s>0 without changing RNA or invoking a different evaluator.
    Arbitrary stacks and zero-strength inverses are explicitly not measured.
    """
    import numpy as np
    masks = native.get("masks", [])
    layer_id = native.get("id")
    record = dict(version=1, representation="GENERIC_LAYER_MASK", semantic="UNASSIGNED_LAYER_MASK",
        status="MEASURED", opacity=float(native.get("opacity", 1)),
        opacity_control_path=["layers",layer_id,"opacity"], source_document=deepcopy(masks),
        source_field_meaning="Evaluated layer mask before layer opacity; semantic effect membership is unassigned",
        semantic_assessment="NOT_MEASURED_WITHOUT_EXPLICIT_COMPONENT_MAPPING")
    if native.get("surface_role") == "SURFACE" and native.get("surface_family") in {"MUD", "MOSS"}:
        record.update(semantic=native["surface_family"], semantic_assessment="DECLARED_NATIVE_SURFACE_ROLE")
    paint_loss = [mask for mask in masks if mask.get("surface_role") == "PAINT_LOSS"]
    if not paint_loss:
        return record, np.asarray(layer_mask, dtype="<f4")
    record.update(representation="NATIVE_PAINT_LOSS",semantic="PAINT_LOSS",status="NOT_MEASURED",
        semantic_assessment="DECLARED_NATIVE_SURFACE_ROLE",opacity=None,opacity_control_path=None,
        source_field_meaning="Non-inverted paint-loss field before its declared mask strength")
    roots = [mask for mask in masks if not mask.get("parent_id")]
    if (native.get("surface_role") != "COATING" or native.get("surface_family") != "PAINTED_METAL"
            or len(paint_loss) != 1 or len(roots) != 1 or roots[0] is not paint_loss[0]
            or roots[0].get("source") not in {"GENERATOR", "GROUP"}
            or roots[0].get("invert") is not True or roots[0].get("enabled", True) is not True):
        record["reason"]="Paint-loss effect is not the sole enabled inverted native root; the composite layer mask cannot prove its source field"
        return record,None
    root = roots[0]
    ids={mask.get("id"):mask for mask in masks}
    for mask in masks:
        cursor=mask;seen=set()
        while cursor is not root:
            parent=cursor.get("parent_id")
            if parent not in ids or parent in seen:
                record["reason"]="Mask stack has unrelated or cyclic descendants"
                return record,None
            seen.add(parent);cursor=ids[parent]
    strength=float(root.get("strength",1))
    record.update(opacity=strength,opacity_control_path=["layers",layer_id,"masks",root["id"],"strength"],
        coating_layer_opacity=float(native.get("opacity",1)))
    record["source_document"][masks.index(root)]["strength"]=1.0
    if not 0 < strength <= 1:
        record["reason"]="A zero or invalid paint-loss strength cannot be inverted without evaluating a separate source"
        return record,None
    values=(1-np.asarray(layer_mask,dtype=np.float64))/strength
    if not np.isfinite(values).all() or (values < -1e-5).any() or (values > 1+1e-5).any():
        record["reason"]="Evaluated coating is inconsistent with the native inverse-opacity formula"
        return record,None
    record.update(status="MEASURED",representation="PAINT_LOSS_INVERTED_SINGLE_ROOT",
        derivation="M=(1-C)/strength; C is evaluated coating coverage, M is pre-strength non-inverted paint loss")
    return record,np.clip(values,0,1).astype("<f4")


def sample_geometry(texture_set):
    import numpy as np
    from open_painter.geometry.snapshots import targets
    barycentric = np.array([[1/3, 1/3, 1/3], [.6, .2, .2], [.2, .6, .2], [.2, .2, .6]], dtype=np.float64)
    chunks = {key: [] for key in ("uv", "weight", "valid", "normal", "relative_height", "position")}
    with targets(texture_set) as meshes:
        for mesh in meshes:
            positions = mesh["positions"][mesh["tri_vertices"]]
            uv = mesh["uv"][mesh["tri_loops"]]
            cross = np.cross(positions[:, 1]-positions[:, 0], positions[:, 2]-positions[:, 0])
            doubled_area = np.linalg.norm(cross, axis=1)
            normals = cross / np.maximum(doubled_area[:, None], 1e-30)
            uv_cross = (uv[:, 1, 0]-uv[:, 0, 0])*(uv[:, 2, 1]-uv[:, 0, 1]) - (uv[:, 1, 1]-uv[:, 0, 1])*(uv[:, 2, 0]-uv[:, 0, 0])
            valid = np.isfinite(uv).all(axis=(1,2)) & (np.abs(uv_cross)>1e-14) & ((uv >= 0)&(uv <= 1)).all(axis=(1,2))
            sampled_positions = np.einsum("sc,tcd->tsd", barycentric, positions).reshape(-1,3)
            sampled_uv = np.einsum("sc,tcd->tsd", barycentric, uv).reshape(-1,2)
            z0,z1 = positions[...,2].min(),positions[...,2].max()
            chunks["uv"].append(sampled_uv)
            chunks["position"].append(sampled_positions)
            chunks["weight"].append(np.repeat(doubled_area/8,4))
            chunks["valid"].append(np.repeat(valid,4))
            chunks["normal"].append(np.repeat(normals,4,axis=0))
            chunks["relative_height"].append((sampled_positions[:,2]-z0)/max(z1-z0,1e-30))
    return {key: np.concatenate(values) for key,values in chunks.items()}


def values_at(mask, samples):
    import numpy as np
    mask = np.asarray(mask)
    uv = np.nan_to_num(samples["uv"], nan=-1)
    height,width = mask.shape
    x,y = np.clip(uv[:,0]*width-.5,0,width-1),np.clip(uv[:,1]*height-.5,0,height-1)
    x0,y0 = x.astype(int),y.astype(int)
    x1,y1 = np.minimum(x0+1,width-1),np.minimum(y0+1,height-1)
    dx,dy = x-x0,y-y0
    return (mask[y0,x0]*(1-dx)+mask[y0,x1]*dx)*(1-dy)+(mask[y1,x0]*(1-dx)+mask[y1,x1]*dx)*dy


def coverage(mask, samples, *, threshold=.5, invalid_area_tolerance=.001):
    import numpy as np
    weight = np.asarray(samples["weight"], dtype=np.float64)
    valid = np.asarray(samples["valid"], dtype=bool)
    if not np.isfinite(weight).all() or (weight<0).any() or not weight.sum()>0:
        raise ValueError("Every assigned triangle needs finite nonnegative area; total area must be positive")
    values = values_at(mask, samples)
    if not np.isfinite(values[valid]).all():
        raise ValueError("Nonfinite effect samples cannot be excluded")
    total = float(weight.sum())
    occupied = valid & (values>threshold)
    invalid_fraction = float(weight[~valid].sum()/total)
    return dict(status="MEASURED" if invalid_fraction<=invalid_area_tolerance else "NOT_ASSESSABLE",
        threshold=threshold, occupied_area=float(weight[occupied].sum()), total_area=total,
        occupied_fraction=float(weight[occupied].sum()/total), invalid_area_fraction=invalid_fraction,
        invalid_area_tolerance=invalid_area_tolerance, samples=len(weight), weighting="TRIANGLE_SURFACE_AREA",
        area_units="WORLD_UNITS_SQUARED", estimator="FOUR_FIXED_INTERIOR_SAMPLES_PER_TRIANGLE",
        convergence_status="NOT_ESTABLISHED; fine chips and narrow bands may alias between samples",
        relative_height_frame="Per object's assigned triangle Z bounds; not a universal band-placement frame",
        invalid_uv_policy="All assigned triangle area remains in denominator; invalid UVs cannot count as occupied",
        seam_policy="Four deterministic interior barycentric samples per triangle; bilinear UV lookup, no seam exclusion")


def compare(before, after, samples, *, threshold=.5, minimum_initial_fraction=0, tolerance=1e-6, invalid_area_tolerance=.001):
    import numpy as np
    first,last = coverage(before,samples,threshold=threshold,invalid_area_tolerance=invalid_area_tolerance),coverage(after,samples,threshold=threshold,invalid_area_tolerance=invalid_area_tolerance)
    old,new = values_at(before,samples),values_at(after,samples)
    valid,weight = samples["valid"],samples["weight"]
    added = valid & (old<=threshold) & (new>threshold)
    ratio = last["occupied_area"]/first["occupied_area"] if first["occupied_area"]>0 and first["occupied_fraction"]>=minimum_initial_fraction else None
    support_measured = first["status"]==last["status"]=="MEASURED"
    delta=np.abs(np.asarray(before)-np.asarray(after))
    if not np.isfinite(delta).all():
        raise ValueError("Nonfinite source arrays cannot establish pattern equality")
    return dict(status="MEASURED" if support_measured else "PARTIALLY_MEASURED", before=first,after=last,
        mask_equality_status="MEASURED",support_status="MEASURED" if support_measured else "NOT_ASSESSABLE",
        occupied_area_ratio_status="MEASURED" if support_measured and ratio is not None else "NOT_ASSESSABLE",
        occupied_area_ratio=ratio, new_support_fraction=float(weight[added].sum()/weight.sum()),
        mask_equal=bool(np.max(np.abs(np.asarray(before)-np.asarray(after)))<=tolerance),
        maximum_mask_difference=float(np.max(np.abs(np.asarray(before)-np.asarray(after)))),
        absolute_tolerance=tolerance, minimum_initial_fraction=minimum_initial_fraction,
        no_artistic_grade=True)
