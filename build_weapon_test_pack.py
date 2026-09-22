#!/usr/bin/env python3
"""Download the two agreed GGBotNet models and package the simple Eevee test guide.

Requires Python 3.9+ and internet access. No third-party dependencies.
Usage: python3 build_weapon_test_pack.py
Output: ./Open_Painter_Weapon_Test.zip

Does not launch Blender, execute model files, alter the repository, or run AI tests.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

ASSETS = (
    {
        "name": "Soviet Assault Rifle 3D", "folder": "assault_rifle",
        "author": "GGBotNet", "license": "CC0 1.0 Universal",
        "page": "https://opengameart.org/content/ak-47-3d",
        "url": "https://opengameart.org/sites/default/files/soviet_assault_rifle_3d.zip",
    },
    {
        "name": "SMG-10 3D", "folder": "smg",
        "author": "GGBotNet", "license": "CC0 1.0 Universal",
        "page": "https://opengameart.org/content/smg-10-3d",
        "url": "https://opengameart.org/sites/default/files/smg-10_3d.zip",
    },
)
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MODEL_SUFFIXES = {".blend", ".obj", ".fbx", ".gltf", ".glb"}
INCLUDE_SUFFIXES = MODEL_SUFFIXES | {".bin", ".mtl", ".txt", ".md", ".pdf"}

GUIDE = """# Open Painter: two-weapon texturing test

## Goal
Can Material Director texture a real game weapon convincingly from a short art-direction prompt?

## Models
Use the included assault rifle and SMG. Test them separately. The original downloads are also preserved in `sources/`.
These are source assets, not prepared benchmark scenes. UVs and Blender compatibility still need preflight verification.

## Prepare once, before the AI test
Import only each weapon's meshes into a fresh file. Preserve geometry, usable UVs and existing material boundaries; use neutral materials and neutral part names. If UVs need preparation, do that once and record it before freezing the starting file. Do not invent material-region masks for Director.
Use the existing fixed **Eevee** harness. No Cycles. Keep cameras, lights, World, resolution, exposure and render settings unchanged throughout. Check that the neutral model is visible before running any AI prompt.
Keep the selected AI model and generated-texture setting identical for both weapons. Generated surface textures are enabled for this test; record whether they actually get generated and assigned.

## Run the same three prompts on each weapon
Start a fresh Director conversation from that weapon's neutral file.

**1. Create**
> Texture this like it belongs in a realistic post-apocalyptic game.

**2. Refine the existing result**
> This doesn't look realistic enough. Improve it.

**3. Refine it again**
> Make it look like it was textured by a professional game artist. Remove anything that looks obviously procedural or artificial.

Do not add material recipes, roughness values, rust instructions or semantic masks to these prompts. Material interpretation and art direction are the capabilities under test. Do not restart between the three prompts or manually repair the AI material. These follow-ups are a fixed challenge, not a claim that each previous result is necessarily bad.

## Save the evidence
Save neutral, initial, refinement 1 and refinement 2 as separate `.blend` checkpoints. At each checkpoint, capture the same fixed Eevee front, back, left, right and close-up views. Keep the prompts, responses and errors with the images. Package the actual images with the gallery, not just relative links in a lone HTML file.
If Create applies nothing, record `NO_MATERIAL_APPLIED`, preserve the response and capture the failure state when possible. Mark later turns `SKIPPED_AFTER_CREATE_FAILURE`. A failed screenshot is `CAPTURE_FAILED`, not an artistic result or a successful test.

