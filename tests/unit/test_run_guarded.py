"""The memory guard kills a process tree past its cap, passes exit codes through and runs one
run at a time (scripts/run_guarded.py; macOS and Windows)."""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts/run_guarded.py"

# The child starts a grandchild, which records its pid, then holds `megabytes` MiB in written
# 64 MiB steps (resident on every platform) and sleeps; the child waits for it.
TREE = """
import subprocess, sys
grandchild = '''
import os, sys, time
open(sys.argv[1], "w", encoding="utf-8").write(str(os.getpid()))
held = []
for _ in range(int(sys.argv[2]) // 64):
    held.append(b"\\\\x01" * (64 << 20))
    time.sleep(0.05)
time.sleep(60)
'''
sys.exit(subprocess.call([sys.executable, "-c", grandchild, sys.argv[1], sys.argv[2]]))
"""


def alive(pid):
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)   # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@unittest.skipUnless(sys.platform in ("darwin", "win32"), "the guard measures macOS and Windows processes")
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.env = dict(os.environ, OP_GUARD_LOCK=str(self.root / "guard.lock"))

    def tearDown(self):
        self.scratch.cleanup()

    def guard(self, *options, command, timeout=120):
        completed = subprocess.run([sys.executable, str(GUARD), "--start-gb", "0", "--floor-gb", "0", *options,
                                    "--", *command], env=self.env, capture_output=True, text=True,
                                   timeout=timeout)
        summaries = [line.split(" ", 1)[1] for line in completed.stdout.splitlines()
                     if line.startswith("GUARD_SUMMARY ")]
        return completed, json.loads(summaries[0]) if summaries else None

    def test_a_tree_past_the_cap_is_killed_with_its_descendants(self):
        pid_file = self.root / "grandchild.pid"
        started = time.monotonic()
        completed, summary = self.guard("--cap-gb", "1", command=[sys.executable, "-c", TREE, str(pid_file), "1536"])
        self.assertEqual(completed.returncode, 137, completed.stdout + completed.stderr)
        self.assertIn("GUARD_KILLED footprint passed 1.0 GiB", completed.stdout)
        self.assertTrue(summary["killed"])
        self.assertEqual(summary["exit"], 137)
        self.assertGreater(summary["peak_gib"], 1.0)
        self.assertLess(summary["peak_gib"], 1.5)   # killed on the way, before the 1.5 GiB it wanted
        self.assertIn("1", summary["gib_reached_at_s"])
        self.assertLess(time.monotonic() - started, 50)   # not the grandchild's 60 s sleep
        grandchild = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(grandchild), "the grandchild outlived the kill")
        if sys.platform == "win32":
            self.assertIn("dedicated GPU memory is not included", summary["measure"])

    def test_a_run_past_its_timeout_is_killed_with_its_descendants(self):
        pid_file = self.root / "grandchild.pid"
        started = time.monotonic()
        completed, summary = self.guard("--timeout-seconds", "3",
                                        command=[sys.executable, "-c", TREE, str(pid_file), "64"])
        self.assertEqual(completed.returncode, 137, completed.stdout + completed.stderr)
        self.assertIn("GUARD_KILLED still running after 3 s", completed.stdout)
        self.assertEqual((summary["killed"], summary["exit"]), (True, 137))
        self.assertGreaterEqual(summary["seconds"], 3)
        self.assertLess(time.monotonic() - started, 30)   # not the grandchild's 60 s sleep
        grandchild = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(grandchild), "the grandchild outlived the kill")

    def test_exit_code_passes_through(self):
        completed, summary = self.guard(command=[sys.executable, "-c", "import sys; sys.exit(3)"])
        self.assertEqual(completed.returncode, 3, completed.stdout + completed.stderr)
        self.assertNotIn("GUARD_KILLED", completed.stdout)
        self.assertEqual((summary["killed"], summary["exit"], summary["reason"]), (False, 3, ""))
        self.assertLess(summary["peak_gib"], 1.0)

    def test_a_second_run_is_refused_while_one_holds_the_lock(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import run_guarded
        finally:
            sys.path.remove(str(ROOT / "scripts"))
        held = run_guarded.lock(self.root / "guard.lock")
        self.assertIsNotNone(held)
        try:
            completed, summary = self.guard(command=[sys.executable, "-c", "pass"])
            self.assertEqual(completed.returncode, 75)
            self.assertIn("GUARD_REFUSED another guarded run is active", completed.stdout)
            self.assertIsNone(summary)
        finally:
            held.close()
        completed, summary = self.guard(command=[sys.executable, "-c", "pass"])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_too_little_available_memory_refuses_to_start(self):
        completed = subprocess.run([sys.executable, str(GUARD), "--start-gb", "100000", "--",
                                    sys.executable, "-c", "raise SystemExit(9)"],
                                   env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 75)
        self.assertIn("GUARD_REFUSED only", completed.stdout)
        self.assertNotIn("GUARD_SUMMARY", completed.stdout)


if __name__ == "__main__":
    unittest.main()
