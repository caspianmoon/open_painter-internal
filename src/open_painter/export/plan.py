"""One resolved export plan for the preview and the export itself (UI plan phase 5).

``resolve(context, contents, scope, ...)`` reads the project's target, every set's own
export settings and the chosen scope, and returns the effective settings, the sets and
objects involved, every output file with its dimensions, format, bit depth, packing and
colour space, the normal convention, the optional mesh and manifest outputs and the
files that already exist. Nothing is written or mutated. ``execute(context, plan)`` writes
exactly what the plan lists, through the existing exporter, target naming and manifest
code, and publishes files only after their generation succeeded.

Three routes keep their distinct outputs: ``TEXTURES`` is the set's own texture export,
``TARGET`` the project target's textures plus manifest and mesh, ``BUNDLE`` the evaluated
scene bundle (staged scene, textures, manifest) of the existing production export.
"""
import json
from tempfile import TemporaryDirectory
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from ..constants import CHANNEL_LABELS
from ..core.project_manager import active_set
from .. import targets as target_registry

CONTENTS = (("TEXTURES", "Textures", "The set's own export settings: evaluated texture files only"),
            ("TARGET", "Target Package", "The project target's textures with its naming, plus a manifest and the mesh"),
            ("BUNDLE", "Evaluated Bundle", "A staged scene with evaluated copies, textures and a manifest"))
SCOPES = (("ACTIVE", "Active Texture Set", "The set selected in the Painter"),
          ("SELECTED", "Selected Objects' Texture Sets", "Every set assigned to a selected object; empty stays empty"),
          ("ALL", "All Texture Sets", "Every texture set of the project"))
FOLDERS = (("PROJECT", "Project Export Folder", "The folder of the project's target export"),
           ("SET", "Set's Own Folder", "The folder saved on the texture set's export settings"))


@dataclass
class Output:
    label: str
    definition: object
    path: Path
    size: int
    file_format: str
    bit_depth: str
    packing: str
    color_space: str
    exists: bool = False

    @property
    def name(self):
        return self.path.name


@dataclass
class SetPlan:
    set_id: str
    name: str
    preset: str
    file_format: str
    bit_depth: str
    size: int
    prefix: str
    suffix: str
    follow_target: bool
    naming: str                     # 'TARGET' or 'SET'
    normal: str                     # 'OPENGL' or 'DIRECTX'
    outputs: list = field(default_factory=list)
    manifest: Path = None
    overrides: list = field(default_factory=list)   # what the set's own settings say when they differ
    own_directory: str = ""
    objects: list = field(default_factory=list)


@dataclass
class Plan:
    contents: str
    scope: str
    directory: Path = None
    folder_source: str = "PROJECT"
    target: str = "BLENDER"
    sets: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    mesh: Path = None
    bundle: Path = None
    problems: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    outcome: object = None          # the publication outcome of execute() (cleanup pending, message)

    @property
    def files(self):
        result = [output.path for item in self.sets for output in item.outputs]
        result += [item.manifest for item in self.sets if item.manifest is not None]
        if self.mesh is not None:
            result.append(self.mesh)
        return result

    @property
    def ready(self):
        return not self.problems and bool(self.sets)


def scope_sets(context, scope):
    """The sets a scope names; an empty selected scope stays empty (no fallback to every set)."""
    project = context.scene.open_painter
    if scope == "ALL":
        return list(project.texture_sets), ""
    if scope == "SELECTED":
        selected = {obj.as_pointer() for obj in context.selected_objects}
        chosen = [ts for ts in project.texture_sets
                  if any(ref.object and ref.object.as_pointer() in selected for ref in ts.assignments)]
        if not chosen:
            return [], ("No selected object belongs to a texture set" if selected
                        else "Nothing is selected: select the objects whose sets should export")
        return chosen, ""
    ts = active_set(context)
    return ([ts], "") if ts is not None else ([], "Create a texture set first")


def definitions(preset, settings):
    from .presets import blender, custom, gltf, godot, unity, unity_hdrp, unity_urp, unreal
    if preset == "CUSTOM":
        return custom.outputs(settings)
    modules = {"BLENDER": blender, "UNREAL": unreal, "UNITY": unity, "GODOT": godot, "GLTF": gltf,
               "UNITY_URP": unity_urp, "UNITY_HDRP": unity_hdrp}
    return modules[preset].outputs()


def packing_text(definition):
    if isinstance(definition, tuple):
        parts = []
        for slot, key in zip("RGBA", definition):
            key = key.replace("ONE_MINUS_", "1−")
            parts.append(f"{slot}={CHANNEL_LABELS.get(key, key.replace('_', ' ').title())}")
        return " ".join(parts)
    if definition == "BASE_COLOR_OPACITY":
        return "RGB=Base Color A=Opacity"
    if definition == "NORMAL_DX":
        return "Normal, green down (DirectX)"
    return CHANNEL_LABELS.get(definition, str(definition).replace("_", " ").title())


