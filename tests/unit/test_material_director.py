"""Boundary and cancellation tests that need neither Blender nor an account."""

import json
from pathlib import Path
import queue
import sys
import unittest
import base64
import struct
import tempfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.authoring import schema
from open_painter.ai import protocol
from open_painter.ai import generated_images
from open_painter.ai.codex_client import CodexClient, launch_arguments


def proposal():
    return dict(version=1, summary="Steel", commands=[dict(op="add_layer", id="new:steel", name="Steel", kind="FILL")])


class SchemaTests(unittest.TestCase):
    def test_generated_image_ids_integrity_and_dimensions(self):
        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory() as directory:
            item = generated_images.store(directory, dict(result=base64.b64encode(png).decode()))
            self.assertEqual(generated_images.read(directory, item), png)
            with self.assertRaises(ValueError):
                generated_images.read(directory, dict(item, image_id="generated:../../auth.json"))
            path = Path(directory) / "painter_textures" / (item["image_id"].split(":")[1] + ".png")
            path.write_bytes(png + b"changed")
            with self.assertRaises(ValueError): generated_images.read(directory, item)
            with self.assertRaises(ValueError): generated_images.inspect(png[:16] + struct.pack(">II", 5000, 5000) + png[24:])
            with self.assertRaises(ValueError): generated_images.store(directory, dict(result="not base64"))

    def test_roundtrip(self):
        self.assertEqual(schema.validate(json.dumps(proposal())), proposal())

    def test_unknown_and_unbounded_requests(self):
        cases = []
        for command in (dict(op="exec", code="print('no')"),
                        dict(op="set_channel", layer_id="x", channel="METALLIC", values=dict(value=2)),
                        dict(op="set_layer", layer_id="x", values=dict(image_path="/tmp/image")),
                        dict(op="set_layer", layer_id="x", values=dict(opacity=float("nan"))),
                        dict(op="set_layer", layer_id="x", values=dict(opacity=True))):
            p = proposal(); p["commands"] = [command]; cases.append(p)
        p = proposal(); p["commands"] *= 65; cases.append(p)
        p = proposal(); p["commands"] *= 2; cases.append(p)
        p = proposal(); p["commands"][0]["id"] = "existing-id"; cases.append(p)
        p = proposal(); p["version"] = True; cases.append(p)
        p = proposal(); p["summary"] = "x" * schema.MAX_BYTES; cases.append(p)
        for p in cases:
            with self.subTest(p=str(p)[:200]), self.assertRaises(schema.AuthoringError):
                schema.validate(p)

    def test_protocol_fragmentation_and_multiple_frames(self):
        decoder = protocol.Decoder()
        data = protocol.encode(dict(event="text", text="çelik")) + protocol.encode(dict(event="done"))
        for byte in data[:-1]:
            decoder.feed(bytes([byte]))
        self.assertEqual(decoder.pop(), dict(event="text", text="çelik"))
        self.assertIsNone(decoder.pop())
        decoder.feed(data[-1:])
        self.assertEqual(decoder.pop(), dict(event="done"))

    def test_protocol_rejects_invalid_and_oversized_frames(self):
        for data in (b"[]\n", b"bad\n", b"x" * (protocol.MAX_MESSAGE + 1)):
            decoder = protocol.Decoder(); decoder.feed(data)
            with self.assertRaises(ValueError): decoder.pop()
        with self.assertRaises(ValueError): protocol.encode(dict(text="x" * protocol.MAX_MESSAGE))


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(CodexClient)
        c = self.client
        c.account = dict(type="chatgpt")
        c.request_id = c.thread_id = c.turn_id = None
        c.workspace = "/tmp/painter-test"
        c.config = {"cli_auth_credentials_store": "ephemeral"}
        c.pending = {}
        c.serial = 0
        c.events = queue.Queue()
        c.emitted = []
        c.emit = c.emitted.append
        c.sent = []
        c.send = c.sent.append
        c.process = type("Process", (), dict(poll=lambda self: None))()
        self.message = dict(request_id="one", tools=[], instructions="Painter", input="Steel")

    def respond(self, index, result=None, error=None):
        event = dict(id=self.client.sent[index]["id"])
        event["error" if error else "result"] = error or result
        self.client.events.put(event)
        self.client.poll()

    def test_stop_before_thread_creation(self):
        c = self.client
        c.run(self.message); c.interrupt()
        self.respond(0, dict(thread=dict(id="thread-old")))
        self.assertEqual([r["method"] for r in c.sent], ["thread/start"])
        self.assertEqual(c.sent[0]["params"]["config"]["cli_auth_credentials_store"], "ephemeral")

    def test_image_generation_can_be_disabled_per_instruction(self):
        c = self.client
        c.run(dict(self.message, generate_textures=False))
        self.assertFalse(c.sent[0]["params"]["config"]["features.image_generation"])
        c.thread_id = "current"
        c.events.put(dict(method="item/started", params=dict(threadId="current",
            item=dict(type="imageGeneration", id="unexpected"))))
        with self.assertRaises(ValueError): c.poll()
        self.assertIsNone(c.request_id)

    def test_multiple_labeled_images_reach_the_codex_turn(self):
        c = self.client
        items = [dict(id="viewport", label="Model", url="data:image/png;base64,model"),
                 dict(id="map:AO", label="AO data", url="data:image/png;base64,ao"),
                 dict(id="reference", label="Appearance reference", url="data:image/png;base64,ref")]
        c.run(dict(self.message, images=items))
        self.respond(0, dict(thread=dict(id="thread-one")))
        inputs = c.sent[-1]["params"]["input"]
        self.assertEqual([i["url"] for i in inputs if i["type"] == "image"], [i["url"] for i in items])
        self.assertIn("reference", inputs[-2]["text"])

    def test_stop_during_turn_start(self):
        c = self.client
        c.run(self.message)
        self.respond(0, dict(thread=dict(id="thread-old")))
        c.interrupt()
        self.respond(1, dict(turn=dict(id="turn-old")))
        self.assertEqual(c.sent[-1]["method"], "turn/interrupt")
        self.assertIsNone(c.request_id)

    def test_scene_tool_returns_labeled_images_in_reference_order(self):
        c = self.client
        c.tool_result(dict(call_id=42, result=dict(target="panel"), images=[
            dict(id="map:AO", label="Panel AO", url="data:image/png;base64,ao"),
            dict(id="reference", label="Appearance", url="data:image/png;base64,ref")]))
        items = c.sent[-1]["result"]["contentItems"]
        self.assertEqual([item["imageUrl"] for item in items if item["type"] == "inputImage"],
                         ["data:image/png;base64,ao", "data:image/png;base64,ref"])
        self.assertIn("reference", items[-2]["text"])

    def test_late_error_cannot_cancel_new_request(self):
        c = self.client
        c.run(self.message); c.interrupt()
        c.run(dict(self.message, request_id="two"))
        self.respond(0, error=dict(code=-1))
        self.assertEqual(c.request_id, "two")
        self.assertFalse(c.emitted)

    def test_late_interrupt_error_cannot_cancel_new_request(self):
        c = self.client
        c.run(self.message)
        self.respond(0, dict(thread=dict(id="thread-old")))
        self.respond(1, dict(turn=dict(id="turn-old")))
        c.interrupt()
        c.run(dict(self.message, request_id="two"))
        self.assertEqual(c.sent[2]["method"], "turn/interrupt")
        self.respond(2, error=dict(code=-1))
        self.assertEqual(c.request_id, "two")
        self.assertFalse(c.emitted)

    def test_unexpected_server_capability_is_denied(self):
        c = self.client
        c.run(self.message)
        c.events.put(dict(id=900, method="item/commandExecution/requestApproval", params={}))
        c.poll()
        self.assertEqual(c.sent[-1]["error"]["code"], -32601)

    def test_launch_isolated_configuration(self):
        args = launch_arguments("codex")
        for setting in (
            'features.shell_tool=false', 'features.unified_exec=false',
            'features.plugins=false', 'web_search="disabled"', 'mcp_servers={}'
        ):
            self.assertIn(setting, args)
        self.assertIn("--strict-config", args)


if __name__ == "__main__":
    unittest.main()
