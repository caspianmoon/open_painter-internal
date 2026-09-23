"""Bounded, atomic authoring across existing managed scene materials."""

from contextlib import contextmanager, ExitStack
import json

from . import commands, document, schema, transactions

MAX_TARGETS = 16
MAX_BYTES = 1024 * 1024
MAX_COMMANDS = 512
SCHEMA = schema.object_schema(dict(
    version=dict(type="integer", const=1), summary=dict(type="string", minLength=1, maxLength=500),
    targets=dict(type="array", minItems=1, maxItems=MAX_TARGETS, items=schema.object_schema(dict(
        texture_set_id=schema.IDENTIFIER, proposal=schema.SCHEMA), ("texture_set_id", "proposal")))),
    ("version", "summary", "targets"))


def materials(scene):
    result = [ts for ts in scene.open_painter.texture_sets if any(
        ref.object and ref.object.name in scene.objects for ref in ts.assignments)]
    if not result or len(result) > MAX_TARGETS:
        raise schema.AuthoringError(f"Scene scope supports 1–{MAX_TARGETS} assigned Open Painter texture sets")
    return result


def overview(targets, bindings):
    from mathutils import Vector
    result = []
    for ts in targets:
        objects = [ref.object for ref in ts.assignments if ref.object]
        points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
        result.append(dict(id=ts.id, name=ts.name, layer_count=len(ts.layers),
            object_ids=[key for key, (_, pointer) in bindings.items() if any(obj.as_pointer() == pointer for obj in objects)],
            world_bounds=[[min(p[i] for p in points) for i in range(3)],
                          [max(p[i] for p in points) for i in range(3)]]))
    return result


def validate(proposal):
    try:
        if isinstance(proposal, str):
            if len(proposal.encode()) > MAX_BYTES:
                raise schema.AuthoringError("Scene proposal exceeds 1 MiB")
            proposal = json.loads(proposal)
        if len(json.dumps(proposal, allow_nan=False).encode()) > MAX_BYTES:
            raise schema.AuthoringError("Scene proposal exceeds 1 MiB")
        schema.check(proposal, SCHEMA)
        ids = [target["texture_set_id"] for target in proposal["targets"]]
        if len(ids) != len(set(ids)):
            raise schema.AuthoringError("A scene material can occur only once in a proposal")
        if sum(len(t["proposal"]["commands"]) for t in proposal["targets"]) > MAX_COMMANDS:
            raise schema.AuthoringError(f"Limit a scene instruction to {MAX_COMMANDS} commands")
        for target in proposal["targets"]:
            schema.validate(target["proposal"])
    except (TypeError, ValueError, RecursionError) as exc:
        raise schema.AuthoringError(str(exc)) from exc
    return proposal


def compile(scene, proposal, operation_id, digests, *, objects=None, images=None, correspondences=None,
            observations=None):
    proposal = validate(proposal)
    targets = {ts.id: ts for ts in materials(scene)}
    result = []
    for item in proposal["targets"]:
        identifier = item["texture_set_id"]
        ts = targets.get(identifier)
        if ts is None or identifier not in digests:
            raise schema.AuthoringError("Proposal contains a material outside the captured scene scope")
        transactions.available(ts)
        if document.digest(ts) != digests[identifier]:
            raise schema.AuthoringError(f"{ts.name} changed; run the scene instruction again")
        compiled = commands.compile_proposal(ts, item["proposal"], operation_id + ":" + identifier,
                                             objects=objects, images=images, correspondences=correspondences,
                                             observations=observations)
        result.append((ts, item["proposal"], compiled))
    return result


@contextmanager
def atomic(targets):
    """Restore every document, receipt and dirty flag if any target fails."""
    from ..evaluation.shader_preview import authoring_batch
    saved = [(ts, document.snapshot(ts), ts.director_history, ts.dirty, ts.edit_serial,
              ts.active_layer, {out.channel: out.dirty for out in ts.outputs}) for ts in targets]
    resources = transactions.resources()
    with ExitStack() as stack:
        for ts in targets:
            stack.enter_context(authoring_batch(ts))
        try:
            yield
        except Exception:
            for ts, before, history, dirty, serial, active, outputs in saved:
                document.apply(ts, before)
                ts.director_history, ts.dirty, ts.edit_serial, ts.active_layer = history, dirty, serial, active
                for out in ts.outputs:
                    out.dirty = outputs.get(out.channel, out.dirty)
            transactions.discard_new(resources)
            raise


def apply(scene, proposal, *, operation_id, digests, objects=None, images=None, correspondences=None, fault=None):
    compiled = compile(scene, proposal, operation_id, digests, objects=objects, images=images, correspondences=correspondences)
    proposal = validate(proposal)
    ids = [ts.id for ts, _, _ in compiled]
    receipts = []
    with atomic([ts for ts, _, _ in compiled]):
        for ts, part, _ in compiled:
            receipt = transactions.apply(ts, part, operation_id=operation_id + ":" + ts.id,
                expected_digest=digests[ts.id], objects=objects, images=images, correspondences=correspondences)
            records = transactions.history(ts)
            record = next(r for r in records if r["id"] == receipt["id"])
            record.update(scene_operation=operation_id, scene_targets=ids, scene_summary=proposal["summary"])
            data = json.dumps(records, allow_nan=False)
            if len(data.encode()) > transactions.MAX_HISTORY_BYTES:
                raise schema.AuthoringError("Scene change is too large to retain a safe undo receipt")
            ts.director_history = data
            receipts.append(dict(record, texture_set_id=ts.id, name=ts.name))
            if fault:
                fault(ts)
    return dict(summary=proposal["summary"], receipts=receipts,
                changes=[ts["name"] + ": " + change for ts in receipts for change in ts["changes"]])


def undo(scene, receipt):
    ids = receipt.get("scene_targets")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_TARGETS or len(set(ids)) != len(ids):
        raise schema.AuthoringError("Saved scene undo group is invalid")
    targets = {ts.id: ts for ts in scene.open_painter.texture_sets}
    group = []
    for identifier in ids:
        ts = targets.get(identifier)
        latest = next((r for r in reversed(transactions.history(ts)) if not r.get("reverted")), None) if ts else None
        if not latest or latest.get("scene_operation") != receipt["scene_operation"] or latest.get("scene_targets") != ids:
            raise schema.AuthoringError("Undo later AI changes on the scene's materials first; the complete scene undo group must still be available")
        transactions.available(ts)
        group.append(ts)
    results = []
    with atomic(group):
        for ts in group:
            results.append((ts.name, transactions.undo(ts, _single=True)))
    return dict(summary="Undid " + receipt.get("scene_summary", receipt["summary"]),
                conflicts=[dict(material=name, conflict=c) for name, result in results for c in result["conflicts"]],
                changes=[name + ": " + c for name, result in results for c in result["changes"]])
