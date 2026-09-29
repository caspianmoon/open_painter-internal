"""Coherent host-Metal material commits backed by native immutable revisions.

All channels are rendered and validated before any host image is changed. The
Blender context thread submits the complete copy batch without yielding. A
recoverable submission error restores every touched image from GPU snapshots.
No host pixels, foreign handles, or private Blender APIs are involved.
"""
import ctypes as C
from contextlib import contextmanager
from .core import NativeError


class Frame(C.Structure):
    _fields_ = [('instance', C.c_uint64), ('revision', C.c_uint64), ('lease', C.c_uint64),
                ('resources', C.c_uint64 * 8), ('channel_mask', C.c_uint32), ('reserved', C.c_uint32)]


class Publication(C.Structure):
    _fields_ = [('resource', C.c_uint64), ('channel', C.c_uint32), ('size', C.c_uint32)]


class Revisions:
    def __init__(self, lib, dev):
        self.lib, self.dev, self.handle = lib, dev, C.c_void_p()
        for name, args, result in [
            ('create', [C.POINTER(C.c_void_p)], C.c_int), ('destroy', [C.c_void_p], None),
            ('begin', [C.c_void_p, C.c_uint32], C.c_int),
            ('stage', [C.c_void_p, C.c_uint32, C.c_uint64], C.c_int),
            ('commit', [C.c_void_p, C.POINTER(C.c_uint64)], C.c_int),
            ('abort', [C.c_void_p], None), ('acquire', [C.c_void_p, C.POINTER(Frame)], C.c_int),
            ('release', [C.c_void_p, C.POINTER(Frame)], C.c_int),
            ('references', [C.c_void_p, C.c_uint64], C.c_uint32)]:
            fn = getattr(lib, 'op_revisions_' + name)
            fn.argtypes, fn.restype = args, result
        self.binding = getattr(lib, '_op_binding', None)
        if self.binding is not None:
            self.handle = self.binding.revisions_create()
        else:
            self.check(lib.op_revisions_create(C.byref(self.handle)))
        self.resources, self.current, self.staged = {}, {}, {}
        self.targets = {}
        self.scene_uid = None
        self.serial = self.revision = 0
        self.submitting = False
        self._scratch = []

    @staticmethod
    def check(status):
        if status:
            raise NativeError(status, 'Native material revision rejected the operation')

    def close(self):
        if self.submitting:
            raise RuntimeError("Cannot close a submitting native revision")
        self._release_scratch()
        if not self.handle:
            return
        if self.binding is not None:
            self.binding.revisions_close(self.handle)
        else:
            self.lib.op_revisions_destroy(self.handle)
        self.handle = None
        for texture, size, fmt in self.resources.values():
            self.dev.release(texture, size, fmt)
        self.resources.clear()
        self.current.clear()
        self.targets.clear()

    def collect(self):
        for token, (texture, size, fmt) in list(self.resources.items()):
            references = (self.binding.revisions_references(self.handle, token) if self.binding is not None
                          else self.lib.op_revisions_references(self.handle, token))
            if not references:
                self.dev.release(texture, size, fmt)
                del self.resources[token]

    def stage(self, engine, image, channel, result, n):
        if tuple(image.size) != (n, n):
            raise ValueError(f'{image.name} is {tuple(image.size)}, expected {n} x {n}')
        dev = self.dev
        donor = engine._donor(n) if engine.pad else None
        fmt = 'RGBA32F' if engine.precise or channel == 'HEIGHT' else 'RGBA16F'
        texture = dev.texture(n, fmt)
        try:
            dev.draw(dev.fixed('publish_pixels'), [texture], n,
                     [('op_source', result), ('op_donor', donor if donor is not None else result)],
                     ints={'op_mode': int(channel == 'NORMAL'), 'op_pad': int(donor is not None),
                           'op_scalar': int(getattr(result, 'format', '') in {'RG16F', 'RG32F'})})
        except BaseException:
            dev.release(texture, n, fmt)
            raise
        self.serial += 1
        self.resources[self.serial] = texture, n, fmt
        self.staged[channel] = image, self.serial

    def mip_chain(self, texture, n, fmt, scratch):
        chain = [texture]
        while n > 1:
            n = max(1, n // 2)
            smaller = self.dev.texture(n, fmt)
            scratch.append((smaller, n, fmt))
            self.dev.draw(self.dev.fixed('downsample'), [smaller], n, [('op_source', chain[-1])])
            chain.append(smaller)
        return chain

    def commit(self):
        import gpu
        from ..constants import CHANNELS
        if not self.staged:
            return
        staged = list(self.staged.items())
        records = (Publication * len(staged))(*(Publication(token, CHANNELS.index(channel), self.resources[token][1])
                                                for channel, (_, token) in staged))
        scratch, prepared = self._scratch, {}
        next_current, next_targets = dict(self.current), dict(self.targets)
        dev = self.dev
        shader = dev.fixed('copy')

        def submit(action, index, level):
            if action == 0:  # Host resource preparation; native owns ordering and commit policy.
                channel, (image, token) = staged[index]
                texture, n, fmt = self.resources[token]
                target = gpu.texture.from_image(image)
                previous = self.current.get(channel)
                if previous is not None and previous[0] == image.as_pointer() and previous[2] == n:
                    old = self.resources[previous[1]][0]
                else:
                    old = dev.texture(n, 'RGBA32F')
                    scratch.append((old, n, 'RGBA32F'))
                    dev.draw(shader, [old], n, [('op_source', target)])
                before = self.mip_chain(old, n, old.format, scratch)
                after = self.mip_chain(texture, n, fmt, scratch)
                for mip in range(len(after)):
                    gpu.types.GPUFrameBuffer(color_slots=[{'texture': target, 'mip': mip}])
                prepared[index] = channel, image, token, target, before, after
                next_current[channel] = image.as_pointer(), token, n
                next_targets[channel] = image, target
            else:
                item = prepared[index]
                texture = item[5 if action == 1 else 4][level]
                dev.draw(shader, [{'texture': item[3], 'mip': level}], texture.width, [('op_source', texture)])

        if self.binding is not None:
            self.revision = self.binding.revisions_publish(self.handle, bytes(records), submit)
        else:
            # Development-only ctypes bridge to the same native transaction driver.
            errors = []
            callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32)
            def invoke(_user, action, index, level):
                try:
                    submit(action, index, level)
                    return 0
                except BaseException as error:
                    errors.append((action, error))
                    return 7
            callback = callback_type(invoke)
            self.lib.op_revisions_publish.argtypes = [C.c_void_p, C.POINTER(Publication), C.c_uint32,
                                                      callback_type, C.c_void_p, C.POINTER(C.c_uint64)]
            self.lib.op_revisions_publish.restype = C.c_int
            revision = C.c_uint64()
            status = self.lib.op_revisions_publish(self.handle, records, len(records), callback, None, C.byref(revision))
            if any(action == 2 for action, _ in errors):
                raise RuntimeError('GPU publication and recovery failed; reevaluate after context recovery') from errors[-1][1]
            if errors:
                raise errors[0][1]
            self.check(status)
            self.revision = revision.value
        self.current, self.targets = next_current, next_targets

    def _release_scratch(self):
        # Retirement follows channel acceptance. A pool cleanup error cannot
        # turn an already visible revision into a rejected channel batch.
        while self._scratch:
            texture, size, fmt = self._scratch[-1]
            self.dev.release(texture, size, fmt)
            self._scratch.pop()

    def restore_evicted(self):
        """Rebind the accepted revision if Blender recreates an image texture.

        Holding the public wrapper gives a stable identity on the qualified
        host. Restoration runs before a viewport draw, without CPU pixel writes
        or a new authoring revision. This is also called by the normal timer.
        """
        if not self.handle or self.submitting or not self.targets:
            return
        import gpu
        from ..evaluation import metrics
        replacements = []
        for channel, (image, previous) in tuple(self.targets.items()):
            try:
                if image.as_pointer() != self.current[channel][0]:
                    continue
                target = gpu.texture.from_image(image)
                if target is not previous:
                    replacements.append((channel, image, target))
            except ReferenceError:
                self.targets.pop(channel, None)
        if not replacements:
            return
        scratch, commands = [], []
        try:
            with self.dev.state():
                shader = self.dev.fixed('copy')
                for channel, image, target in replacements:
                    _, token, n = self.current[channel]
                    texture, _, fmt = self.resources[token]
                    if tuple(image.size) != (n, n):
                        continue  # the recipe's resolution rebuild owns this change
                    chain = self.mip_chain(texture, n, fmt, scratch)
                    for level, mip in enumerate(chain):
                        gpu.types.GPUFrameBuffer(color_slots=[{'texture': target, 'mip': level}])
                        commands.append((target, level, mip))
                for target, level, mip in commands:
                    self.dev.draw(shader, [{'texture': target, 'mip': level}], mip.width, [('op_source', mip)])
                for channel, image, target in replacements:
                    if tuple(image.size) == (self.current[channel][2],) * 2:
                        self.targets[channel] = image, target
                        metrics.count('native_image_restorations')
        finally:
            for texture, n, fmt in scratch:
                self.dev.release(texture, n, fmt)

    @contextmanager
    def transaction(self, engine, *, external_commit=False):
        self._release_scratch()
        self.scene_uid = engine.scene_uid
        previous = (dict(engine.published), dict(engine.images), set(engine.dirty_cpu))
        self.staged = {}
        self.submitting = True
        try:
            yield
            if not external_commit:
                self.commit()
        except BaseException:
            engine.published, engine.images, engine.dirty_cpu = previous
            engine.stages['published'].clear()
            raise
        finally:
            self.submitting = False
            self.staged = {}
            self._release_scratch()
            self.collect()
