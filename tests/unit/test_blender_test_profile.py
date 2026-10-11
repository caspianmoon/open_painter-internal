"""A test launcher must not inherit or accept Blender's real user profile."""

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


HELPER = Path(__file__).resolve().parents[2] / "scripts/blender_test_profile.py"
SPEC = importlib.util.spec_from_file_location("blender_test_profile", HELPER)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


class BlenderTestProfileTests(unittest.TestCase):
    def test_inherited_profile_is_replaced_and_resolved_fallback_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "new-test-profile"
            real = Path(temporary).resolve() / "real-user-profile"
            inherited = {key: str(real) for key in profile.RESOURCE_DIRS}
            inherited.update(BLENDER_USER_RESOURCES=str(real), KEEP_ME="unchanged")
            original = dict(inherited)
            environment = profile.isolated_environment(root, inherited)
            self.assertEqual(inherited, original)
            self.assertEqual(environment["KEEP_ME"], "unchanged")
            self.assertEqual(environment["CYCLES_METAL_GROW_PERCENT"], "0")
            chosen = profile.isolated_environment(root, dict(inherited, CYCLES_METAL_GROW_PERCENT="50"))
            self.assertEqual(chosen["CYCLES_METAL_GROW_PERCENT"], "50")
            self.assertEqual(environment["BLENDER_USER_RESOURCES"], str(root))
            for variable, directory in profile.RESOURCE_DIRS.items():
                self.assertEqual(Path(environment[variable]), root / directory)
                self.assertTrue((root / directory).is_dir())

            resource = Mock(return_value=str(root / "config"))
            bpy = SimpleNamespace(utils=SimpleNamespace(user_resource=resource))
            verified = profile.assert_blender_profile(bpy, root, environment)
            self.assertTrue(verified["configuration_verified"])
            resource.assert_called_once_with("CONFIG", create=True)

            # Correct environment values do not prove that Blender honored them.
            resource.return_value = str(real / "config")
            with self.assertRaisesRegex(RuntimeError, "CONFIG escaped"):
                profile.assert_blender_profile(bpy, root, environment)
            resource.return_value = str(root / "config")
            escaped = dict(environment, BLENDER_USER_SCRIPTS=str(real))
            with self.assertRaisesRegex(RuntimeError, "BLENDER_USER_SCRIPTS"):
                profile.assert_blender_profile(bpy, root, escaped)


if __name__ == "__main__":
    unittest.main()
