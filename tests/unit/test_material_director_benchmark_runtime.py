"""Account isolation and evidence privacy: no real credential or provider access."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/material_director"))
from runtime import AuthRefreshRequired, MAX_AUTH_BYTES, isolated_login, preflight_login, redact, save_attachment, sha256


class BenchmarkRuntimeTests(unittest.TestCase):
    def assert_private_copy(self, target):
        if os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            return
        # Windows stat() exposes CRT read/write bits, not access permissions.
        # Check the actual inherited ACL on both the temporary directory and
        # the file, using only a synthetic login created by this test.
        command = """
$privateUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$privatePaths = @([System.IO.Path]::GetDirectoryName($env:OP_TEST_PRIVATE_PATH), $env:OP_TEST_PRIVATE_PATH)
$privateRecords = foreach ($privatePath in $privatePaths) {
    $privateAcl = Get-Acl -LiteralPath $privatePath -ErrorAction Stop
    [pscustomobject]@{
        user = $privateUser
        owner = $privateAcl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        allowed = @($privateAcl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
            Where-Object { $_.AccessControlType -eq 'Allow' } |
            ForEach-Object { $_.IdentityReference.Value })
    }
}
ConvertTo-Json -InputObject @($privateRecords) -Depth 4 -Compress
"""
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            env=dict(os.environ, OP_TEST_PRIVATE_PATH=str(target)),
            capture_output=True, text=True, check=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        records = json.loads(completed.stdout)
        self.assertEqual(len(records), 2)
        for access in records:
            self.assertEqual(access["owner"], access["user"])
            self.assertTrue(access["allowed"])
            # SYSTEM/admin access is analogous to privileged access to POSIX
            # 0600 files. OWNER RIGHTS applies only to the verified file owner.
            self.assertLessEqual(set(access["allowed"]),
                                 {access["user"], "S-1-5-18", "S-1-5-32-544", "S-1-3-4"})

    def auth(self, directory, expires):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).decode().rstrip("=")
        path = Path(directory) / "source.json"
        path.write_text(json.dumps({"tokens": {"access_token": "header." + payload + ".signature",
            "id_token": "test-identity", "refresh_token": "never-copy-me", "account_id": "test-account"}}), encoding="utf-8")
        return path

    def test_access_only_private_copy_removed_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.auth(directory, time.time() + 3600)
            before = source.read_bytes()
            with isolated_login(source, minimum_seconds=100) as isolated:
                target = isolated / "auth.json"
                value = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(value["tokens"]["refresh_token"], "")
                self.assertNotIn("never-copy-me", target.read_text(encoding="utf-8"))
                self.assert_private_copy(target)
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

    def test_preflight_is_bounded_read_only_and_returns_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            source=self.auth(directory,time.time()+3600);original=source.read_bytes()
            report=preflight_login(source,minimum_seconds=1920)
            self.assertEqual(report['status'],'READY')
            self.assertFalse(report['refresh_performed'])
            self.assertNotIn('test-',json.dumps(report))
            self.assertNotIn('never-copy-me',json.dumps(report))
            self.assertEqual(list(Path(directory).iterdir()),[source])
            self.assertEqual(source.read_bytes(),original)
            for value in ('{}','[]','{"tokens":"private-token"}',json.dumps({'tokens':{'access_token':123,'id_token':'private-token'}}),'x'*(MAX_AUTH_BYTES+1)):
                source.write_text(value, encoding="utf-8")
                with self.assertRaises(AuthRefreshRequired) as caught:
                    preflight_login(source,minimum_seconds=1920)
                self.assertNotIn('private-token',str(caught.exception))
                self.assertEqual(source.read_text(encoding="utf-8"),value)
            source.unlink()
            with self.assertRaises(AuthRefreshRequired):
                preflight_login(source,minimum_seconds=1920)

    def test_preflight_rejects_nonfinite_and_invalid_expiry_without_token_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            for expiry in (None,True,'private-token',float('nan'),float('inf'),time.time()+1900):
                with self.subTest(expiry=expiry):
                    source=self.auth(directory,expiry);original=source.read_bytes()
                    with self.assertRaises(AuthRefreshRequired):
                        preflight_login(source,minimum_seconds=1920)
                    self.assertEqual(source.read_bytes(),original)

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

    def test_numeric_tool_ids_remain_json_safe_and_nested_secrets_are_redacted(self):
        value={'tool_started':{7:{'name':'get_context','at':1.5,'access_token':'private'}},
               'nested':[{42:{'REFRESH_TOKEN':'private'}}]}
        cleaned=json.loads(json.dumps(redact(value)))
        self.assertEqual(cleaned['tool_started']['7']['name'],'get_context')
        self.assertEqual(cleaned['tool_started']['7']['access_token'],'<REDACTED>')
        self.assertEqual(cleaned['nested'][0]['42']['REFRESH_TOKEN'],'<REDACTED>')
        self.assertEqual(value['tool_started'][7]['access_token'],'private')


if __name__ == "__main__":
    unittest.main()
