"""Affine suffix collapse: the layers above an edited layer as one pass.

While one layer is edited (a slider drag, a brush stroke), every layer above it
re-renders on each tick although only its backdrop changed. For the blend modes
op_ir_backdrop_affine proves affine, such a run of layers maps its input x to
``A * x + B`` per texel and component. C plans the runs (op_graph_suffix_runs):
chains of affine units stale only through their backdrop, keyed by everything
else they read. This module arms a run once (each member's own program with a
constant backdrop of 0 and of 1 gives its slope and offset; a fixed pass composes
them in full float) and afterwards materializes the run's top unit with one pass
per tick. The top then holds its desired stamp, so the normal executor treats it
as rendered. The result differs from sequential rendering only by rounding (it
skips the per-layer half-float stores).

Arming costs about three passes per member, so a run is armed when it repeats
on consecutive evaluations; a one-off edit renders sequentially as before. Only
evaluations that changed content plan runs: a forced re-render without edits
stays sequential and bit-identical.
Transforms are recomputable caches under their own budget and are dropped first
under memory pressure.
"""
from collections import OrderedDict
from dataclasses import dataclass

from .. import metrics
from .formats import scalar_unit, texture_bytes

ENABLED = True
MIN_LENGTH = 3                        # shorter runs are cheaper to render than to apply
# While idle, arm the runs an edit of the selected layer would use, so the first
# tick of a drag is already one pass. Off in background Blender unless requested:
# tests drive ticks by hand and count the passes each one submits.
PREARM = None                         # None: on with a UI, off in background (OP_PREARM=1 forces it)
PREARM_DELAY = 0.3                    # seconds since the last evaluation before idle arming starts


def _budget():
    from .runtime import MIB, memory_share
    return memory_share(1 / 32, 128 * MIB, 512 * MIB)


BUDGET_BYTES = _budget()              # armed transforms kept per texture set
NONE = 255
IDENTITY = (0, 1, 2, 3)


def prearm_enabled():
    if PREARM is not None:
        return PREARM
    import os
    import bpy
    return os.environ.get('OP_PREARM') == '1' or not bpy.app.background


def pack(source):
    """Two bits per output component: which input component it reads."""
    return sum((0 if c == NONE else c) << (2 * i) for i, c in enumerate(source))


@dataclass
class Transform:
    a: object
    b: object
    fmt: str
    n: int
    source: tuple
    bytes: int


