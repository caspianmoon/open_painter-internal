"""The complete writable surface of Material Director. No RNA paths or code."""

import json
import math
import re

VERSION = 1
MAX_BYTES = 256 * 1024
MAX_COMMANDS = 64
MAX_LAYERS = 24
MAX_ITEMS = 128


def number(lo, hi):
    return dict(type="number", minimum=lo, maximum=hi)


def enum(*values):
    return dict(type="string", enum=list(values))


def array(size, item):
    return dict(type="array", items=item, minItems=size, maxItems=size)


def object_schema(properties, required=()):
    return dict(type="object", properties=properties, required=list(required),
                additionalProperties=False)


IDENTIFIER = dict(type="string", maxLength=160)
NAME = dict(type="string", minLength=1, maxLength=120)
BOOL = dict(type="boolean")
UNIT = number(0, 1)
SEED = dict(type="integer", minimum=0, maximum=2147483647)
CHANNEL = enum("BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO", "EMISSION", "OPACITY")
SEMANTIC = enum("NONE", "DIRT", "DUST", "FADE", "SCRATCHES", "OXIDATION", "COATING_LOSS")
COMBINE = enum("NORMAL", "MULTIPLY", "ADD", "MAX", "MIN")
LAYER_FIELDS = dict(name=NAME, visible=BOOL, opacity=UNIT, effect_semantic=SEMANTIC,
                    group_mode=enum("ISOLATED", "PASSTHROUGH"), cache_mode=enum('LIVE', 'CACHED'))
CHANNEL_FIELDS = dict(enabled=BOOL, opacity=UNIT, value=UNIT, color=array(4, UNIT),
                      procedural=dict(type="string", maxLength=80),
                      procedural_scale=number(.01, 512), procedural_seed=SEED,
                      procedural_detail=number(0, 2), procedural_height=number(-1, 1),
                      projection=enum("UV", "TRIPLANAR"),
                      scale=array(2, number(.001, 512)), offset=array(2, number(-100, 100)),
                      rotation=number(-100, 100), triplanar_hardness=number(1, 64))
MASK_FIELDS = dict(name=NAME, enabled=BOOL, operation=COMBINE, invert=BOOL,
                   period_m=number(.000001, 100), chip_radius_m=number(.000001, 10),
                   black=UNIT, white=UNIT, strength=UNIT, amount=UNIT,
                   contrast=number(.01, 20), edge_width=number(.05, 1),
                   edge_size=number(0, 3), noise_scale=number(1, 512),
                   noise_strength=UNIT, seed=SEED,
                   projection=enum("UV", "TRIPLANAR"), scale=array(2, number(.001, 512)),
                   offset=array(2, number(-100, 100)), rotation=number(-100, 100),
                   triplanar_hardness=number(1, 64),
                   procedural=dict(type="string", minLength=1, maxLength=80),
                   generator=enum("EDGE_WEAR", "DIRT", "DUST", "CAVITY_DIRT", "RUST",
                                  "CHIPS", "SCRATCHES", "DRIPS", "RUNOFF", "NOISE", "WORLD_NORMAL",
                                  "POSITION", "THICKNESS", "OBJECT_NORMAL", "MATERIAL_ID",
                                  "OBJECT_ID", "UV_ISLAND", "UV_BORDER", "CURVATURE", "AO"),
                   edge_source=enum("BAKED", "LIVE"),
                   map_type=enum("AO", "CURVATURE", "NORMAL", "POSITION", "WORLD_NORMAL",
                                 "OBJECT_NORMAL", "THICKNESS", "MATERIAL_ID", "OBJECT_ID",
                                 "UV_ISLAND", "UV_BORDER", "MEAN_CURVATURE", "SELECTION",
                                 "BEVEL_NORMAL", "BEVEL_MASK"))
FIELD_FIELDS = dict(kind=enum("UP", "DOWN", "HEIGHT", "WORLD_HEIGHT", "GROUND", "CONVEXITY",
                             "CONCAVITY", "EDGE", "ATTRIBUTE", "VERTEX_GROUP", "PROXIMITY",
                             "CONTACT", "DISTANCE", "EXPOSURE", "MOISTURE", "HEAT", "SOOT"),
                    mode=enum("LIVE"), resolution=enum("64", "128", "256", "512"),
                    distance=number(.00001, 10000), bounds=array(2, number(-100000, 100000)),
                    distance_m=number(.000001, 10000), bounds_m=array(2, number(-100000, 100000)),
                    direction=array(3, number(-1, 1)),
                    attribute=dict(type="string", maxLength=120),
                    falloff=enum("LINEAR", "SMOOTH", "SQUARED", "ROOT"))
