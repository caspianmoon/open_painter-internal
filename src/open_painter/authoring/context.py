"""Small JSON-safe context, with opaque bindings to explicitly selected objects."""

import json

from . import assets, commands, document, schema


def bindings(context, ts, extra_objects=()):
    objects = {ref.object for ref in ts.assignments if ref.object}
    objects.update(extra_objects)
    objects.update(obj for obj in context.selected_objects if obj.type in {"MESH", "EMPTY"})
    if len(objects) > 64:
        raise schema.AuthoringError("Limit this instruction to 64 assigned or selected objects")
    return {f"object:{i}": (obj.name_full, obj.as_pointer())
            for i, obj in enumerate(sorted(objects, key=lambda item: item.name_full))}


def resolve_bindings(saved):
    import bpy
    result = {}
    for key, (name, pointer) in saved.items():
        obj = bpy.data.objects.get(name)
        if obj is None or obj.as_pointer() != pointer:
            # Linked objects are keyed by name_full, which the name lookup does not accept.
            obj = next((item for item in bpy.data.objects if item.as_pointer() == pointer), None)
        if obj is None or obj.name_full != name:
            raise schema.AuthoringError("A bound object changed or was deleted; run the instruction again")
        result[key] = obj
    return result


def _values(group, fields):
    result = {}
    for key in fields:
        if not hasattr(group, key):
            continue
        value = getattr(group, key)
        result[key] = list(value) if hasattr(value, "__len__") and not isinstance(value, str) else value
    return result


def capture(ts, saved_objects, scope=""):
    from . import resource_budget
    from ..evaluation.anchors import index
    from ..geometry.provenance import status
    from ..texture_engine.procedurals import CATALOG as PROCEDURALS
    from ..texture_engine.filters import CATALOG as FILTERS
    objects = resolve_bindings(saved_objects)
    allowed = commands.scope_ids(document.snapshot(ts), scope)
    layers = []
    for layer in ts.layers:
        layers.append(dict(id=layer.id, name=layer.name, type=layer.type, parent_id=layer.parent_id,
            in_scope=layer.id in allowed, values=_values(layer, schema.LAYER_FIELDS),
            channels=[dict(channel=prop.channel, values=_values(prop, schema.CHANNEL_FIELDS),
                           image=bool(prop.image), detail_image=bool(prop.detail_image), anchor=prop.anchor,
                           generated_image=prop.image.get("director_image_id", "") if prop.image else "",
                           generated_detail=prop.detail_image.get("director_image_id", "") if prop.detail_image else "",
                           effects=[dict(id=effect.id or f"@{i}", kind=effect.kind,
                                         values=_values(effect, schema.FILTER_FIELDS))
                                    for i, effect in enumerate(prop.effects)]) for prop in layer.channels],
            masks=[dict(id=mask.id, source=mask.source, values=_values(mask, schema.MASK_FIELDS),
                        generated_image=mask.image.get("director_image_id", "") if mask.image else "",
                        anchor=mask.reference_anchor,
                        field=_values(mask.influence, schema.FIELD_FIELDS) if mask.source == "SCENE" or mask.edge_source == "LIVE" else None,
                        effect=dict(kind=mask.effect.kind, values=_values(mask.effect, schema.FILTER_FIELDS)) if mask.source == "FILTER" else None)
                   for mask in layer.masks]))
    geometry = []
    for identifier, obj in objects.items():
        geometry.append(dict(id=identifier, name=obj.name_full, type=obj.type,
                             dimensions=list(obj.dimensions), location=list(obj.matrix_world.translation),
                             vertices=len(obj.data.vertices) if obj.type == "MESH" else 0,
                             faces=len(obj.data.polygons) if obj.type == "MESH" else 0,
                             uv_maps=[uv.name for uv in obj.data.uv_layers] if obj.type == "MESH" else [],
                             vertex_groups=[g.name for g in obj.vertex_groups] if obj.type == "MESH" else [],
                             attributes=[a.name for a in obj.data.attributes if not a.name.startswith(".")] if obj.type == "MESH" else []))
    result = dict(version=schema.VERSION, target=dict(id=ts.id, name=ts.name, scope=scope,
                  resolution=int(ts.resolution), unit_scale=ts.id_data.unit_settings.scale_length,
                  object_ids=[key for key, obj in objects.items() if any(ref.object == obj for ref in ts.assignments)]),
                  layers=layers, objects=geometry,
                  protected_paths=[list(path) for path in commands.protected(ts, document.snapshot(ts))],
                  anchors=[dict(id=a.id, name=a.name, layer_id=a.layer_id, channel=a.channel, kind=a.kind)
                           for a in index(ts).values()],
                  maps=[dict(kind=m.kind, status=status(ts, m)) for m in ts.baked_maps],
                  procedurals=list(PROCEDURALS), filters=list(FILTERS),
                      library=assets.search("", 8),
                  limits=dict(commands=schema.MAX_COMMANDS, new_layers=schema.MAX_LAYERS, new_items=schema.MAX_ITEMS,
                              shader_texture_inputs=resource_budget.LIMIT,
                              estimated_current_texture_inputs=resource_budget.estimate(document.snapshot(ts))))
    if len(json.dumps(result, allow_nan=False).encode()) > schema.MAX_BYTES:
        raise schema.AuthoringError("This material is too large for one instruction; simplify the target stack first")
    return result