def color_space_of(label, definition, target):
    if target is not None and target.key != "CUSTOM":
        return target.color_spaces.get(label, "Non-Color")
    return "sRGB" if definition in {"BASE_COLOR", "EMISSION", "BASE_COLOR_OPACITY"} else "Non-Color"


def effective(project, ts, contents):
    """The settings an export would use, computed without touching the set's own settings."""
    settings = ts.export
    target = target_registry.get(project.target)
    own = dict(preset=settings.preset, file_format=settings.file_format, bit_depth=settings.bit_depth)
    if contents == "TARGET" and target.key != "CUSTOM":
        used = dict(preset=target.preset, file_format=target.file_format, bit_depth=target.bit_depth)
        size = target_registry.resolution(target, ts, settings)
        naming = "TARGET"
    else:
        used = own
        size = int(settings.resolution) if settings.resolution != "SET" else int(ts.resolution)
        naming = "SET"
    overrides = []
    if contents == "TARGET" and target.key != "CUSTOM" and not settings.follow_target:
        for key, label in (("preset", "preset"), ("file_format", "format"), ("bit_depth", "bit depth")):
            if own[key] != used[key]:
                overrides.append(f"{label} {own[key]} (set) → {used[key]} (target)")
    return target, used, size, naming, overrides


def resolve(context, *, contents="TEXTURES", scope="ACTIVE", folder_source="PROJECT", directory=None):
    project = context.scene.open_painter
    plan = Plan(contents=contents, scope=scope, folder_source=folder_source, target=project.target)
    chosen, problem = scope_sets(context, scope)
    if problem:
        plan.problems.append(problem)
    target = target_registry.get(project.target)
    project_folder = bpy.path.abspath(project.export_directory) if project.export_directory else ""
    for ts in chosen:
        settings = ts.export
        _target, used, size, naming, overrides = effective(project, ts, contents)
        item = SetPlan(set_id=ts.id, name=ts.name, preset=used["preset"], file_format=used["file_format"],
                       bit_depth=used["bit_depth"], size=size, prefix=settings.prefix, suffix=settings.suffix,
                       follow_target=settings.follow_target, naming=naming,
                       normal="DIRECTX" if (naming == "TARGET" and target.normal == "DIRECTX")
                       or used["preset"] == "UNREAL" else "OPENGL",
                       overrides=overrides, own_directory=settings.directory,
                       objects=sorted({ref.object.name for ref in ts.assignments if ref.object}))
        own_folder = bpy.path.abspath(settings.directory) if settings.directory else ""
        if directory is not None:
            folder = Path(directory)
        elif contents == "TEXTURES" or contents == "BUNDLE":
            folder = Path(own_folder) if own_folder else None
            if folder is None and project_folder and folder_source == "PROJECT":
                folder = Path(project_folder)
        else:
            folder = Path(own_folder) if (folder_source == "SET" and own_folder) else (Path(project_folder) if project_folder else None)
        if folder is None:
            plan.problems.append(f"{ts.name}: choose an export folder")
            plan.sets.append(item)
            continue
        if plan.directory is None:
            plan.directory = folder
        if contents == "BUNDLE":
            from .exporter import safe_name
            bundle = folder / (safe_name(ts.name) + "_bundle")
            plan.bundle = bundle
            item.outputs = []
            plan.notes.append(f"{ts.name}: evaluated scene, textures and manifest in {bundle.name}/")
            if bundle.exists():
                plan.conflicts.append(bundle)
            plan.sets.append(item)
            continue
        extension = "png" if used["file_format"] == "PNG" else "tif"
        from .exporter import set_filename
        for label, definition in definitions(used["preset"], settings):
            if naming == "TARGET":
                name = set_filename(ts, label, extension, naming=lambda set_name, channel, ext:
                    target_registry.filename(target, set_name, channel, ext, settings.prefix, settings.suffix))
            else:
                name = set_filename(ts, label, extension, settings.prefix, settings.suffix)
            path = folder / name
            item.outputs.append(Output(label=label, definition=definition, path=path, size=size,
                                       file_format=used["file_format"], bit_depth=used["bit_depth"],
                                       packing=packing_text(definition),
                                       color_space=color_space_of(label, definition, target if naming == "TARGET" else None),
                                       exists=path.exists()))
        if naming == "TARGET":
            from .exporter import safe_name
            item.manifest = folder / f"{safe_name(ts.name)}_manifest.json"
        plan.sets.append(item)
        if own_folder and project_folder and Path(own_folder) != Path(project_folder) and contents == "TARGET":
            plan.notes.append(f"{ts.name} keeps its own folder {settings.directory}; this export uses the "
                              f"{'set' if folder_source == 'SET' else 'project'} folder")
    plan.objects = sorted({name for item in plan.sets for name in item.objects})
    if contents == "TARGET" and project.export_mesh and target.mesh_format and plan.objects and plan.directory is not None:
        from .exporter import safe_name
        extension = "fbx" if target.mesh_format == "FBX" else "glb"
        plan.mesh = plan.directory / f"{safe_name(context.scene.name)}.{extension}"
    plan.conflicts.extend(path for path in plan.files if path.exists() and path not in plan.conflicts)
    if plan.directory is not None and plan.directory.exists() and not _writable(plan.directory):
        plan.problems.append(f"{plan.directory} is not writable")
    return plan


