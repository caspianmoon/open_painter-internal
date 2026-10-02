"""Real Blender authoring, shader, persistence and orchestration acceptance tests."""

import json
import base64
import struct
import tempfile
import zlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.authoring import assets, commands, context as authoring, document, schema, transactions
from open_painter.ai import session, viewport, visual_context
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.evaluation.metrics import Metrics


def batch(*items, summary="Test material change"):
    return dict(version=1, summary=summary, commands=list(items))


class DirectorTests(unittest.TestCase):
    def setUp(self):
        session.request = None
        session.clear_conversation()
        # This suite exercises authoring/transactions without a graphics context.
        # Actual enabled candidate review is covered by director_candidates.py.
        bpy.context.window_manager.open_painter_director.correction_enabled = False
        self.scene = bpy.data.scenes.new("Director test")
        bpy.context.window.scene = self.scene
        bpy.ops.mesh.primitive_cube_add()
        self.obj = bpy.context.object
        self.ts = sets.create(self.scene, "Director", 512)
        sets.assign(self.scene, self.ts, self.obj)
        self.serial = 0

    def tearDown(self):
        session.request = None
        session.progress.reset()
        session._pending.clear()
        session._announced.clear()
        shader_preview.clear()
        # Every test builds a scene with a set of its own: release it and what it leaves
        # orphaned, or the suite's memory grows with its number of tests (each set's images
        # hold their pixels and, published into, a GPU texture).
        try:
            scene = self.scene
            others = [other for other in bpy.data.scenes if other != scene]
            if others and scene.name in bpy.data.scenes:
                bpy.context.window.scene = others[0]
                for obj in list(scene.objects):
                    bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.scenes.remove(scene)
                bpy.data.orphans_purge(do_recursive=True)
        except ReferenceError:
            pass

    def apply(self, proposal, **kwargs):
        self.serial += 1
        return transactions.apply(self.ts, proposal, operation_id=f"test-{self.serial}",
                                  expected_digest=document.digest(self.ts), **kwargs)

    def steel(self):
        r = self.apply(batch(dict(op="add_layer", id="new:steel", name="Steel", kind="FILL"),
                             dict(op="set_channel", layer_id="new:steel", channel="METALLIC", values=dict(value=1))))
        return r["aliases"]["new:steel"]

    def layer(self, identifier):
        return next(l for l in self.ts.layers if l.id == identifier)

    def test_context_is_bounded_and_has_no_pixels_or_paths(self):
        self.steel()
        data = authoring.capture(self.ts, authoring.bindings(bpy.context, self.ts))
        raw = json.dumps(data)
        self.assertLess(len(raw), schema.MAX_BYTES)
        self.assertNotIn("$datablock", raw)
        self.assertEqual(data["objects"][0]["vertices"], 8)
        self.assertTrue(data["objects"][0]["uv_maps"])
        self.assertTrue(assets.search("steel"))

    def test_map_previews_preserve_authored_pixels_and_use_correct_labels(self):
        import numpy as np
        from open_painter.ai import visual_context
        from open_painter.baking.bake_manager import register_map
        from open_painter.core import image_manager
        image = image_manager.create("Fresh AO test", 128)
        pixels = np.ones((128, 128, 4), np.float32)
        pixels[:64, :, :3] = .2
        image_manager.write(image, pixels)
        register_map(self.ts, "AO", image)
        before = document.digest(self.ts)
        resources = transactions.resources()
        attachments, missing = visual_context.mesh_maps(self.ts)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["id"], "map:AO")
        self.assertEqual(attachments[0]["provenance_status"], "Ready")
        self.assertIn("CURVATURE", missing)
        self.assertNotIn("url", visual_context.manifest(attachments)[0])
        self.assertEqual(transactions.resources(), resources)
        self.assertEqual(document.digest(self.ts), before)
        np.testing.assert_allclose(image_manager.pixels(image), pixels)
        self.assertLess(len(attachments[0]["url"]), visual_context.MAX_ATTACHMENT_BYTES)
        # PNG rows flip Blender's bottom-up image coordinates without color-map reinterpretation.
        raw = base64.b64decode(attachments[0]["url"].split(",", 1)[1])
        compressed = b""; offset = 8
        while offset < len(raw):
            n = struct.unpack(">I", raw[offset:offset+4])[0]
            if raw[offset+4:offset+8] == b"IDAT": compressed += raw[offset+8:offset+8+n]
            offset += n + 12
        rows = np.frombuffer(zlib.decompress(compressed), np.uint8).reshape(128, 128 * 4 + 1)
        self.assertEqual(int(rows[0, 1]), 255)
        self.assertEqual(int(rows[-1, 1]), 51)

    def test_generated_texture_draft_commit_rollback_and_undo(self):
        from open_painter.ai import generated_images
        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress((b"\0" + b"\x30\x60\x20" * 2) * 2)) + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory(prefix="painter-image-test-") as directory:
            meta = generated_images.store(directory, dict(result=base64.b64encode(png).decode(), revisedPrompt="Test enamel"))
            images = {meta["image_id"]: dict(meta, state_directory=directory)}
            p = batch(dict(op="add_layer", id="new:paint", name="Generated enamel", kind="FILL"),
                      dict(op="assign_texture", layer_id="new:paint", image_id=meta["image_id"], channel="BASE_COLOR"),
                      dict(op="add_mask", layer_id="new:paint", id="new:wear", name="Wear", source="FILL"),
                      dict(op="assign_texture", layer_id="new:paint", mask_id="new:wear", image_id=meta["image_id"]))
            before = document.canonical(document.snapshot(self.ts))
            resources = transactions.resources()
            with self.assertRaises(schema.AuthoringError):
                commands.compile_proposal(self.ts, p, "unknown-image")
            commands.compile_proposal(self.ts, p, "draft", images=images)
            self.assertEqual(transactions.resources(), resources)
            self.assertEqual(document.canonical(document.snapshot(self.ts)), before)
            def fail(stage):
                if stage == "applied": raise RuntimeError("rollback image import")
            with self.assertRaises(RuntimeError): self.apply(p, images=images, fault=fail)
            self.assertEqual(transactions.resources(), resources)
            self.assertEqual(document.canonical(document.snapshot(self.ts)), before)
            receipt = self.apply(p, images=images)
            layer = self.layer(receipt["aliases"]["new:paint"])
            color = stack.channel_for(layer, "BASE_COLOR").image
            mask = layer.masks[0].image
            self.assertNotEqual(color, mask)
            self.assertEqual(color.colorspace_settings.name, "sRGB")
            self.assertEqual(mask.colorspace_settings.name, "Non-Color")
            for image in (color, mask):
                self.assertEqual(bytes(image.packed_file.data), png)
                self.assertEqual(tuple(image.size), (2, 2))
            # Lost cache cannot prevent offline use or undo of already packed images.
            (Path(directory) / "painter_textures" / (meta["image_id"].split(":")[1] + ".png")).unlink()
            transactions.undo(self.ts)
            self.assertEqual(document.canonical(document.snapshot(self.ts)), before)

    def test_finished_instructions_leave_no_texture_cache_files(self):
        import os
        from types import SimpleNamespace
        from open_painter.ai import generated_images
        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
        def png(fill):
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) +
                    chunk(b"IDAT", zlib.compress((b"\0" + bytes([fill]) * 6) * 2)) + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory(prefix="painter-image-test-") as directory:
            cache = Path(directory) / "painter_textures"
            fixture = SimpleNamespace(send=lambda message: None, close=lambda: None, state_directory=directory)
            def generate(data):
                meta = generated_images.store(directory, dict(result=base64.b64encode(data).decode()))
                session.handle(dict(event="generated_image", request_id=session.request["id"], **meta))
                return cache / (meta["image_id"].split(":")[1] + ".png"), meta["image_id"]
            with patch.object(session, "connection", fixture), patch.object(session, "ensure_online"):
                session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Enamel")
                path, image_id = generate(png(0x30))
                proposal = batch(dict(op="add_layer", id="new:paint", name="Generated enamel", kind="FILL"),
                                 dict(op="assign_texture", layer_id="new:paint", image_id=image_id, channel="BASE_COLOR"))
                session.tool_call(dict(request_id=session.request["id"], tool="propose_changes",
                                       arguments=dict(proposal_json=json.dumps(proposal))))
                session.completed(dict(request_id=session.request["id"], status="completed"))
                # The draft imports from the cache only when it is applied.
                self.assertTrue(path.is_file())
                receipt = session.apply_draft(session.request["id"])
                self.assertFalse(path.exists())
                image = stack.channel_for(self.layer(receipt["aliases"]["new:paint"]), "BASE_COLOR").image
                self.assertEqual(bytes(image.packed_file.data), png(0x30))
                session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Enamel")
                stopped, _ = generate(png(0x40))
                session.stop()
                self.assertFalse(stopped.exists())
                session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Enamel")
                failed, _ = generate(png(0x50))
                session.completed(dict(request_id=session.request["id"], status="failed"))
                self.assertFalse(failed.exists())
            # A texture the companion stored after its turn was stopped ages out; fresh ones stay.
            old = generated_images.store(directory, dict(result=base64.b64encode(png(0x60)).decode()))
            old_path = cache / (old["image_id"].split(":")[1] + ".png")
            os.utime(old_path, (0, 0))
            fresh = generated_images.store(directory, dict(result=base64.b64encode(png(0x70)).decode()))
            generated_images.prune(directory, keep=[fresh["image_id"]])
            self.assertFalse(old_path.exists())
            self.assertTrue((cache / (fresh["image_id"].split(":")[1] + ".png")).is_file())

    def test_numeric_edit_updates_native_shader_without_raster(self):
        identifier = self.steel()
        shader_preview.tick(self.scene, self.ts)
        self.assertTrue(shader_preview.current(self.scene, self.ts))
        material_pointer = self.ts.material.as_pointer()
        with Metrics() as metrics:
            self.apply(batch(dict(op="set_channel", layer_id=identifier, channel="ROUGHNESS", values=dict(value=.27))))
            shader_preview.tick(self.scene, self.ts)
        self.assertAlmostEqual(stack.channel_for(self.layer(identifier), "ROUGHNESS").value, .27)
        self.assertEqual(self.ts.material.as_pointer(), material_pointer)
        self.assertTrue(shader_preview.current(self.scene, self.ts))
        for phase in ("image_write", "source_generation", "masking", "blending"):
            self.assertEqual(metrics.phases[phase]["calls"], 0, metrics.report())

    def test_structural_batch_refreshes_other_layers_scene_analysis(self):
        from open_painter.generators.scene_fields import signature
        r = self.apply(batch(dict(op="add_layer", id="new:steel", name="Steel", kind="FILL"),
            dict(op="add_layer", id="new:dirt", name="Dirt", kind="FILL"),
            dict(op="add_mask", id="new:height", layer_id="new:dirt", name="Height", source="SCENE"),
            dict(op="set_field", layer_id="new:dirt", mask_id="new:height",
                 values=dict(kind="WORLD_HEIGHT", bounds=[-1, 1], resolution="64"))))
        shader_preview.flush(self.scene, self.ts)
        dirt = self.layer(r["aliases"]["new:dirt"])
        old_token = dirt.masks[0].influence.provenance
        before = document.canonical(document.snapshot(self.ts))
        self.apply(batch(dict(op="add_mask", id="new:shared", layer_id=r["aliases"]["new:steel"],
                              name="Shared wear", source="ANCHOR"),
                         dict(op="set_field", layer_id=dirt.id, mask_id=dirt.masks[0].id,
                              values=dict(bounds=[-.5, .5])),
                         dict(op="set_layer", layer_id=dirt.id, values=dict(opacity=.43))))
        shader_preview.flush(self.scene, self.ts)
        field = dirt.masks[0].influence
        self.assertNotEqual(field.provenance, old_token)
        self.assertEqual(field.provenance, repr(signature(self.ts, field)))
        graph = shader_preview.ensure(self.scene, self.ts)
        for binding in graph.bindings.values():
            self.assertEqual(binding.value, binding.read())
        transactions.undo(self.ts)
        self.assertEqual(document.canonical(document.snapshot(self.ts)), before)

    def test_idempotency_and_operation_id_collision(self):
        p = batch(dict(op="add_layer", id="new:steel", name="Steel", kind="FILL"))
        digest = document.digest(self.ts)
        transactions.apply(self.ts, p, operation_id="same", expected_digest=digest)
        self.assertTrue(transactions.apply(self.ts, p, operation_id="same", expected_digest=digest)["replayed"])
        self.assertEqual(len(self.ts.layers), 1)
        p["summary"] = "Different"
        with self.assertRaises(schema.AuthoringError):
            transactions.apply(self.ts, p, operation_id="same", expected_digest=digest)

    def test_stale_material_and_geometry_are_rejected(self):
        identifier = self.steel()
        p = batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.6)))
        digest = document.digest(self.ts)
        self.layer(identifier).opacity = .91
        with self.assertRaises(schema.AuthoringError):
            transactions.apply(self.ts, p, operation_id="stale", expected_digest=digest)
        digest = document.digest(self.ts)
        self.obj.location.z += 1
        bpy.context.view_layer.update()
        with self.assertRaises(schema.AuthoringError):
            transactions.apply(self.ts, p, operation_id="stale-mesh", expected_digest=digest)

    def test_saving_between_draft_and_apply_keeps_the_digest(self):
        # Packing on save clears ``is_dirty``; a stroke painted before the draft
        # was captured leaves the canvas dirty until Ctrl+S, which must not
        # turn a finished draft into "the material changed" (0.9.7).
        import numpy as np
        from open_painter.core import image_manager
        from open_painter.evaluation import cache
        identifier = self.steel()
        paint = stack.add(self.ts, "PAINT", "Hand painted")
        image = image_manager.create("Director paint test", 64)
        stack.channel_for(paint, "BASE_COLOR").image = image
        image.pixels.foreach_set(np.full(64 * 64 * 4, .5, np.float32))
        image.update()
        image["open_painter_pixel_revision"] = image.get("open_painter_pixel_revision", 0) + 1
        self.assertTrue(image.is_dirty)
        p = batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.6)))
        digest = document.digest(self.ts)
        cache.on_save(None)
        self.assertFalse(image.is_dirty)
        self.assertEqual(document.digest(self.ts), digest)
        transactions.apply(self.ts, p, operation_id="after-save", expected_digest=digest)
        self.assertAlmostEqual(self.layer(identifier).opacity, .6, places=5)
        # Pixels that changed since the capture are still caught by the revision.
        image["open_painter_pixel_revision"] += 1
        self.assertNotEqual(document.digest(self.ts), digest)

    def test_paint_overlay_is_preserved_and_stays_above_new_content(self):
        paint = stack.add(self.ts, "PAINT", "Hand painted label")
        identifier = paint.id
        before = document.canonical(stack.snapshot(paint))
        self.steel()
        self.assertEqual(self.ts.layers[-1].id, identifier)
        self.assertEqual(document.canonical(stack.snapshot(self.layer(identifier))), before)
        with self.assertRaises(schema.AuthoringError):
            self.apply(batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.1))))

    def test_channel_lock_does_not_lock_other_channels(self):
        identifier = self.steel()
        self.ts.director_locks = json.dumps([["layers", identifier, "channels", "BASE_COLOR"]])
        self.apply(batch(dict(op="set_channel", layer_id=identifier, channel="ROUGHNESS", values=dict(value=.2))))
        with self.assertRaises(schema.AuthoringError):
            self.apply(batch(dict(op="set_channel", layer_id=identifier, channel="BASE_COLOR", values=dict(color=[1, 0, 0, 1]))))

    def test_folder_scope_and_folder_protection(self):
        r = self.apply(batch(dict(op="add_layer", id="new:folder", name="Wear", kind="FOLDER")))
        root = r["aliases"]["new:folder"]
        other = self.steel()
        with self.assertRaises(schema.AuthoringError):
            self.apply(batch(dict(op="set_layer", layer_id=other, values=dict(opacity=.2))), scope=root)
        self.apply(batch(dict(op="add_layer", id="new:dirt", name="Dirt", kind="FILL")), scope=root)
        self.ts.director_locks = json.dumps([["layers", root]])
        with self.assertRaises(schema.AuthoringError):
            self.apply(batch(dict(op="add_layer", id="new:rust", name="Rust", kind="FILL", parent_id=root)))

    def test_anchor_cycle_and_protected_shared_source(self):
        identifier = self.steel()
        r = self.apply(batch(dict(op="add_mask", id="new:noise", layer_id=identifier, name="Noise", source="NOISE"),
                             dict(op="add_mask", id="new:a", layer_id=identifier, name="Shared wear", source="ANCHOR"),
                             dict(op="add_layer", id="new:rust", name="Rust", kind="FILL"),
                             dict(op="add_mask", id="new:r", layer_id="new:rust", name="Wear input", source="REFERENCE"),
                             dict(op="connect_anchor", layer_id="new:rust", mask_id="new:r", anchor_id="new:a")))
        consumer = r["aliases"]["new:rust"]
        self.ts.director_locks = json.dumps([["layers", consumer]])
        with self.assertRaises(schema.AuthoringError):
            self.apply(batch(dict(op="set_mask", layer_id=identifier, mask_id=r["aliases"]["new:noise"], values=dict(amount=.2))))
        self.ts.director_locks = "[]"
        with self.assertRaises(schema.AuthoringError):
            self.apply(batch(dict(op="add_mask", id="new:back", layer_id=identifier, name="Back edge", source="REFERENCE"),
                             dict(op="add_mask", id="new:late", layer_id=identifier, name="Late output", source="ANCHOR"),
                             dict(op="connect_anchor", layer_id=identifier, mask_id="new:back", anchor_id="new:late")))

    def test_live_scene_mask_and_missing_bake(self):
        identifier = self.steel()
        self.apply(batch(dict(op="add_mask", id="new:bottom", layer_id=identifier, name="Bottom dirt", source="SCENE"),
                         dict(op="set_field", layer_id=identifier, mask_id="new:bottom", values=dict(kind="WORLD_HEIGHT", bounds=[-1, 1])),
                         dict(op="set_mask", layer_id=identifier, mask_id="new:bottom", values=dict(invert=True))))
        mask = self.layer(identifier).masks[0]
        self.assertEqual(mask.influence.mode, "LIVE")
        self.assertEqual(mask.influence.kind, "WORLD_HEIGHT")
        with self.assertRaisesRegex(schema.AuthoringError, "Bake missing"):
            self.apply(batch(dict(op="add_mask", id="new:edge", layer_id=identifier, name="Edges", source="GENERATOR")))

    def test_catalog_creates_normal_layers_and_no_false_module_lineage(self):
        self.apply(batch(dict(op="insert_asset", id="new:material", asset_id="material:Clean Brushed Steel", map_mode="LIVE")))
        self.assertGreater(len(self.ts.layers), 1)
        self.assertTrue(self.ts.layers[0].director_recipe)
        self.assertTrue(all(not getattr(l, "module_id", "") for l in self.ts.layers))
        self.apply(batch(dict(op="insert_asset", id="new:fingerprints", asset_id="effect:Fingerprints", map_mode="LIVE")))
        last = self.ts.layers[-1]
        self.assertEqual({c.channel for c in last.channels if c.enabled}, {"ROUGHNESS"})

    def test_rollback_restores_data_history_and_new_resources(self):
        self.steel()
        before = document.canonical(document.snapshot(self.ts))
        history = self.ts.director_history
        resources = transactions.resources()
        proposal = batch(dict(op="insert_asset", id="new:scratches", asset_id="effect:Scratches", map_mode="LIVE"))
        for step in ("prepared", "applied", "recorded"):
            def fail(stage):
                if stage == step: raise RuntimeError("injected failure")
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.apply(proposal, fault=fail)
            self.assertEqual(document.canonical(document.snapshot(self.ts)), before)
            self.assertEqual(self.ts.director_history, history)
            self.assertEqual(transactions.resources(), resources)

    def test_conflict_aware_undo_keeps_manual_changes(self):
        identifier = self.steel()
        self.apply(batch(dict(op="set_channel", layer_id=identifier, channel="ROUGHNESS", values=dict(value=.2)),
                         dict(op="set_layer", layer_id=identifier, values=dict(opacity=.7))))
        stack.channel_for(self.layer(identifier), "ROUGHNESS").value = .83
        result = transactions.undo(self.ts)
        self.assertAlmostEqual(stack.channel_for(self.layer(identifier), "ROUGHNESS").value, .83)
        self.assertAlmostEqual(self.layer(identifier).opacity, 1)
        self.assertTrue(result["conflicts"])

    def test_undo_keeps_later_artist_layer_order(self):
        stack.add(self.ts, "FILL", "Base")
        self.apply(batch(dict(op="add_layer", id="new:rust", name="Rust", kind="FILL")))
        self.ts.active_layer = -1
        stack.add(self.ts, "FILL", "Artist bottom")
        self.assertEqual([l.name for l in self.ts.layers], ["Artist bottom", "Base", "Rust"])
        result = transactions.undo(self.ts)
        self.assertEqual(result["changes"], ["Removed layer: Rust"])
        self.assertEqual([l.name for l in self.ts.layers], ["Artist bottom", "Base"])

    def test_plan_cancel_and_explain_do_not_mutate(self):
        identifier = self.steel()
        before = document.canonical(document.snapshot(self.ts))
        proposal = batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.5)))
        for mode in ("PLAN", "EXPLAIN"):
            session.request = session.capture(bpy.context, mode=mode, scope="SET", prompt="Less wear")
            event = dict(request_id=session.request["id"], tool="propose_changes", arguments=dict(proposal_json=json.dumps(proposal)))
            if mode == "EXPLAIN":
                with self.assertRaises(schema.AuthoringError): session.tool_call(event)
            else:
                session.tool_call(event)
                session.completed(dict(request_id=session.request["id"], status="completed"))
                self.assertIsNotNone(session.request["draft"])
            self.assertEqual(document.canonical(document.snapshot(self.ts)), before)
            session.stop()
            with self.assertRaises(schema.AuthoringError): session.tool_call(event)

    def test_stale_material_at_completion_keeps_connection(self):
        identifier = self.steel()
        session.request = session.capture(bpy.context, mode="REFINE", scope="SET", prompt="Less wear")
        event = dict(request_id=session.request["id"], tool="propose_changes", arguments=dict(proposal_json=json.dumps(batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.5))))))
        session.tool_call(event)
        self.layer(identifier).opacity = .9  # artist edit during the turn
        from types import SimpleNamespace
        fixture = SimpleNamespace(send=lambda message: None, close=lambda: None)
        with patch.object(session, "connection", fixture), patch.object(session, "ensure_online"):
            session.handle(dict(event="completed", request_id=session.request["id"], status="completed"))
            self.assertIs(session.connection, fixture)
        self.assertIn("changed", session.state["status"])
        self.assertAlmostEqual(self.layer(identifier).opacity, .9)

    def test_browsing_another_set_during_a_turn_keeps_tools_working(self):
        identifier = self.steel()
        self.scene_fixture()
        session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Less wear")
        self.scene.open_painter.active_texture_set = 1
        context = session.tool_call(dict(request_id=session.request["id"], tool="get_context", arguments={}))
        self.assertEqual(context["target"]["id"], self.ts.id)
        proposal = batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.5)))
        event = dict(request_id=session.request["id"], tool="propose_changes", arguments=dict(proposal_json=json.dumps(proposal)))
        self.assertTrue(session.tool_call(event)["valid"])
        session.completed(dict(request_id=session.request["id"], status="completed"))
        self.assertIsNotNone(session.request["draft"])
        with self.assertRaisesRegex(schema.AuthoringError, "original texture set"):
            session.apply_draft(session.request["id"])
        self.scene.open_painter.active_texture_set = 0
        session.apply_draft(session.request["id"])
        self.assertAlmostEqual(self.layer(identifier).opacity, .5)

    def test_account_errors_keep_a_running_turn_and_a_reviewed_draft(self):
        identifier = self.steel()
        session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Less wear")
        event = dict(request_id=session.request["id"], tool="propose_changes", arguments=dict(proposal_json=json.dumps(batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.5))))))
        session.tool_call(event)
        session.handle(dict(event="error", message="Codex account/rateLimits/read failed (error)."))
        self.assertTrue(session.request and session.request["running"])
        self.assertEqual(len(session.state["changes"]), 1)
        session.completed(dict(request_id=session.request["id"], status="completed"))
        session.handle(dict(event="error", message="Codex account/read failed (error)."))
        session.handle(dict(event="error", request_id="stale", message="Late error of an earlier instruction"))
        self.assertIsNotNone(session.request["draft"])
        self.assertIn("account/read", session.state["status"])
        session.handle(dict(event="error", request_id=session.request["id"], message="The instruction timed out."))
        self.assertIsNone(session.request)
        self.assertEqual(session.state["changes"], [])

    def test_idle_connection_tick_does_not_redraw(self):
        from types import SimpleNamespace
        events = []
        fixture = SimpleNamespace(send=lambda message: None, close=lambda: None,
                                  poll=lambda: [events.pop() for _ in range(len(events))])
        with patch.object(session, "connection", fixture), patch.object(session, "ensure_online"), \
                patch.object(session, "redraw") as redraw:
            self.assertEqual(session.tick(), .25)
            redraw.assert_not_called()
            events.append(dict(event="limits", limits={}))
            self.assertEqual(session.tick(), .25)
            redraw.assert_called_once()
            session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Less wear")
            self.assertEqual(session.tick(), .05)
            self.assertIs(session.connection, fixture)

    def test_blocking_tool_step_is_drawn_before_blender_runs_it(self):
        from types import SimpleNamespace
        sent, events, calls = [], [], []
        fixture = SimpleNamespace(send=sent.append, close=lambda: None,
                                  poll=lambda: [events.pop(0) for _ in range(len(events))])
        session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Less wear")
        identifier = session.request["id"]
        session.progress.begin(identifier)
        events.extend([
            dict(event="thinking", request_id=identifier, item_id="rs", part=0, text="**Checking the channels**\n\nRoughness"),
            dict(event="tool", call_id=7, request_id=identifier, tool="inspect_material_channels",
                 arguments=dict(texture_set_id=self.ts.id)),
            dict(event="commentary", request_id=identifier, item_id="note", text="Next I'll propose less wear.")])

        def tool_call(event):
            calls.append(event["tool"])
            return dict(channels={})
        with patch.object(session, "connection", fixture), patch.object(session, "ensure_online"), \
                patch.object(session, "tool_call", tool_call), patch.object(session, "redraw") as redraw:
            session.tick()
            # The step is recorded and drawn while Blender has not started the render.
            self.assertEqual(calls, [])
            self.assertEqual(sent, [])
            redraw.assert_called_once()
            self.assertEqual([s["kind"] for s in session.progress.steps], ["THOUGHT", "WAIT"])
            self.assertEqual(session.progress.activity()[:2], ("Rendering the material channels", "WAIT"))
            self.assertIn("Blender pauses", session.progress.hint())
            session.tick()
            self.assertEqual(calls, ["inspect_material_channels"])
            self.assertEqual([(m["call_id"], m["success"]) for m in sent], [(7, True)])
            wait = session.progress.steps[1]
            self.assertFalse(wait["busy"])
            self.assertIsNotNone(wait["seconds"])
            self.assertEqual(session.progress.steps[-1]["text"], "Next I'll propose less wear.")
            self.assertEqual(session.progress.activity()[1], "THINK")
            # Quick tools answer in the same tick; a rejection stays visible in the log.
            events.append(dict(event="tool", call_id=8, request_id=identifier, tool="propose_changes",
                               arguments=dict(proposal_json="{}")))
            with patch.object(session, "tool_call", side_effect=schema.AuthoringError("Proposal is invalid")):
                session.tick()
            self.assertEqual([(m["call_id"], m["success"]) for m in sent], [(7, True), (8, False)])
            self.assertEqual(session.progress.steps[-1]["error"], "Proposal is invalid")
        self.assertIsNone(session.request["draft"])

    def test_progress_notes_stay_out_of_the_reply_and_close_the_log(self):
        session.request = session.capture(bpy.context, mode="EXPLAIN", scope="SET", prompt="Why is the wear uniform?")
        identifier = session.request["id"]
        session.progress.begin(identifier)
        session.state["response"] = ""
        session.handle(dict(event="turn_metadata", request_id=identifier, model="gpt-5.5", reasoning_effort="high", time_limit=180))
        session.handle(dict(event="commentary", request_id=identifier, item_id="note", text="I'll read the layers."))
        session.handle(dict(event="thinking", request_id="stale", item_id="rs", part=0, text="**Elsewhere**"))
        self.assertEqual(session.state["response"], "")
        self.assertEqual(session.progress.time_limit, 180)
        self.assertEqual([s["text"] for s in session.progress.steps],
                         ["Codex started on gpt-5.5 · high reasoning", "I'll read the layers."])
        session.handle(dict(event="completed", request_id=identifier, status="completed"))
        # A turn that only narrated still answers in the reply, as before notes were split out.
        self.assertEqual(session.state["response"], "I'll read the layers.")
        self.assertFalse(session.progress.running)
        self.assertEqual(session.progress.outcome, "completed")
        self.assertEqual(session.progress.steps[-1]["kind"], "DONE")
        self.assertTrue(session.progress.summary().startswith("Finished in"))

    def test_scene_inspection_captures_from_the_painter_area(self):
        other, _, _ = self.scene_fixture()
        window = bpy.context.window
        areas = list(window.screen.areas)
        painter = next(a for a in areas if a.type == "VIEW_3D")
        solid = next(a for a in areas if a.type != "VIEW_3D" and areas.index(a) < areas.index(painter))
        previous = solid.type
        with bpy.context.temp_override(window=window, area=solid):
            solid.type = "VIEW_3D"
        try:
            painter.spaces.active.shading.type = "MATERIAL"
            region = next(r for r in painter.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=painter, region=region):
                captured = session.capture(bpy.context, mode="REFINE", scope="SCENE", prompt="Match the wear")
            captured["visual_settings"] = dict(send_view=True, send_maps=False, reference=None)
            session.request = captured
            seen = []
            def record(context, ts, **kwargs):
                seen.append(context.area.as_pointer())
                return [], []
            with patch.object(session, "attachments_for", record):
                session.tool_call(dict(request_id=captured["id"], tool="get_material_context", arguments=dict(texture_set_id=other.id)))
            self.assertEqual(seen, [painter.as_pointer()])
            self.assertIn(other.id, captured["inspected_materials"])
            # A capture without its space still prefers a Material-shaded view over the first viewport.
            captured["space"] = 0
            self.assertEqual(session.painter_area(captured, window).as_pointer(), painter.as_pointer())
        finally:
            with bpy.context.temp_override(window=window, area=solid):
                solid.type = previous

    def test_isolated_view_capture_keeps_the_artists_selection(self):
        # Hiding a base clears its selected flag on the next view-layer update,
        # so a Direct Material send used to leave the high-poly sources (and the
        # active object) deselected once the isolated capture unhid them.
        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        source = bpy.context.object
        bpy.ops.mesh.primitive_cube_add(location=(-3, 0, 0))
        untouched = bpy.context.object
        bpy.ops.object.select_all(action="DESELECT")
        for obj in (self.obj, source):
            obj.select_set(True)
        bpy.context.view_layer.objects.active = source
        with viewport.isolated(bpy.context, {self.obj}):
            self.assertTrue(source.hide_get() and untouched.hide_get())
            self.assertFalse(self.obj.hide_get())
        self.assertFalse(source.hide_get() or untouched.hide_get())
        self.assertEqual([o.select_get() for o in (self.obj, source, untouched)], [True, True, False])
        self.assertEqual(bpy.context.view_layer.objects.active, source)

    def test_reports_name_removed_masks_and_filters(self):
        identifier = self.steel()
        r = self.apply(batch(dict(op="add_mask", id="new:dirt", layer_id=identifier, name="Dirt", source="NOISE"),
                             dict(op="add_filter", id="new:levels", layer_id=identifier, channel="ROUGHNESS", kind="LEVELS", values=dict(black=.1))))
        self.assertEqual(r["changes"], ["Added mask: Steel > Dirt", "Added filter: Steel > Levels"])
        with self.assertRaisesRegex(schema.AuthoringError, "effect_id"):
            self.apply(batch(dict(op="set_filter", layer_id=identifier, channel="ROUGHNESS", values=dict(black=.2))))
        self.apply(batch(dict(op="set_filter", layer_id=identifier, channel="ROUGHNESS", effect_id=r["aliases"]["new:levels"], values=dict(black=.2))))
        transactions.undo(self.ts)
        result = transactions.undo(self.ts)
        self.assertEqual(result["changes"], ["Removed mask: Steel > Dirt", "Removed filter: Steel > Levels"])

    def test_large_map_preview_does_not_stall(self):
        from time import perf_counter
        from open_painter.ai import visual_context
        from open_painter.core import image_manager
        image = image_manager.create("Large preview test", 4096)
        started = perf_counter()
        result = visual_context.thumbnail(image, side=256)
        # Row slices of image.pixels convert the whole buffer each time: 4 s per 4K map before.
        self.assertLess(perf_counter() - started, 1.5)
        self.assertEqual((result["width"], result["height"]), (256, 256))

    def test_invalid_replacement_clears_earlier_valid_draft(self):
        identifier = self.steel()
        session.request = session.capture(bpy.context, mode="PLAN", scope="SET", prompt="Less wear")
        event = dict(request_id=session.request["id"], tool="propose_changes", arguments=dict(proposal_json=json.dumps(batch(dict(op="set_layer", layer_id=identifier, values=dict(opacity=.5))))))
        session.tool_call(event)
        event["arguments"]["proposal_json"] = "{}"
        with self.assertRaises(schema.AuthoringError): session.tool_call(event)
        self.assertIsNone(session.request["draft"])

    def test_explain_reply_reaches_followup_without_cross_material_history(self):
        self.steel()
        session.request = session.capture(bpy.context, mode="EXPLAIN", scope="SET", prompt="Why is the wear uniform?")
        identifier = session.request["id"]
        session.handle(dict(event="text", request_id=identifier, text="Narrow the edges and coarsen the dirt."))
        session.completed(dict(request_id=identifier, status="completed"))
        settings = bpy.context.window_manager.open_painter_director
        settings.mode, settings.prompt = "REFINE", "Fix those two problems"
        settings.scope, settings.send_view = "SET", False
        # No model images in a background run: no generated textures and no candidate review.
        settings.generate_textures, settings.correction_enabled = False, False
        messages = []
        from types import SimpleNamespace
        # About the conversation history, not the geometry evidence authoring requires (no mesh
        # maps are baked here, and a background run captures no view).
        with patch.object(session, "ensure_online"), patch.object(session, "connection", SimpleNamespace(send=messages.append)), \
                patch.object(visual_context, "require_geometry_maps"):
            with patch.dict(session.state, signed_in=True):
                session.run(bpy.context)
        history = json.loads(messages[-1]["input"])["recent_conversation"]
        self.assertEqual(history[-1]["response"], "Narrow the edges and coarsen the dirt.")
        self.assertEqual(history[-1]["outcome"], "explained")
        self.assertEqual(session.conversation(dict(scene=self.scene.as_pointer(), set_id="another")), [])
        session.clear_conversation()
        self.assertEqual(session.conversation(session.request), [])

    def test_texture_budget_rejects_growth_before_mutation(self):
        from open_painter.authoring import resource_budget
        identifier = self.steel()
        before = document.canonical(document.snapshot(self.ts))
        proposal = batch(*(dict(op="add_mask", id=f"new:noise{i}", layer_id=identifier,
                               name=f"Noise {i}", source="PROCEDURAL", values=dict(seed=i))
                           for i in range(resource_budget.LIMIT+1)))
        with self.assertRaisesRegex(schema.AuthoringError, "Shader texture budget exceeded"):
            self.apply(proposal)
        self.assertEqual(document.canonical(document.snapshot(self.ts)), before)

    def test_zz_receipts_survive_save_reopen(self):
        identifier = self.steel()
        scene_name, set_id = self.scene.name, self.ts.id
        self.ts.director_locks = json.dumps([["layers", identifier, "channels", "BASE_COLOR"]])
        path = ROOT / ".blender-test/material-director.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        self.scene = bpy.data.scenes[scene_name]
        self.ts = next(t for t in self.scene.open_painter.texture_sets if t.id == set_id)
        self.assertEqual(transactions.history(self.ts)[-1]["aliases"]["new:steel"], identifier)
        self.assertEqual(commands.locks(self.ts)[0][1], identifier)
        self.assertIsNone(session.request)
        self.assertIsNone(session.connection)

    def scene_fixture(self):
        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        other = sets.create(self.scene, "Other scene region", 512)
        sets.assign(self.scene, other, bpy.context.object)
        self.scene.open_painter.active_texture_set = 0
        proposal = dict(version=1, summary="Coherent scene", targets=[
            dict(texture_set_id=ts.id, proposal=batch(dict(op="add_layer", id="new:paint", name="Paint", kind="FILL")))
            for ts in (self.ts, other)])
        return other, proposal, {ts.id: document.digest(ts) for ts in (self.ts, other)}

    def test_scene_atomic_rollback_and_grouped_undo(self):
        from open_painter.authoring import scene
        other, proposal, digests = self.scene_fixture()
        def fail_last(ts):
            if ts.id == other.id: raise RuntimeError("last target failed")
        with self.assertRaisesRegex(RuntimeError, "last target"):
            scene.apply(self.scene, proposal, operation_id="scene-test", digests=digests, fault=fail_last)
        self.assertEqual({ts.id: document.digest(ts) for ts in (self.ts, other)}, digests)
        self.assertFalse(transactions.history(self.ts))
        self.assertFalse(transactions.history(other))
        scene.apply(self.scene, proposal, operation_id="scene-test", digests=digests)
        self.assertEqual(len(self.ts.layers), 1)
        self.assertEqual(len(other.layers), 1)
        transactions.undo(other)
        self.assertEqual({ts.id: document.digest(ts) for ts in (self.ts, other)}, digests)
        self.assertTrue(all(transactions.history(ts)[-1]["reverted"] for ts in (self.ts, other)))

    def test_scene_stale_target_and_missing_undo_group_never_partially_apply(self):
        from open_painter.authoring import scene
        other, proposal, digests = self.scene_fixture()
        stack.add(other, "FILL", "Artist edit")
        before = document.canonical(document.snapshot(self.ts))
        with self.assertRaisesRegex(schema.AuthoringError, "changed"):
            scene.apply(self.scene, proposal, operation_id="scene-test", digests=digests)
        self.assertEqual(document.canonical(document.snapshot(self.ts)), before)
        digests = {ts.id: document.digest(ts) for ts in (self.ts, other)}
        scene.apply(self.scene, proposal, operation_id="scene-test", digests=digests)
        other.director_history = "[]"
        before = document.canonical(document.snapshot(self.ts))
        with self.assertRaisesRegex(schema.AuthoringError, "complete scene undo group"):
            transactions.undo(self.ts)
        self.assertEqual(document.canonical(document.snapshot(self.ts)), before)

    def test_scene_scope_requires_inspection_and_complete_create(self):
        other, proposal, digests = self.scene_fixture()
        captured = session.capture(bpy.context, mode="CREATE", scope="SCENE", prompt="Texture the full scene")
        session.request = captured
        self.assertEqual(set(captured["target_digests"]), {self.ts.id, other.id})
        self.assertEqual(len(captured["context"]["scene_materials"]), 2)
        event = dict(request_id=captured["id"], tool="propose_scene_changes", arguments=dict(proposal_json=json.dumps(proposal)))
        with self.assertRaisesRegex(schema.AuthoringError, "Inspect every"):
            session.tool_call(event)
        captured["inspected_materials"] = {self.ts.id, other.id}
        partial = dict(proposal, targets=proposal["targets"][:1])
        with self.assertRaisesRegex(schema.AuthoringError, "every captured"):
            session.tool_call(dict(event, arguments=dict(proposal_json=json.dumps(partial))))
        result = session.tool_call(event)
        self.assertTrue(result["valid"])
        self.assertEqual({ts.id: document.digest(ts) for ts in (self.ts, other)}, digests)
        captured["running"] = False
        receipt = session.apply_draft(captured["id"])
        self.assertEqual(len(receipt["receipts"]), 2)
        transactions.undo(self.ts)
        self.assertEqual({ts.id: document.digest(ts) for ts in (self.ts, other)}, digests)


open_painter.register()
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DirectorTests))
open_painter.unregister()
assert result.wasSuccessful(), "Material Director integration failed"
print("MATERIAL_DIRECTOR_PASS")