def tools(scene_scope=False):
    empty = schema.object_schema({})
    result = [dict(type="function", name=name, description=description, inputSchema=spec) for name, description, spec in (
        ("get_context", "Read the current, explicitly selected Open Painter material and allowed geometry bindings.", empty),
        ("search_library", "Search existing local material, effect and module recipes before building a new one.",
         schema.object_schema(dict(query=dict(type="string", maxLength=200)), ("query",))),
        ("describe_asset", "Inspect the channels, masks and requirements of a local recipe.",
         schema.object_schema(dict(asset_id=schema.IDENTIFIER), ("asset_id",))),
        ("list_generated_images", "After native image generation completes, obtain opaque image_ids for assign_texture. Only images from this instruction can be newly assigned; existing material textures remain editable.", empty),
        # The pinned runtime drops schemas inside anyOf/oneOf. Transport the
        # document as JSON text and validate it against our complete schema.
        ("propose_changes", "Validate one complete editable material change. Pass a JSON string matching command_schema in CURRENT CONTEXT. Replaces an earlier draft; never appends. The host applies after turn completion in Create/Refine mode.",
         schema.object_schema(dict(proposal_json=dict(type="string", maxLength=schema.MAX_BYTES)), ("proposal_json",))),
    )]
    if scene_scope:
        from . import scene as scene_authoring
        result = [tool for tool in result if tool["name"] != "propose_changes"]
        result.extend([
            dict(type="function", name="get_material_context", description="Inspect one scene material: editable stack, bound geometry, isolated 3D view, fresh mesh-map previews and the appearance reference. Inspect every affected material before proposing a coordinated change.",
                 inputSchema=schema.object_schema(dict(texture_set_id=schema.IDENTIFIER), ("texture_set_id",))),
            dict(type="function", name="propose_scene_changes", description="Validate one complete coordinated scene change. JSON must match scene_proposal_schema. Each target contains texture_set_id and a normal proposal matching command_schema. Replaces the entire draft. The host commits all targets together after turn completion; no partial application.",
                 inputSchema=schema.object_schema(dict(proposal_json=dict(type="string", maxLength=scene_authoring.MAX_BYTES)), ("proposal_json",)))])
    return result


# A reviewable prompt shipped with the add-on, not model-supplied instructions.
from pathlib import Path

INSTRUCTIONS = Path(__file__).with_name("material_director_prompt.md").read_text(encoding="utf-8")
