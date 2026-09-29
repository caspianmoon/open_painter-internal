"""Gate 0 environment + artifact collector (no Blender required).

Records the machine and source identity needed before any native-bridge
claim. Run from ordinary Python::

    python scripts/collect_gate0_baseline.py --output .blender-test/gate0/env.json
    python scripts/collect_gate0_baseline.py --blender C:/Blender/blender.exe \
        --output .blender-test/gate0/env.json

The python digest matches scripts/benchmark_windows_viewport.py::source_digest
(src/open_painter/**/*.py). The extended digest additionally covers the GPU
shader sources, the Blender manifest/build files, and any future native
adapter or binary artifacts. Absent native artifacts are recorded as absent;
they must not be faked.
"""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Shader/compiler sources whose change invalidates a benchmark comparison.
SHADER_SOURCES = [
    Path("src/open_painter/evaluation/engine/glsl.py"),
    Path("src/open_painter/evaluation/engine/procedural_glsl.py"),
    Path("src/open_painter/evaluation/engine/compiler.py"),
    Path("src/open_painter/evaluation/engine/runtime.py"),
    Path("src/open_painter/evaluation/engine/formats.py"),
]

BUILD_FILES = [
    Path("blender_manifest.toml"),
    Path("pyproject.toml"),
    Path("libopenpainter/CMakeLists.txt"),
]

# Future adapter locations (do not exist in P0 scaffolding; recorded absent).
ADAPTER_FILES = [
    Path("src/open_painter/native_bridge/loader.py"),
    Path("src/open_painter/native_bridge/capabilities.py"),
]


def digest_paths(root, paths):
    digest = hashlib.sha256()
    for rel in sorted(paths):
        full = root / rel
        digest.update(rel.as_posix().encode())
        if full.is_file():
            digest.update(full.read_bytes())
        else:
            digest.update(b"<absent>")
    return digest.hexdigest()


def digest_glob(root, pattern):
    files = [p.relative_to(root) for p in root.glob(pattern) if p.is_file()]
    return digest_paths(root, files), len(files)


def artifact_digests(root, native_library=None, build_cache=None):
    """Identity, not compatibility: absent artifacts remain explicitly null."""
    python_digest, count = digest_glob(root, "src/open_painter/**/*.py")
    adapter, _ = digest_glob(root, "src/open_painter/native_bridge/**/*.py")
    native_sources = [p.relative_to(root) for p in (root / "libopenpainter").rglob("*")
                      if p.is_file() and p.suffix in {".c", ".h", ".inc", ".glsl"} and "build" not in p.parts]
    result = {
        "python_addon_sha256": python_digest, "python_file_count": count,
        "adapter_sha256": adapter,
        "shader_bundle_sha256": digest_paths(root, SHADER_SOURCES +
                                               [Path("libopenpainter/src/gpu/probe.c"), Path("libopenpainter/src/shaders/catalog.inc"),
                                                Path("libopenpainter/src/shaders/fixed.inc"),
                                                Path("libopenpainter/src/shaders/blend.inc"),
                                                Path("libopenpainter/src/shaders/effects.inc")]),
        "native_source_sha256": digest_paths(root, native_sources),
        "fixture_source_sha256": digest_paths(root, [Path("scripts/native_fixtures.py"),
                                                     Path("scripts/benchmark_windows_viewport.py")]),
        "build_files_sha256": digest_paths(root, BUILD_FILES),
        "native_binary": None, "python_binding": None, "build_options_sha256": None,
    }
    if native_library is not None:
        path = Path(native_library).resolve(strict=True)
        result["native_binary"] = {"path": str(path), "bytes": path.stat().st_size,
                                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        binding = path.parent / ('_native.pyd' if path.suffix == '.dll' else '_native.abi3.so')
        if binding.is_file():
            result['python_binding'] = {'path': str(binding), 'bytes': binding.stat().st_size,
                                        'sha256': hashlib.sha256(binding.read_bytes()).hexdigest()}
    if build_cache is not None:
        result["build_options_sha256"] = hashlib.sha256(Path(build_cache).read_bytes()).hexdigest()
    return result


def git_info(root):
    def run(*args):
        try:
            out = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=15
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "status_short": run("status", "--short"),
    }


def blender_version(blender):
    if not blender:
        return None
    try:
        out = subprocess.run(
            [str(blender), "--version"], capture_output=True, text=True, timeout=30
        )
        text = (out.stdout or "") + (out.stderr or "")
        return text.strip().splitlines()[0] if text.strip() else None
    except Exception as exc:
        return f"<error: {exc}>"


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--blender", type=Path, default=None)
    parser.add_argument("--native-library", type=Path)
    parser.add_argument("--build-cache", type=Path)
    parser.add_argument("--package", type=Path, help="Exact installed ZIP for package identity")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.source_root = args.source_root.resolve()
    args.output = args.output.resolve()
    return args


def main():
    args = arguments()
    root = args.source_root
    report = {
        "collector": "collect_gate0_baseline.py",
        "measurement_note": "Environment + artifact identity only; not a latency measurement.",
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "blender_arg": str(args.blender) if args.blender else None,
        "blender_version": blender_version(args.blender),
        "git": git_info(root),
        "digests": artifact_digests(root, args.native_library, args.build_cache),
        "installed_package_sha256": (hashlib.sha256(args.package.read_bytes()).hexdigest()
                                      if args.package else None),
        "target_windows_reproduction": "unavailable on this host; no improvement claim",
        "gate0_fields_still_manual": [
            "GPU identity / driver / active backend (run inside Blender)",
            "display refresh rate / power mode / viewport dimensions",
            "paired baseline timings + visual recording",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("GATE0_BASELINE_COLLECTED", args.output, flush=True)


if __name__ == "__main__":
    main()
