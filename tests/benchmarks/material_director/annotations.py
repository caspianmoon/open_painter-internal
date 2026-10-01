"""Freeze evaluator-only labels and measure archived channels, never AI inputs.

No labels are inferred from an AI result. A human supplies regions before scored
outputs exist. UV masks use bottom-left-origin boolean NPY arrays; preferred
surface samples use NPZ arrays uv[N,2], weight[N] (surface area), member[N].
Optional valid[N] excludes declared seams/boundaries and height[N] records
normalized physical height for band measurements. No pickle or code is loaded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import zipfile

SHA = re.compile(r"[0-9a-f]{64}\Z")
CHANNELS = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO", "EMISSION", "OPACITY"}
STAGES = {"before", "after_create", "after_refine_1", "after_refine_2"}
MAX_BYTES = 300 * 1024 * 1024


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _array(path):
    import numpy as np
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Annotation/buffer exceeds the bounded file size")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 8 or sum(entry.file_size for entry in entries) > MAX_BYTES:
                raise ValueError("Uncompressed samples exceed the annotation budget")
    value = np.load(path, allow_pickle=False)
    if isinstance(value, np.lib.npyio.NpzFile):
        try:
            result = {key: value[key] for key in value.files}
        finally:
            value.close()
        if sum(array.nbytes for array in result.values()) > MAX_BYTES:
            raise ValueError("Uncompressed samples exceed the annotation budget")
        return result
    if value.nbytes > MAX_BYTES or value.dtype.hasobject:
        raise ValueError("Unsupported annotation array")
    return value


def _validate_region(region, base):
    import numpy as np
    kind = region["kind"]
    if kind not in {"uv_mask", "surface_samples"}:
        raise ValueError("Region kind must be uv_mask or surface_samples")
    path = Path(base) / region["path"]
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing/nonregular annotation dependency")
    if region.get("sha256") and file_hash(path) != region["sha256"]:
        raise ValueError("Frozen annotation dependency changed")
    data = _array(path)
    if kind == "uv_mask":
        if not isinstance(data, np.ndarray) or data.ndim != 2 or data.dtype != np.bool_:
            raise ValueError("UV semantic mask must be a boolean H×W NPY, bottom-left origin")
        if min(data.shape) < 2 or max(data.shape) > 4096 or not data.any():
            raise ValueError("UV mask must contain a nonempty region within 4096²")
        if region.get("origin", "bottom-left") != "bottom-left":
            raise ValueError("Annotation origin must match captured bottom-left buffers")
    else:
        if not isinstance(data, dict) or not {"uv", "weight", "member"} <= set(data):
            raise ValueError("Surface NPZ requires uv, weight and member arrays")
        uv, weight, member = data["uv"], data["weight"], data["member"]
        count = len(weight)
        if not 1 <= count <= 1_000_000 or uv.shape != (count, 2) or member.shape != (count,):
            raise ValueError("Surface sample dimensions are inconsistent or unbounded")
        if member.dtype != np.bool_ or not member.any() or not np.isfinite(uv).all() or (uv < 0).any() or (uv > 1).any():
            raise ValueError("Surface membership must be boolean and UV coordinates finite within [0,1]")
        if weight.shape != (count,) or not np.isfinite(weight).all() or (weight <= 0).any():
            raise ValueError("Each surface sample needs a finite positive area weight")
        for key in ("valid", "height"):
            if key in data and data[key].shape != (count,):
                raise ValueError(f"{key} must match sample count")
        if "valid" in data and data["valid"].dtype != np.bool_:
            raise ValueError("valid exclusions must be boolean")
        if "height" in data and (not np.isfinite(data["height"]).all() or (data["height"] < 0).any() or (data["height"] > 1).any()):
            raise ValueError("height must be normalized physical height within [0,1]")
    return data


def validate_spec(spec, base, *, check_arrays=True):
    if spec.get("schema_version") != 1 or not spec.get("annotator"):
        raise ValueError("Schema1 and an identified human annotator are required")
    if spec.get("declared_before_ai_outputs") is not True:
        raise ValueError("Evaluator must declare labels before scored outputs are viewed")
    fixtures = spec.get("fixtures", [])
    if not fixtures or len(fixtures) > 8:
        raise ValueError("Provide one to eight explicit fixture records")
    seen = set()
    for fixture in fixtures:
        key = fixture.get("fixture_sha256", "")
        if not SHA.fullmatch(key) or key in seen:
            raise ValueError("Each fixture requires a unique SHA-256 pin")
        seen.add(key)
        regions = fixture.get("regions", [])
        identifiers = [region.get("id") for region in regions]
        if len(regions) > 64 or len(set(identifiers)) != len(identifiers) or any(not name for name in identifiers):
            raise ValueError("Region IDs must be unique and bounded")
        for region in regions:
            if not region.get("texture_set_id"):
                raise ValueError("Region must identify an actual frozen texture set")
            if region.get("kind") not in {"uv_mask", "surface_samples"}:
                raise ValueError("Region kind must be uv_mask or surface_samples")
            dependency = Path(base) / region["path"]
            if not dependency.is_file() or dependency.is_symlink() or dependency.stat().st_size > MAX_BYTES:
                raise ValueError("Invalid annotation dependency")
            if region.get("sha256") and file_hash(dependency) != region["sha256"]:
                raise ValueError("Frozen annotation dependency changed")
            if check_arrays:
                _validate_region(region, base)
        measurements = fixture.get("measurements", [])
        if len(measurements) > 128 or len({item.get("id") for item in measurements}) != len(measurements):
            raise ValueError("Measurement IDs must be unique and bounded")
        for item in measurements:
            if item.get("region_id") not in identifiers or item.get("channel") not in CHANNELS:
                raise ValueError("Measurement requires an explicit region and supported channel")
            if item.get("kind") == "preservation":
                if item.get("before") not in STAGES or item.get("after") not in STAGES:
                    raise ValueError("Preservation needs two declared checkpoint labels")
                if not 0 <= item.get("tolerance", 1 / 255) <= 1 or not 0 < item.get("minimum_fraction", .995) <= 1:
                    raise ValueError("Invalid preservation tolerances")
            elif item.get("kind") in {"coverage", "bands"}:
                if item.get("stage") not in STAGES:
                    raise ValueError("Coverage/bands need a declared checkpoint label")
                selector = item.get("selector", {})
                if set(selector) != {"minimum", "maximum"}:
                    raise ValueError("Freeze an explicit per-channel linear-value minimum/maximum selector")
                lower, upper = selector["minimum"], selector["maximum"]
                if (not isinstance(lower, list) or not isinstance(upper, list) or len(lower) != len(upper)
                        or not 1 <= len(lower) <= 4 or any(not math.isfinite(v) for v in [*lower, *upper])
                        or any(a > b for a, b in zip(lower, upper))):
                    raise ValueError("Invalid channel selector bounds")
                if item["kind"] == "bands":
                    region = next(r for r in regions if r["id"] == item["region_id"])
                    data = _validate_region(region, base) if check_arrays else {"height": True}
                    if region["kind"] != "surface_samples" or "height" not in data:
                        raise ValueError("Band counts require declared physical-height surface samples")
                    if not 8 <= item.get("bins", 100) <= 1000 or not 0 < item.get("occupied_fraction", .5) <= 1:
                        raise ValueError("Invalid frozen band sampling settings")
            else:
                raise ValueError("Measurement kind must be coverage, bands or preservation")
        probes = fixture.get("editability_probe_requirements", [])
        if len(probes) > 24 or len({p.get("id") for p in probes}) != len(probes):
            raise ValueError("Editability requirements must have unique IDs, at most24")
        for probe in probes:
            if not probe.get("id") or not probe.get("component") or not probe.get("field") or "value" not in probe:
                raise ValueError("Each probe needs an ID, semantic component, ordinary-control field and test value")
            if probe.get("channel") and probe["channel"] not in CHANNELS:
                raise ValueError("Unsupported probe channel")
    return spec


def freeze(source, output):
    """Copy dependencies into a new immutable bundle; do not inspect AI evidence."""
    source, output = Path(source).resolve(), Path(output).resolve()
    spec = validate_spec(json.loads(source.read_text(encoding="utf-8")), source.parent)
    if output.exists():
        raise FileExistsError("Never replace frozen annotation bundles")
    output.mkdir(parents=True)
    for fixture in spec["fixtures"]:
        for region in fixture.get("regions", []):
            original = (source.parent / region["path"]).resolve()
            checksum = file_hash(original)
            suffix = ".npy" if region["kind"] == "uv_mask" else ".npz"
            relative = Path("regions") / (checksum + suffix)
            destination = output / relative
            destination.parent.mkdir(exist_ok=True)
            if not destination.exists():
                shutil.copyfile(original, destination)
            region.update(path=relative.as_posix(), sha256=checksum)
    spec.update(frozen_at=datetime.now(timezone.utc).isoformat(), evaluator_only=True,
                chronology="Annotator declaration; runner pins bundle before scored sessions")
    manifest = output / "annotations.json"
    _write(manifest, spec)
    checksum = file_hash(manifest)
    (output / "annotations.sha256").write_text(checksum + "\n", encoding="utf-8")
    return dict(annotations_manifest=str(manifest), annotations_sha256=checksum)


def verify_frozen(path, expected_sha256=None, fixture_sha256=None):
    path = Path(path).resolve()
    expected = expected_sha256 or path.with_suffix(".sha256").read_text(encoding="utf-8").strip()
    if not SHA.fullmatch(expected) or file_hash(path) != expected:
        raise ValueError("Frozen annotation manifest changed")
    # Verification is usable by the ordinary-Python launcher without NumPy.
    # Full numeric validation happened on freeze and repeats during measurement;
    # the content hashes below pin those exact array bytes in the meantime.
    spec = validate_spec(json.loads(path.read_text(encoding="utf-8")), path.parent, check_arrays=False)
    if not spec.get("frozen_at") or spec.get("evaluator_only") is not True:
        raise ValueError("Annotations must be a frozen evaluator-only bundle")
    for fixture in spec["fixtures"]:
        for region in fixture.get("regions", []):
            if not SHA.fullmatch(region.get("sha256", "")):
                raise ValueError("Every frozen region dependency needs a SHA-256")
    if fixture_sha256:
        matches = [item for item in spec["fixtures"] if item["fixture_sha256"] == fixture_sha256]
        if len(matches) != 1:
            raise ValueError("No frozen annotations for this exact fixture hash")
        return matches[0]
    return spec


def _buffer(checkpoint, region, channel):
    import numpy as np
    entry = checkpoint["channels"]["sets"][region["texture_set_id"]]
    record = entry["channels"][channel]
    values = _array(record["path"])
    if not isinstance(values, np.ndarray) or values.ndim != 3:
        raise ValueError("Archived evaluated channel must be H×W×C")
    encoded = np.ascontiguousarray(values).astype(values.dtype.newbyteorder("<"), copy=False)
    if hashlib.sha256(encoded.tobytes()).hexdigest() != record["decoded"]["sha256"]:
        raise ValueError("Archived decoded channel changed")
    valid = _array(entry["valid_samples"]["path"])
    if valid.shape != values.shape[:2] or valid.dtype != np.bool_:
        raise ValueError("Invalid archived sample coverage")
    valid_hash = entry["valid_samples"].get("decoded", {}).get("sha256")
    if valid_hash and hashlib.sha256(np.ascontiguousarray(valid).tobytes()).hexdigest() != valid_hash:
        raise ValueError("Archived valid-sample mask changed")
    return values, valid


def _samples(values, valid, region, base):
    import numpy as np
    data = _validate_region(region, base)
    if region["kind"] == "uv_mask":
        if data.shape != values.shape[:2]:
            raise ValueError("Frozen UV mask resolution differs from evaluated buffer; no silent resizing")
        return values.reshape(-1, values.shape[-1]), valid.ravel(), data.ravel(), np.ones(data.size), None
    uv, weight, member = data["uv"], data["weight"], data["member"]
    height, width = values.shape[:2]
    x = np.clip(uv[:, 0] * width - .5, 0, width - 1)
    y = np.clip(uv[:, 1] * height - .5, 0, height - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    sampled = ((values[y0, x0] * (1 - fx) + values[y0, x1] * fx) * (1 - fy)
               + (values[y1, x0] * (1 - fx) + values[y1, x1] * fx) * fy)
    keep = valid[y0, x0] & valid[y0, x1] & valid[y1, x0] & valid[y1, x1]
    keep &= data.get("valid", np.ones(len(weight), dtype=bool))
    return sampled, keep, member, weight, data.get("height")


def _measure(checkpoint, spec, region, base, checkpoints):
    import numpy as np
    values, valid = _buffer(checkpoint, region, spec["channel"])
    samples, keep, member, weight, height = _samples(values, valid, region, base)
    finite = np.isfinite(samples).all(axis=1)
    invalid = keep & ~finite
    if invalid.any():
        raise ValueError("Nonfinite evaluated samples cannot be silently excluded")
    total = float(weight[keep].sum())
    inside = keep & member
    inside_weight = float(weight[inside].sum())
    if not inside_weight:
        return dict(status="NOT_TESTED", reason="No valid samples in the frozen region")
    result = dict(region_id=region["id"], channel=spec["channel"],
                  weighting="surface_area" if region["kind"] == "surface_samples" else "uv_pixels",
                  valid_samples=int(keep.sum()), excluded_samples=int((~keep).sum()), region_weight=inside_weight)
    if spec["kind"] == "preservation":
        before = checkpoints.get(spec["before"])
        if before is None:
            return dict(result, status="NOT_TESTED", reason="Declared before checkpoint absent")
        old, previous_valid = _buffer(before, region, spec["channel"])
        previous, old_keep, old_member, old_weight, _ = _samples(old, previous_valid, region, base)
        if previous.shape != samples.shape:
            raise ValueError("Preservation compared different evaluator sample coordinates")
        mask = inside & old_keep
        if not mask.any():
            return dict(result, status="NOT_TESTED", reason="No shared valid protected samples")
        delta = np.max(np.abs(samples[mask] - previous[mask]), axis=1)
        if not np.isfinite(delta).all():
            raise ValueError("Nonfinite preservation data")
        tolerance, minimum = spec.get("tolerance", 1 / 255), spec.get("minimum_fraction", .995)
        fraction = float(weight[mask][delta <= tolerance].sum() / weight[mask].sum())
        return dict(result, status="PASS" if fraction >= minimum else "FAIL", within_fraction=fraction,
                    max_error=float(delta.max()), exact=bool(np.array_equal(samples[mask], previous[mask])),
                    tolerance=tolerance, minimum_fraction=minimum,
                    visible_unauthorized_change="REVIEW_REQUIRED regardless of numeric threshold")
    selector = spec["selector"]
    lower, upper = np.asarray(selector["minimum"]), np.asarray(selector["maximum"])
    selected = (samples[:, :len(lower)] >= lower).all(axis=1) & (samples[:, :len(upper)] <= upper).all(axis=1)
    selected_weight = float(weight[keep & selected].sum())
    coverage = float(weight[inside & selected].sum() / inside_weight)
    spill = float(weight[keep & ~member & selected].sum() / selected_weight) if selected_weight else 0.0
    result.update(status="MEASURED", coverage=coverage, missed_region_fraction=1 - coverage,
                  spill_fraction_of_selected=spill if (keep & ~member).any() else None,
                  outside_region_sampling="PRESENT" if (keep & ~member).any() else "MISSING",
                  selected_weight=selected_weight,
                  total_valid_weight=total, selector=selector, semantic_validity="REVIEW_REQUIRED")
    if spec["kind"] == "bands":
        bins = spec.get("bins", 100)
        index = np.minimum((height * bins).astype(int), bins - 1)
        denominator = np.bincount(index[inside], weights=weight[inside], minlength=bins)
        numerator = np.bincount(index[inside & selected], weights=weight[inside & selected], minlength=bins)
        fraction = np.divide(numerator, denominator, out=np.zeros(bins), where=denominator > 0)
        occupied = fraction >= spec.get("occupied_fraction", .5)
        edges = np.diff(np.r_[False, occupied, False].astype(int))
        starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
        result.update(band_count=len(starts), height_bin_resolution=1 / bins,
                      unsampled_height_bins=int((denominator == 0).sum()),
                      bands=[dict(bottom=float(a / bins), top=float(b / bins), center=float((a+b)/2/bins),
                                  width=float((b-a)/bins)) for a,b in zip(starts, ends)],
                      wrap_continuity="REVIEW_REQUIRED; height counts alone do not prove full circumference coverage")
    return result


def evaluate_checkpoint(checkpoint, config, *, fixture_sha256=None):
    """Read-only optional capture hook; results never assign artistic scores."""
    if not config.get("annotations_manifest"):
        return dict(status="NOT_TESTED", reason="Frozen evaluator-only annotations were not supplied")
    path = Path(config["annotations_manifest"]).resolve()
    checksum = fixture_sha256 or config.get("fixture_sha256")
    if not checksum:
        return dict(status="BLOCKED", reason="Exact fixture hash is required for annotation measurements")
    spec = verify_frozen(path, config.get("annotations_sha256"), checksum)
    if isinstance(checkpoint, (str, Path)):
        checkpoint_path = Path(checkpoint)
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    else:
        checkpoint_path = Path(checkpoint.get("state_path", "checkpoint.json"))
    checkpoints = {checkpoint["label"]: checkpoint}
    measurements = {}
    for item in spec.get("measurements", []):
        if item.get("after", item.get("stage")) != checkpoint["label"]:
            continue
        if item.get("before"):
            before_path = checkpoint_path.parent / (item["before"] + "_state.json")
            if before_path.exists():
                checkpoints[item["before"]] = json.loads(before_path.read_text(encoding="utf-8"))
        region = next(r for r in spec["regions"] if r["id"] == item["region_id"])
        try:
            measurements[item["id"]] = _measure(checkpoint, item, region, path.parent, checkpoints)
        except (ValueError, KeyError, OSError) as exc:
            measurements[item["id"]] = dict(status="BLOCKED", reason=str(exc))
    statuses = [item["status"] for item in measurements.values()]
    status = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "MEASURED" if statuses else "NOT_SCHEDULED"
    return dict(status=status, measurements=measurements, fixture_sha256=checksum,
                annotations_sha256=file_hash(path), artistic_acceptance="REVIEW_REQUIRED")


def validate_probe_mapping(mapping, checkpoint, frozen_fixture):
    """Bind post-result control IDs to predeclared probe requirements; no guessing."""
    requirements = {item["id"]: item for item in frozen_fixture.get("editability_probe_requirements", [])}
    sets = checkpoint["state"]["texture_sets"]
    probes = []
    for item in mapping.get("bindings", []):
        requirement = requirements.get(item.get("requirement_id"))
        if requirement is None:
            raise ValueError("Probe binding must reference a predeclared component requirement")
        ts = sets[item["texture_set_id"]]
        layer = next((layer for layer in ts["semantic"]["layers"] if layer["id"] == item["layer_id"]), None)
        if layer is None:
            raise ValueError("Probe layer ID is absent from the final checkpoint")
        if item.get("mask_id") and item["mask_id"] not in {m["id"] for m in layer.get("masks", [])}:
            raise ValueError("Probe mask ID is absent")
        if item.get("mask_id") and requirement.get("channel"):
            raise ValueError("Choose a mask or channel control, not both")
        if any(identifier not in sets for identifier in item.get("protected_set_ids", [])):
            raise ValueError("Protected texture set is absent from the checkpoint")
        if requirement.get("channel") and requirement["channel"] not in CHANNELS:
            raise ValueError("Unsupported probe channel")
        probes.append(dict(texture_set_id=item["texture_set_id"], layer_id=item["layer_id"],
                           **({"mask_id": item["mask_id"]} if item.get("mask_id") else {}),
                           **({"channel": requirement["channel"]} if requirement.get("channel") else {}),
                           field=requirement["field"], value=requirement["value"],
                           protected_set_ids=item.get("protected_set_ids", [])))
    if len(probes) != len(requirements) or len({b["requirement_id"] for b in mapping.get("bindings", [])}) != len(requirements):
        raise ValueError("Map each predeclared editability probe exactly once")
    return dict(editability_probes=probes, mapping_review="Evaluator-supplied IDs; semantic correspondence requires independent review")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "freeze"):
        command = sub.add_parser(name)
        command.add_argument("source", type=Path)
        if name == "freeze":
            command.add_argument("--output", type=Path, required=True)
    command = sub.add_parser("measure")
    command.add_argument("--annotations", type=Path, required=True)
    command.add_argument("--fixture-sha256", required=True)
    command.add_argument("--checkpoint", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = sub.add_parser("map-probes")
    command.add_argument("--annotations", type=Path, required=True)
    command.add_argument("--fixture-sha256", required=True)
    command.add_argument("--checkpoint", type=Path, required=True)
    command.add_argument("--bindings", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "validate":
        validate_spec(json.loads(args.source.read_text(encoding="utf-8")), args.source.parent)
        print("VALID_EVALUATOR_SPEC")
    elif args.action == "freeze":
        print(json.dumps(freeze(args.source, args.output), indent=2))
    else:
        fixture = verify_frozen(args.annotations, fixture_sha256=args.fixture_sha256)
        if args.action == "measure":
            result = evaluate_checkpoint(args.checkpoint, dict(annotations_manifest=str(args.annotations)), fixture_sha256=args.fixture_sha256)
        else:
            result = validate_probe_mapping(json.loads(args.bindings.read_text(encoding="utf-8")), json.loads(args.checkpoint.read_text(encoding="utf-8")), fixture)
            result["source_checkpoint_sha256"] = file_hash(args.checkpoint)
        _write(args.output, result)
        print(args.output)


if __name__ == "__main__":
    main()
