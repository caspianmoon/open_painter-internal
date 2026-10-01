"""Download the exact public CC0 inputs; no provider calls and no Blender required.

python tests/benchmarks/material_director/assets.py --download --assets A01,A04
Files are pinned by SHA-256 after their first acquisition. Later runs verify the
pin and refuse changed content; use a new manifest for a new benchmark edition.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import struct
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEFAULT_ROOT = ROOT / ".blender-test/material-director-benchmark/assets"
DEFAULT_MANIFEST = HERE / "asset_manifest.json"
HOSTS = {"api.polyhaven.com", "dl.polyhaven.org"}


def digest(path, algorithm="sha256"):
    value = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fetch(url, path, *, expected_size=None, md5=None, sha256=None):
    """Official-host-only, atomic, bounded download with integrity verification."""
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in HOSTS:
        raise ValueError(f"Not an official asset endpoint: {url}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_name(path.name + ".partial")
        try:
            subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                            "--proto", "=https", "--proto-redir", "=https", "--retry", "2",
                            "--connect-timeout", "30", "--max-time", "600",
                            "--max-filesize", str(expected_size or 16 * 1024 * 1024),
                            url, "--output", str(temporary)], check=True)
            _verify(temporary, expected_size, md5, sha256)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    _verify(path, expected_size, md5, sha256)
    return {"path": str(path), "size": path.stat().st_size, "sha256": digest(path)}


def _verify(path, size=None, md5=None, sha256=None):
    if size is not None and path.stat().st_size != size:
        raise ValueError(f"Unexpected byte count for {path}")
    for algorithm, expected in (("md5", md5), ("sha256", sha256)):
        if expected and digest(path, algorithm) != expected:
            raise ValueError(f"{algorithm} mismatch for {path}; preserve the frozen manifest")


def acquire(manifest_path=DEFAULT_MANIFEST, asset_root=DEFAULT_ROOT, asset_ids=None):
    manifest_path, asset_root = Path(manifest_path), Path(asset_root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = set(asset_ids or [entry["id"] for entry in manifest["assets"]])
    unknown = selected - {entry["id"] for entry in manifest["assets"]}
    if unknown:
        raise ValueError(f"Unknown assets: {sorted(unknown)}")
    for entry in manifest["assets"]:
        if entry["id"] not in selected:
            continue
        slug, directory = entry["slug"], asset_root / entry["id"]
        if entry.get("files"):
            for item in entry["files"] + entry.get("metadata_files", []):
                fetch(item["url"], asset_root / item["path"], expected_size=item["size"],
                      md5=item.get("provider_md5"), sha256=item["sha256"])
            print("ASSET_VERIFIED", entry["id"], flush=True)
            continue
        metadata = {}
        entry["metadata_files"] = []
        for endpoint in ("files", "info"):
            url = f"https://api.polyhaven.com/{endpoint}/{slug}"
            result = fetch(url, directory / f"{slug}.{endpoint}.json")
            metadata[endpoint] = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            result.update(path=str(Path(result["path"]).relative_to(asset_root)), url=url)
            entry["metadata_files"].append(result)
        entry["creators"] = metadata["info"].get("authors", {})
        entry["source_metadata"] = {key: metadata["info"].get(key) for key in
                                    ("name", "polycount", "dimensions", "texel_density", "files_hash")}
        if entry["id"] == "R01":
            base = metadata["files"]["col_01"]["1k"]["png"]
            items = [("R01_fabric_reference.png", base)]
        else:
            base = metadata["files"]["blend"]["2k"]["blend"]
            items = [(Path(urlparse(base["url"]).path).name, base)]
            items += list(base.get("include", {}).items())
        entry["files"] = []
        for relative, source in items:
            destination = directory / relative
            if not destination.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Unsafe package filename")
            result = fetch(source["url"], destination, expected_size=source["size"], md5=source.get("md5"))
            result.update(path=str(destination.relative_to(asset_root)), url=source["url"],
                          provider_md5=source.get("md5"))
            entry["files"].append(result)
        entry["downloaded_at"] = datetime.now(timezone.utc).isoformat()
        entry["status"] = "DOWNLOADED_VERIFIED"
        entry["entrypoint"] = entry["files"][0]["path"]
        if entry["id"] == "R01":
            header = (asset_root / entry["entrypoint"]).read_bytes()[:24]
            if header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", header[16:24]) != (1024, 1024):
                raise ValueError("Reference must be the specified 1K PNG")
            entry["dimensions"] = [1024, 1024]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print("ASSET_DOWNLOADED", entry["id"], sum(f["size"] for f in entry["files"]), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", "--assets-root", dest="assets_root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--assets", help="Comma-separated IDs; default all six models and R01")
    parser.add_argument("--download", action="store_true", help="Explicitly allow public asset downloads")
    args = parser.parse_args()
    if not args.download:
        parser.error("Pass --download to acquire public assets")
    acquire(args.asset_manifest, args.assets_root, args.assets.split(",") if args.assets else None)


if __name__ == "__main__":
    main()