## Review side by side
For each weapon, compare the initial material with both revisions. Judge appearance, believable material separation, placement of wear/detail, art-direction fit and whether each revision helped or harmed the result. Check that material components remain independently editable.
Ask: **Would I use this in a realistic game without manually retexturing it?** Record yes/no and a short explanation. No named reviewer or custom human-authored reference file is required to run this development test. Keep unreviewed results unscored.
The two weapons can share a palette and surface sources. That is not a failure. Look for geometry-appropriate placement and scale, rather than demanding different materials everywhere.
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = Request(url, headers={
                "User-Agent": "OpenPainterWeaponTestPack/1.0",
                "Accept": "application/zip, application/octet-stream;q=0.9, */*;q=0.5",
            })
            with urlopen(request, timeout=45) as response:
                if response.getcode() != 200:
                    raise RuntimeError(f"Unexpected HTTP status: {response.getcode()}")
                data = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise ValueError("Archive exceeds the 25 MiB safety limit")
            if not zipfile.is_zipfile(io.BytesIO(data)):
                raise ValueError("Server returned something other than a ZIP archive")
            return data
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                print(f"Download attempt {attempt + 1} failed: {exc}. Retrying...", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Could not retrieve {url}: {last_error}")


def unpack_models(data: bytes, destination: Path) -> list[dict]:
    """Preserve source filenames. Reject unsafe archives instead of executing anything."""
    records: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 500:
            raise ValueError("Unexpectedly large number of archive members")
        if sum(entry.file_size for entry in entries) > MAX_EXTRACTED_BYTES:
            raise ValueError("Uncompressed archive exceeds the 100 MiB safety limit")
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            if (relative.is_absolute() or ".." in relative.parts or
                    any(":" in part for part in relative.parts) or "\x00" in name):
                raise ValueError(f"Unsafe archive path: {name!r}")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError(f"Archive symlink is not allowed: {name}")
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"Archive CRC verification failed: {bad_member}")
        for entry in entries:
            relative = PurePosixPath(entry.filename.replace("\\", "/"))
            if entry.is_dir() or "__MACOSX" in relative.parts or relative.name.startswith("."):
                continue
            if relative.suffix.lower() not in INCLUDE_SUFFIXES:
                continue
            payload = archive.read(entry)
            path = destination.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            records.append({"path": str(relative), "bytes": len(payload), "sha256": sha256(payload)})
    if not any(PurePosixPath(record["path"]).suffix.lower() in MODEL_SUFFIXES for record in records):
        raise ValueError("Downloaded ZIP contains no supported 3D model")
    return records


def build(output: Path) -> Path:
    output = output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {output}. Choose another --output path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="open-painter-weapons-", dir=output.parent) as temporary:
        work = Path(temporary)
        root = work / "open_painter_weapon_test"
        (root / "sources").mkdir(parents=True)
        manifest = {"assets": [], "uv_validation": "NOT_RUN", "blender_validation": "NOT_RUN",
                    "live_ai_test": "NOT_RUN", "notes": "Source model packaging only. No invented assets."}
        for asset in ASSETS:
            print(f"Downloading {asset['name']}...", flush=True)
            data = fetch(asset["url"])
            archive_name = asset["url"].rsplit("/", 1)[1]
            (root / "sources" / archive_name).write_bytes(data)
            records = unpack_models(data, root / asset["folder"])
            manifest["assets"].append({**asset, "source_archive_sha256": sha256(data),
                                       "source_archive_bytes": len(data), "files": records})
            print(f"Verified archive and extracted {len(records)} model/support files.", flush=True)
        (root / "TEST_GUIDE.md").write_text(GUIDE, encoding="utf-8")
        license_text = "# Sources and license\n\nBoth source pages list CC0 1.0 Universal. Creator: GGBotNet.\n\n"
        for asset in ASSETS:
            license_text += f"## {asset['name']}\n\nSource: {asset['page']}\nArchive: {asset['url']}\n\n"
        license_text += "License reference: https://creativecommons.org/publicdomain/zero/1.0/\n\nOriginal model bytes are preserved; this builder does not prepare UVs, texture models or run Blender.\n"
        (root / "SOURCES_AND_LICENSE.md").write_text(license_text, encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        temporary_zip = work / "verified-pack.zip"
        with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(work).as_posix())
        with zipfile.ZipFile(temporary_zip) as archive:
            if archive.testzip() is not None:
                raise ValueError("Final ZIP failed CRC verification")
        # Exclusive creation: never replace an existing user file, including a race.
        with output.open("xb") as destination, temporary_zip.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("Open_Painter_Weapon_Test.zip"))
    args = parser.parse_args()
    try:
        path = build(args.output)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}\nNo completed model pack was produced.", file=sys.stderr)
        return 1
    print(f"Created {path}\nContains both actual model downloads, extracted files, license sources and TEST_GUIDE.md.")
    print("Model imports and UV suitability have not been tested. Follow the guide's preparation step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
