"""Scoped, idempotent main-thread commits with persisted change receipts."""

import hashlib
import json
import time

from . import assets, commands, document, schema
from .schema import AuthoringError

MAX_HISTORY_BYTES = 8 * 1024 * 1024
_writers = set()


def history(ts):
    try:
        if len(ts.director_history.encode()) > MAX_HISTORY_BYTES:
            raise ValueError()
        records = json.loads(ts.director_history or "[]")
        if not isinstance(records, list) or len(records) > 8 or any(
                not isinstance(r, dict) or r.get("version") != 1 or
                not all(isinstance(r.get(k), str) for k in ("id", "hash", "summary")) or
                not all(isinstance(r.get(k), dict) for k in ("before", "after", "aliases")) or
                not isinstance(r.get("changes"), list) or not all(isinstance(v, str) for v in r["changes"])
                for r in records):
            raise ValueError()
        return records
    except (ValueError, TypeError):
        raise AuthoringError("Saved Material Director history is invalid; clear its history to continue") from None


def remember(ts, receipt):
    records = [*history(ts), receipt][-8:]
    while len(json.dumps(records, allow_nan=False).encode()) > MAX_HISTORY_BYTES and len(records) > 1:
        records.pop(0)
    data = json.dumps(records, allow_nan=False)
    if len(data.encode()) > MAX_HISTORY_BYTES:
        raise AuthoringError("This change is too large to retain a safe undo receipt")
    ts.director_history = data


def resources():
    import bpy
    return {name: {item.as_pointer() for item in getattr(bpy.data, name)}
            for name in ("images", "materials", "node_groups", "texts")}


def discard_new(before):
    import bpy
    # The executor only allocates bundled source images. Other resources are
    # retained unless unused, so a failure cannot remove an artist datablock.
    for name in ("materials", "node_groups", "texts", "images"):
        store = getattr(bpy.data, name)
        for item in list(store):
            if item.as_pointer() not in before[name] and item.users <= int(item.use_fake_user):
                store.remove(item)


def available(ts):
    import bpy
    import threading
    if threading.current_thread() is not threading.main_thread():
        raise AuthoringError("Material changes must run on Blender's main thread")
    from ..baking.jobs import OP_OT_edge_map_refresh
    if bpy.context.mode not in {"OBJECT", "PAINT_TEXTURE"}:
        raise AuthoringError("Finish the current edit before applying Material Director changes")
    if any(ref.object and ref.object.mode == "EDIT" for ref in ts.assignments):
        raise AuthoringError("Leave Edit Mode before applying material changes")
    if OP_OT_edge_map_refresh.running:
        raise AuthoringError("Wait for the current mesh-map bake to finish")
    for wm in bpy.data.window_managers:
        for window in wm.windows:
            for op in window.modal_operators:
                if op.bl_idname in {"PAINT_OT_image_paint", "OPEN_PAINTER_OT_native_layer_drag", "OPEN_PAINTER_OT_bake_queue"}:
                    raise AuthoringError("Finish the active stroke, drag or bake before applying")


def apply(ts, proposal, *, operation_id, expected_digest, scope="", objects=None, images=None, correspondences=None, fault=None):
    proposal = schema.validate(proposal)
    payload_hash = hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest()
    for receipt in history(ts):
        if receipt.get("id") == operation_id:
            if receipt.get("hash") != payload_hash:
                raise AuthoringError("An operation ID cannot be reused for different changes")
            return dict(receipt, replayed=True)
    available(ts)
    if document.digest(ts) != expected_digest:
        raise AuthoringError("The material or mesh changed. Run the instruction again with fresh context.")
    key = (ts.id_data.as_pointer(), ts.id)
    if key in _writers:
        raise AuthoringError("Another material change is being applied")
    compiled = commands.compile_proposal(ts, proposal, operation_id, scope=scope, objects=objects, images=images, correspondences=correspondences)
    before = compiled["before"]
    old_active = ts.layers[ts.active_layer].id if 0 <= ts.active_layer < len(ts.layers) else ""
    old_history = ts.director_history
    old_dirty = (ts.dirty, ts.edit_serial, {out.channel: out.dirty for out in ts.outputs})
    before_resources = resources()
    from ..evaluation.shader_preview import authoring_batch
    _writers.add(key)
    try:
        with authoring_batch(ts):
            try:
                desired = assets.prepare(compiled["after"], images=images)
                if fault:
                    fault("prepared")
                document.apply(ts, desired)
                if fault:
                    fault("applied")
                actual = document.snapshot(ts)
                commands.validate_document(ts, before, actual)
                commands.validate_scope(ts, before, actual, scope)
                commands.surfaces.validate_patches(before, actual, compiled.get("patch_constraints", []))
                receipt = dict(version=1, id=operation_id, hash=payload_hash, timestamp=time.time(),
                               summary=proposal["summary"], before=document.encode(before),
                               after=document.encode(actual), aliases=compiled["aliases"],
                               changes=document.report(before, actual), reverted=False)
                remember(ts, receipt)
                if fault:
                    fault("recorded")
            except Exception:
                document.apply(ts, before)
                ts.director_history = old_history
                ts.dirty, ts.edit_serial = old_dirty[:2]
                for out in ts.outputs:
                    out.dirty = old_dirty[2].get(out.channel, out.dirty)
                discard_new(before_resources)
                raise
    finally:
        _writers.discard(key)
        ts.active_layer = next((i for i, layer in enumerate(ts.layers) if layer.id == old_active), 0)
    from ..evaluation import cache
    cache.wake()
    return receipt


def undo(ts, *, _single=False):
    """Reverse the last AI change, keeping later artist edits on conflicts."""
    from ..core.material_inheritance import preview
    from ..evaluation.shader_preview import authoring_batch
    records = history(ts)
    receipt = next((item for item in reversed(records) if not item.get("reverted")), None)
    if receipt is None:
        raise AuthoringError("There is no Material Director change to undo")
    if receipt.get("scene_operation") and not _single:
        from . import scene
        return scene.undo(ts.id_data, receipt)
    available(ts)
    current = document.snapshot(ts)
    # The old result is the merge base; the desired parent is the pre-AI state.
    # Drop derived analysis references before resolving IDs: a later scene-field
    # publication may legitimately have retired an old analysis image.
    desired, conflicts = preview(document.encode(receipt["after"], digest=True),
                                 document.encode(receipt["before"], digest=True),
                                 document.encode(current, digest=True))
    desired = document.decode(desired)
    # The merge lists pre-AI items first: an artist layer added or moved since
    # would silently change position, so follow the current order instead.
    document.keep_order(desired, current, receipt["before"])
    commands.validate_scope(ts, current, desired, "")
    commands.validate_document(ts, current, desired)
    old_history = ts.director_history
    with authoring_batch(ts):
        try:
            document.apply(ts, desired)
            receipt["reverted"] = True
            receipt["conflicts"] = conflicts
            ts.director_history = json.dumps(records)
        except Exception:
            document.apply(ts, current)
            ts.director_history = old_history
            raise
    from ..evaluation import cache
    cache.wake()
    return dict(summary="Undid " + receipt["summary"], conflicts=conflicts,
                changes=document.report(current, document.snapshot(ts)))
