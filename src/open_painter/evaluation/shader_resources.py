"""Explicit, isolated compatibility images for the procedural preview graph.

Only operations that cannot run in the material shader request these resources.
Work advances in bounded row strips. Numeric shader bindings never enter this
module. Cached images are derived inputs, never authored paint or baked outputs.
"""
import weakref
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter

import bpy
import numpy as np

from ..constants import OWNER
from . import metrics, purpose

RESOURCE = 'open_painter_shader_resource'


INTERACTIVE_BUDGET = .2    # seconds a step may spend on the leaves that finish a gesture: every
                           # channel of the stroke publishes in one step, so the viewport re-uploads once


@dataclass
class Entry:
    image: object
    job: object = None
    error: str = ''
    owner: str = ''   # layer id the work is attributed to (0.9.0 profiling)
    interactive: bool = False   # a small incremental job the artist is waiting on (a stroke's leaf)


def alive(image):
    """False once Blender removed the datablock behind a held Image reference."""
    try:
        image.name
    except ReferenceError:
        return False
    return True


def bound(image):
    """A node still samples the image: a Blender node (a user) or an engine unit's node."""
    from .engine.headless import bound_count
    try:
        return bool(image.users) or bound_count(image) > 0
    except ReferenceError:
        return False


_leaf_index = {}   # RESOURCE key -> image, rebuilt from bpy.data when a key is missing
_serial = [0]      # publication order of leaves (their ``op_resource_serial``), for the sweep


def _usable(image, key, size):
    """A published leaf that still holds its pixels: an undo or Blender's image cache can
    leave a leaf flagged ready but regenerated blank, which must never be bound again. A
    leaf saved with the file holds them packed (loaded on first use)."""
    return (alive(image) and image.get(RESOURCE) == key and image.get('op_shader_resource_ready')
            and tuple(image.size) == (size, size) and (image.has_data or image.packed_file is not None))


def persist_bound_leaves():
    """Before a save: the leaves the engine binds get a fake user, so they are written (and
    packed with the other owned images) and a reopened file adopts them; any other leaf loses
    it and is not written. Only saves change the flag, so a saved leaf counts as bound until
    the next save (``bound``)."""
    from .engine.headless import bound_count
    for image in bpy.data.images:
        if not image.get(RESOURCE):
            continue
        keep = bool(image.get('op_shader_resource_ready')) and bound_count(image) > 0
        if image.use_fake_user != keep:
            image.use_fake_user = keep


_released = []   # accounting only

# After a memfile undo/redo the timer reconstructs the graph from the persisted material tree.
# Creating a *new* leaf datablock in that window (and binding it to a persisted node) is the
# open 0.13 crash: a later undo frees the datablock while the tree still points at it, and the
# next rehydration walks a freed pointer (docs/quality-stability-0.13.md, S2). While frozen,
# ``request`` never allocates a new datablock: it regenerates into the datablock the node already
# binds (``reuse``) or returns None so the node keeps its current binding until the next authored,
# undo-pushed gesture releases the freeze. Content still refreshes; only creation is deferred.
_frozen_new_leaves = [False]


def freeze_new_leaves(value):
    """Enter (True) or leave (False) the post-undo window where no new leaf datablock is created."""
    _frozen_new_leaves[0] = bool(value)


def new_leaves_frozen():
    return _frozen_new_leaves[0]


def release_image(image):
    """Remove a leaf, placeholder or preview datablock nothing binds any more.

    Only zero-user datablocks come here; the datablock is removed as 0.13.0 did (its
    references are remapped, which is what keeps the draw engine safe — scaling or unpacking
    an image a compiled material still sampled crashed it instead). A leaf a node still
    binds is never released: after an undo or reload its pixels are regenerated into the
    same datablock (``Resources.request(..., reuse=)``)."""
    try:
        if not alive(image) or bound(image):
            return
        bpy.data.images.remove(image)
    except (ReferenceError, RuntimeError):
        pass


def sweep_graveyard(now=None, force=False):
    """Kept for callers: nothing is deferred any more."""
    return 0


def released_count():
    return 0


def forget_graveyard():
    _released.clear()


def released(image):
    """True once ``release_image`` let the image go: the datablock is gone."""
    return not alive(image)


