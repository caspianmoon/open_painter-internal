"""Build a Blender extension ZIP with its manifest at archive root."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import tomllib
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
# The guides a release package carries (docs/): what an artist reads, not development records.
GUIDES = ("user-guide.md", "texture-workflow-v1.md", "anchor-points.md", "asset-categories.md",
          "bake-doctor.md", "bake-diagnostics.md", "damaged-bevel.md", "decal-objects.md",
          "geometry-effects.md", "gpu-authoring-tools.md", "layer-performance.md", "mask-architecture.md",
          "mask-graphs.md", "master-controls.md", "material-aging.md", "material-catalog.md",
          "material-director.md", "material-modules.md", "mesh-masks.md", "open-graph.md",
          "open-painter-engine.md", "physical-scale.md", "scene-fields.md", "surface-filters.md",
          "target-renderer.md", "udim-workflow.md", "vector-strokes.md")
version = tomllib.loads((root / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, help="Build a candidate without replacing a published archive")
parser.add_argument("--native-library", type=Path,
                    help="Bundle a tested library for this machine's platform and architecture")
args = parser.parse_args()
output = args.output or root / "dist" / f"open_painter-{version}.zip"
output.parent.mkdir(parents=True, exist_ok=True)
native = None
if args.native_library:
    native = args.native_library.resolve(strict=True)
    # Building a package is a developer action. Check the actual binary ABI,
    # rather than trusting a sidecar supplied with an arbitrary artifact.
    import sys
    sys.path.insert(0, str(root / 'src'))
    from open_painter.native_bridge.loader import load_library
    from open_painter.native_bridge.core import OP_ABI_VERSION
    lib, _ = load_library(native)
    for symbol in ('op_graph_create', 'op_graph_replace', 'op_graph_evaluate', 'op_graph_plan_root'):
        getattr(lib, symbol)
    systems = {'Darwin': 'macos', 'Windows': 'windows', 'Linux': 'linux'}
    arches = {'arm64': 'arm64', 'aarch64': 'arm64', 'amd64': 'x64', 'x86_64': 'x64'}
    package_platform = systems[platform.system()] + '-' + arches[platform.machine().lower()]
    bundle_dir = Path('native_bridge/bin') / f'{platform.system().lower()}-{platform.machine().lower()}'
    binding = native.parent / ('_native.pyd' if os.name == 'nt' else '_native.abi3.so')
    if not binding.is_file():
        raise RuntimeError('A native release requires the compiled CPython binding beside the library')
    # What the bundle runs by default (lifecycle.host_backends()): the host scheduler on Metal,
    # and on Windows' Vulkan and OpenGL; elsewhere the CPU kernels on OpenGL (Vulkan schedules).
    execution = {'Darwin': 'c17-host-metal', 'Windows': 'c17-host-vulkan-opengl'}.get(platform.system(),
                                                                                  'c17-cpu-kernels')
    native_manifest = dict(binding_sha256=hashlib.sha256(binding.read_bytes()).hexdigest(), python_abi='cp311-abi3', abi_version=OP_ABI_VERSION, sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
                           platform=package_platform, execution=execution)


def write_file(archive, path, archive_name):
    """Keep documentation links valid after flattening the source package."""
    if path.suffix != '.md':
        archive.write(path, archive_name)
        return
    source_root = root / 'src/open_painter'
    local_reviews = {root / 'docs/material-surface-review.html',
                     root / 'docs/validation/material-relief-release.json'}

    def rewrite(match):
        label, destination = match.groups()
        url = urlsplit(destination)
        if url.scheme or url.netloc or not url.path:
            return match.group(0)
        target = (path.parent / url.path).resolve()
        if target in local_reviews:
            return f'{label} (local workspace artifact: `{target.relative_to(root)}`)'
        if target.is_relative_to(source_root):
            packaged_target = target.relative_to(source_root)
            relative = Path(os.path.relpath(packaged_target, Path(archive_name).parent)).as_posix()
            return f'[{label}]({urlunsplit(("", "", relative, url.query, url.fragment))})'
        return match.group(0)

    content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', rewrite, path.read_text(encoding="utf-8"))
    archive.writestr(Path(archive_name).as_posix(), content)


with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in sorted((root / "src" / "open_painter").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            write_file(archive, path, path.relative_to(root / "src" / "open_painter"))
    for name in ("blender_manifest.toml", "LICENSE", "NOTICE.md", "README.md", "ARCHITECTURE.md",
                 "SMART_MATERIAL_FORMAT.md", "TESTING.md", "CONTRIBUTING.md", "CHANGELOG.md"):
        if name == 'blender_manifest.toml' and native:
            content = (root / name).read_text(encoding='utf-8')
            content = content.replace('[permissions]', f'platforms = ["{package_platform}"]\n\n[permissions]')
            archive.writestr(name, content)
        else:
            write_file(archive, root / name, Path(name))
    if native:
        archive.write(native, (bundle_dir / native.name).as_posix())
        archive.write(binding, (bundle_dir / binding.name).as_posix())
        archive.writestr((bundle_dir / (native.name + '.json')).as_posix(),
                         json.dumps(native_manifest, indent=2) + '\n')
    # The user-facing guides and their images; validation reports, performance records and
    # research notes stay in the repository (a release package carries no development artefacts).
    for name in GUIDES:
        write_file(archive, root / "docs" / name, Path("docs") / name)
    for name in ("docs/native-painter.png", "docs/open_graph.png", "docs/vector_strokes.png"):
        write_file(archive, root / name, Path(name))
print(output)
