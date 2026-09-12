"""Access scene-owned persistent project state."""

from ..utils.ids import new_id


def project_for(scene):
    project = scene.open_painter
    if not project.id:
        project.id = new_id()
    return project


def active_set(context):
    project = context.scene.open_painter
    i = project.active_texture_set
    return project.texture_sets[i] if 0 <= i < len(project.texture_sets) else None


def require_set(context):
    ts = active_set(context)
    if ts is None:
        raise ValueError("No active texture set. Create one first.")
    return ts
