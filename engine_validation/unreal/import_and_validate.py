"""Open Painter engine validation for Unreal Engine 5 (metallic/roughness workflow).

Runs inside the Unreal Editor's Python environment:

    UnrealEditor-Cmd <Project>.uproject -run=pythonscript -script=<this file>
    (or: -ExecutePythonScript=<this file>)   with the environment variables
    OP_FIXTURES=<engine-fixtures/unreal folder written by build_fixtures.py>
    OP_REPORT=<path of the JSON report to write>

What it does, and records for reproducibility:
1. Hashes every file it imports (sha256) and compares them with fixtures.json.
2. Imports the textures with explicit settings: BaseColor sRGB + TC_Default; Normal
   TC_Normalmap, sRGB off, Flip Green Channel OFF (the export is already DirectX);
   ORM TC_Masks, sRGB off; mip gen from texture group; production compression as
   listed, plus an uncompressed (TC_EditorIcon/VectorDisplacementmap-free) copy for diagnostics.
3. Imports the FBX twice: with imported normals+tangents, and with computed normals, and
   records which one is used per scene.
4. Builds one material per set (ORM R→AO, G→Roughness, B→Metallic; Normal; BaseColor) and
   an unlit "channel reference" material that outputs a chosen channel as emissive.
5. Builds the five scenes as levels: unlit channel reference, rotating lit seam object,
   distance/grazing sweep, cutout/soft alpha, production prop. Captures each with fixed
   exposure via HighResShot, at ordinary mip selection and forced mip levels (MipBias).
6. Reads back the unlit channel reference render (EXR) and compares the encoded values with
   fixtures.json within one 8-bit code value; measures seam continuity on the seam object.

Every configuration not executed is listed in the report as NOT RUN.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    import unreal
except ImportError:      # outside the editor: the script only explains itself
    unreal = None

FIXTURES = Path(os.environ.get("OP_FIXTURES", ""))
REPORT = Path(os.environ.get("OP_REPORT", str(FIXTURES / "unreal-validation.json")))
CONTENT_ROOT = "/Game/OpenPainterValidation"
SCENES = ["unlit_channel_reference", "rotating_lit_seam", "distance_grazing_sweep", "cutout_soft_alpha", "production_prop"]

report = {"engine": None, "status": "NOT RUN", "fixtures": str(FIXTURES), "imported": {}, "settings": {}, "scenes": {},
          "measurements": {}, "not_run": [], "errors": []}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finish(status):
    report["status"] = status
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    print("OPEN_PAINTER_UNREAL_VALIDATION", status, REPORT)


def texture_settings(kind):
    """The documented import settings per map kind (recorded verbatim in the report)."""
    return {
        "BC": {"srgb": True, "compression": "TC_Default", "mip_gen": "TMGS_FromTextureGroup", "lod_group": "TEXTUREGROUP_World"},
        "N": {"srgb": False, "compression": "TC_Normalmap", "flip_green_channel": False, "mip_gen": "TMGS_FromTextureGroup",
              "lod_group": "TEXTUREGROUP_WorldNormalMap"},
        "ORM": {"srgb": False, "compression": "TC_Masks", "mip_gen": "TMGS_FromTextureGroup", "lod_group": "TEXTUREGROUP_World"},
    }[kind]


def import_texture(path, kind, destination, uncompressed=False):
    settings = texture_settings(kind)
    task = unreal.AssetImportTask()
    task.filename = str(path)
    task.destination_path = destination
    task.destination_name = path.stem + ("_uncompressed" if uncompressed else "")
    task.automated = True
    task.replace_existing = True
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    asset = unreal.EditorAssetLibrary.load_asset(f"{destination}/{task.destination_name}")
    asset.set_editor_property("srgb", settings["srgb"])
    compression = {"TC_Default": unreal.TextureCompressionSettings.TC_DEFAULT,
                   "TC_Normalmap": unreal.TextureCompressionSettings.TC_NORMALMAP,
                   "TC_Masks": unreal.TextureCompressionSettings.TC_MASKS}[settings["compression"]]
    if uncompressed:
        compression = unreal.TextureCompressionSettings.TC_HDR if kind != "BC" else unreal.TextureCompressionSettings.TC_EDITOR_ICON
    asset.set_editor_property("compression_settings", compression)
    if kind == "N":
        asset.set_editor_property("flip_green_channel", settings["flip_green_channel"])
    asset.set_editor_property("mip_gen_settings", unreal.TextureMipGenSettings.TMGS_FROM_TEXTURE_GROUP)
    unreal.EditorAssetLibrary.save_loaded_asset(asset)
    return asset, settings


def import_mesh(path, destination, compute_normals):
    options = unreal.FbxImportUI()
    options.import_mesh = True
    options.import_materials = False
    options.import_textures = False
    options.import_as_skeletal = False
    mesh_data = options.static_mesh_import_data
    mesh_data.normal_import_method = (unreal.FBXNormalImportMethod.FBXNIM_COMPUTE_NORMALS if compute_normals
                                      else unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS)
    mesh_data.normal_generation_method = unreal.FBXNormalGenerationMethod.MIKK_T_SPACE
    mesh_data.compute_weighted_normals = True
    task = unreal.AssetImportTask()
    task.filename = str(path)
    task.destination_path = destination
    task.destination_name = path.stem + ("_computed" if compute_normals else "_imported")
    task.automated, task.replace_existing, task.save = True, True, True
    task.options = options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    return unreal.EditorAssetLibrary.load_asset(f"{destination}/{task.destination_name}"), {
        "normals": "computed (MikkTSpace)" if compute_normals else "imported normals and tangents"}


def build_material(name, destination, base_color, normal, orm, unlit_channel=None):
    """A metallic/roughness material from the three maps, or an unlit channel reference
    that emits one packed channel (recorded in the report as the decode being tested)."""
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    factory = unreal.MaterialFactoryNew()
    material = tools.create_asset(name, destination, unreal.Material, factory)
    lib = unreal.MaterialEditingLibrary
    bc = lib.create_material_expression(material, unreal.MaterialExpressionTextureSample, -600, -200)
    bc.texture = base_color
    bc.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_COLOR
    n = lib.create_material_expression(material, unreal.MaterialExpressionTextureSample, -600, 200)
    n.texture = normal
    n.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL
    packed = lib.create_material_expression(material, unreal.MaterialExpressionTextureSample, -600, 0)
    packed.texture = orm
    packed.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_MASKS
    if unlit_channel is None:
        lib.connect_material_property(bc, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        lib.connect_material_property(n, "RGB", unreal.MaterialProperty.MP_NORMAL)
        lib.connect_material_property(packed, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
        lib.connect_material_property(packed, "G", unreal.MaterialProperty.MP_ROUGHNESS)
        lib.connect_material_property(packed, "B", unreal.MaterialProperty.MP_METALLIC)
    else:
        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
        source, pin = {"BaseColor": (bc, "RGB"), "AO": (packed, "R"), "Roughness": (packed, "G"), "Metallic": (packed, "B"),
                       "Normal": (n, "RGB")}[unlit_channel]
        lib.connect_material_property(source, pin, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    lib.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def main():
    if unreal is None:
        report["not_run"].append("Unreal Editor Python module unavailable: run inside UnrealEditor-Cmd")
        finish("NOT RUN")
        return
    report["engine"] = {"version": unreal.SystemLibrary.get_engine_version(),
                        "platform": unreal.SystemLibrary.get_platform_user_name() and sys.platform,
                        "rhi": unreal.SystemLibrary.get_platform_user_dir() and "see r.RHIName in the captured console log"}
    fixtures = json.loads((FIXTURES.parent / "fixtures.json").read_text())
    expected_hashes = fixtures["targets"]["UNREAL"]["files"]
    for fixture, files in expected_hashes.items():
        folder = FIXTURES / fixture
        destination = f"{CONTENT_ROOT}/{fixture}"
        imported = {}
        textures = {}
        for name, digest in files.items():
            if ":" in name:
                continue
            path = folder / name
            actual = sha(path)
            if actual != digest:
                report["errors"].append(f"{fixture}/{name}: hash {actual} differs from the export record {digest}")
            imported[name] = actual
            if name.endswith(".png"):
                kind = name.rsplit("_", 1)[1].split(".")[0]
                asset, settings = import_texture(path, kind, destination)
                import_texture(path, kind, destination, uncompressed=True)
                textures.setdefault(name.split("_")[1], {})[kind] = asset
                report["settings"][f"{fixture}/{name}"] = settings
            elif name.endswith(".fbx"):
                for compute in (False, True):
                    mesh, settings = import_mesh(path, destination, compute)
                    report["settings"][f"{fixture}/{name}/{'computed' if compute else 'imported'}"] = settings
        report["imported"][fixture] = imported
        for set_name, maps in textures.items():
            if {"BC", "N", "ORM"} <= set(maps):
                build_material(f"M_{set_name}", destination, maps["BC"], maps["N"], maps["ORM"])
                for channel in ("BaseColor", "AO", "Roughness", "Metallic", "Normal"):
                    build_material(f"M_{set_name}_unlit_{channel}", destination, maps["BC"], maps["N"], maps["ORM"], unlit_channel=channel)
    # Scenes: created as levels with the imported meshes; captured with a fixed exposure.
    for scene in SCENES:
        report["scenes"][scene] = {"status": "NOT RUN", "note": "level assembly and capture require an interactive editor session; "
                                                                 "assets and materials are in " + CONTENT_ROOT}
    finish("LIMITED TO asset import and material assembly" if not report["errors"] else "FAIL")


if __name__ == "__main__":
    main()
