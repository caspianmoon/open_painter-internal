"""Actionable validation of persistent references."""


def validate(ts) -> list[str]:
    issues = hierarchy_issues(ts)
    from .udim import validate as validate_udim
    try:
        validate_udim(ts)
    except ValueError as exc:
        issues.append(str(exc))
    if not ts.material:
        issues.append("Managed material is missing. Use Repair Material.")
    if not ts.assignments:
        issues.append("No mesh slots assigned. Select a UV-unwrapped mesh and assign it.")
    for ref in ts.assignments:
        obj = ref.object
        if not obj:
            issues.append(f"Missing object: {ref.object_name}.")
        elif ref.slot_index >= len(obj.material_slots):
            issues.append(f"Material slot missing on {obj.name}.")
        elif obj.material_slots[ref.slot_index].material != ts.material:
            issues.append(f"Material assignment changed on {obj.name}.")
        elif not obj.data.uv_layers:
            issues.append(f"No UV map found on {obj.name}.")
        elif obj.hide_viewport:
            # No view layer evaluates such an object, so every CPU evaluation, export and
            # shader boundary refuses it (geometry.snapshots.depsgraph_for): say why up front.
            issues.append(f"{obj.name} is disabled in viewports: enable it or unassign it.")
    ids = {item.id for item in ts.layers}
    for layer in ts.layers:
        if layer.parent_id and layer.parent_id not in ids:
            issues.append(f"Parent folder missing on {layer.name}.")
        for channel in layer.channels:
            if channel.enabled and channel.image_name and not channel.image:
                issues.append(f"Texture image missing on {layer.name}: {channel.image_name}.")
            if channel.enabled and channel.detail_image_name and not channel.detail_image:
                issues.append(f"Surface detail missing on {layer.name}: {channel.detail_image_name}.")
        for mask in layer.masks:
            if not mask.enabled:
                continue
            if mask.source in {"PAINT", "FILL"} and not mask.image:
                issues.append(f"Painted mask image missing on {layer.name}.")
            from ..generators.base import required_maps
            required = ({mask.map_type} if mask.source == "BAKED" else
                        required_maps(mask.generator) if mask.source == "GENERATOR" else set())
            for kind in required:
                if not any(m.kind == kind and m.image for m in ts.baked_maps):
                    issues.append(f"{layer.name} requires a baked {kind} map.")
    return issues


def warnings(ts) -> list[str]:
    """Non-blocking problems: missing anchors and published sources read as neutral, and
    a face selection no modifier stack maps through reads as empty. Composed here rather
    than in ``references`` (bpy-free, run by tests/unit under plain Python): the mesh
    warnings evaluate geometry, so they need bpy."""
    from ..evaluation.references import warnings as reference_warnings
    from ..evaluation.mesh_masks import warnings as mesh_warnings
    return reference_warnings(ts) + mesh_warnings(ts)


def hierarchy_issues(ts) -> list[str]:
    """Detect unreachable layers and invalid hierarchy before writing outputs."""
    from ..evaluation.references import issues as reference_issues
    issues = reference_issues(ts)
    by_id = {item.id: item for item in ts.layers}
    if len(by_id) != len(ts.layers) or "" in by_id:
        issues.append("Layer IDs are missing or duplicated. Restore a valid project copy.")
    for layer in ts.layers:
        cursor = layer.parent_id
        seen = {layer.id}
        while cursor:
            if cursor in seen:
                issues.append(f"Folder cycle on {layer.name}. Reparent the layer.")
                break
            seen.add(cursor)
            parent = by_id.get(cursor)
            if parent is None or parent.type != "FOLDER":
                issues.append(
                    f"Parent folder is missing or invalid on {layer.name}. Reparent the layer."
                )
                break
            cursor = parent.parent_id
    return issues