def _writable(folder):
    import os
    return os.access(folder, os.W_OK)


def execute(context, plan, *, overwrite=False):
    """Write what the plan lists; returns the written paths. Refuses conflicts unless ``overwrite``."""
    if plan.problems:
        raise ValueError(" | ".join(plan.problems))
    if not plan.sets:
        raise ValueError("The scope names no texture set.")
    if plan.conflicts and not overwrite:
        names = ", ".join(path.name for path in plan.conflicts[:4]) + (" …" if len(plan.conflicts) > 4 else "")
        raise ValueError(f"Files already exist: {names}. Choose another folder, prefix or suffix, or allow replacing.")
    project = context.scene.open_painter
    by_id = {ts.id: ts for ts in project.texture_sets}
    written = []
    if plan.contents == "BUNDLE":
        from .mesh_bundle import export_bundle
        for item in plan.sets:
            ts = by_id[item.set_id]
            export_bundle(context, [ts], plan.bundle, "ACTUAL")
            written.append(plan.bundle)
        return written
    from .exporter import generate
    from .transaction import Transaction
    target = target_registry.get(project.target)
    transaction = Transaction()
    generated, staging = [], TemporaryDirectory(prefix="open-painter-request-")
    try:
        for item in plan.sets:
            ts = by_id.get(item.set_id)
            if ts is None:
                raise ValueError(f"{item.name} no longer exists; resolve the plan again")
            settings = ts.export
            if item.naming == "TARGET":
                # The exporter reads settings attributes: the target's values overlay the set's
                # own, which stay untouched (an export never edits what the artist saved).
                size = item.size
                resolution = str(size) if str(size) in {"512", "1024", "2048", "4096", "8192"} else "SET"
                settings = _Overlay(settings, preset=item.preset, file_format=item.file_format,
                                    bit_depth=item.bit_depth, resolution=resolution)
                naming = (lambda set_name, label, ext, prefix=settings.prefix, suffix=settings.suffix:
                          target_registry.filename(target, set_name, label, ext, prefix, suffix))
            else:
                naming = None
            folder = item.outputs[0].path.parent if item.outputs else plan.directory
            result = generate(context, ts, settings, naming=naming, directory=folder)
            generated.append(result)
            expected = [output.path for output in item.outputs]
            if [p.name for p in result.destinations] != [p.name for p in expected]:
                raise RuntimeError(f"{item.name}: the exporter wrote {[p.name for p in result.destinations]} but the plan listed "
                                   f"{[p.name for p in expected]}")
            for destination, source in zip(result.destinations, result.staged):
                transaction.add(destination, source)
            if item.manifest is not None:
                labels = [output.label for output in item.outputs]
                staged_manifest = Path(staging.name) / f"{len(generated):03d}-{item.manifest.name}"
                staged_manifest.write_text(json.dumps(target_registry.manifest(target, ts, list(zip(labels, result.destinations))), indent=2))
                transaction.add(item.manifest, staged_manifest)
        if plan.mesh is not None:
            from ..ui.target_panel import _export_mesh
            objects = {by_id[item.set_id].assignments[i].object for item in plan.sets
                       for i in range(len(by_id[item.set_id].assignments)) if by_id[item.set_id].assignments[i].object}
            # The mesh is written into staging like every map; the existing mesh stays until
            # the whole request commits.
            mesh_staging = Path(staging.name) / "mesh"
            mesh_staging.mkdir()
            staged_mesh = _export_mesh(context, target, mesh_staging, objects)
            transaction.add(plan.mesh, staged_mesh)
        # Everything of the request exists in staging: publish all of it or none of it.
        plan.outcome = transaction.commit(overwrite=overwrite)
        written.extend(plan.outcome.destinations)
    finally:
        for result in generated:
            result.close()
        staging.cleanup()
    return written


class _Overlay:
    """Export settings read with a few values replaced; writes reach the real settings."""

    def __init__(self, settings, **overrides):
        object.__setattr__(self, "_settings", settings)
        object.__setattr__(self, "_overrides", overrides)

    def __getattr__(self, name):
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_settings"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_settings"), name, value)


def summary(plan):
    """One line per set for status messages."""
    return "; ".join(f"{item.name}: {len(item.outputs)} file(s)" + (" + manifest" if item.manifest else "")
                     for item in plan.sets) + (f" + mesh {plan.mesh.name}" if plan.mesh else "")
