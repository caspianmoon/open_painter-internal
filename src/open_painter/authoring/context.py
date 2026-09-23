"""Small JSON-safe context, with opaque bindings to explicitly selected objects."""

import json
from collections import Counter, defaultdict

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
        if key in {"period_m", "chip_radius_m"} and not getattr(group, "material_mapping_version", 0):
            continue
        if key == "chip_radius_m" and getattr(group, "procedural", "") != "DOTS":
            continue
        if key in {"distance_m", "bounds_m"} and not getattr(group, "physical_mapping_version", 0):
            continue
        if not hasattr(group, key):
            continue
        value = getattr(group, key)
        result[key] = list(value) if hasattr(value, "__len__") and not isinstance(value, str) else value
    return result


def compact(value):
    """Lossless provider representation; never edit a material or its snapshot.

    Common fields live in explicit tables rather than being repeated in every
    channel/geometry record. Only keys present in *every* group member qualify,
    so an absent field never becomes an inherited null (or any other value).
    Small contexts retain the existing representation and the same byte cap.
    """
    def encoded(item):
        return json.dumps(item, allow_nan=False, sort_keys=True)

    if len(encoded(value).encode()) <= schema.MAX_BYTES:
        return value
    result = document.clone(value)
    defaults = {}

    def factor(name, records, explicit=()):
        if len(records) < 2:
            return
        keys = set.intersection(*(set(record) for record in records)) - set(explicit)
        shared = {}
        for key in sorted(keys):
            counts = Counter(encoded(record[key]) for record in records)
            chosen, count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
            # Tiny/unshared fields cannot repay even their table entry.
            if count >= 2 and (count - 1) * (len(key) + len(chosen) + 4) > 16:
                shared[key] = json.loads(chosen)
        if not shared:
            return
        reduced = [dict({key: item for key, item in record.items()
                         if key not in shared or encoded(item) != encoded(shared[key])}, **{"$defaults": name})
                   for record in records]
        if len(encoded(reduced)) + len(encoded({name: shared})) >= len(encoded(records)):
            return
        defaults[name] = shared
        for record, replacement in zip(records, reduced):
            record.clear()
            record.update(replacement)

    layers = result.get("layers", [])
    channels = [channel for layer in layers for channel in layer.get("channels", [])]
    masks = [mask for layer in layers for mask in layer.get("masks", [])]
    effects = [effect for channel in channels for effect in channel.get("effects", [])]
    effects.extend(mask["effect"] for mask in masks if mask.get("effect"))
    for name, records, explicit in (
            ("channel_values", channels, ("enabled", "opacity", "color", "value", "procedural")),
            ("layer_values", layers, ("name", "visible", "opacity")),
            ("mask_values", masks, ("name", "enabled", "strength", "invert", "operation")),
            ("filter_values", effects, ("enabled", "strength"))):
        factor(name, [record["values"] for record in records if isinstance(record.get("values"), dict)], explicit)
    factor("channel_records", channels,
           ("channel", "values", "material_source_id", "physical_mapping", "source_metadata", "effects"))
    factor("physical_mapping", [record["physical_mapping"] for record in channels + masks
        if isinstance(record.get("physical_mapping"), dict)],
        ("version", "frame", "period_m", "height_m", "image_extent_m", "source_id", "meters_per_world_unit"))
    grouped = defaultdict(list)
    for region in result.get("regions", []):
        grouped[region.get("object_id", "")].append(region)
    factor("region_uv", [region["material_space"] for region in result.get("regions", [])
                         if isinstance(region.get("material_space"), dict)],
           ("valid_triangles", "total_triangles", "valid_surface_fraction"))
    for object_id, records in sorted(grouped.items()):
        factor("regions:" + object_id, records,
               ("id", "kind", "object_id", "slot_index", "face_count", "triangle_count"))
    if defaults:
        result["context_encoding"] = dict(version=1, format="SHARED_DEFAULTS",
            rule="Any object with $defaults inherits all fields from defaults[$defaults]. Its explicit fields override "
                 "those defaults; remove only the $defaults marker when reconstructing. Apply recursively. "
                 "Missing fields without a declared default remain absent; null is an explicit value. "
                 "All IDs and exact numeric values are retained. Tables describe this captured state, not factory defaults.",
            defaults=defaults)
    if len(encoded(result).encode()) >= len(encoded(value).encode()):
        return value
    return result