def retire_unloaded_leaves():
    """Leaves whose pixels did not survive an undo, a redo or a file load: the datablock came
    back from the memfile or the file flagged ready, without its buffer (``has_data`` False,
    nothing packed to reload). Left alone, the first viewport draw regenerates such a leaf
    blank — and from then on it passes every ``has_data`` check while showing nothing and
    seeding incremental updates with empty rows. Called from the undo/redo/load handlers,
    before any draw, so the session rebuilds these leaves instead of trusting them."""
    retired = 0
    now = perf_counter()
    for image in bpy.data.images:
        if not image.get(RESOURCE) or not image.get('op_shader_resource_ready'):
            continue
        if image.has_data or image.packed_file is not None:
            continue
        image['op_shader_resource_ready'] = False
        # Marked, never removed in the session: a node that still binds it gets its pixels
        # regenerated into this datablock (``Resources.request(reuse=)``); an unbound one
        # stays a datablock header until reopen.
        image['op_resource_retired'] = now
        if 'op_resource_state' in image:
            del image['op_resource_state']
        retired += 1
    if retired:
        _leaf_index.clear()
        metrics.count('shader_resource_leaves_retired_unloaded', retired)
    return retired




def _published_leaf(key, size):
    """A ready leaf of ``key`` and ``size`` in bpy.data, found through an index so a request
    does not walk every image of the file; the index is rebuilt on a miss."""
    image = _leaf_index.get(key)
    if image is not None and _usable(image, key, size):
        return image
    _leaf_index.clear()
    found = None
    for item in bpy.data.images:
        item_key = item.get(RESOURCE)
        if item_key and item.get('op_shader_resource_ready'):
            _leaf_index[item_key] = item
            if item_key == key and _usable(item, key, size):
                found = item
    return found


def _touch(image):
    _serial[0] += 1
    image['op_resource_serial'] = _serial[0]


_tables = weakref.WeakSet()   # every live Resources table, for the orphan sweep


def sweep_orphans(keep_bytes=64 * 1024 * 1024):
    """Published leaves nothing binds and no live table lists (an undo or a rebuilt graph left
    them behind): the newest ``keep_bytes`` worth stay for revisits, the rest are removed. A
    session of hundreds of strokes would otherwise keep every leaf it ever published."""
    listed = {entry.image.as_pointer() for table in _tables for entry in table.entries.values() if alive(entry.image)}
    orphans = []
    for image in bpy.data.images:
        if not image.get(RESOURCE) or bound(image):
            continue
        if image.get('op_resource_retired') is not None:
            continue   # bufferless since an undo or reload: left alone (a datablock header) until reopen
        if not image.get('op_shader_resource_ready'):
            continue
        if image.as_pointer() in listed:
            continue
        orphans.append((image.get('op_resource_serial', 0), image))
    orphans.sort(key=lambda item: -item[0])
    held = 0
    removed = 0
    for _, image in orphans:
        held += image.size[0] * image.size[1] * 16
        if held <= keep_bytes:
            continue
        try:
            release_image(image)
            removed += 1
        except (ReferenceError, RuntimeError):
            pass
    if removed:
        _leaf_index.clear()
        metrics.count('shader_resource_orphans_removed', removed)
    sweep_graveyard()
    return removed


