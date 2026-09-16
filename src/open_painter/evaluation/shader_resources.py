"""Explicit, isolated compatibility images for the procedural preview graph.

Only operations that cannot run in the material shader request these resources.
Work advances in bounded row strips. Numeric shader bindings never enter this
module. Cached images are derived inputs, never authored paint or baked outputs.
"""
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter

import bpy
import numpy as np

from ..constants import OWNER
from . import metrics, purpose

RESOURCE = 'open_painter_shader_resource'


@dataclass
class Entry:
    image: object
    job: object = None
    error: str = ''
    owner: str = ''   # layer id the work is attributed to (0.9.0 profiling)


class Resources:
    def __init__(self, size):
        self.size = size
        self.entries = {}
        self.used = set()
        self.queue = []
        self.completed = 0
        self.timings = {}   # owner layer id -> seconds of factory work since the last step report

    def request(self, signature, factory, owner=''):
        """Return a cached image or queue just this immutable source snapshot.

        factory() returns an iterator yielding (low_row, HxWxC float data).
        The iterator must finish with all rows populated. Publication is atomic.
        ``owner`` names the layer whose profile the work is attributed to.
        """
        key = sha256(repr((self.size, signature)).encode()).hexdigest()
        self.used.add(key)
        if key in self.entries:
            if self.entries[key].error:
                raise ValueError(self.entries[key].error)
            return self.entries[key].image
        image = next((item for item in bpy.data.images
                      if item.get(RESOURCE) == key and tuple(item.size) == (self.size, self.size)
                      and item.get('op_shader_resource_ready')), None)
        if image:
            self.entries[key] = Entry(image, owner=owner)
            metrics.count('shader_resource_cache_hits')
            return image
        image = bpy.data.images.new('OP Shader Source · ' + key[:12],
                                    width=self.size, height=self.size, alpha=True, float_buffer=True)
        image[OWNER] = True
        image[RESOURCE] = key
        image['op_shader_resource_ready'] = False
        image.colorspace_settings.name = 'Non-Color'
        image.generated_color = (.5, .5, .5, 1)
        entry = Entry(image, owner=owner)
        entry.job = self._produce(entry, factory())
        self.entries[key] = entry
        self.queue.append(key)
        metrics.count('shader_resource_requests')
        return image

    def _produce(self, entry, parts):
        # Allocate only for an actually scheduled unsupported source. No per-event
        # material/output buffers are created by the native graph.
        data = np.ones((self.size, self.size, 4), np.float32)
        covered = np.zeros(self.size, dtype=np.bool_)
        for low, rows in parts:
            rows = np.asarray(rows, dtype=np.float32)
            high = low + rows.shape[0]
            if rows.ndim == 2:
                data[low:high, :, :3] = rows[..., None]
            elif rows.shape[-1] == 4:
                data[low:high] = rows
            else:
                data[low:high, :, :3] = rows
            covered[low:high] = True
            metrics.count('shader_resource_rows', high - low)
            yield
        if not covered.all() or not np.isfinite(data).all():
            raise ValueError('Incomplete or non-finite shader compatibility resource')
        # Do not use authored image_manager.write: this is a separate disposable
        # resource, with a separately instrumented publication boundary.
        entry.image.pixels.foreach_set(data.ravel())
        entry.image.update()
        entry.image['op_shader_resource_ready'] = True
        metrics.count('shader_resource_publications')
        metrics.count('shader_resource_written_bytes', data.nbytes)
        self.completed += 1

    @property
    def pending(self):
        return bool(self.queue)

    def step(self, budget=.006):
        started = perf_counter()
        published = self.completed
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
                if not entry.image.users:
                    bpy.data.images.remove(entry.image)
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
            if perf_counter() - started >= budget:
                break
        # Retain at most 64 MiB of unused completed leaves for undo/slider
        # revisits. Active shader inputs are never evicted.
        unused = [key for key, entry in self.entries.items()
                  if key not in self.used and not entry.job and not entry.image.users]
        keep = (64 * 1024 * 1024) // (self.size * self.size * 16)
        for key in unused[:max(0, len(unused) - keep)]:
            entry = self.entries.pop(key)
            bpy.data.images.remove(entry.image)
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
                if not entry.image.users:
                    bpy.data.images.remove(entry.image)
            except ReferenceError:
                pass
        self.entries.clear()
        self.queue.clear()


def values(group, excluded=()):
    """Stable authoring inputs without UI state or the global dirty serial."""
    from .resource_keys import image_key, datablock_key
    # Cache state (0.9.0) never enters an identity: a cache publication must
    # not re-identify the layer it was built for.
    skip = {'rna_type', 'id_data', 'id', 'name', 'dirty', 'order', 'active_effect',
            'active_mask', 'expanded', 'image_name', 'detail_image_name', 'cache', 'cache_mode',
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
