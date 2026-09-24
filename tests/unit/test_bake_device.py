"""Bake device policy: GPU only when Cycles has an enabled device; CPU otherwise."""

import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def load_devices():
    # Load the policy without importing the add-on package (which needs bpy),
    # the same isolation test_bake_queue uses for a module with imports.
    logging_stub = types.ModuleType("open_painter.utils.logging")
    logging_stub.logger = logging.getLogger("open_painter.devices_test")
    with patch.dict(sys.modules, {
        "open_painter": types.ModuleType("open_painter"),
        "open_painter.baking": types.ModuleType("open_painter.baking"),
        "open_painter.utils": types.ModuleType("open_painter.utils"),
        "open_painter.utils.logging": logging_stub,
    }):
        spec = importlib.util.spec_from_file_location(
            "open_painter.baking.devices_test", ROOT / "src/open_painter/baking/devices.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


devices = load_devices()


class Preferences:
    """A CyclesPreferences stand-in; enumeration is opt-in and counted."""

    def __init__(self, backend, saved=(), count=None):
        self.compute_device_type = backend
        self.devices = [SimpleNamespace(type=kind, use=use) for kind, use in saved]
        self.count = count
        self.enumerations = 0

    def get_num_gpu_devices(self):
        self.enumerations += 1
        if self.count is None:
            raise AssertionError("device enumeration must not run for a saved device list")
        return self.count


def scene(device):
    return SimpleNamespace(cycles=SimpleNamespace(device=device))


class GpuAvailableTests(unittest.TestCase):
    def setUp(self):
        devices._PROBES.clear()

    def live(self, preferences):
        return patch.object(devices, "_cycles_preferences", return_value=preferences)

    def test_enabled_saved_device_answers_without_enumerating(self):
        preferences = Preferences("OPTIX", [("OPTIX", True)])
        with self.live(preferences):
            self.assertTrue(devices.gpu_available())
        self.assertEqual(preferences.enumerations, 0)

    def test_disabled_devices_stay_disabled(self):
        preferences = Preferences("CUDA", [("CUDA", False), ("CUDA", False)])
        with self.live(preferences):
            self.assertFalse(devices.gpu_available())
        self.assertEqual(preferences.enumerations, 0)

    def test_non_gpu_backend_is_cpu(self):
        preferences = Preferences("NONE", [("CUDA", True)])
        with self.live(preferences):
            self.assertFalse(devices.gpu_available())
        self.assertEqual(preferences.enumerations, 0)

    def test_missing_cycles_preferences_is_cpu(self):
        with patch.object(devices, "_cycles_preferences", return_value=None):
            self.assertFalse(devices.gpu_available())

    def test_empty_device_list_probes_once_and_caches(self):
        preferences = Preferences("CUDA", (), count=2)
        with self.live(preferences):
            self.assertTrue(devices.gpu_available())
            self.assertTrue(devices.gpu_available())
        self.assertEqual(preferences.enumerations, 1)

    def test_failed_enumeration_answers_cpu(self):
        preferences = Preferences("ONEAPI", ())
        with self.live(preferences), self.assertLogs("open_painter.devices_test", level="WARNING") as logs:
            self.assertFalse(devices.gpu_available())
        self.assertTrue(any("Cycles GPU query failed" in line for line in logs.output))

    def test_resolve_prefers_gpu_for_auto_when_available(self):
        preferences = Preferences("OPTIX", [("OPTIX", True)])
        with self.live(preferences):
            self.assertEqual(devices.resolve(scene("CPU"), devices.AUTO), ("GPU", None))
            self.assertEqual(devices.resolve(scene("GPU"), devices.SCENE), ("GPU", None))
            self.assertEqual(devices.resolve(scene("CPU"), devices.SCENE), ("CPU", None))

    def test_resolve_cpu_never_reads_devices(self):
        preferences = Preferences("OPTIX", [("OPTIX", True)])
        with self.live(preferences):
            self.assertEqual(devices.resolve(scene("GPU"), devices.CPU), ("CPU", None))
        self.assertEqual(preferences.enumerations, 0)

    def test_resolve_reports_scene_gpu_fallback(self):
        preferences = Preferences("CUDA", [("CUDA", False)])
        with self.live(preferences):
            device, note = devices.resolve(scene("GPU"), devices.SCENE)
        self.assertEqual(device, "CPU")
        self.assertTrue(note)

    def test_resolve_auto_stays_quiet_without_gpu(self):
        preferences = Preferences("NONE")
        with self.live(preferences):
            device, note = devices.resolve(scene("GPU"), devices.AUTO)
        self.assertEqual(device, "CPU")
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main()
