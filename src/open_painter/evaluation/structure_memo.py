"""Per-set answers that only a structural edit changes, kept through numeric drags.

Every property callback moves its set's edit serial. A uniform (numeric) edit changes
nothing structural, so ``numeric_edit`` maps a run of them back to the serial before it:
``token`` is the serial as structure sees it, with a per-set generation that
``structural_edit`` bumps for structural changes that move no serial. ``memoised`` answers
from a store while the token stands. No bpy: plain-attribute fixtures compute afresh.
"""

_numeric = {}   # (set pointer, id) -> (edit serial, the serial it stands for)
_generations = {}   # (set pointer, id) -> structural changes that moved no edit serial
_stores = []   # every store: emptied on load, undo and redo


def store():
    """A store for ``memoised``: (set pointer, id) -> (token, answer)."""
    created = {}
    _stores.append(created)
    return created


def _key(ts):
    return ts.as_pointer(), ts.id


def numeric_edit(ts, before):
    """A uniform edit moved the set's edit serial from ``before``. The caller vouches that
    it changed nothing a store answers from: decal frames are numeric edits that the
    geometry key reads, so their layers never come here."""
    key = _key(ts)
    held = _numeric.get(key)
    _numeric[key] = (ts.edit_serial, held[1] if held is not None and held[0] == before else before)


def structural_edit(ts):
    """Something a store answers from may have changed without moving the edit serial: a
    units switch whose derived texture values stayed put, a layer added under a restored
    serial."""
    key = _key(ts)
    _generations[key] = _generations.get(key, 0) + 1


def token(ts):
    """The set's edit serial as structure sees it, with its generation."""
    key = _key(ts)
    held = _numeric.get(key)
    return (held[1] if held is not None and held[0] == ts.edit_serial else ts.edit_serial,
            _generations.get(key, 0))


def memoised(answers, ts, compute):
    """``compute()`` for the set's current structure. A replay's edits skip their callbacks
    and move no serial until it ends, so while one is open the answer is computed afresh."""
    from ..core.layer_stack import replaying
    if replaying() or not hasattr(ts, 'as_pointer'):
        return compute()
    key, current = _key(ts), token(ts)
    cached = answers.get(key)
    if cached is None or cached[0] != current:
        cached = answers[key] = (current, compute())
    return cached[1]


def clear():
    _numeric.clear()
    _generations.clear()
    for answers in _stores:
        answers.clear()


def prune(ids):
    """Forget the sets whose ids are not in ``ids``."""
    for values in (_numeric, _generations, *_stores):
        for key in [key for key in values if key[1] not in ids]:
            values.pop(key, None)