class SuffixCache:
    def __init__(self):
        self.entries = OrderedDict()   # run key -> Transform, least recently used first
        self.seen = {}                 # run key -> serial of the evaluation that last planned it
        self.serial = 0
        self.active = None             # the entry being applied: never reclaimed under it
        self.idle = None               # (layer, root stamps) whose predicted runs are all armed
        self.prearming = None          # (that state, run keys already armed for it while idle)

    def begin(self):
        self.serial += 1
        # Only the previous evaluation's runs can arm now.
        self.seen = {key: serial for key, serial in self.seen.items() if serial >= self.serial - 1}

    def memory(self):
        return sum(entry.bytes for entry in self.entries.values())

    def clear(self):
        from .runtime import device
        for entry in self.entries.values():
            _release(device(), entry)
        self.entries.clear()
        self.seen.clear()
        self.idle = self.prearming = None

    def reclaim(self):
        """Memory pressure: release every transform not being applied right now."""
        from .runtime import device
        for key in [key for key, entry in self.entries.items() if entry is not self.active]:
            _release(device(), self.entries.pop(key))
            metrics.count('engine_suffix_reclaims')

    def collapse(self, engine, root, n, memo, epochs, defer=False):
        """Materialize every armed (or now armable) run under ``root``. ``defer``: the run topped
        by ``root`` is returned instead of applied, for the channel's publication pass to apply
        (``fusion.publish_suffix``, else ``apply_deferred``): (top, input key or None, bottom,
        transform), or None."""
        graph = engine._native_graph
        # Only content edits collapse. A forced re-render (host stamps reset without
        # any change) renders sequentially, so it reproduces canonical output.
        if not ENABLED or graph is None or engine.profiling or not getattr(graph, 'last_patch_count', 0):
            return None
        deferred = None
        for top, source, key, members in graph.runs(engine, root):
            if len(members) < MIN_LENGTH:
                continue
            entry = self.entries.get(key)
            if entry is not None and entry.n != n:
                _release_key(self, key)
                entry = None
            if entry is None:
                armable = self.seen.get(key) == self.serial - 1
                self.seen[key] = self.serial
                if not armable:
                    metrics.count('engine_suffix_deferred')
                    continue
                entry = self._arm(engine, members, n, memo, epochs)
                if entry is None:
                    continue
                self.entries[key] = entry
                self._trim(protected=key)
            self.seen[key] = self.serial
            self.entries.move_to_end(key)
            if defer and top == root:
                deferred = (top, source, members[0], entry)
                continue
            self.apply_deferred(engine, (top, source, members[0], entry), n, memo, epochs)
        return deferred

    def apply_deferred(self, engine, run, n, memo, epochs):
        """Apply a run (``collapse``) as one pass."""
        top, source, bottom, entry = run
        self.active = entry
        try:
            self._apply(engine, top, source, bottom, entry, n, memo, epochs)
        finally:
            self.active = None

    def prearm(self, engine, layer_id, n, memo, epochs):
        """Arm one predicted run of an edit of ``layer_id``; False once there is nothing left."""
        from ...constants import CHANNELS
        graph = engine._native_graph
        if not ENABLED or graph is None or not prearm_enabled():
            return False
        roots = tuple((channel, engine.finals[channel][1]) for channel in CHANNELS
                      if engine.finals.get(channel) and engine.finals[channel][0] == 'unit')
        state = (layer_id, tuple(graph.stamps.get(root) for _, root in roots))
        if state == self.idle:
            return False
        if self.prearming is None or self.prearming[0] != state:
            self.prearming = (state, set())
        # A run armed for this state is never armed again, even if the budget
        # evicted it for a sibling: arming must not cycle while idle.
        done = self.prearming[1]
        for channel, root in roots:
            for top, source, key, members in graph.edit_runs(root, ('CHANNEL', layer_id, channel)):
                if len(members) < MIN_LENGTH or key in self.entries or key in done:
                    continue
                done.add(key)
                entry = self._arm(engine, members, n, memo, epochs)
                if entry is None:
                    self.idle = state
                    return False
                self.entries[key] = entry
                self._trim(protected=key)
                _warm(engine, engine._state(top).formats[0], n)
                metrics.count('engine_suffix_prearms')
                return True
        self.idle = state
        return False

    def _trim(self, protected):
        from .runtime import device
        total = self.memory()
        for key in list(self.entries):
            if total <= BUDGET_BYTES:
                break
            if key != protected:
                total -= self.entries[key].bytes
                _release(device(), self.entries.pop(key))

    def _arm(self, engine, members, n, memo, epochs):
        from .runtime import device
        dev = device()
        scalar = scalar_unit(members[0])
        work = 'RG32F' if scalar else 'RGBA32F'
        # Slopes and their products accumulate in full float. At display precision
        # the finished transform is stored in half floats, like every intermediate
        # of sequential rendering; Height and precise sets keep full float.
        compact = not engine.precise and members[0][2] != 'HEIGHT'
        final = ('RG16F' if scalar else 'RGBA16F') if compact else work
        zero, one = engine._constant(n, (0.0, 0.0, 0.0, 0.0)), engine._constant(n, (1.0, 1.0, 1.0, 1.0))
        accumulate = dev.fixed('suffix.accumulate')
        owned = {}   # id -> (texture, format): everything arming holds until it succeeds

        def allocate(fmt):
            texture = dev.texture(n, fmt)
            owned[id(texture)] = texture, fmt
            return texture

        def release(texture):
            _, fmt = owned.pop(id(texture))
            dev.release(texture, n, fmt)

        a, b, source, passes = one, zero, IDENTITY, 0
        try:
            f0, f1 = allocate(work), allocate(work)
            for index, key in enumerate(members):
                state = engine._state(key)
                program = state.program
                position = engine.positions[key]
                grouped_ref = position[1]
                grouped = (engine._texture_of(grouped_ref, n, memo, epochs, scalar_unit(key))
                           if grouped_ref is not None else None)
                held = grouped_ref[1] if grouped_ref is not None and grouped_ref[0] == 'unit' else None
                if held is not None:
                    engine._in_use[held] += 1
                try:
                    uniforms = state.uniforms()
                    for constant, target in ((zero, f0), (one, f1)):
                        samplers = [(s.name, engine._bind(s, n, memo, epochs, constant, grouped, key))
                                    for s in program.samplers]
                        dev.draw(state.shader, [target], n, samplers, uniforms)
                    fmt = final if index == len(members) - 1 else work
                    new_a, new_b = allocate(fmt), allocate(fmt)
                    dev.draw(accumulate, [new_a, new_b], n,
                             [('op_f0', f0), ('op_f1', f1), ('op_a', a), ('op_b', b)],
                             ints={'op_swizzle': pack(program.affine)})
                    passes += 3
                finally:
                    if held is not None:
                        engine._in_use[held] -= 1
                        engine._settle(held, n)
                for previous in (a, b):
                    if previous is not zero and previous is not one:
                        release(previous)
                a, b = new_a, new_b
                source = tuple(NONE if c == NONE else source[c] for c in program.affine)
        except MemoryError:
            for texture, fmt in owned.values():
                dev.release(texture, n, fmt)
            metrics.count('engine_suffix_arm_failures')
            return None
        except Exception:
            for texture, fmt in owned.values():
                dev.release(texture, n, fmt)
            raise
        release(f0)
        release(f1)
        engine.stages['passes']['SUFFIX_ARM'] += passes
        metrics.count('engine_suffix_arms')
        metrics.count('engine_suffix_armed_units', len(members))
        return Transform(a, b, final, n, source, 2 * texture_bytes(n, n, final))

    def _apply(self, engine, top, source, bottom, entry, n, memo, epochs):
        from .runtime import device
        dev = device()
        ref = ('unit', source) if source is not None else engine.positions[bottom][0]
        x = engine._texture_of(ref, n, memo, epochs, scalar_unit(bottom))
        state = engine._state(top)
        if state.textures is None:
            allocated = []
            try:
                for fmt in state.formats:
                    allocated.append(dev.texture(n, fmt))
            except Exception:
                for texture, fmt in zip(allocated, state.formats):
                    dev.release(texture, n, fmt)
                raise
            state.textures = allocated
        # A failed submission may have overwritten the target: never a cache hit then.
        previous, state.stamp = state.stamp, None
        dev.draw(dev.fixed('suffix.apply'), [state.textures[0]], n,
                 [('op_source', x), ('op_a', entry.a), ('op_b', entry.b)],
                 ints={'op_swizzle': pack(entry.source)})
        state.stamp = engine._native_graph.stamps[top]
        engine.rendered.append(top)
        engine._note_reads(top, previous != state.stamp, (source,) if source is not None else ())
        engine.stages['passes']['SUFFIX'] += 1
        metrics.count('engine_suffix_applications')
        if source is not None:
            engine._settle(source, n)
        if engine.graph.consumers.get(top):
            engine._retain(top, state, n)


def _warm(engine, fmt, n):
    """Compile the apply pass and create its pipeline for targets of ``fmt`` while idle (one
    texel of a scratch texture): the first application would otherwise pay for both."""
    from .runtime import device
    dev = device()
    warmed = dev.__dict__.setdefault('_suffix_warmed', set())
    if fmt in warmed:
        return
    warmed.add(fmt)
    constant = engine._constant(n, (0.0, 0.0, 0.0, 0.0))
    target = dev.texture(n, fmt)
    try:
        dev.draw(dev.fixed('suffix.apply'), [target], n,
                 [('op_source', constant), ('op_a', constant), ('op_b', constant)],
                 ints={'op_swizzle': 0}, region=(0, 0, 1, 1))
    finally:
        dev.release(target, n, fmt)


def _release_key(cache, key):
    from .runtime import device
    _release(device(), cache.entries.pop(key))


def _release(dev, entry):
    dev.release(entry.a, entry.n, entry.fmt)
    dev.release(entry.b, entry.n, entry.fmt)
