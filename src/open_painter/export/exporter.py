"""Color-managed beauty export and raw data export with collision protection.

Order of operations (quality remedy M2A, plan §6.5): the set is evaluated at its own
resolution; the export geometry's ownership (valid texels, islands) is derived at the
source and output resolutions; every channel is resampled with coverage-aware filtering
so empty texels never enter a valid sample; the combined height/normal is derived at the
output resolution; every channel field is extended into the gutters from its nearest
valid texel; then the destination normal convention, packing and encoding happen once.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np

from ..core import image_manager as images
from ..evaluation.blend_modes import sample_uv
from ..evaluation.evaluator import LayerEvaluator
from . import padding
from .channel_pack import pack
from .image_writer import write
from .surface_normal import combined as surface_normal
from .presets import blender, custom, unity, unreal
from .presets import godot,gltf,unity_urp,unity_hdrp

COLOR_DEFINITIONS = {"BASE_COLOR", "EMISSION", "BASE_COLOR_OPACITY"}


def safe_name(value: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", value, flags=re.UNICODE).strip("._") or "TextureSet"


def filename(ts_name, channel, extension, prefix="", suffix=""):
    return f"{safe_name(prefix + ts_name)}_{safe_name(channel + suffix)}.{extension}"


def set_filename(ts, channel, extension, prefix='', suffix='', naming=None):
    """All export entry points share exactly the same UDIM naming contract."""
    number = getattr(ts, 'udim_tile', 0)
    name = ts.udim_name if number else ts.name
    result = naming(name, channel, extension) if naming else filename(name, channel, extension, prefix, suffix)
    if number:
        stem, dot, ext = result.rpartition('.')
        result = f'{stem}.{number}.{ext}' if dot else f'{result}.{number}'
    return result


def definitions_for(settings):
    return (custom.outputs(settings) if settings.preset == "CUSTOM"
            else {"BLENDER": blender, "UNREAL": unreal, "UNITY": unity, 'GODOT': godot,
                  'GLTF': gltf, 'UNITY_URP': unity_urp, 'UNITY_HDRP': unity_hdrp}[settings.preset].outputs())


@dataclass
class Generated:
    """The maps of one set, written to a staging folder, and where they are meant to go."""
    destinations: list
    staged: list
    report: dict = field(default_factory=dict)
    staging: object = None     # the TemporaryDirectory keeping ``staged`` alive

    def close(self):
        if self.staging is not None:
            self.staging.cleanup()
            self.staging = None


def destinations_for(ts, settings, naming=None, directory=None):
    if directory is None:
        if not settings.directory:
            raise ValueError("Choose an export directory.")
        directory = Path(bpy.path.abspath(settings.directory))
    directory = Path(directory)
    extension = "png" if settings.file_format == "PNG" else "tif"
    return [directory / set_filename(ts, key, extension, settings.prefix, settings.suffix, naming)
            for key, _ in definitions_for(settings)]


class _Channels:
    """Evaluated channels of a set at the export resolution: resampled with the geometry's
    coverage, combined with height where the normal asks for it, extended into the gutters."""

    def __init__(self, ts, n, pad):
        self.ts, self.n, self.pad = ts, n, pad
        self.cache, self.raw = {}, {}
        self.report = {"padding": pad, "geometry": False}
        self.source_ownership = self.ownership = None
        self.donor = self.d2 = self.has_data = None
        ready = bool(ts.assignments) and all(ref.object and ref.object.type == 'MESH' for ref in ts.assignments)
        source = next((o.image for o in ts.outputs if o.image is not None), None)
        self.n_source = int(source.size[0]) if source is not None else int(ts.resolution)
        if ready:
            try:
                self.source_ownership = padding.ownership(ts, self.n_source)
                self.ownership = (self.source_ownership if n == self.n_source else padding.ownership(ts, n))
                self.report["geometry"] = True
            except ValueError as error:
                self.report["geometry_error"] = str(error)
        if self.ownership is None:
            self.report["note"] = ("geometry-derived padding is unavailable: assign a mesh with a render UV map "
                                   "or use an explicit tile workflow")

    def _prepare_donors(self, has_data):
        """The shared donor map, once the first channel told which output texels hold data."""
        self.has_data = has_data
        valid = self.ownership.covered & has_data
        self.report.update({"valid_texels": int(valid.sum()), **self.ownership.diagnostics})
        if self.pad:
            if not valid.any():
                self.report["note"] = "no valid texel: nothing to extend into the gutters"
                self.pad = 0
                return
            self.donor, self.d2 = padding.nearest_donors(valid, self.ownership.island, self.pad)
            if not padding.donors_valid(valid, self.donor):
                raise ValueError("UV padding produced a donor outside the valid texels; the export was not published")
            unresolved = padding.unresolved_texels(valid, self.donor)
            if self.pad == "FILL" and unresolved:
                raise ValueError(f"Fill Empty Space left {unresolved} texels without a donor; the export was not published")
            self.report["gutters"] = padding.gutters(self.ownership, self.donor, self.d2, self.pad)
            self.report["padded_texels"] = int((self.donor >= 0).sum())
            self.report["unresolved_texels"] = unresolved

    def resampled(self, key):
        """A channel at the export resolution, before padding (the base of the combined normal)."""
        if key in self.raw:
            return self.raw[key]
        image = next((o.image for o in self.ts.outputs if o.channel == key), None)
        if image is None:
            raise ValueError(f"Output channel {key} is missing. Refresh the texture set.")
        value = images.pixels(image)
        n, source = self.n, value.shape[0]
        if self.source_ownership is not None:
            value, has_data = padding.resample(value, self.source_ownership.covered, n, vector=key == "NORMAL")
            if self.has_data is None:
                self._prepare_donors(has_data)
        elif source > n and source % n == 0:
            # No geometry to tell valid texels apart: the plain box filter of earlier releases.
            factor = source // n
            value = value.reshape(n, factor, n, factor, 4).mean(axis=(1, 3), dtype=np.float32)
            if key == "NORMAL":
                xyz = value[..., :3] * 2 - 1
                xyz /= np.maximum(np.linalg.norm(xyz, axis=-1, keepdims=True), 1e-6)
                value[..., :3] = xyz * .5 + .5
        elif source != n:
            scaled = np.empty((n, n, 4), dtype=np.float32)
            for start in range(0, n, 128):
                u, v = np.meshgrid(
                    (np.arange(n, dtype=np.float32) + 0.5) / n,
                    (np.arange(start, min(n, start + 128), dtype=np.float32) + 0.5) / n,
                )
                # Clamped like the atlas: a repeating fetch bleeds the opposite border
                # into the outermost exported texels of an island flush with the UV edge.
                scaled[start : start + len(v)] = sample_uv(value, u, v, tiling="NONE")
            value = scaled
        if key == "NORMAL":
            # Height relief at the export texel spacing, on the chart-aware metric, before
            # the gutters exist: padded texels would otherwise flatten the border gradient.
            value = surface_normal(self.ts, value, self.resampled("HEIGHT"))
        self.raw[key] = value
        return value

    def __call__(self, key):
        """A finished channel field: resampled, combined, and extended into the gutters."""
        if key not in self.cache:
            value = self.resampled(key)
            if self.donor is not None:
                # Its unpadded field is not read again, except Height's, which the normal's relief
                # samples: extend it in place, the others into a copy.
                value = padding.extend(value, self.donor, in_place=key != "HEIGHT")
            self.cache[key] = value
        return self.cache[key]

    def release(self, key):
        self.cache.pop(key, None)
        if key != "HEIGHT":
            self.raw.pop(key, None)


def generate(context, ts, settings, naming=None, directory=None):
    """Write the set's maps into a fresh staging folder and return them with their destinations.

    Nothing at the destination is touched: publication (``publish`` or the export
    transaction) moves the staged files into place once every map of the request exists.
    """
    destinations = destinations_for(ts, settings, naming, directory)
    definitions = definitions_for(settings)
    from ..painting.paint_session import detect

    detect(context.scene, force=True)
    LayerEvaluator(context.scene).evaluate_texture_set(ts.id)
    n = int(settings.resolution) if settings.resolution != "SET" else int(ts.resolution)
    channels = _Channels(ts, n, padding.effective_padding(settings, n))
    staging = TemporaryDirectory(prefix="open-painter-export-")
    generated = Generated(destinations, [], staging=staging)
    try:
        for (label, definition), destination in zip(definitions, destinations):
            if isinstance(definition, tuple):
                for key in definition:
                    key = key.removeprefix("ONE_MINUS_")
                    if key not in {"ZERO", "ONE"}:
                        channels(key)
                # Semantic channels are padded first; the pack reads finished fields.
                values = pack(channels.cache, definition, shape=(n, n, 4))
            elif definition == 'BASE_COLOR_OPACITY':
                values = channels('BASE_COLOR').copy()
                values[..., 3] = channels('OPACITY')[..., 0]
            elif definition == "NORMAL_DX":
                values = channels("NORMAL").copy()
                values[..., 1] = 1 - values[..., 1]
            else:
                values = channels(definition)
            path = Path(staging.name) / destination.name
            # Image arrays use Blender's bottom-left origin; files use top-left.
            write(path, values[::-1], settings.file_format, int(settings.bit_depth),
                  definition in COLOR_DEFINITIONS if isinstance(definition, str) else False)
            generated.staged.append(path)
            for key in list(channels.cache):
                channels.release(key)
    except Exception:
        generated.close()
        raise
    generated.report = channels.report
    _record_report(ts, settings, n, channels.report)
    return generated


def _record_report(ts, settings, n, report):
    """One line about the gutters for the Export view, and the full record on the set."""
    import json
    pad = report.get("padding")
    if not report.get("geometry"):
        line = "UV padding unavailable: no assigned mesh geometry"
    elif not pad:
        line = "UV padding: none"
    else:
        width = "fill empty space" if pad == "FILL" else f"{pad} px"
        parts = [f"UV padding: {width}", f"{report.get('islands', 0)} islands"]
        gutters = report.get("gutters", {})
        tight = gutters.get("tight_islands", [])
        if tight:
            narrowest = min(gutters["narrowest_gutter_px"].get(i, 0.0) for i in tight)
            parts.append(f"{len(tight)} island(s) closer than {2 * gutters['padding_px']:.0f} px (nearest {narrowest:.0f} px): "
                         f"gutters meet, mips may mix them")
        undersized = report.get("undersized_islands", [])
        if undersized:
            parts.append(f"{len(undersized)} island(s) under {padding.UNDERSIZED_TEXELS} texels at {n} px")
        if report.get("overlap_texels"):
            parts.append(f"{report['overlap_texels']} overlapping texels")
        line = " · ".join(parts)
    try:
        settings.last_report = line
        settings.last_report_detail = json.dumps({k: v for k, v in report.items() if k != "island_texels"}, default=str)
    except (AttributeError, TypeError):
        pass


last_outcome = None   # the transaction outcome of the last publish() (cleanup-pending details)


def publish(generated, overwrite=False):
    """Move staged maps to their destinations as one transaction (``export.transaction``):
    all of them or none, with the previous files restored on failure. Returns the
    destinations; ``last_outcome`` keeps the outcome (a cleanup still pending, for instance)."""
    global last_outcome
    from .transaction import Transaction
    transaction = Transaction()
    try:
        for destination, source in zip(generated.destinations, generated.staged):
            transaction.add(destination, source)
        last_outcome = transaction.commit(overwrite=overwrite)
        return last_outcome.destinations
    finally:
        generated.close()


def export(context: bpy.types.Context, ts, settings, naming=None, directory=None, overwrite=False) -> list[Path]:
    """Write the set's maps; ``naming(ts_name, label, extension)`` overrides file names.

    Files are generated into a temporary folder first and published at the end, so a
    failure leaves nothing half-written; with ``overwrite`` an existing file is replaced
    only at that publish step, after every map has been generated."""
    destinations = destinations_for(ts, settings, naming, directory)
    if not overwrite and any(p.exists() for p in destinations):
        raise ValueError("Export files already exist. Choose another folder, prefix or suffix.")
    generated = generate(context, ts, settings, naming, directory)
    return publish(generated, overwrite)
