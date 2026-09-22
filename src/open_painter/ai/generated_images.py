"""Bounded native Codex image artifacts. No Blender imports or model-chosen paths."""

import base64
import hashlib
import re
import struct
import time
from pathlib import Path

MAX_IMAGES = 8
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_SERVER_MESSAGE = 32 * 1024 * 1024
# Longer than the longest turn, so another Blender sharing the state
# directory never loses a texture its own instruction is still using.
PRUNE_AGE = 24 * 60 * 60


def inspect(data):
    if len(data) > MAX_IMAGE_BYTES or len(data) < 33 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError("Codex must return a PNG texture smaller than 16 MiB")
    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise ValueError("Generated texture dimensions must not exceed 4096 pixels")
    return width, height


def store(state_directory, item):
    """Use the inline result; never open a path supplied by the model/runtime."""
    result = item.get("result", "")
    if not isinstance(result, str) or len(result) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("Generated texture exceeds the image size limit")
    try:
        data = base64.b64decode(result, validate=True)
    except ValueError:
        raise ValueError("Codex returned an invalid image") from None
    width, height = inspect(data)
    digest = hashlib.sha256(data).hexdigest()
    directory = Path(state_directory).resolve() / "painter_textures"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Generated texture cache must be a private directory")
    path = directory / (digest + ".png")
    if path.is_symlink():
        raise ValueError("Generated texture cache entry is not a regular file")
    path.write_bytes(data)
    path.chmod(0o600)
    return dict(image_id="generated:" + digest, width=width, height=height,
                prompt=(item.get("revisedPrompt") or "")[:5000])


def _path(state_directory, identifier):
    if not re.fullmatch(r"generated:[0-9a-f]{64}", identifier):
        raise ValueError("Unknown generated texture ID")
    return Path(state_directory).resolve() / "painter_textures" / (identifier.split(":")[1] + ".png")


def read(state_directory, metadata):
    identifier = metadata.get("image_id", "")
    path = _path(state_directory, identifier)
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError("Generated texture is missing; generate it again")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Generated texture exceeds the image size limit")
    data = path.read_bytes()
    width, height = inspect(data)
    if hashlib.sha256(data).hexdigest() != identifier.split(":")[1] or (width, height) != (metadata["width"], metadata["height"]):
        raise ValueError("Generated texture changed after creation; generate it again")
    return data


def discard(entries):
    """Once the owning request ends, applied pixels are packed in the .blend
    and unapplied ones are unwanted; the cache copies would otherwise stay forever."""
    for entry in entries:
        try:
            path = _path(entry["state_directory"], entry.get("image_id", ""))
            if not path.is_symlink():
                path.unlink(missing_ok=True)
        except (KeyError, ValueError, OSError):
            continue  # Housekeeping never fails the apply that triggered it.


def prune(state_directory, keep=(), *, now=None):
    """Textures left by interrupted turns age out on the next connection."""
    directory = Path(state_directory).resolve() / "painter_textures"
    if directory.is_symlink() or not directory.is_dir():
        return
    kept = {identifier.split(":")[-1] for identifier in keep}
    now = time.time() if now is None else now
    for path in directory.glob("*.png"):
        try:
            if path.stem in kept or path.is_symlink() or not path.is_file():
                continue
            if now - path.stat().st_mtime > PRUNE_AGE:
                path.unlink(missing_ok=True)
        except OSError:
            continue
