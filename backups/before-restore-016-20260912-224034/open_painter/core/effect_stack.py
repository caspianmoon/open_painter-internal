"""Effect operations detach RNA data before changing collections."""

from ..utils.ids import new_id
from .layer_stack import invalidate, restore, snapshot


def collection(layer, target):
    return layer.mask_effects if target == "MASK" else layer.effects


def index_key(target):
    return "active_mask_effect" if target == "MASK" else "active_effect"


def add(ts, layer, target="CONTENT", kind="LEVELS", channel="BASE_COLOR", data=None):
    effects = collection(layer, target)
    effect = effects.add()
    try:
        if data is not None:
            restore(data, effect)
        else:
            effect.type, effect.channel, effect.name = kind, channel, kind.title()
        effect.id = new_id()
    except Exception:
        effects.remove(len(effects) - 1)
        invalidate(ts)
        raise
    setattr(layer, index_key(target), len(effects) - 1)
    invalidate(ts)
    return effect


def edit(ts, layer, target, action):
    effects = collection(layer, target)
    if not effects:
        raise ValueError("Add or select an effect first.")
    key = index_key(target)
    i = max(0, min(getattr(layer, key), len(effects) - 1))
    if action == "DUPLICATE":
        return add(ts, layer, target, data=snapshot(effects[i]))
    if action == "DELETE":
        effects.remove(i)
        setattr(layer, key, max(0, i - 1))
    else:
        j = max(0, min(len(effects) - 1, i + (-1 if action == "UP" else 1)))
        effects.move(i, j)
        setattr(layer, key, j)
    invalidate(ts)
