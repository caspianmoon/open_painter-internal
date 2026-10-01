"""Keep Python's cyclic garbage collector off the edit path.

A 200-layer project holds some 600,000 tracked Python objects, most of them the
headless node trees of its compiled units. A full collection walks every one of
them (about 300 ms on an M3 Pro) and CPython starts one whenever a quarter as many
objects as the last one kept have survived since: every few structural edits, in
the middle of whichever edit tipped it.

Once edits pause, ``settle`` collects what is not frozen (only what was allocated
since the last settle, so a few milliseconds) and freezes what survived
(``gc.freeze``). Collections then walk only newer objects. Frozen objects are still
freed by reference counting the moment nothing uses them; only a reference cycle
among them waits, for ``sweep``, which runs when a file loads and after a long idle
spell in which the frozen set grew. bpy-free: tests/unit drive it directly.
"""
from contextlib import contextmanager
import gc
import os
from time import monotonic

ENABLED = os.environ.get('OP_GC_FREEZE') != '0'   # 0: leave collection to CPython (comparisons)

SETTLE_DELAY = 0.3        # seconds since the last evaluation before survivors are frozen
SWEEP_IDLE = 30.0         # seconds without an evaluation before frozen garbage is swept
SWEEP_GROWTH = 1.25       # ... once the frozen set grew by a quarter since the last sweep
SWEEP_MINIMUM = 100_000   # ... and by at least this many objects

_state = {'pending': False, 'swept': 0, 'active': monotonic(), 'frozen': False, 'sweep_due': False}


def allocated():
    """A graph was built: the next settle freezes what it keeps."""
    _state['pending'] = True
    _state['active'] = monotonic()


def evaluated():
    _state['active'] = monotonic()


def unsettled():
    """A graph was built or patched since the last settle (its evaluation allocates too)."""
    return _state['pending']


@contextmanager
def paused():
    """No automatic collection while a graph is built from scratch (a file load, an undo) or
    first evaluated: its ~600,000 new objects would set off full collections over a growing
    heap, seconds at 200 layers. What survives is frozen at once (the young generations would
    otherwise hold it all for the next collection to walk); a reference cycle among it waits
    for ``sweep``, as frozen garbage does."""
    if not ENABLED or not gc.isenabled():
        yield
        return
    gc.disable()
    try:
        yield
    finally:
        gc.freeze()
        _state['frozen'] = True
        gc.enable()


def settle(now=None):
    """Called on idle ticks. Returns 'settled', 'swept' or None."""
    if not ENABLED:
        return None
    now = monotonic() if now is None else now
    # Deadlines, not ``now - active``: that difference rounds below the delay for about a
    # fifth of clock readings, so a pause of exactly the delay would not count.
    active = _state['active']
    if _state['sweep_due'] and now >= active + SETTLE_DELAY:
        sweep()   # what a reset let go (``release(defer=True)``), with what was built since
        return 'swept'
    if _state['pending'] and now >= active + SETTLE_DELAY:
        _state['pending'] = False
        gc.collect()   # the frozen objects are not walked: only what is new since the last settle
        gc.freeze()
        _state['frozen'] = True
        return 'settled'
    frozen = gc.get_freeze_count()
    if (_state['frozen'] and now >= active + SWEEP_IDLE
            and frozen > max(_state['swept'] * SWEEP_GROWTH, _state['swept'] + SWEEP_MINIMUM)):
        sweep()
        return 'swept'
    return None


def sweep():
    """Collect everything, frozen or not, and freeze what survives."""
    gc.unfreeze()
    gc.collect()
    gc.freeze()
    _state.update(swept=gc.get_freeze_count(), frozen=True, pending=False, sweep_due=False)


def release(defer=False):
    """Hand every frozen object back to normal collection (unregister). ``defer``: every
    session was just dropped (a file load, an undo), but the rebuild may still adopt what a
    dropped graph holds: a collection now would walk it and free nothing. The next pause
    sweeps instead."""
    if defer:
        _state.update(sweep_due=True, active=monotonic())
        return
    if _state['frozen']:
        gc.unfreeze()
        gc.collect()
    _state.update(swept=0, frozen=False, pending=False, active=monotonic(), sweep_due=False)
