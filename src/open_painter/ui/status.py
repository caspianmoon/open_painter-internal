"""One truthful status for the active texture set (UI plan phase 8).

The panels used to repeat the backend's notices; this reads what the subsystems actually
report — the bake queue, the edge-map job, the shader session, the CPU scheduler, the
paint-time freeze, the 2D snapshot and the mask view — and names one of six states:

    Idle · Updating · Stale · Baking · Failed · Paused

with a short detail. It is presentation only: nothing here evaluates, and the last
operator message (``project.status``) stays available as the detail of an Idle state.
"""
from dataclasses import dataclass

STATES = ("Idle", "Updating", "Stale", "Baking", "Failed", "Paused")
ICONS = {"Idle": "CHECKMARK", "Updating": "SORTTIME", "Stale": "TIME", "Baking": "RENDER_STILL",
         "Failed": "CANCEL", "Paused": "PAUSE"}


@dataclass
class Status:
    state: str = "Idle"
    detail: str = ""

    @property
    def icon(self):
        return ICONS[self.state]

    @property
    def text(self):
        return self.state + (f" · {self.detail}" if self.detail else "")


def _shorten(text, limit=90):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def of(context, ts=None):
    """The active set's status; an Idle status carries the last operator message as its detail."""
    from ..core.project_manager import active_set
    scene = context.scene
    project = scene.open_painter
    ts = ts if ts is not None else active_set(context)
    if ts is None:
        return Status("Idle", "no texture set")
    from ..baking.jobs import OP_OT_bake_queue, OP_OT_edge_map_refresh
    if OP_OT_bake_queue.running_set_id == ts.id:
        from ..baking import history
        live = history.running(ts) or {}
        current = (live.get("current") or "").replace("_", " ").title()
        return Status("Baking", f"{current} · {len(live.get('done', []))}/{len(live.get('kinds', []))} maps")
    if OP_OT_edge_map_refresh.running and OP_OT_edge_map_refresh.running_set_id == ts.id:
        return Status("Baking", "edge maps in the background")
    from ..evaluation import cache, shader_preview
    if shader_preview.requested(ts):
        info = shader_preview.diagnostics(scene, ts, details=False)
        if info["error"]:
            return Status("Failed", _shorten(info["error"]))
        if info["frozen"]:
            return Status("Paused", "frozen while painting; catches up after the stroke")
        if info["state"] in {"BOUNDARY", "QUEUED"} or shader_preview.pending_work(scene, ts):
            return Status("Updating", "preparing the shader" if info["state"] == "QUEUED" else "preparing isolated inputs")
    else:
        job = cache.job_state(scene, ts)
        if job["state"] == "FAILED":
            return Status("Failed", _shorten(job["error"]))
        if job["state"] not in {"READY"} or ts.dirty:
            return Status("Updating", "CPU reference evaluating")
    # Views that can fall behind the material: the mask snapshot and the 2D composite.
    from . import mask_view
    view = mask_view.active(ts)
    if view is not None and mask_view.stale(ts, view):
        return Status("Stale", "mask snapshot out of date")
    area = getattr(context, "area", None)
    if area is not None and area.type == "IMAGE_EDITOR" and not project.paint_image:
        from ..evaluation.composite_snapshot import MARKER, stale
        image = getattr(getattr(context, "space_data", None), "image", None)
        if image is not None and image.get(MARKER) and stale(ts, image):
            return Status("Stale", "composite snapshot out of date")
    # The shader session writes its generic line into project.status; that is the notice this
    # status replaces, so only an operator's own message stays as the Idle detail.
    generic = shader_preview.status(scene, ts) if shader_preview.requested(ts) else ""
    detail = project.status if project.status and project.status != generic else ""
    return Status("Idle", _shorten(detail))


def draw(layout, context, ts=None, *, compact=False):
    """One status line; the detail is the tooltip-free text after the state."""
    status = of(context, ts)
    row = layout.row(align=True)
    if compact:
        row.label(text=status.state, icon=status.icon)
    else:
        row.label(text=status.text, icon=status.icon)
    if status.state == "Failed":
        row.operator("open_painter.retry_evaluation", text="", icon="FILE_REFRESH")
    return status
