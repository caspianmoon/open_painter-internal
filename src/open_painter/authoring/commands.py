"""Compile bounded command batches against a detached, explicitly scoped document."""

import hashlib
import json
from types import SimpleNamespace

from . import assets, document, schema, surfaces, physical_space
from .schema import AuthoringError


def namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: namespace(v) for k, v in value.items()})
    if isinstance(value, list):
        return [namespace(v) for v in value]
    return value


def find(items, identifier, key="id"):
    for index, item in enumerate(items):
        if item.get(key) == identifier or (not item.get(key) and identifier == f"@{index}"):
            return item
    raise AuthoringError(f"Item no longer exists: {identifier}")


def locks(ts):
    try:
        value = json.loads(ts.director_locks or "[]")
        if not isinstance(value, list) or any(not isinstance(x, list) or not all(type(y) is str for y in x) for x in value):
            raise ValueError()
        return [tuple(x) for x in value]
    except (ValueError, TypeError):
        raise AuthoringError("Invalid saved protection settings") from None


def protected(ts, before):
    paths = locks(ts)
    for path in list(paths):
        if len(path) == 2 and path[0] == "layers" and any(l["id"] == path[1] for l in before["layers"]):
            paths.extend(("layers", identifier) for identifier in scope_ids(before, path[1]) if identifier != path[1])
    paths += [("layers", layer["id"]) for layer in before["layers"] if layer["type"] == "PAINT"]
    # A painted mask on a fill/folder is authored content too.
    paths += [("layers", layer["id"], "masks", mask["id"]) for layer in before["layers"]
              for mask in layer["masks"] if mask["source"] in {"PAINT", "STROKES"}]
    return paths


def scope_ids(before, root_id=""):
    if not root_id:
        return {item["id"] for item in before["layers"]}
    find(before["layers"], root_id)
    selected = {root_id}
    while True:
        expanded = selected | {item["id"] for item in before["layers"] if item["parent_id"] in selected}
        if expanded == selected:
            return selected
        selected = expanded


def validate_scope(ts, before, after, scope):
    allowed = scope_ids(before, scope)
    old_ids = {layer["id"] for layer in before["layers"]}
    locked = protected(ts, before) + protected(ts, after)
    changed_layers = set()
    for path, _, _ in document.changes(before, after):
        if len(path) < 2 or path[0] != "layers" or path[1] == "$order":
            continue
        identifier = path[1]
        changed_layers.add(identifier)
        if identifier in old_ids and identifier not in allowed:
            raise AuthoringError("The proposal changes a layer outside the selected scope")
        if any(path[:len(lock)] == lock for lock in locked):
            raise AuthoringError("The proposal changes protected content. Narrow the instruction or unlock it.")
    if scope:
        all_allowed = scope_ids(after, scope)
        if any(layer["id"] not in old_ids and layer["id"] not in all_allowed for layer in after["layers"]):
            raise AuthoringError("New content must be inside the selected folder")
    # A protected consumer can depend on a mask/channel on another layer.
    from ..evaluation.references import graph
    edges, _, _ = graph(namespace(before))
    by_id = {layer["id"]: layer for layer in before["layers"]}
    changed_paths = [p for p, _, _ in document.changes(before, after)]
    for path in locked:
        if len(path) < 2 or path[0] != "layers" or path[1] not in by_id:
            continue
        layer = by_id[path[1]]
        pending = []
        if len(path) == 2 or path[2] == "masks":
            through = len(layer["masks"]) if len(path) == 2 else next((i + 1 for i, m in enumerate(layer["masks"]) if m["id"] == path[3]), 0)
            pending.append((layer["id"], "MASK", through))
        if len(path) == 2 or path[2] == "channels":
            pending.extend((layer["id"], prop["channel"], len(prop["effects"])) for prop in layer["channels"]
                           if len(path) == 2 or prop["channel"] == path[3])
        seen = set()
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            composite = node[0] in {"OUT", "GROUP", "BELOW"}
            owner = node[1] if composite else node[0]
            source = by_id.get(owner)
            if source is None:
                continue
            prefix = ("layers", owner)
            if composite:
                affected = any(p[:2] == prefix or p == ("layers", "$order") for p in changed_paths)
            elif node[1] == "MASK":
                identifiers = {m["id"] for m in source["masks"][:node[2]]}
                affected = any(p[:3] == (*prefix, "masks") and len(p) > 3 and p[3] in identifiers for p in changed_paths)
            else:
                prop = find(source["channels"], node[1], "channel")
                prefix += ("channels", node[1])
                effect_ids = {document.item_key(e, i) for i, e in enumerate(prop["effects"][:node[2]])}
                affected = any(p[:4] == prefix and (p[4] != "effects" or (len(p) > 5 and p[5] in effect_ids)) for p in changed_paths if len(p) > 4)
            if affected:
                raise AuthoringError("A changed shared output also feeds protected content")
            pending.extend(edges.get(node, ()))