FILTER_FIELDS = dict(enabled=BOOL, strength=UNIT, contrast=number(0, 10),
                     black=UNIT, white=UNIT, gamma=number(.01, 10),
                     output_black=UNIT, output_white=UNIT,
                     amount=number(-10, 10), radius=number(0, 32),
                     scale=number(.01, 512), seed=SEED,
                     hue=number(-1, 1), saturation=number(0, 4), lightness=number(-1, 1),
                     angle=number(-100, 100), softness=number(.0001, 1),
                     steps=dict(type="integer", minimum=2, maximum=256))


def command(op, fields, required):
    return object_schema(dict(op=enum(op), **fields), ("op", *required))


SURFACE_VALUES = dict(color=array(4, UNIT), roughness_range=array(2, UNIT),
    period_m=number(.000001, 100), height_m=number(0, .1), relative_period=number(.0001, 1),
    color_variation=number(0, 2), frame=enum("WORLD", "OBJECT", "COMPONENT"),
    origin=array(3, number(-1e6, 1e6)), axis_u=array(3, number(-1, 1)), axis_v=array(3, number(-1, 1)),
    seed=SEED, opacity=UNIT, paint_loss_opacity=UNIT, wear_coverage=UNIT,
    chip_radius_m=number(.000001, 10), effect_distance_m=number(.000001, 100))
PATCH_VALUES = {key: SURFACE_VALUES[key] for key in
                ("color", "roughness_range", "period_m", "height_m", "color_variation", "opacity", "paint_loss_opacity")}
PATCH_VALUES.update(band_translation=number(-1, 1), coverage_ratio=number(.01, 1))

