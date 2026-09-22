"""Account isolation and evidence privacy: no real credential or provider access."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/material_director"))
from runtime import isolated_login, redact, save_attachment, sha256


class BenchmarkRuntimeTests(unittest.TestCase):
    def auth(self, directory, expires):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).decode().rstrip("=")
        path = Path(directory) / "source.json"
        path.write_text(json.dumps({"tokens": {"access_token": "header." + payload + ".signature",
            "id_token": "test-identity", "refresh_token": "never-copy-me", "account_id": "test-account"}}))
        return path

    def test_access_only_private_copy_removed_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.auth(directory, time.time() + 3600)
            before = source.read_bytes()
            with isolated_login(source, minimum_seconds=100) as isolated:
                target = isolated / "auth.json"
                value = json.loads(target.read_text())
                self.assertEqual(value["tokens"]["refresh_token"], "")
                self.assertNotIn("never-copy-me", target.read_text())
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(value["tokens"]["id_token"], "test-identity")
            self.assertFalse(isolated.exists())
            self.assertEqual(source.read_bytes(), before)

    def test_expiring_access_is_rejected_without_refreshing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.auth(directory, time.time() + 30)
            before = source.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "expires"):
                with isolated_login(source, minimum_seconds=60):
                    self.fail("Expiring login was accepted")
            self.assertEqual(source.read_bytes(), before)

    def test_evidence_redaction_and_host_owned_attachment_paths(self):
        sensitive = {"nested": {"access_token": "private", "refresh_token": "private"},
            "text": f"Bearer private-token {Path.home()}/secret /private/tmp/cache/secret sk-abcdefghijklmnopq",
            "image": "data:image/png;base64,c2VjcmV0"}
        text = json.dumps(redact(sensitive))
        self.assertNotIn("private-token", text)
        self.assertNotIn("c2VjcmV0", text)
        self.assertNotIn(str(Path.home()), text)
        with tempfile.TemporaryDirectory() as directory:
            item = save_attachment(directory, dict(id="../../unsafe", label="../../unsafe",
                url="data:image/png;base64," + base64.b64encode(b"image-data").decode()), prefix="create")
            target = Path(directory) / item["path"]
            self.assertTrue(target.is_relative_to(directory))
            self.assertEqual(target.read_bytes(), b"image-data")
            self.assertEqual(item["sha256"], sha256(target))


if __name__ == "__main__":
    unittest.main()
