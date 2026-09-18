"""The consolidated asset workflow (UI plan phase 3).

The shelf library carries one asset catalog per category so the shelf shows category
tabs next to its search; a shelf click selects (the Assets panel names the asset, its
type, its missing mesh maps and the exact target) and a second click, Apply or the
context menu applies; a material becomes a named editable group, a smart mask goes to
the selected layer, Replace is its own action, filters follow the content/mask
selection, and one application is one undo step.
"""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.ui import library_categories as categories, native, selection, shelf_library
from ui_layout_recorder import Recorder

open_painter.register()


def run(context):
    scene = context.scene
    project = scene.open_painter
    ts = sets.create(scene, "Body", 512)
    set_id = ts.id
    sets.assign(scene, ts, scene.objects["Cube"])
    base = stack.add(ts, "FILL", "Base")
    base_id = base.id

    assert shelf_library.current(), "the registered library carries the catalogs"

    # --- selection before application: the first activation only selects ---
    ui = selection.ui_state(context)
    assert not ui.pending_asset
    library_names = shelf_library.manifest()["names"]
    name = next(n for n, k in library_names.items() if k == "Worn Painted Metal")
    identifier = shelf_library.LIBRARY_FILE + "/Image/" + name
    before = [layer.name for layer in ts.layers]
    # invoke() never runs in background Blender: the click logic is exercised directly.
    entry, _source, key = native.resolve_entry(context, library_type="CUSTOM",
                                               library_identifier=shelf_library.LIBRARY_NAME,
                                               relative_identifier=identifier)
    assert key == "Worn Painted Metal"
    assert not native.select_click(context, entry, key), "the first click selects"
    assert ui.pending_asset == "Worn Painted Metal" and [layer.name for layer in ts.layers] == before
    assert "Selected Worn Painted Metal" in project.status, project.status
    entry = native.entry_for_key(ui.pending_asset)
    target, possible = native.application_target(context, entry)
    assert target == "Adds an editable group to Body" and possible, target
    assert native.missing_requirements(context, entry) == ["AO", "CURVATURE"]
    panel = Recorder()
    native.OP_PT_native_library.draw(SimpleNamespace(layout=panel), context)
    text = panel.text
    assert "Worn Painted Metal" in text and "Smart Material" in text and "Needs baked Ao, Curvature" in text
    assert "Adds an editable group to Body" in text and "open_painter.native_asset" in text
    apply_button = next(op for op in panel.operators("open_painter.native_asset") if op["text"] == "Apply")
    assert apply_button["props"] == {"entry_key": "Worn Painted Metal", "apply_now": True}
    print("ASSETS select before apply: PASS", flush=True)

    # --- the second activation applies: one named, editable group; one undo step ---
    import ui_fixture
    ui_fixture.bake_synthetic(ts)
    assert native.missing_requirements(context, entry) == []
    assert native.select_click(context, entry, key), "the second click applies"
    applied = bpy.ops.open_painter.native_asset(asset_library_type="CUSTOM",
                                                 asset_library_identifier=shelf_library.LIBRARY_NAME,
                                                 relative_asset_identifier=identifier)
    assert applied == {"FINISHED"}, applied
    folder = stack.active(ts)
    assert folder.type == "FOLDER" and folder.name == "Worn Painted Metal" and folder.module_id
    assert selection.resolve(context).kind == "MATERIAL"
    assert len(ts.layers) == len(before) + 6
    # The application is one operator call with the UNDO option: one undo step by construction
    # (native_ui.py undoes a real shelf click in a window; background Blender keeps no undo stack).
    print("ASSETS material adds one group: PASS", flush=True)

    # --- a saved whole-set document becomes a named group too ---
    from open_painter.smart_materials import serializer
    with TemporaryDirectory(prefix="op-doc-") as tmp:
        document = Path(tmp) / "Saved Look.openpaintmaterial"
        serializer.save(ts, str(document))
        from open_painter.ui.assets_panel import apply_document
        group = apply_document(context, ts, document)
        assert group.type == "FOLDER" and group.name == "Saved Look"
        assert {layer.parent_id for layer in ts.layers if layer.name == "Base"} >= {group.id}
    print("ASSETS saved material is one group: PASS", flush=True)

    # --- masks target the selected layer; Replace is a separate action ---
    ts = next(item for item in bpy.context.scene.open_painter.texture_sets if item.id == set_id)
    bpy.ops.open_painter.native_select(layer_id=base_id)
    mask_name = next(n for n, k in library_names.items() if k == "Edge Broken")
    mask_identifier = shelf_library.LIBRARY_FILE + "/Image/" + mask_name
    entry = native.entry_for_key("Edge Broken")
    assert native.application_target(context, entry)[0] == "Adds to the mask stack of Base"
    for _ in range(2):
        bpy.ops.open_painter.native_asset(asset_library_type="CUSTOM", asset_library_identifier=shelf_library.LIBRARY_NAME,
                                          relative_asset_identifier=mask_identifier)
    base = next(item for item in ts.layers if item.id == base_id)
    assert [m.name for m in base.masks] == ["Edge Broken", "Edge Broken"], "plain application never replaces"
    assert selection.resolve(context).kind == "MASK"
    result = bpy.ops.open_painter.native_asset(entry_key="Edge Broken", apply_now=True, replace=True)
    assert result == {"FINISHED"}
    base = next(item for item in ts.layers if item.id == base_id)
    assert [m.name for m in base.masks] == ["Edge Broken"], "Replace is explicit"
    # Older scripts' replace_masks still means the same thing.
    bpy.ops.open_painter.native_asset(entry_key="Edge Broken", apply_now=True, replace_masks=True)
    base = next(item for item in ts.layers if item.id == base_id)
    assert len(base.masks) == 1
    print("ASSETS masks and explicit replace: PASS", flush=True)

    # --- filters follow the content / mask selection; a material cannot become a mask ---
    bpy.ops.open_painter.native_select(layer_id=base_id)
    project.properties_channel = "ROUGHNESS"
    entry = native.entry_for_key("FILTER:LEVELS")
    assert native.application_target(context, entry)[0] == "Applies to Base › Roughness"
    bpy.ops.open_painter.native_asset(entry_key="FILTER:LEVELS", apply_now=True)
    base = next(item for item in ts.layers if item.id == base_id)
    assert stack.channel_for(base, "ROUGHNESS").effects[0].kind == "LEVELS"
    bpy.ops.open_painter.native_select(layer_id=base_id, mask=True)
    assert native.application_target(context, entry)[0] == "Applies to the mask stack of Base"
    bpy.ops.open_painter.native_asset(entry_key="FILTER:INVERT", apply_now=True)
    base = next(item for item in ts.layers if item.id == base_id)
    assert base.masks[-1].source == "FILTER" and base.masks[-1].effect.kind == "INVERT"
    masks_before = [m.source for m in base.masks]
    bpy.ops.open_painter.native_asset(entry_key="Worn Painted Metal", apply_now=True)
    base = next(item for item in ts.layers if item.id == base_id)
    assert [m.source for m in base.masks] == masks_before, "a material never lands in a mask stack"
    assert stack.active(ts).name == "Worn Painted Metal"
    print("ASSETS filters follow the selection: PASS", flush=True)

    # --- geometry presets are shelf entries that need an object ---
    entry = native.entry_for_key("GEOMETRY:BOLT")
    assert entry is not None and entry[1] == "GEOMETRY"
    assert native.application_target(context, entry)[0] == "Adds pieces to Cube"
    with context.temp_override(active_object=None):
        assert not native.application_target(context, entry)[1]
    # The context menu offers Apply (and Replace for masks) with the resolved key.
    menu = Recorder()
    asset = SimpleNamespace(local_id=None, name=mask_name, full_library_path=str(shelf_library.library_path()),
                            metadata=SimpleNamespace(tags=[SimpleNamespace(name=shelf_library.KEY_TAG + "Edge Broken"),
                                                           SimpleNamespace(name=shelf_library.CATEGORY_TAG + "MASKS")]))
    native.draw_asset_context_menu(context, asset, menu)
    labels = [op["text"] for op in menu.operators("open_painter.native_asset")]
    assert labels == ["Apply", "Replace Masks with This"], labels
    assert all(op["props"]["entry_key"] == "Edge Broken" for op in menu.operators("open_painter.native_asset"))
    # Browsing (selecting) never changed the material.
    serial = ts.edit_serial
    ui.pending_asset = ""
    assert not native.select_click(context, native.entry_for_key("Edge Broken"), "Edge Broken")
    assert ts.edit_serial == serial and ui.pending_asset == "Edge Broken"
    # --- catalogs: one per category, written with the library, on every library asset ---
    with TemporaryDirectory(prefix="op-shelf-") as tmp:
        path = shelf_library.build(tmp)
        cats = (Path(tmp) / shelf_library.CATALOGS_FILE).read_text()
        for identifier, label in categories.CATALOG_LABELS.items():
            assert f"{categories.CATALOG_IDS[identifier]}:{label}:{label}" in cats, label
        with bpy.data.libraries.load(str(path), link=True) as (available, loaded):
            names = list(available.images)
            loaded.images = names
        assigned = {image.asset_data.catalog_id for image in loaded.images if image.asset_data}
        assert assigned == set(categories.CATALOG_IDS.values()), assigned - set(categories.CATALOG_IDS.values())
        by_key = {shelf_library.key_of(image.asset_data and SimpleNamespace(metadata=image.asset_data)): image
                  for image in loaded.images}
        assert by_key["GEOMETRY:BOLT"].asset_data.catalog_id == categories.CATALOG_IDS["GEOMETRY_EFFECTS"]
        assert by_key["Worn Painted Metal"].asset_data.catalog_id == categories.CATALOG_IDS["SMART_MATERIALS"]
        for image in loaded.images:
            bpy.data.images.remove(image)
    print("ASSETS catalogs: PASS", flush=True)

    print("ASSET_WORKFLOW_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