def validate_document(ts, before, after):
    from ..core.validation import hierarchy_issues
    from ..evaluation.references import warnings
    from ..generators.base import required_maps
    from ..texture_engine.procedurals import CATALOG as PROCEDURALS
    from ..texture_engine.filters import CATALOG as FILTERS
    view = namespace(after)
    errors = hierarchy_issues(view)
    if errors:
        raise AuthoringError("; ".join(errors))
    new_warnings = set(warnings(view)) - set(warnings(namespace(before)))
    if new_warnings:
        raise AuthoringError("; ".join(sorted(new_warnings)))
    available = {entry.kind for entry in ts.baked_maps if entry.image}
    old = {layer["id"]: layer for layer in before["layers"]}
    for layer in after["layers"]:
        if layer["id"] in old and document.canonical(layer) == document.canonical(old[layer["id"]]):
            continue
        for prop in layer["channels"]:
            if prop.get("material_mapping_version") == 1:
                from ..evaluation.material_space import frame_axes
                try:
                    frame_axes(prop["material_axis_u"], prop["material_axis_v"])
                except ValueError as exc:
                    raise AuthoringError(str(exc)) from exc
                if prop["material_period_m"] <= 0 or prop["material_meters_per_unit"] <= 0:
                    raise AuthoringError("Physical material scale must be positive")
                if prop["channel"] == "HEIGHT" and prop["material_height_m"] > prop["material_meters_per_unit"] * after["surface"]["height_distance"]:
                    raise AuthoringError("Relief exceeds the texture set's encodable height distance")
            if prop["procedural"] and prop["procedural"] not in PROCEDURALS:
                raise AuthoringError(f"Unknown procedural: {prop['procedural']}")
            for effect in prop["effects"]:
                if effect["kind"] not in FILTERS and effect["kind"] != "ANCHOR":
                    raise AuthoringError(f"Unknown filter: {effect['kind']}")
                if effect["kind"] == "LEVELS" and effect["white"] <= effect["black"]:
                    raise AuthoringError("Filter white level must exceed black level")
        for mask in layer["masks"]:
            if not mask["enabled"]:
                continue
            if mask["white"] <= mask["black"]:
                raise AuthoringError("Mask white level must exceed black level")
            required = set()
            if mask["source"] == "GENERATOR":
                required = required_maps(mask["generator"])
                if mask["generator"] in {"EDGE_WEAR", "CHIPS"} and mask["edge_source"] == "LIVE":
                    required = set()
            if mask["source"] == "BAKED":
                required = {mask["map_type"]}
            if required - available:
                raise AuthoringError("Bake missing maps first: " + ", ".join(sorted(required - available)))
            if mask["source"] == "PROCEDURAL" and mask["procedural"] not in PROCEDURALS:
                raise AuthoringError("Unknown mask procedural")
            if mask["source"] == "FILTER" and mask["effect"]["kind"] not in FILTERS:
                raise AuthoringError("Unknown mask filter")
            if mask["source"] == "FILTER" and mask["effect"]["kind"] == "LEVELS" and mask["effect"]["white"] <= mask["effect"]["black"]:
                raise AuthoringError("Filter white level must exceed black level")
            if mask["source"] == "SCENE" or (mask["source"] == "GENERATOR" and mask["edge_source"] == "LIVE"):
                field = mask["influence"]
                if not ts.assignments or any(not ref.object or not ref.object.data.uv_layers for ref in ts.assignments):
                    raise AuthoringError("Scene masks need an assigned UV-unwrapped mesh")
                if sum(v * v for v in field["direction"]) < 1e-12:
                    raise AuthoringError("Scene direction cannot be zero")
                if field["kind"] in {"HEIGHT", "WORLD_HEIGHT"} and field["bounds"][1] <= field["bounds"][0]:
                    raise AuthoringError("Height bounds must increase")
                if field["kind"] in {"HEIGHT", "HEAT", "MOISTURE", "SOOT", "CONTACT", "DISTANCE", "PROXIMITY", "EXPOSURE"} and not field["object"]:
                    raise AuthoringError("Choose an existing reference object for this scene mask")
                if field["kind"] in {"CONTACT", "DISTANCE", "PROXIMITY", "EXPOSURE"}:
                    from ..geometry.influencers import objects as influencers
                    if not influencers(ts, namespace(field)):
                        raise AuthoringError("This scene field needs an external mesh as its reference object")
                for ref in ts.assignments:
                    obj = ref.object
                    if field["kind"] in {"VERTEX_GROUP", "ATTRIBUTE"} and field["object"] and obj != field["object"]:
                        continue
                    if field["kind"] == "VERTEX_GROUP" and field["attribute"] not in obj.vertex_groups:
                        raise AuthoringError(f"Missing vertex group: {field['attribute']}")
                    if field["kind"] == "ATTRIBUTE" and field["attribute"] not in obj.data.attributes:
                        raise AuthoringError(f"Missing named attribute: {field['attribute']}")
                if field["kind"] in {"VERTEX_GROUP", "ATTRIBUTE"} and field["object"] and not any(
                        ref.object == field["object"] for ref in ts.assignments):
                    raise AuthoringError("Attribute reference object must belong to this texture set")