class Resources:
    def __init__(self, size):
        _tables.add(self)
        self.size = size
        self.entries = {}
        self.used = set()
        self.queue = []
        self.completed = 0
        self.timings = {}   # owner layer id -> seconds of factory work since the last step report
        # Leaves a rehydrated or adopted node still samples under an identity this
        # table does not list (an interrupted placeholder, a leaf of another size):
        # released once the binding moves on, never cached.
        self.superseded = []
        # Leaves a pending incremental job reads its base rows from: never evicted while pinned.
        self.pins = {}

    def pin(self, image):
        key = image.get(RESOURCE) if alive(image) else None
        if key:
            self.pins[key] = self.pins.get(key, 0) + 1
        return key

    def unpin(self, key):
        if key in self.pins:
            self.pins[key] -= 1
            if self.pins[key] <= 0:
                del self.pins[key]

    def key(self, signature):
        return sha256(repr((self.size, signature)).encode()).hexdigest()

    def request(self, signature, factory, owner='', placeholder=(.5, .5, .5, 1), interactive=False, reuse=None, stable=None):
        """Return a cached image or queue just this immutable source snapshot.

        ``stable`` is the datablock a node already binds that must stay bound: the new content is
        produced into it in place (re-keyed) rather than into a new datablock, so a stroke boundary
        keeps one leaf for its life and a memfile undo never frees a leaf the material still samples
        (the open 0.13 crash). The datablock is left ready while ``factory()`` runs so an incremental
        base can read its current pixels, then flipped to pending; content, not the datablock, changes.

        factory() returns an iterator yielding (low_row, HxWxC float data). It may
        start with ``('base', image)``, a published leaf whose pixels fill every row
        before the parts that follow overwrite the rows that changed (an incremental
        re-identification), and end with ``('state', mapping)``, stored on the leaf as
        ``op_resource_state`` for the next factory to build on. The rows must all be
        populated when the iterator finishes. Publication is atomic. ``owner`` names
        the layer whose profile the work is attributed to; ``placeholder`` is the
        colour the leaf shows until it is published. ``reuse`` is a leaf datablock the
        requesting node still binds whose pixels did not survive an undo or reload: the
        source is produced into that datablock instead of a new one, so the node never
        swaps images and the draw engine never sees the old one go (0.13 hardening).
        """
        key = self.key(signature)
        self.used.add(key)
        if key in self.entries and not alive(self.entries[key].image):
            # Another set's table evicted the shared leaf, or Purge Unused Data
            # took it: the source is scheduled again rather than handed back dead.
            self._drop(key)
        if (stable is not None and alive(stable) and stable.get(RESOURCE)
                and tuple(stable.size) == (self.size, self.size)):
            existing = self.entries.get(key)
            if existing is not None and existing.image == stable and not existing.job and not existing.error:
                return stable   # already this content in this datablock
            # Re-key the bound datablock and produce the new content into it. Any entry that lists
            # this datablock (this binding's previous content, under the old key) is dropped, and
            # its job closed; the datablock itself is kept and never removed.
            for other in [k for k, e in self.entries.items() if e.image == stable or k == key]:
                dropped = self.entries.pop(other)
                if dropped.job:
                    dropped.job.close()
                if other in self.queue:
                    self.queue.remove(other)
                if dropped.image is not stable and alive(dropped.image) and not bound(dropped.image):
                    release_image(dropped.image)   # a cached datablock for this content is superseded
            entry = Entry(stable, owner=owner, interactive=interactive)
            # factory() reads the incremental base (the still-ready ``stable``) as it is built.
            entry.job = self._produce(entry, factory())
            stable[OWNER] = True
            stable[RESOURCE] = key
            stable['op_shader_resource_ready'] = False
            if 'op_resource_state' in stable:
                del stable['op_resource_state']
            self.entries[key] = entry
            self.queue.append(key)
            _leaf_index.clear()
            metrics.count('shader_resource_inplace_regenerations')
            return stable
        if key in self.entries:
            if self.entries[key].error:
                raise ValueError(self.entries[key].error)
            return self.entries[key].image
        image = _published_leaf(key, self.size)
        if image:
            self.entries[key] = Entry(image, owner=owner)
            _touch(image)
            metrics.count('shader_resource_cache_hits')
            return image
        if reuse is None:
            # A leaf of this source that lost its pixels to an undo or reload: produced again
            # into the same datablock rather than beside it.
            reuse = next((item for item in bpy.data.images if item.get(RESOURCE) == key
                          and item.get('op_resource_retired') is not None), None)
        if (reuse is not None and alive(reuse) and tuple(reuse.size) == (self.size, self.size)
                and not reuse.get('op_shader_resource_ready')
                and not any(entry.image == reuse for table in _tables for entry in table.entries.values())):
            image = reuse
            metrics.count('shader_resource_reused_datablocks')
        else:
            # Leaves are read by the Open Painter engine only, never bound into a material,
            # so one created between undo steps cannot leave a material pointing at freed data.
            image = bpy.data.images.new('OP Shader Source · ' + key[:12],
                                        width=self.size, height=self.size, alpha=True, float_buffer=True)
        image[OWNER] = True
        image[RESOURCE] = key
        if 'op_resource_retired' in image:
            del image['op_resource_retired']
        if image.use_half_precision:
            # Leaves feed levels and thresholds that can steepen them tenfold: half floats on
            # the GPU (Blender's default for float images) would round 0.5 to a 5e-4 step.
            image.use_half_precision = False
        image['op_shader_resource_ready'] = False
        if 'op_resource_state' in image:
            del image['op_resource_state']
        image.colorspace_settings.name = 'Non-Color'
        image.generated_color = tuple(placeholder)
        entry = Entry(image, owner=owner, interactive=interactive)
        entry.job = self._produce(entry, factory())
        self.entries[key] = entry
        self.queue.append(key)
        metrics.count('shader_resource_requests')
        return image

    def adopt(self, image):
        """List a finished leaf a rehydrated node held under an identity it no
        longer has, so the retention budget evicts it like any superseded source
        instead of leaving it in bpy.data at 0 users; an unfinished one (or a
        leaf of another size) is removed as soon as nothing samples it. While a
        node still binds it, it waits in ``superseded`` (0.9.8): a node that moves
        on, or dies with a recompiled unit, would otherwise leave it in bpy.data
        unlisted forever."""
        key = image.get(RESOURCE)
        if not key:
            return
        listed = self.entries.get(key)
        if listed is not None and listed.image == image:
            return
        if (listed is None and image.get('op_shader_resource_ready')
                and tuple(image.size) == (self.size, self.size)):
            self.entries[key] = Entry(image)
        elif image.get('op_resource_retired') is not None:
            return   # the orphan sweep removes it after its grace period
        elif not bound(image):
            release_image(image)
        elif not any(held == image for held in self.superseded):
            self.superseded.append(image)

    def failure(self, signature):
        """The failed entry this table holds for ``signature``, or None."""
        entry = self.entries.get(self.key(signature))
        return entry if entry is not None and entry.error and alive(entry.image) else None

    def inherit(self, signature, previous):
        """Move ``previous``'s failed raster of ``signature`` into this table (0.9.8).

        A rebuilt graph adopting the binding reports the failure it inherited
        instead of running the factory again: a permanently failing input would
        otherwise re-run on every structural rebuild. The entry leaves the
        previous table so closing that table cannot take the placeholder the
        binding still shows. Returns the image, or None when nothing failed.
        """
        entry = previous.failure(signature)
        if entry is None:
            return None
        key = self.key(signature)
        del previous.entries[key]
        self.entries[key] = entry
        self.used.add(key)
        return entry.image

    def restore(self, signature, previous):
        """Hand an inherited failure back (a rolled-back build)."""
        key = self.key(signature)
        entry = self.entries.get(key)
        if entry is not None and entry.error and key not in previous.entries:
            previous.entries[key] = self.entries.pop(key)

    def forget_failures(self):
        """Drop every failed entry (Retry) so its source is requested again; a
        placeholder nothing samples goes with it. Returns the dropped keys."""
        failed = {key for key, entry in self.entries.items() if entry.error}
        for key in failed:
            entry = self.entries.pop(key)
            if alive(entry.image) and not bound(entry.image):
                release_image(entry.image)
        return failed

    def _release_superseded(self):
        kept = []
        for image in self.superseded:
            if not alive(image):
                continue
            if bound(image):
                kept.append(image)
            else:
                release_image(image)
        self.superseded = kept

    def _produce(self, entry, parts):
        # Allocate only for an actually scheduled unsupported source. No per-event
        # material/output buffers are created by the native graph.
        # Every row must be covered before publication (checked below), so the buffer
        # needs no fill: at 4k that fill alone was a quarter of a small incremental job.
        data = np.empty((self.size, self.size, 4), np.float32)
        covered = np.zeros(self.size, dtype=np.bool_)
        state = None
        for low, rows in parts:
            if low == 'base':
                # An earlier leaf of the same source: every row starts as it published it.
                if not alive(rows) or tuple(rows.size) != (self.size, self.size):
                    raise ValueError('The base leaf of an incremental shader resource is gone')
                rows.pixels.foreach_get(data.ravel())
                covered[:] = True
                metrics.count('shader_resource_base_rows', self.size)
                yield
                continue
            if low == 'state':
                state = rows
                continue
            rows = np.asarray(rows, dtype=np.float32)
            high = low + rows.shape[0]
            if rows.ndim == 2:
                data[low:high, :, :3] = rows[..., None]
                data[low:high, :, 3] = 1.0
            elif rows.shape[-1] == 4:
                data[low:high] = rows
            else:
                data[low:high, :, :3] = rows
                data[low:high, :, 3] = 1.0
            covered[low:high] = True
            metrics.count('shader_resource_rows', high - low)
            yield
        if not covered.all() or not np.isfinite(data).all():
            raise ValueError('Incomplete or non-finite shader compatibility resource')
        # Do not use authored image_manager.write: this is a separate disposable
        # resource, with a separately instrumented publication boundary.
        entry.image.pixels.foreach_set(data.ravel())
        entry.image.update()
        if state is not None:
            import json
            entry.image['op_resource_state'] = json.dumps(state)
        _touch(entry.image)
        entry.image['op_shader_resource_ready'] = True
        metrics.count('shader_resource_publications')
        metrics.count('shader_resource_written_bytes', data.nbytes)
        self.completed += 1

    @property
    def pending(self):
        return bool(self.queue)

    def _drop(self, key):
        """Forget an entry whose image no longer exists, closing its job."""
        entry = self.entries.pop(key)
        if entry.job:
            entry.job.close()
        entry.job = None
        if key in self.queue:
            self.queue.remove(key)

    def step(self, budget=.006):
        started = perf_counter()
        published = self.completed
        # Entries are validated lazily: nothing tells this table when another
        # set's table or a purge removes an image it still lists.
        for key in [key for key, entry in self.entries.items() if not alive(entry.image)]:
            self._drop(key)
        self._release_superseded()
        while self.queue:
            key = self.queue[0]
            entry = self.entries[key]
            # A later unsupported-source edit supersedes only this unused leaf.
            if key not in self.used:
                if entry.job:
                    entry.job.close()
                entry.job = None
                self.queue.pop(0)
                # Never cache an unfinished neutral placeholder. Returning to
                # this parameter value must schedule its source again.
                self.entries.pop(key)
                if not bound(entry.image):
                    release_image(entry.image)
                continue
            job_started = perf_counter()
            try:
                with purpose.preview():
                    next(entry.job)
            except StopIteration:
                entry.job = None
                self.queue.pop(0)
            except Exception as error:
                entry.error = str(error)
                entry.job = None
                self.queue.pop(0)
                raise
            finally:
                self.timings[entry.owner] = self.timings.get(entry.owner, 0.0) + perf_counter() - job_started
            if perf_counter() - started >= (max(budget, INTERACTIVE_BUDGET) if entry.interactive else budget):
                break
        # Retain at most 64 MiB of unused completed leaves for undo/slider
        # revisits. Active shader inputs are never evicted.
        unused = [key for key, entry in self.entries.items()
                  if key not in self.used and not entry.job and not bound(entry.image) and key not in self.pins]
        keep = (64 * 1024 * 1024) // (self.size * self.size * 16)
        for key in unused[:max(0, len(unused) - keep)]:
            entry = self.entries.pop(key)
            release_image(entry.image)
        if published != self.completed:
            # A publication is when leaves change hands: leaves no table lists any more go too.
            sweep_orphans()
        timings, self.timings = self.timings, {}
        return {'resource_publications': self.completed - published,
                'pending_resources': len(self.queue),
                'resource_step_ms': (perf_counter() - started) * 1000,
                'owner_timings': timings}

    def close(self):
        for entry in self.entries.values():
            if entry.job:
                entry.job.close()
            try:
                if not bound(entry.image):
                    release_image(entry.image)
            except ReferenceError:
                pass
        self.entries.clear()
        self.queue.clear()
        self._release_superseded()
        _tables.discard(self)


