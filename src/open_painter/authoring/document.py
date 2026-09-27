"""Detached authoring documents and identity-preserving RNA reconciliation."""

import hashlib
import json

from ..core.surface_settings import FIELDS as SURFACE
from .schema import AuthoringError

TRANSIENT = {"dirty", "revision", "active_mask", "active_effect", "expanded", "order",
             "opacity_percent", "master_factor", "master_scale", "image_name", "detail_image_name"}


def clone(value):
    if isinstance(value, dict):
        return {k: clone(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clone(v) for v in value]
    return value  # Blender ID handles remain on the main thread.


def snapshot(ts):
    from ..core.layer_stack import snapshot as capture
    return dict(layers=[capture(layer) for layer in ts.layers],
                surface={name: getattr(ts, name) for name in SURFACE})


def encode(value, *, digest=False, path=()):
    """JSON-safe local receipts. No pixels, executable text, or material node trees."""
    if isinstance(value, dict):
        skip = TRANSIENT if digest else {"opacity_percent", "master_factor", "master_scale"}
        if digest and value.get('cache_mode') == 'CACHED':
            # Auto-refreshed images are evaluated state, not artist edits or
            # undo conflicts. Frozen cache content remains authored state.
            skip = skip | {'cache'}
        if digest and path and path[-1] == "influence":
            skip = skip | {"image", "provenance", "error", "update_serial"}
        return {k: encode(v, digest=digest, path=(*path, k)) for k, v in value.items() if k not in skip}
    if isinstance(value, (list, tuple)):
        return [encode(v, digest=digest, path=path) for v in value]
    if value is not None and hasattr(value, "bl_rna"):
        return {"$datablock": value.bl_rna.identifier, "name": value.name_full,
                "library": value.library.filepath if value.library else ""}
    return value


def decode(value):
    import bpy
    if isinstance(value, dict) and "$datablock" in value:
        stores = {"Image": bpy.data.images, "Object": bpy.data.objects,
                  "Collection": bpy.data.collections, "Material": bpy.data.materials}
        store = stores.get(value["$datablock"])
        if store is None:
            raise AuthoringError("Unsupported resource in the saved change")
        found = next((item for item in store if item.name_full == value["name"] and
                      (item.library.filepath if item.library else "") == value.get("library", "")), None)
        if found is None:
            raise AuthoringError(f"Resource no longer exists: {value['name']}")
        return found
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def canonical(value):
    return json.dumps(encode(value, digest=True), sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(ts):
    from ..geometry.influencers import signature
    from ..geometry.provenance import record
    # Pixel edits are tracked by the managed revision alone: ``is_dirty`` flips
    # on every save (packing clears it) without any material change.
    sources = []
    for layer in ts.layers:
        for channel in layer.channels:
            for image in (channel.image, channel.detail_image):
                if image:
                    sources.append((image.name_full, tuple(image.size), image.get("open_painter_pixel_revision", 0)))
        for mask in layer.masks:
            if mask.image:
                image = mask.image
                sources.append((image.name_full, tuple(image.size), image.get("open_painter_pixel_revision", 0)))
    from .physical_space import capture as material_space
    inputs = dict(document=encode(snapshot(ts), digest=True), geometry=record(ts, "SCENE_FIELD"),
                  maps=[(entry.kind, entry.provenance, encode(entry.image),
                         entry.image.get("open_painter_pixel_revision", 0) if entry.image else 0)
                        for entry in ts.baked_maps],
                  fields=[signature(ts, mask.influence) for layer in ts.layers for mask in layer.masks
                          if mask.source == "SCENE" or mask.edge_source == "LIVE"],
                  physical_space=material_space(ts),
                  sources=sources, locks=getattr(ts, "director_locks", ""),
                  assignments=[(ref.object.name_full if ref.object else None, ref.slot_index,
                                ref.object.material_slots[ref.slot_index].material.name_full
                                if ref.object and ref.slot_index < len(ref.object.material_slots)
                                and ref.object.material_slots[ref.slot_index].material else None)
                               for ref in ts.assignments])
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def defaults(identifier):
    import bpy
    from ..core.layer_stack import DERIVED
    cls = bpy.types.PropertyGroup.bl_rna_get_subclass_py(identifier)
    if cls is None:
        raise AuthoringError(f"Unregistered authoring type: {identifier}")
    rna = cls.bl_rna
    result = {}
    for prop in rna.properties:
        name = prop.identifier
        if name in {"rna_type", "id_data", "opacity_percent", "master_factor", "master_scale"} or (identifier, name) in DERIVED:
            continue
        if prop.type == "COLLECTION":
            result[name] = []
        elif prop.type == "POINTER":
            fixed = prop.fixed_type
            result[name] = defaults(fixed.identifier) if fixed.identifier.startswith("OP_") else None
        elif not prop.is_readonly:
            result[name] = list(prop.default_array) if getattr(prop, "is_array", False) else prop.default
    return result


def item_key(item, index, collection=""):
    return item.get("channel") if collection == "channels" else item.get("id") or f"@{index}"


def reconcile(group, desired):
    """Write only changed values, retaining unchanged layers, images and shader bindings."""
    import bpy
    for name, value in desired.items():
        if name in {"opacity_percent", "master_factor", "master_scale", "rna_type", "id_data"}:
            continue
        prop = group.bl_rna.properties.get(name)
        if prop is None:
            raise AuthoringError(f"Saved change contains unsupported field {name}")
        current = getattr(group, name)
        if prop.type == "COLLECTION":
            collection(group, name, value)
        elif prop.type == "POINTER" and isinstance(current, bpy.types.PropertyGroup):
            reconcile(current, value)
        elif not prop.is_readonly:
            equal = list(current) == value if getattr(prop, "is_array", False) else current == value
            if not equal:
                setattr(group, name, value)


def collection(owner, name, desired):
    from ..core.layer_stack import snapshot as capture
    records = getattr(owner, name)
    wanted = [item_key(item, i, name) for i, item in enumerate(desired)]
    keys = [item_key(capture(item), i, name) for i, item in enumerate(records)]
    if keys != wanted:
        from ..evaluation.shader_preview import authoring_structure_changed
        from ..core.layer_store import scene_of
        authoring_structure_changed(scene_of(owner))
    for i in reversed(range(len(keys))):
        if keys[i] not in wanted:
            records.remove(i)
            keys.pop(i)
    for index, (key, values) in enumerate(zip(wanted, desired)):
        if key not in keys:
            records.add()
            keys.append(key)
        source = keys.index(key)
        if source != index:
            records.move(source, index)
            keys.insert(index, keys.pop(source))
        # Collection moves/additions invalidate cached RNA references.
        reconcile(records[index], values)


def apply(ts, desired):
    serial = ts.edit_serial
    before = canonical(snapshot(ts))
    collection(ts, "layers", desired["layers"])
    for name, value in desired["surface"].items():
        if name not in SURFACE:
            raise AuthoringError("Unsupported surface parameter")
        if getattr(ts, name) != value:
            setattr(ts, name, value)
    for index, layer in enumerate(ts.layers):
        layer.order = index
    if ts.edit_serial == serial and before != canonical(snapshot(ts)):
        # Removing the last collection item triggers no property setter.
        ts.edit_serial += 1
        ts.dirty = True
        for output in ts.outputs:
            output.dirty = True


def keep_order(desired, current, reference):
    """Keep the artist's current stack order after a merge; restored items
    return next to the neighbours they had in ``reference``."""
    for collection, path in (("layers", ()), ("masks", ("layers",))):
        pairs = [(desired, current, reference)] if not path else [
            (d, c, r) for d in desired["layers"]
            for c in current["layers"] if c["id"] == d["id"]
            for r in reference["layers"] if r["id"] == d["id"]]
        for want, have, base in pairs:
            by_id = {item["id"]: item for item in want[collection] if item.get("id")}
            if len(by_id) != len(want[collection]):
                continue  # Positional items: leave the merge result alone.
            order = [item["id"] for item in have[collection] if item["id"] in by_id]
            for index, item in enumerate(base[collection]):
                if item["id"] in by_id and item["id"] not in order:
                    preceding = [x["id"] for x in base[collection][:index] if x["id"] in order]
                    order.insert(order.index(preceding[-1]) + 1 if preceding else 0, item["id"])
            want[collection] = [by_id[key] for key in order]


def comparable(value):
    """Flatten keyed collections for diffs, locks and conflict-aware reversal."""
    result = {}

    def visit(item, path):
        if isinstance(item, dict) and "$datablock" not in item:
            if not item:
                result[path] = {}
            for key, child in item.items():
                visit(child, (*path, key))
        elif isinstance(item, list) and (not item or all(isinstance(v, dict) for v in item)):
            result[(*path, "$order")] = [item_key(v, i, path[-1] if path else "") for i, v in enumerate(item)]
            for i, child in enumerate(item):
                visit(child, (*path, item_key(child, i, path[-1] if path else "")))
        else:
            result[path] = item

    visit(encode(value, digest=True), ())
    return result


def changes(before, after):
    a, b = comparable(before), comparable(after)
    return [(path, a.get(path), b.get(path)) for path in sorted(a.keys() | b.keys())
            if a.get(path) != b.get(path)]


def report(before, after):
    old = {item["id"]: item for item in before["layers"]}
    new = {item["id"]: item for item in after["layers"]}
    lines = [f"Added layer: {item['name']}" for key, item in new.items() if key not in old]
    lines += [f"Removed layer: {item['name']}" for key, item in old.items() if key not in new]
    names = {key: item["name"] for key, item in {**old, **new}.items()}
    summarized = []

    def collections(layer):
        yield "mask", layer["masks"], ("masks",)
        for prop in layer["channels"]:
            yield "filter", prop["effects"], ("channels", prop["channel"], "effects")

    for identifier in [*old, *(key for key in new if key not in old)]:
        sides = {}
        for side, layer in (("old", old.get(identifier)), ("new", new.get(identifier))):
            for kind, items, suffix in collections(layer or dict(masks=[], channels=[])):
                for i, item in enumerate(items):
                    key = item_key(item, i)
                    names[key] = item.get("name") or item.get("kind", key)
                    sides.setdefault((kind, suffix, key), set()).add(side)
        if identifier not in old.keys() & new.keys():
            continue  # The layer line covers every item inside it.
        for (kind, suffix, key), present in sides.items():
            if present != {"old", "new"}:
                verb = "Added" if present == {"new"} else "Removed"
                lines.append(f"{verb} {kind}: {names[identifier]} > {names[key]}")
                summarized.append(("layers", identifier, *suffix, key))

    def display(value):
        if isinstance(value, float):
            return f"{value:.4g}"
        if isinstance(value, list):
            return ", ".join(display(item) for item in value)[:140]
        if isinstance(value, dict) and "$datablock" in value:
            return value["name"][:140]
        return str(names.get(value, value) if isinstance(value, str) else value)[:140]
    for path, a, b in changes(before, after):
        if len(path) > 1 and path[0] == "layers" and path[1] not in old.keys() & new.keys():
            continue
        if any(path[:len(prefix)] == prefix for prefix in summarized) or path[-1] == "$order":
            continue
        labels = [names.get(label, label.replace("_", " ").title()) for label in path if label not in {"layers", "channels", "masks", "effects"}]
        lines.append(f"{' > '.join(labels)}: {display(a)} → {display(b)}")
    return lines[:300]
