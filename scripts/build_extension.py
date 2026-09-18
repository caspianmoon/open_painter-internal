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
    for name in ("blender_manifest.toml", "LICENSE", "README.md", "ARCHITECTURE.md", "SMART_MATERIAL_FORMAT.md",
                 "TESTING.md", "CONTRIBUTING.md", "CHANGELOG.md", "PARITY.md", "docs/substance_parity.json"):
        write_file(archive, root / name, Path(name))
    # The user-facing guides and their images; validation reports, performance records and
    # research notes stay in the repository (0.12.2: a release package carries no development
    # artefacts).
    for path in sorted(p for p in (root / "docs").iterdir() if p.is_file() and p.suffix == ".md"):
        write_file(archive, path, path.relative_to(root))
    for name in ("docs/native-painter.png", "docs/open_graph.png", "docs/mask_controls.png", "docs/vector_strokes.png"):
        write_file(archive, root / name, Path(name))
print(output)