COMMANDS = [
    command("create_surface", dict(id=IDENTIFIER, name=NAME, parent_id=IDENTIFIER, region_id=IDENTIFIER,
        family=enum("PAINTED_METAL", "BARE_METAL", "WOOD", "WOVEN_FABRIC", "RUBBER", "CERAMIC_GLAZE", "MUD", "STONE", "MOSS"),
        values=object_schema(SURFACE_VALUES)), ("id", "name", "family")),
    command("create_bands", dict(id=IDENTIFIER, name=NAME, parent_id=IDENTIFIER,
        centers=dict(type="array", items=UNIT, minItems=1, maxItems=8), width=number(.001, .25),
        color=array(4, UNIT), metallic=UNIT, roughness=UNIT, direction=array(3, number(-1, 1))),
        ("id", "name", "centers", "width", "color")),
    command("patch_surface", dict(surface_id=IDENTIFIER,
        mode=enum("COLOR_ONLY", "PERIOD_ONLY", "APPEARANCE_ONLY", "OPACITY_ONLY", "PLACEMENT_ONLY", "COVERAGE_ONLY"),
        values=object_schema(PATCH_VALUES)), ("surface_id", "mode", "values")),
    command("add_region_mask", dict(id=IDENTIFIER, layer_id=IDENTIFIER, name=NAME, region_id=IDENTIFIER),
        ("id", "layer_id", "name", "region_id")),
    command("add_layer", dict(id=IDENTIFIER, name=NAME, kind=enum("FILL", "FOLDER"),
                              parent_id=IDENTIFIER), ("id", "name", "kind")),
    command("set_layer", dict(layer_id=IDENTIFIER, values=object_schema(LAYER_FIELDS)),
            ("layer_id", "values")),
    command("set_channel", dict(layer_id=IDENTIFIER, channel=CHANNEL,
                                values=object_schema(CHANNEL_FIELDS)),
            ("layer_id", "channel", "values")),
    command("assign_bundle", dict(id=IDENTIFIER, layer_id=IDENTIFIER,
        sources=object_schema({key: IDENTIFIER for key in CHANNEL["enum"]}),
        projection=enum("UV", "TRIPLANAR"), scale=array(2, number(.001, 512)),
        offset=array(2, number(-100, 100)), rotation=number(-100, 100),
        physical_extent_m=array(2, number(.000001, 10000))), ("id", "layer_id", "sources")),
    command("add_generated_detail", dict(id=IDENTIFIER, name=NAME, image_id=IDENTIFIER,
        region_id=IDENTIFIER, parent_id=IDENTIFIER, purpose=dict(type="string", minLength=1, maxLength=600),
        roughness=UNIT, projection=enum("UV", "TRIPLANAR"), scale=array(2, number(.001, 512)),
        offset=array(2, number(-100, 100)), rotation=number(-100, 100)),
        ("id", "name", "image_id", "region_id", "purpose")),
    command("assign_generated_surface", dict(layer_id=IDENTIFIER, image_id=IDENTIFIER,
        name=NAME, purpose=dict(type="string", minLength=1, maxLength=600),
        roughness_range=array(2, UNIT), height_amplitude=number(0, .15),
        projection=enum("UV", "TRIPLANAR"), scale=array(2, number(.001, 512)),
        offset=array(2, number(-100, 100)), rotation=number(-100, 100),
        physical_extent_m=array(2, number(.000001, 10000))),
        ("layer_id", "image_id", "name", "purpose", "roughness_range")),
    command("assign_texture", dict(layer_id=IDENTIFIER, image_id=IDENTIFIER, channel=CHANNEL,
                                    mask_id=IDENTIFIER, target=enum("CONTENT", "DETAIL"),
                                    physical_extent_m=array(2, number(.000001, 10000))),
            ("layer_id", "image_id")),
    command("assign_surface_maps", dict(layer_id=IDENTIFIER, reference_image_id=IDENTIFIER,
        maps=object_schema({key: IDENTIFIER for key in ("HEIGHT", "ROUGHNESS", "METALLIC")}),
        height_amplitude=number(.0001, 1)), ("layer_id", "reference_image_id", "maps")),
    command('replace_surface_source', dict(layer_id=IDENTIFIER, reference_image_id=IDENTIFIER,
        image_id=IDENTIFIER, correspondence_id=IDENTIFIER, mode=enum('LOCAL', 'GLOBAL'),
        reason=dict(type='string', minLength=12, maxLength=1200),
        maps=object_schema({key: IDENTIFIER for key in ('HEIGHT', 'ROUGHNESS', 'METALLIC')}),
        height_amplitude=number(.0001, 1)),
        ('layer_id', 'reference_image_id', 'image_id', 'correspondence_id', 'mode', 'reason')),
    command("add_mask", dict(id=IDENTIFIER, layer_id=IDENTIFIER, name=NAME,
                             source=enum("GENERATOR", "PROCEDURAL", "SCENE", "BAKED", "NOISE", "ANCHOR", "REFERENCE", "FILTER", "FILL"),
                             values=object_schema(MASK_FIELDS)),
            ("id", "layer_id", "name", "source")),
    command("set_mask", dict(layer_id=IDENTIFIER, mask_id=IDENTIFIER,
                             values=object_schema(MASK_FIELDS)), ("layer_id", "mask_id", "values")),
    command("set_field", dict(layer_id=IDENTIFIER, mask_id=IDENTIFIER,
                              values=object_schema(FIELD_FIELDS), object_id=IDENTIFIER),
            ("layer_id", "mask_id", "values")),
    command("add_filter", dict(id=IDENTIFIER, layer_id=IDENTIFIER, channel=CHANNEL,
                               kind=dict(type="string", minLength=1, maxLength=80),
                               values=object_schema(FILTER_FIELDS)), ("id", "layer_id", "channel", "kind")),
    command("set_filter", dict(layer_id=IDENTIFIER, channel=CHANNEL, effect_id=IDENTIFIER,
                               mask_id=IDENTIFIER, values=object_schema(FILTER_FIELDS),
                               kind=dict(type="string", minLength=1, maxLength=80)), ("layer_id", "values")),
    command("connect_anchor", dict(layer_id=IDENTIFIER, mask_id=IDENTIFIER, channel=CHANNEL,
                                   anchor_id=IDENTIFIER), ("layer_id", "anchor_id")),
    command("insert_asset", dict(id=IDENTIFIER, asset_id=IDENTIFIER,
                                 parent_id=IDENTIFIER, seed=SEED,
                                 map_mode=enum("BAKED", "LIVE")), ("id", "asset_id")),
]
# A reusable geometric frame, with no object-specific or semantic defaults.
PROJECTION_FRAME = object_schema(dict(
    origin=array(3, number(-1e6, 1e6)), axis_u=array(3, number(-1, 1)),
    axis_v=array(3, number(-1, 1)), extent=array(2, number(.000001, 1e6)),
    depth=number(.000001, 1e6)), ('origin', 'axis_u', 'axis_v', 'extent', 'depth'))
