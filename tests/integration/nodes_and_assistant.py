"""Nodes and the assistant on the same editing model (UI plan phase 7).

Sockets say what they carry (material bundle, colour, scalar, normal, coverage); a saved
tree with the old socket types reconciles once; opening the graph frames the selected
item and keeps node positions; Frame Selected / Full Graph are explicit. The assistant
panel reads Target → Instruction → Run → Changes, and the sharing summary the panel shows
is generated from the same payload composition a real run sends: the toggles and the
serialized request agree.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from ui_layout_recorder import Recorder
from open_painter.ai import session
from open_painter.authoring import document
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.graph import mirror, operators as graph_operators, tree as graph_tree
from open_painter.ui import editor, material_director, selection

open_painter.register()


def run(context):
    scene = context.scene
    project = scene.open_painter
    ids = ui_fixture.build(scene)
    editor.mount(context.window, context.area)
    body = next(ts for ts in project.texture_sets if ts.id == ids["body"])

    # --- sockets carry what they say ---
    tree = mirror.ensure_tree(body)
    coat = next(item for item in body.layers if item.id == ids["material_layers"]["Blue paint coat"])
    node = next(item for item in tree.nodes if getattr(item, "op_id", "") == coat.id)
    kinds = {socket.identifier: socket.bl_idname for socket in node.inputs}
    assert kinds["below"] == graph_tree.SOCKET_CONTENT and kinds["mask"] == graph_tree.SOCKET_COVERAGE
    assert kinds["content#BASE_COLOR"] == graph_tree.SOCKET_COLOR, kinds
    assert kinds["content#ROUGHNESS"] == graph_tree.SOCKET_SCALAR
    assert node.outputs["Output (unpublished)"].bl_idname == graph_tree.SOCKET_CONTENT
    output = next(item for item in tree.nodes if getattr(item, "op_id", "") == graph_tree.OUTPUT_NODE)
    kinds = {socket.identifier: socket.bl_idname for socket in output.inputs}
    roots = [item for item in body.layers if not item.parent_id]
    assert roots and all(f"stack#{item.id}" in kinds for item in roots), kinds
    assert kinds[f"stack#{roots[0].id}"] == graph_tree.SOCKET_CONTENT
    # Compact view: channels ride the bundle in the background, not as sockets.
    assert not any(name.startswith("final#") for name in kinds)
    from open_painter.constants import CHANNELS
    assert mirror.project(body)[0][graph_tree.OUTPUT_NODE]["maps"] == list(CHANNELS)
    assert graph_tree.OP_SocketContent.bl_label == "Material" and "bundle" in node.inputs["Backdrop"].description
    assert node.inputs["BaseColor"].description == "One colour channel (RGB)"
    # A tree saved with the previous socket types reconciles once (the format is in the signature).
    old = node.inputs["content#BASE_COLOR"]
    node.inputs.remove(old)
    node.inputs.new(graph_tree.SOCKET_CONTENT, "BaseColor", identifier="content#BASE_COLOR")
    current = mirror.signature(body)
    with patch.object(mirror, "GRAPH_FORMAT", mirror.GRAPH_FORMAT - 1):
        assert mirror.signature(body) != current, "the projection format is part of the signature"
    mirror.reconcile(tree, body)
    node = next(item for item in tree.nodes if getattr(item, "op_id", "") == coat.id)
    assert node.inputs["content#BASE_COLOR"].bl_idname == graph_tree.SOCKET_COLOR, "reconciled to the colour socket"
    assert tree["op_signature"] == current
    print("NODES sockets: PASS", flush=True)

    # --- open at the selection; positions kept; explicit framing ---
    positions = {getattr(item, "op_id", ""): tuple(item.location) for item in tree.nodes}
    assert bpy.ops.open_painter.native_select(layer_id=coat.id, mask=True) == {"FINISHED"}
    expected = mirror.mask_node_id(coat, 0, coat.masks[0])
    assert graph_operators.target_node_id(context, body) == expected
    assert bpy.ops.open_painter.open_graph() == {"FINISHED"}
    area = graph_operators.graph_area(context.screen, body)
    assert area is not None and area.spaces.active.node_tree == tree
    active = tree.nodes.active
    assert active is not None and active.op_id == expected and active.select, "framed on the selected mask"
    assert {getattr(item, "op_id", ""): tuple(item.location) for item in tree.nodes} == positions, "positions survive"
    digest = document.digest(body)
    with context.temp_override(area=area, space_data=area.spaces.active):
        assert bpy.ops.open_painter.graph_view(scope="ALL") == {"FINISHED"}
        assert bpy.ops.open_painter.native_select(layer_id=ids["stripes"]) == {"FINISHED"}
        assert bpy.ops.open_painter.graph_view(scope="SELECTED") == {"FINISHED"}
    assert tree.nodes.active.op_id == ids["stripes"]
    assert document.digest(body) == digest, "framing and selection never edit the material"
    header = Recorder()
    graph_operators.header(SimpleNamespace(layout=header), SimpleNamespace(space_data=area.spaces.active, scene=scene,
                                                                          area=area, screen=context.screen))
    text = header.text
    assert "Body › Details › Stripes" in text and "Frame Selected" in text and "Full Graph" in text and "Auto Layout" in text
    assert graph_operators.OP_OT_open_graph.bl_label == "Edit Nodes"
    print("NODES open at selection: PASS", flush=True)

    # --- the assistant: Target → Instruction → Run → Changes ---
    settings = context.window_manager.open_painter_director
    settings.prompt = "Make the paint a deeper blue"
    settings.scope = "SET"
    layout = Recorder()
    material_director.OP_PT_material_director.draw(SimpleNamespace(layout=layout), context)
    text = layout.text
    assert text.index("Target") < text.index("Instruction") < text.index("open_painter.director_connection") < text.index("Changes")
    assert "Body" in text and "prompt" in text and "mode" in text and "What is sent" in text
    assert "Protected:" in text and "Codex Executable" not in text, "connection settings live in the preferences"
    assert material_director.OP_PT_material_director.bl_label == "Material Assistant"
    assert "OP_PT_director_settings" not in dir(bpy.types)
    print("ASSISTANT layout: PASS", flush=True)

    # --- the sharing summary comes from the payload; toggles and payload agree ---
    sent = []

    class Connection:
        def send(self, payload):
            sent.append(payload)

    from open_painter.ai import visual_context
    from open_painter.authoring.schema import AuthoringError
    with patch.object(session, "connection", Connection()), patch.object(session, "ensure_online"), \
            patch("open_painter.ai.viewport.capture", lambda *a, **k: "data:image/png;base64,AAAA"):
        session.state.update(connected=True, signed_in=True, models=[])
        # Authoring needs the view, the mesh maps and current bakes: without them nothing is sent.
        for send_view, send_maps in ((False, True), (True, False), (False, False), (True, True)):
            settings.send_view, settings.send_maps = send_view, send_maps
            count = len(sent)
            try:
                session.run(context)
                raise AssertionError(f"authored without its geometry evidence {(send_view, send_maps)}")
            except AuthoringError as error:
                expected = "Create current geometry maps" if (send_view and send_maps) else "Enable Send Mesh Maps"
                assert expected in str(error), error
            assert len(sent) == count, "a refused run sent a request"
        print("ASSISTANT geometry evidence required: PASS", flush=True)
    with patch.object(session, "connection", Connection()), patch.object(session, "ensure_online"), \
            patch("open_painter.ai.viewport.capture", lambda *a, **k: "data:image/png;base64,AAAA"), \
            patch.object(visual_context, "require_geometry_maps"):
        # The payload builder: for any toggles, what is sent is what the summary says. Candidate
        # review needs the view, so texture generation and review are off for the view-less rows.
        settings.generate_textures, settings.correction_enabled = False, False
        session.state.update(connected=True, signed_in=True, models=[])
        for send_view, send_maps in ((True, True), (False, True), (True, False), (False, False)):
            settings.send_view, settings.send_maps = send_view, send_maps
            dry = session.sharing_summary(session.dry_payload(context))
            session.run(context)
            payload = sent[-1]
            actual = session.sharing_summary(payload)
            assert actual == dry, (send_view, send_maps, dry, actual)
            assert session.state["sharing"] == actual
            image_ids = [item["id"] for item in payload["images"]]
            assert ("viewport" in image_ids) == send_view, image_ids
            assert any(i.startswith("map:") for i in image_ids) == send_maps, image_ids
            body_json = json.loads(payload["input"])
            visual = body_json["current_context"]["visual_context"]
            assert (visual["view_sent"], visual["maps_sent"], visual["reference_sent"]) == (send_view, send_maps, False)
            assert bool(visual["missing_maps"]) == send_maps, "unbaked maps are only reported when maps are sent"
            maps_line = next(line for line in actual if line.startswith("Mesh maps:"))
            assert ("none" in maps_line) != send_maps, maps_line
            session.stop()
        # The panel's own list agrees with the payload for the same toggles.
        settings.send_view, settings.send_maps = True, False
        lines = material_director.sharing_lines(context, settings)
        assert any(line.startswith("Model views: viewport") for line in lines) and "Mesh maps: none" in lines, lines
        session.run(context)
        summary = session.sharing_summary(sent[-1])
        assert "Mesh maps: none" in summary and any(line.startswith("Model views: viewport") for line in summary)
        session.stop()
    print("NODES_AND_ASSISTANT_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