def values(group, excluded=()):
    """Stable authoring inputs without UI state or the global dirty serial."""
    from .resource_keys import image_key, datablock_key
    # Cache state (0.9.0) never enters an identity: a cache publication must
    # not re-identify the layer it was built for. Metre sizes (0.9.5) act only
    # through the texture fields they derive, so locking a set to metres
    # re-identifies nothing. ``opacity_percent`` is the slider mirror of
    # ``opacity``: callers excluding the uniform must not get it back in 1% steps.
    skip = {'rna_type', 'id_data', 'id', 'name', 'dirty', 'order', 'active_effect',
            'active_mask', 'expanded', 'image_name', 'detail_image_name', 'cache', 'cache_mode',
            'units', 'feature_size', 'physical_radius', 'opacity_percent', 'master_factor', 'master_scale',
            *excluded}
    result = []
    for field in group.bl_rna.properties:
        name = field.identifier
        if name in skip:
            continue
        value = getattr(group, name)
        if field.type == 'COLLECTION':
            value = tuple(values(item) for item in value)
        elif field.type == 'POINTER':
            if isinstance(value, bpy.types.PropertyGroup):
                value = values(value)
            elif isinstance(value, bpy.types.Image):
                value = image_key(value)
            else:
                value = datablock_key(value)
        elif getattr(field, 'is_array', False):
            value = tuple(value)
        result.append((name, value))
    return tuple(result)