COMMANDS.append(command('assign_projected_surface', dict(layer_id=IDENTIFIER, image_id=IDENTIFIER,
    region_id=IDENTIFIER, frame=PROJECTION_FRAME, name=NAME,
    purpose=dict(type='string', minLength=1, maxLength=600), roughness_range=array(2, UNIT),
    height_amplitude=number(0, .15)),
    ('layer_id', 'image_id', 'region_id', 'frame', 'name', 'purpose', 'roughness_range')))
SURFACE_OBSERVATION = object_schema(dict(observation_id=IDENTIFIER, base_color=IDENTIFIER,
    roughness=IDENTIFIER, height=IDENTIFIER, metallic=IDENTIFIER), ('observation_id', 'base_color'))
COMMANDS.append(command('generate_surface_field', dict(layer_id=IDENTIFIER, name=NAME,
    purpose=dict(type='string', minLength=1, maxLength=600), region_id=IDENTIFIER,
    observations=dict(type='array', items=SURFACE_OBSERVATION, minItems=1, maxItems=8),
    roughness_range=array(2, UNIT), height_amplitude=number(0, .15)),
    ('layer_id', 'name', 'purpose', 'observations')))
SCHEMA = object_schema(dict(version=dict(type="integer", const=VERSION),
                            summary=dict(type="string", minLength=1, maxLength=500),
                            commands=dict(type="array", items=dict(anyOf=COMMANDS),
                                          minItems=1, maxItems=MAX_COMMANDS)),
                       ("version", "summary", "commands"))


class AuthoringError(ValueError):
    """An actionable rejected proposal; no authoring side effects."""


def check(value, spec, path="proposal"):
    if "anyOf" in spec:
        options = [s for s in spec["anyOf"]
                   if isinstance(value, dict) and value.get("op") in s["properties"]["op"]["enum"]]
        if not options:
            raise AuthoringError(f"{path}: unknown operation")
        return check(value, options[0], path)
    kind = spec.get("type")
    valid = {"object": lambda: type(value) is dict, "array": lambda: type(value) is list,
             "string": lambda: type(value) is str, "boolean": lambda: type(value) is bool,
             "number": lambda: type(value) in (int, float) and math.isfinite(value),
             "integer": lambda: type(value) is int}.get(kind, lambda: True)
    if not valid():
        raise AuthoringError(f"{path}: expected {kind}")
    if "enum" in spec and value not in spec["enum"]:
        raise AuthoringError(f"{path}: choose one of {', '.join(spec['enum'])}")
    if "const" in spec and value != spec["const"]:
        raise AuthoringError(f"{path}: unsupported version")
    if kind in ("integer", "number"):
        if value < spec.get("minimum", -math.inf) or value > spec.get("maximum", math.inf):
            raise AuthoringError(f"{path}: value outside supported range")
    elif kind == "string":
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", MAX_BYTES):
            raise AuthoringError(f"{path}: invalid text length")
        if "\x00" in value:
            raise AuthoringError(f"{path}: NUL is not supported")
    elif kind == "array":
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", MAX_ITEMS):
            raise AuthoringError(f"{path}: too many or too few items")
        for i, item in enumerate(value):
            check(item, spec["items"], f"{path}[{i}]")
    elif kind == "object":
        unknown = set(value) - spec.get("properties", {}).keys()
        missing = set(spec.get("required", ())) - value.keys()
        if unknown or missing:
            raise AuthoringError(f"{path}: unknown fields {sorted(unknown)}; missing fields {sorted(missing)}")
        for key, item in value.items():
            check(item, spec["properties"][key], f"{path}.{key}")


def validate(proposal):
    try:
        if isinstance(proposal, str):
            if len(proposal.encode()) > MAX_BYTES:
                raise AuthoringError("Proposal exceeds 256 KiB")
            proposal = json.loads(proposal)
        if len(json.dumps(proposal, allow_nan=False).encode()) > MAX_BYTES:
            raise AuthoringError("Proposal exceeds 256 KiB")
        check(proposal, SCHEMA)
    except (TypeError, ValueError, RecursionError) as exc:
        raise AuthoringError(str(exc)) from exc
    aliases = set()
    for item in proposal["commands"]:
        if "id" in item:
            identifier = item["id"]
            if not re.fullmatch(r"new:[A-Za-z0-9_-]{1,60}", identifier) or identifier in aliases:
                raise AuthoringError("New item IDs must be unique new:name aliases")
            aliases.add(identifier)
    return proposal