def compile_proposal(ts, proposal, operation_id, *, scope="", objects=None, images=None, correspondences=None,
                     observations=None):
    proposal = schema.validate(proposal)
    before = document.snapshot(ts)
    after = document.clone(before)
    aliases = {}
    objects = objects or {}
    count = 0
    patches = []

    def allocate(alias):
        return "md." + hashlib.sha256(f"{operation_id}:{alias}".encode()).hexdigest()[:32]

    def resolve(identifier):
        if identifier.startswith("new:"):
            if identifier not in aliases:
                raise AuthoringError(f"New item must be declared before use: {identifier}")
            return aliases[identifier]
        return identifier

    def insertion(parent):
        siblings = [(i, layer) for i, layer in enumerate(after["layers"]) if layer["parent_id"] == parent]
        locked = protected(ts, before)
        protect_ids = {p[1] for p in locked if len(p) > 1 and p[0] == "layers"}
        # Keep existing paint/protected overlays above newly inserted material.
        protected_positions = [i for i, layer in siblings if layer["id"] in protect_ids]
        if protected_positions:
            return min(protected_positions)
        if siblings:
            subtree = scope_ids(after, siblings[-1][1]["id"])
            return max(i + 1 for i, layer in enumerate(after["layers"]) if layer["id"] in subtree)
        return next((i + 1 for i, layer in enumerate(after["layers"]) if layer["id"] == parent), len(after["layers"]))

    for cmd in proposal["commands"]:
        op = cmd["op"]
        if "id" in cmd:
            aliases[cmd["id"]] = allocate(cmd["id"])
        if op == "patch_surface":
            patches.append(surfaces.patch(ts, after, cmd, resolve(cmd["surface_id"])))
            continue
        if op in {"add_layer", "insert_asset", "create_surface", "create_bands", "add_generated_detail"}:
            parent = resolve(cmd.get("parent_id", scope))
            if parent and find(after["layers"], parent)["type"] != "FOLDER":
                raise AuthoringError("New layers need a folder or texture-set scope")
            if op in {"create_surface", "create_bands"}:
                expand = surfaces.expand if op == "create_surface" else surfaces.expand_bands
                items = expand(ts, cmd, aliases[cmd["id"]], parent,
                               lambda key: allocate(cmd["id"] + key), objects)
                # Reserved colon suffixes cannot collide with declared new:name
                # aliases, and are usable later in the same complete proposal.
                for index, item in enumerate(items):
                    role = item.get("surface_role", "")
                    if role in {"FINISH", "COATING", "SUBSTRATE"}:
                        suffix = "finish" if role in {"FINISH", "COATING"} else "substrate"
                        aliases[cmd["id"] + ":" + suffix] = item["id"]
                    if role == "BAND":
                        aliases[cmd["id"] + ":band" + str(index)] = item["id"]
                    for mask in item["masks"]:
                        if mask["source"] == "MESH":
                            aliases[cmd["id"] + ":region"] = mask["id"]
                        if role == "COATING" and mask.get("surface_role") == "PAINT_LOSS":
                            aliases[cmd["id"] + ":paint_loss"] = mask["id"]
                        if mask.get("surface_role") == "CHIP_PATTERN":
                            aliases[cmd["id"] + ":chips"] = mask["id"]
                        if mask.get("surface_role") == "WEAR_PLACEMENT":
                            aliases[cmd["id"] + ":wear_placement"] = mask["id"]
            elif op == "add_generated_detail":
                from . import generated_surfaces
                items = [generated_surfaces.detail_layer(ts, cmd, aliases[cmd["id"]], parent, objects, images)]
            elif op == "add_layer":
                items = [assets.new_layer(aliases[cmd["id"]], cmd["name"], cmd["kind"], parent)]
            else:
                length = max((max(ref.object.dimensions) for ref in ts.assignments if ref.object), default=1)
                items = assets.expand(cmd["asset_id"], aliases[cmd["id"]], parent, cmd.get("seed", 1),
                                      cmd.get("map_mode", "BAKED"), lambda key: allocate(cmd["id"] + key),
                                      {m.kind: m.image for m in ts.baked_maps if m.image}, length)
            at = insertion(parent)
            after["layers"][at:at] = items
            count += len(items)
            continue
        layer = find(after["layers"], resolve(cmd["layer_id"]))
        values = document.clone(cmd.get("values", {}))
        if op == "add_region_mask":
            from . import regions
            mask = document.defaults("OP_Mask")
            mask.update(id=aliases[cmd["id"]], name=cmd["name"], source="MESH")
            mask["mesh"].update(regions.selector(ts, cmd["region_id"], objects))
            layer["masks"].append(mask)
        elif op == "set_layer":
            layer.update(values)
            if values.get('cache_mode') == 'CACHED' and not layer['cache']:
                from ..constants import CHANNELS
                layer['cache'] = [dict(document.defaults('OP_LayerCache'), channel=channel) for channel in CHANNELS]
        elif op == "set_channel":
            if layer["type"] == "FOLDER" and set(values) - {"enabled", "opacity"}:
                raise AuthoringError("Edit a fill layer inside this folder to change channel content")
            find(layer["channels"], cmd["channel"], "channel").update(values)
        elif op == "assign_bundle":
            from . import sources
            sources.assign(ts, layer, cmd, aliases[cmd["id"]], images)
        elif op == "assign_generated_surface":
            from . import generated_surfaces
            generated_surfaces.assign(ts, layer, cmd, images)
        elif op == "assign_surface_maps":
            from . import surface_maps
            surface_maps.assign(layer, cmd, images)
        elif op == 'replace_surface_source':
            from . import surface_replacement
            surface_replacement.assign(ts, layer, cmd, images, correspondences)
        elif op == 'generate_surface_field':
            from . import surface_field
            surface_field.assign(ts, layer, cmd, images, observations, objects)
        elif op == 'assign_projected_surface':
            from . import surface_projection
            surface_projection.assign(ts, layer, cmd, images, objects)
        elif op == "assign_texture":
            from .generated_textures import placeholder
            if bool(cmd.get("channel")) == bool(cmd.get("mask_id")):
                raise AuthoringError("Choose exactly one texture channel or mask")
            if cmd.get("mask_id"):
                mask = find(layer["masks"], resolve(cmd["mask_id"]))
                if mask["source"] != "FILL" or "target" in cmd:
                    raise AuthoringError("Generated mask images require a FILL mask without a channel target")
                if "physical_extent_m" in cmd:
                    raise AuthoringError("Generated fill-mask images currently use native UV/world projection controls; physical_extent_m is supported for channel images only")
                mask["image"] = placeholder(cmd["image_id"], "Non-Color", images)
            else:
                if layer["type"] != "FILL":
                    raise AuthoringError("Generated channel textures belong on a fill layer")
                prop = find(layer["channels"], cmd["channel"], "channel")
                detail = cmd.get("target", "CONTENT") == "DETAIL"
                if detail and cmd["channel"] in {"NORMAL", "AO", "OPACITY"}:
                    raise AuthoringError("Use CONTENT for normal, AO and opacity textures")
                color_space = "sRGB" if not detail and cmd["channel"] in {"BASE_COLOR", "EMISSION"} else "Non-Color"
                prop["detail_image" if detail else "image"] = placeholder(cmd["image_id"], color_space, images)
                if not detail:
                    prop["anchor"] = ""
                    prop["detail_image"] = None
                else:
                    prop["image"] = None
                    prop["anchor"] = ""
                prop["procedural"] = ""
                prop["enabled"] = True
                if "physical_extent_m" in cmd:
                    if cmd["channel"] == "NORMAL":
                        raise AuthoringError("Tangent normal images need UV mapping; use a metric height source for projected relief")
                    from .physical_space import require as metric_scale
                    prop["material_mapping_version"] = 1
                    prop["material_meters_per_unit"] = metric_scale(ts)
                    prop["material_image_extent_m"] = cmd["physical_extent_m"]
                elif prop.get("material_mapping_version") == 1:
                    prop["material_image_extent_m"] = [prop["material_period_m"]] * 2
                prop["material_source_id"] = cmd["image_id"]
                record = (images or {}).get(cmd["image_id"], {})
                prop["source_metadata"] = json.dumps(dict(version=1, source_id=cmd["image_id"],
                    purpose="Generated scalar detail" if detail else "Generated channel content",
                    channels=[cmd["channel"]], mapping_frame=prop["projection"],
                    physical_extent_m=prop["material_image_extent_m"] if prop.get("material_mapping_version") == 1 else None, provenance="GENERATED", calibrated_bundle=False,
                    revision_identity=record.get("sha256", cmd["image_id"]),
                    dimensions=record.get("size") or record.get("dimensions") or [record.get("width"), record.get("height")],
                    note="Approximate generated source; independent images are not a calibrated PBR bundle"), sort_keys=True)
        elif op == "add_mask":
            mask = document.defaults("OP_Mask")
            mask.update(id=aliases[cmd["id"]], source=cmd["source"], name=cmd["name"])
            mask.update(physical_space.mask_values(ts, mask, values))
            if mask["source"] == "SCENE":
                mask["influence"].update(mode="LIVE", resolution="256")
            if mask["edge_source"] == "LIVE":
                mask["influence"].update(kind="EDGE", mode="LIVE", resolution="512")
            layer["masks"].append(mask)
        elif op in {"set_mask", "set_field"}:
            mask = find(layer["masks"], resolve(cmd["mask_id"]))
            if op == "set_mask":
                was_live = mask["edge_source"] == "LIVE"
                mask.update(physical_space.mask_values(ts, mask, values))
                if mask["edge_source"] == "LIVE":
                    mask["influence"].update(kind="EDGE", mode="LIVE")
                    if not was_live and mask["influence"]["resolution"] == "64":
                        mask["influence"]["resolution"] = "512"
            else:
                if mask["source"] != "SCENE" and mask["edge_source"] != "LIVE":
                    raise AuthoringError("Select a scene or live-edge mask")
                mask["influence"].update(physical_space.field_values(ts, mask["influence"], values))
                mask["influence"]["mode"] = "LIVE"
                if "object_id" in cmd:
                    obj = objects.get(cmd["object_id"])
                    if obj is None:
                        raise AuthoringError("Object binding is outside the captured context")
                    mask["influence"]["object"] = obj
                    if mask["influence"]["kind"] == "GROUND":
                        mask["influence"]["ground_object"] = obj
        elif op in {"add_filter", "set_filter"}:
            if cmd.get("mask_id"):
                mask = find(layer["masks"], resolve(cmd["mask_id"]))
                if mask["source"] != "FILTER":
                    raise AuthoringError("Select a filter mask")
                effect = mask["effect"]
            else:
                if not cmd.get("channel"):
                    raise AuthoringError("A filter requires a channel or mask target")
                prop = find(layer["channels"], cmd["channel"], "channel")
                if op == "add_filter":
                    from ..texture_engine.filter_defaults import initial_amount
                    effect = document.defaults("OP_TextureEffect")
                    effect.update(id=aliases[cmd["id"]], kind=cmd["kind"], name=cmd["kind"].title(),
                                  amount=initial_amount(cmd["kind"]))
                    prop["effects"].append(effect)
                else:
                    # An empty id would otherwise match the first id-less artist effect.
                    if not cmd.get("effect_id"):
                        raise AuthoringError("set_filter on a channel needs the effect_id from the context")
                    effect = find(prop["effects"], resolve(cmd["effect_id"]))
            if "kind" in cmd:
                if effect["kind"] != cmd["kind"]:
                    from ..texture_engine.filter_defaults import initial_amount
                    effect["amount"] = initial_amount(cmd["kind"])
                effect["kind"] = cmd["kind"]
            effect.update(values)
        elif op == "connect_anchor":
            identifier = resolve(cmd["anchor_id"])
            from ..evaluation.anchors import index as anchors
            if identifier not in anchors(namespace(after)):
                raise AuthoringError("Select an existing published anchor")
            if bool(cmd.get("mask_id")) == bool(cmd.get("channel")):
                raise AuthoringError("Choose exactly one anchor consumer")
            if cmd.get("mask_id"):
                mask = find(layer["masks"], resolve(cmd["mask_id"]))
                if mask["source"] != "REFERENCE":
                    raise AuthoringError("Anchors feed Published Input masks")
                mask["reference_anchor"] = identifier
            else:
                if layer["type"] != "FILL":
                    raise AuthoringError("Anchored channel content belongs on a fill layer")
                prop = find(layer["channels"], cmd["channel"], "channel")
                prop["anchor"] = identifier
    added_items = sum(len(layer["masks"]) + sum(len(c["effects"]) for c in layer["channels"]) for layer in after["layers"]) - sum(len(layer["masks"]) + sum(len(c["effects"]) for c in layer["channels"]) for layer in before["layers"])
    if count > schema.MAX_LAYERS or added_items > schema.MAX_ITEMS or len(after["layers"]) > 256:
        raise AuthoringError("This proposal exceeds the layer/mask budget; split it into smaller edits")
    surfaces.validate_patches(before, after, patches)
    validate_scope(ts, before, after, scope)
    validate_document(ts, before, after)
    from .resource_budget import validate as validate_resources
    validate_resources(before, after)
    return dict(before=before, after=after, aliases=aliases, summary=proposal["summary"],
                changes=document.report(before, after), operation_id=operation_id, patch_constraints=patches)
