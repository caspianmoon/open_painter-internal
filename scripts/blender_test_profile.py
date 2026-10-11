"""Explicit Blender test resources and a fail-closed configuration-path guard.

BLENDER_USER_RESOURCES alone can fall back to a real user profile when the
expected subdirectories do not exist. Set each resource override before
launching Blender, then verify its resolved CONFIG path before doing any work.
This helper stays beside the launchers so saved add-on source trees need not
contain a matching copy of the test infrastructure.
"""

import os
from pathlib import Path


RESOURCE_DIRS = {
    "BLENDER_USER_CONFIG": "config",
    "BLENDER_USER_SCRIPTS": "scripts",
    "BLENDER_USER_EXTENSIONS": "extensions",
    "BLENDER_USER_DATAFILES": "datafiles",
}


def isolated_environment(profile, inherited=None):
    """Create a profile and replace inherited resource overrides in a copy."""
    root = Path(profile).resolve()
    environment = dict(os.environ if inherited is None else inherited)
    environment["BLENDER_USER_RESOURCES"] = str(root)
    for variable, directory in RESOURCE_DIRS.items():
        target = root / directory
        target.mkdir(parents=True, exist_ok=True)
        if not target.resolve().is_relative_to(root):
            raise RuntimeError(f"Test resource escapes its profile: {target}")
        environment[variable] = str(target)
    # Cycles on Metal doubles its path states twice when the GPU's working set has room:
    # 6 GiB and more for a cube's bake on an 18 GB Mac, past the memory guard's cap. Tests
    # keep the base count, which M1 Macs always use; a caller can still set it.
    environment.setdefault("CYCLES_METAL_GROW_PERCENT", "0")
    return environment


def assert_blender_profile(bpy, profile, environment=None):
    """Reject fallback to a user's real CONFIG before a test runs.

    Call this before importing/registering the add-on, initializing the GPU or
    executing a test. An explicit expected path is required independently of
    the environment, so a stale inherited override cannot certify itself.
    """
    if profile is None:
        raise RuntimeError("An explicit isolated test profile is required")
    root = Path(profile).resolve()
    environment = os.environ if environment is None else environment
    expected = {"BLENDER_USER_RESOURCES": root}
    expected.update({key: root / value for key, value in RESOURCE_DIRS.items()})
    for variable, target in expected.items():
        value = environment.get(variable)
        if not value or Path(value).resolve() != target:
            raise RuntimeError(f"Test profile override mismatch: {variable}")
        if not target.is_dir() or not target.resolve().is_relative_to(root):
            raise RuntimeError(f"Missing or escaped test resource directory: {target}")
    resolved = bpy.utils.user_resource("CONFIG", create=True)
    if not resolved or Path(resolved).resolve() != root / "config":
        raise RuntimeError(
            f"Blender CONFIG escaped the requested test profile: {resolved!r}; "
            f"expected {str(root / 'config')!r}")
    return {"root": str(root), "configuration": str(Path(resolved).resolve()),
            "resource_overrides": {key: str(value) for key, value in expected.items()},
            "configuration_verified": True}
