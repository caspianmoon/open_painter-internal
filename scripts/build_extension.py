"""Build a Blender extension ZIP with its manifest at archive root."""

import argparse
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "blender_manifest.toml").read_text())["version"]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, help="Build a candidate without replacing a published archive")
args = parser.parse_args()
output = args.output or root / "dist" / f"open_painter-{version}.zip"
output.parent.mkdir(exist_ok=True)


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

    content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', rewrite, path.read_text())
    archive.writestr(str(archive_name), content)


with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in sorted((root / "src" / "open_painter").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            write_file(archive, path, path.relative_to(root / "src" / "open_painter"))
    for name in ("blender_manifest.toml", "LICENSE", "README.md", "ARCHITECTURE.md", "SMART_MATERIAL_FORMAT.md", "TESTING.md", "CONTRIBUTING.md", "CHANGELOG.md", "PARITY.md", "docs/substance_parity.json", "docs/channel_blending.png", "docs/reference_layout.png", "docs/repaired_workspace.png", "docs/bottom-shelf.png", "docs/research/improvements-0.1.9.md", "docs/research/performance-0.1.9.json", "docs/research/workflows-0.1.10.md", "docs/research/projection-0.1.11.md", "docs/research/smart-masks-0.1.12.md", "docs/research/native-ui-0.1.13.md", "docs/native-painter.png", "docs/texture-workflow-v1.md", "docs/material-catalog.md", "docs/asset-categories.md", "docs/research/utility-mesh-maps.md", "docs/research/texture-performance-0.2.0.json"):
        if (root / name).exists():
            write_file(archive, root / name, name)
    for path in [root / "docs/implementation-status.md", root / "docs/live-procedural-preview.md",
                 root / "docs/anchor-points.md", root / "docs/milestone-workflows.md",
                 root / "docs/generated-material-quality.md",
                 root / "docs/shader-preview-architecture.md",
                 root / "docs/validation/shader-preview-contact.html",
                 root / "docs/validation/shader-user-project-contact.html",
                 *sorted((root / "docs/validation").glob("shader-*.json")),
                 root / "docs/validation/actual-project-persistence-export.json",
                 root / "docs/material-surface-quality.md",
                 root / "docs/material-review-metals.md",
                 root / "docs/material-review-natural.md",
                 root / "docs/material-relief-metal-review.md",
                 root / "docs/material-relief-industrial-review.md",
                 root / "docs/material-relief-natural-review.md",
                 root / "docs/research/material-relief-source-index.md",
                 root / "docs/research/natural-relief-review.md",
                 root / "docs/research/natural-final-review.json",
                 root / "docs/research/generated-material-prompts.json",
                 root / "docs/research/generated-material-prompts-specialized.json",
                 root / "docs/research/generated-material-refinement.json",
                 *sorted((root / "docs/research").glob("*-relief-prompts.json")),
                 *sorted((root / "docs/research").glob("*-relief-provenance.json")),
                 *sorted((root / "docs/validation").glob("generated-material-*.json")),
                 *sorted(p for p in (root / "docs/validation").glob("material-relief-*.json")
                         if p.name != "material-relief-release.json"),
                 root / "docs/live-preview-latency.md", root / "docs/live-preview-latency-research.md",
                 root / "docs/bake-queue-fix.md", *sorted((root / "docs/validation").glob("bake-queue-*.json")),
                 *sorted((root / "docs/performance").glob("*"))]:
        if path.is_file():
            write_file(archive, path, path.relative_to(root))
print(output)
