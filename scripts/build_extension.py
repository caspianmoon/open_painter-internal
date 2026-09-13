"""Build a Blender extension ZIP with its manifest at archive root."""

from pathlib import Path
import tomllib
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "blender_manifest.toml").read_text())["version"]
output = root / "dist" / f"open_painter-{version}.zip"
output.parent.mkdir(exist_ok=True)
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in sorted((root / "src" / "open_painter").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            archive.write(path, path.relative_to(root / "src" / "open_painter"))
    for name in ("blender_manifest.toml", "LICENSE", "README.md", "ARCHITECTURE.md", "SMART_MATERIAL_FORMAT.md", "TESTING.md", "CONTRIBUTING.md", "CHANGELOG.md", "PARITY.md", "docs/substance_parity.json", "docs/channel_blending.png", "docs/reference_layout.png", "docs/repaired_workspace.png", "docs/bottom-shelf.png", "docs/research/improvements-0.1.9.md", "docs/research/performance-0.1.9.json", "docs/research/workflows-0.1.10.md", "docs/research/projection-0.1.11.md", "docs/research/smart-masks-0.1.12.md", "docs/research/native-ui-0.1.13.md", "docs/native-painter.png", "docs/texture-workflow-v1.md", "docs/material-catalog.md", "docs/research/utility-mesh-maps.md", "docs/research/texture-performance-0.2.0.json"):
        if (root / name).exists():
            archive.write(root / name, name)
print(output)