def capture(ts, saved_objects, scope="", *, region_registry=None):
    from . import resource_budget, physical_space, regions
    from ..evaluation.anchors import index
    from ..geometry.provenance import status
    from ..texture_engine.procedurals import CATALOG as PROCEDURALS
    from ..texture_engine.filters import CATALOG as FILTERS
    objects = regions.bindings(resolve_bindings(saved_objects), region_registry or {})
    allowed = commands.scope_ids(document.snapshot(ts), scope)
    layers = []
    for layer in ts.layers:
        layers.append(dict(id=layer.id, name=layer.name, type=layer.type, parent_id=layer.parent_id,
            in_scope=layer.id in allowed, values=_values(layer, schema.LAYER_FIELDS),
            channels=[dict(channel=prop.channel, values=_values(prop, schema.CHANNEL_FIELDS),
                           image=bool(prop.image), detail_image=bool(prop.detail_image), anchor=prop.anchor,
                           material_source_id=getattr(prop, "material_source_id", ""),
                           source_metadata=getattr(prop, "source_metadata", ""),
                           physical_mapping=(dict(version=prop.material_mapping_version,
                               meters_per_world_unit=prop.material_meters_per_unit,
                               frame=prop.material_frame, origin=list(prop.material_origin),
                               axis_u=list(prop.material_axis_u), axis_v=list(prop.material_axis_v),
                               directional=prop.material_directional, period_m=prop.material_period_m,
                               height_m=prop.material_height_m, image_extent_m=list(prop.material_image_extent_m))
                               if prop.material_mapping_version else None),
                           generated_image=prop.image.get("director_image_id", "") if prop.image else "",
                           generated_detail=prop.detail_image.get("director_image_id", "") if prop.detail_image else "",
                           effects=[dict(id=effect.id or f"@{i}", kind=effect.kind,
                                         values=_values(effect, schema.FILTER_FIELDS))
                                    for i, effect in enumerate(prop.effects)]) for prop in layer.channels],
            masks=[dict(id=mask.id, source=mask.source, values=_values(mask, schema.MASK_FIELDS),
                        generated_image=mask.image.get("director_image_id", "") if mask.image else "",
                        selection=(dict(kind=mask.mesh.kind, region_id=mask.mesh.region_id,
                                        object_id=next((key for key, obj in objects.items() if obj == mask.mesh.object), None),
                                        topology_digest=mask.mesh.region_topology, provenance=mask.mesh.region_provenance)
                                   if mask.source == "MESH" else None),
                        anchor=mask.reference_anchor,
                        field=_values(mask.influence, schema.FIELD_FIELDS) if mask.source == "SCENE" or mask.edge_source == "LIVE" else None,
                        physical_mapping=(dict(version=mask.material_mapping_version,
                            meters_per_world_unit=mask.material_meters_per_unit,
                            period_m=mask.material_period_m, frame=mask.material_frame,
                            origin=list(mask.material_origin), axis_u=list(mask.material_axis_u),
                            axis_v=list(mask.material_axis_v),
                            source_id=mask.material_source_id, provenance=mask.material_provenance) if mask.material_mapping_version else None),
                        field_mapping=(dict(version=mask.influence.physical_mapping_version,
                            meters_per_world_unit=mask.influence.physical_meters_per_unit,
                            provenance=mask.influence.physical_provenance)
                            if mask.influence.physical_mapping_version else None),
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
                  layers=layers, objects=geometry, physical_space=physical_space.capture(ts),
                  regions=regions.capture(ts, objects),
                  protected_paths=[list(path) for path in commands.protected(ts, document.snapshot(ts))],
                  anchors=[dict(id=a.id, name=a.name, layer_id=a.layer_id, channel=a.channel, kind=a.kind)
                           for a in index(ts).values()],
                  maps=[dict(kind=m.kind, status=status(ts, m)) for m in ts.baked_maps],
                  procedurals=list(PROCEDURALS), filters=list(FILTERS),
                      library=assets.search("", 8),
                  limits=dict(commands=schema.MAX_COMMANDS, new_layers=schema.MAX_LAYERS, new_items=schema.MAX_ITEMS,
                              shader_texture_inputs=resource_budget.LIMIT,
                              estimated_current_texture_inputs=resource_budget.estimate(document.snapshot(ts))))
    from . import surfaces, sources
    result["surfaces"] = surfaces.describe(ts)
    result["surface_catalog"] = surfaces.catalog()
    result["existing_sources"] = sources.describe(ts)
    result = compact(result)
    if len(json.dumps(result, allow_nan=False).encode()) > schema.MAX_BYTES:
        raise schema.AuthoringError("This material is too large for one instruction; simplify the target stack first")
    return result


def tools(scene_scope=False):
    from .surface_workflow import PLAN_SCHEMA, CRITIQUE_SCHEMA
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
    for tool in result:
        if tool["name"] in {"propose_changes", "propose_scene_changes"}:
            tool["inputSchema"]["properties"].update(
                candidate_digest=dict(type="string", maxLength=64),
                visual_review=dict(type="string", minLength=20, maxLength=2000))
            tool["description"] += " When candidate review is enabled, supply the exact candidate_digest and a concrete visual_review of the returned Eevee views and channel diagnostics."
    target = dict(texture_set_id=schema.IDENTIFIER)
    result.extend(dict(type="function", name=name, description=description, inputSchema=spec) for name, description, spec in (
        ("plan_surface", "Record the object's visual art direction and independent component histories, macro/meso/micro detail, generated-source roles, mapping, and optional fictional details. Required before preview when Generate Textures is enabled. This does not author or approve anything.", PLAN_SCHEMA),
        ("critique_candidate", "Evaluate the current actual Eevee full views, closeups, channels and generated-source bypass against the request. Do not praise it: identify only concrete weaknesses and materially useful corrections, with evidence image IDs. Required after each generated-surface preview. At most two correction passes; this is not an aesthetic pass/fail score.", CRITIQUE_SCHEMA),
        ("list_regions", "List exact assigned faces, connected components and explicit artist selections with bounds, UV coverage and uncertainty. Region names are not semantic proof.",
         schema.object_schema(target, ("texture_set_id",))),
        ("query_region", "Propose an editable face region inside one existing material assignment using geometric bounds and optional surface-normal limits. Useful when connected geometry contains differently oriented members. Returns membership, uncertainty and an opaque region ID; no artist geometry or materials change. Preview the region before using its ID in add_region_mask or create_surface.",
         schema.object_schema(dict(**target, object_id=schema.IDENTIFIER, slot_index=dict(type="integer", minimum=0, maximum=255),
            space=dict(type="string", enum=["WORLD", "OBJECT", "ASSIGNMENT_NORMALIZED"]),
            bounds_min=schema.array(3, schema.number(-1000000, 1000000)), bounds_max=schema.array(3, schema.number(-1000000, 1000000)),
            selection=dict(type="string", enum=["CENTROID", "INTERSECTION"]),
            normal_direction=schema.array(3, schema.number(-1, 1)), min_dot=schema.number(-1, 1), max_dot=schema.number(-1, 1)),
            ("texture_set_id", "object_id", "slot_index", "space", "bounds_min", "bounds_max", "selection"))),
        ("capture_surface_observations", "Capture surface observations: real rendered views of this material's actual 3D surface, each with exact per-pixel triangle, barycentric, position, normal and UV identity. Six geometric directions first, then cameras chosen automatically from measured coverage of under-covered geometry. Every observation packet carries its rendered view, AO, Curvature, Normal, World Normal, Position and Thickness evidence on the same visible surface, the valid surface mask and generic surface directions. Generate one image per observation keeping its exact framing and silhouette; Open Painter reprojects and fuses them into the existing UV atlas. This is the primary path for generated surface appearance. Read-only.",
         schema.object_schema(dict(**target, region_id=schema.IDENTIFIER,
             directions=dict(type='array', items=schema.enum('front', 'back', 'left', 'right', 'top', 'bottom'), minItems=1, maxItems=6),
             max_observations=dict(type='integer', minimum=1, maximum=12),
             packet_limit=dict(type='integer', minimum=1, maximum=3),
             resolution=dict(type='integer', minimum=128, maximum=1024),
             complete_coverage=schema.BOOL), ("texture_set_id",))),
        ("inspect_surface_observation", "Read the full evidence packet of one already captured observation: rendered surface view, geometry evidence, valid surface mask and surface directions, with its camera transform and projection. Use it before generating that observation's appearance.",
         schema.object_schema(dict(observation_id=schema.IDENTIFIER), ("observation_id",))),
        ("inspect_surface_coverage", "Measure how well the captured observations actually cover the assigned surface. Returns area-weighted GOOD/WEAK/MISSING classification, the uncovered surface in world coordinates, measured fused-field coverage, and the directions that would cover the rest. Use it before deciding that generation is complete; never fill uncovered surface by stretching nearby pixels.",
         schema.object_schema(dict(**target, region_id=schema.IDENTIFIER), ("texture_set_id",))),
        ("inspect_surface_area", "Capture one corrective observation framed on a specific piece of surface in world coordinates, for geometry that coverage reports as weak or missing, or that the candidate shows badly represented. Returns a normal observation packet; reproject it into the same field with the earlier observations and fusion weights will decide where it replaces weaker coverage.",
         schema.object_schema(dict(**target, center=schema.array(3, schema.number(-1000000, 1000000)),
             radius=schema.number(0, 1000000), direction=schema.array(3, schema.number(-1, 1)),
             region_id=schema.IDENTIFIER, resolution=dict(type='integer', minimum=128, maximum=1024)),
             ("texture_set_id", "center"))),
        ("inspect_surface", "Get an exact surface-aligned generation template and matching projection frame for any selected region. Choose outward viewing and up directions in WORLD coordinates. Generate a localized nonrepeating source from this template, then use assign_projected_surface with the returned frame and region ID. Does not alter geometry, UVs, material slots or artist state.",
         schema.object_schema(dict(**target, region_id=schema.IDENTIFIER,
             normal_direction=schema.array(3, schema.number(-1, 1)), up_direction=schema.array(3, schema.number(-1, 1))),
             ('texture_set_id', 'region_id', 'normal_direction', 'up_direction'))),
        ("inspect_surface_source", "Read an existing surface layer's original albedo image as the exact reference for matching grayscale height, roughness and metallic generation. Use assign_surface_maps to attach maps without changing its color or projection. Height drives preview and exported normal relief.",
         schema.object_schema(dict(**target, layer_id=schema.IDENTIFIER), ("texture_set_id", "layer_id"))),
        ('inspect_texture_correspondence', 'Inspect a visible defect in the CURRENT candidate. Coordinates are normalized, top-left origin in the named Eevee view. Returns pixel→surface identity, nearby paired UV seams and orientation, high-resolution Eevee/UV/channel/geometry crops, actual generated sources, local source edit masks and geometric flow arrows. Call before correcting seams, stretching, direction, leakage or channel discontinuities. Read-only; no artist state changes.',
         schema.object_schema(dict(**target, candidate_digest=dict(type='string', minLength=64, maxLength=64),
             view=dict(type='string', enum=['front', 'back', 'left', 'right', 'top', 'bottom', 'close', 'component_1', 'component_2', 'component_3']),
             x=schema.number(0, 1), y=schema.number(0, 1)), ('texture_set_id', 'candidate_digest', 'view', 'x', 'y'))),
        ("inspect_region", "Read one allowed region's membership and spatial metadata before authoring an editable region mask.",
         schema.object_schema(dict(**target, region_id=schema.IDENTIFIER), ("texture_set_id", "region_id"))),
        ("inspect_material_channels", "Read actual composed channel statistics and UV images for an allowed material. Does not commit or alter the artist material.",
         schema.object_schema(target, ("texture_set_id",))),
        ("inspect_mask_coverage", "Measure an existing effect mask over actual mesh surface area, with a fixed 0.5 threshold and explicit invalid-UV denominator. Opacity is separate from coverage.",
         schema.object_schema(dict(**target, layer_id=schema.IDENTIFIER), ("texture_set_id", "layer_id"))),
        ("preview_proposal", "Compile one complete proposal and show disposable Eevee candidate views and actual channel diagnostics on the artist geometry. Uses the existing environment. Never commits; replaces the last candidate. One initial candidate and at most two corrections per instruction. Scene scope uses scene_proposal_schema; other scopes use command_schema.",
         schema.object_schema(dict(proposal_json=dict(type="string", maxLength=1024 * 1024)), ("proposal_json",))),
        ("inspect_candidate", "Inspect images and diagnostics of the current uncommitted candidate again. Rejects stale or superseded candidates.",
         schema.object_schema(dict(candidate_digest=dict(type="string", minLength=64, maxLength=64)), ("candidate_digest",))),
        ("compare_candidates", "Compare a previous candidate from this instruction to the current candidate. Returns actual Eevee images and channel statistics, with explicit historical/current labels. Comparison does not approve an older candidate for commit.",
         schema.object_schema(dict(previous_digest=dict(type="string", minLength=64, maxLength=64),
                                   current_digest=dict(type="string", minLength=64, maxLength=64)), ("previous_digest", "current_digest"))),
        ("report_inability", "End with a structured unmet requirement, missing information, or unavailable capability. Never fake a successful material when the request cannot be met.",
         schema.object_schema(dict(code=dict(type="string", enum=["NEEDS_INPUT", "UNSUPPORTED_CAPABILITY", "VALIDATION_FAILED", "VISUAL_REQUIREMENTS_UNMET"]),
                                  reason=dict(type="string", minLength=1, maxLength=2000)), ("code", "reason"))),
    ))
    return result


# A reviewable prompt shipped with the add-on, not model-supplied instructions.
from pathlib import Path

INSTRUCTIONS = Path(__file__).with_name("material_director_prompt.md").read_text(encoding="utf-8")
