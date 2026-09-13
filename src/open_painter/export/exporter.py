"""Color-managed beauty export and raw data export with collision protection."""

import re
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np

from ..core import image_manager as images
from ..evaluation.blend_modes import sample_uv
from ..evaluation.evaluator import LayerEvaluator
from .channel_pack import pack
from .image_writer import write
from .presets import blender, custom, unity, unreal
from .presets import godot,gltf,unity_urp,unity_hdrp


def safe_name(value: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", value, flags=re.UNICODE).strip("._") or "TextureSet"


def filename(ts_name, channel, extension, prefix="", suffix=""):
    return f"{safe_name(prefix + ts_name)}_{safe_name(channel + suffix)}.{extension}"


def export(context: bpy.types.Context, ts, settings) -> list[Path]:
    if not settings.directory:
        raise ValueError("Choose an export directory.")
    directory = Path(bpy.path.abspath(settings.directory))
    definitions = (
        custom.outputs(settings)
        if settings.preset == "CUSTOM"
        else {"BLENDER": blender, "UNREAL": unreal, "UNITY": unity, 'GODOT':godot,
              'GLTF':gltf,'UNITY_URP':unity_urp,'UNITY_HDRP':unity_hdrp}[settings.preset].outputs()
    )
    extension = "png" if settings.file_format == "PNG" else "tif"
    destinations = [
        directory / filename(ts.name, key, extension, settings.prefix, settings.suffix)
        for key, _ in definitions
    ]
    if any(p.exists() for p in destinations):
        raise ValueError("Export files already exist. Choose another folder, prefix or suffix.")
    from ..painting.paint_session import detect

    detect(context.scene, force=True)
    LayerEvaluator(context.scene).evaluate_texture_set(ts.id)
    n = int(settings.resolution) if settings.resolution != "SET" else int(ts.resolution)
    cache = {}

    def channel(key):
        if key not in cache:
            image = next((o.image for o in ts.outputs if o.channel == key), None)
            if image is None:
                raise ValueError(f"Output channel {key} is missing. Refresh the texture set.")
            value = images.pixels(image)
            if value.shape[0] != n:
                scaled = np.empty((n, n, 4), dtype=np.float32)
                for start in range(0, n, 128):
                    u, v = np.meshgrid(
                        (np.arange(n, dtype=np.float32) + 0.5) / n,
                        (np.arange(start, min(n, start + 128), dtype=np.float32) + 0.5) / n,
                    )
                    scaled[start : start + len(v)] = sample_uv(value, u, v)
                value = scaled
            cache[key] = value
        return cache[key]

    created = []
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="open-painter-export-") as temporary:
            staged = []
            for (label, definition), destination in zip(definitions, destinations):
                if isinstance(definition, tuple):
                    for key in definition:
                        key = key.removeprefix("ONE_MINUS_")
                        if key not in {"ZERO", "ONE"}:
                            channel(key)
                    values = pack(cache, definition)
                elif definition == 'BASE_COLOR_OPACITY':
                    values=channel('BASE_COLOR').copy()
                    values[...,3]=channel('OPACITY')[...,0]
                elif definition == "NORMAL_DX":
                    values = channel("NORMAL").copy()
                    values[..., 1] = 1 - values[..., 1]
                else:
                    values = channel(definition)
                path = Path(temporary) / destination.name
                # Image arrays use Blender's bottom-left origin; files use top-left.
                write(
                    path,
                    values[::-1],
                    settings.file_format,
                    int(settings.bit_depth),
                    definition in {"BASE_COLOR","EMISSION",'BASE_COLOR_OPACITY'} if isinstance(definition,str) else False,
                )
                staged.append(path)
                cache.clear()
            for source, destination in zip(staged, destinations):
                with destination.open("xb") as out:
                    created.append(destination)
                    out.write(source.read_bytes())
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return destinations
