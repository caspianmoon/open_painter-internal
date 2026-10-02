"""Coherent host-Metal material commits backed by native immutable revisions.

All channel sources are rendered and targets validated before any host image
is changed. The Blender context thread submits the complete batch without yielding. A
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
        self._mips, self._channels, self._spares = {}, {}, {}
        self._sources, self._pins = {}, []
        self._pin_sources = True

    @staticmethod
    def check(status):
        if status:
            raise NativeError(status, 'Native material revision rejected the operation')

    def close(self):
        if self.submitting:
            raise RuntimeError("Cannot close a submitting native revision")
        self._release_scratch()
        self.trim_spares()
        if not self.handle:
            return
        if self.binding is not None:
            self.binding.revisions_close(self.handle)
        else:
            self.lib.op_revisions_destroy(self.handle)
        self.handle = None
        for texture, size, fmt in self.resources.values():
            self.dev.release(texture, size, fmt)
        for chain in self._mips.values():
            self._release_chain(chain)
        self._mips.clear()
        self._channels.clear()
        self.resources.clear()
        self.current.clear()
        self.targets.clear()

    def collect(self):
        for token, (texture, size, fmt) in list(self.resources.items()):
            references = (self.binding.revisions_references(self.handle, token) if self.binding is not None
                          else self.lib.op_revisions_references(self.handle, token))
            if not references:
                channel = self._channels.pop(token)
                chain = self._mips.pop(token, [])
                old = self._spares.pop(channel, [])
                self._release_chain(old)
                self._spares[channel] = [(texture, size, fmt), *chain]
                del self.resources[token]

    def _release_chain(self, chain):
        for texture, size, fmt in chain:
            self.dev.release(texture, size, fmt)

    def trim_spares(self):
        """Unreferenced work buffers remain reclaimable under device pressure."""
        for chain in self._spares.values():
            self._release_chain(chain)
        self._spares.clear()

    def stage(self, engine, image, channel, result, n):
        if tuple(image.size) != (n, n):
            raise ValueError(f'{image.name} is {tuple(image.size)}, expected {n} x {n}')
        dev = self.dev
        import gpu
        donor = engine._donor(n) if engine.pad else None
        # Recovery must preserve the actual image precision, including an
        # explicit alternate target; MRT writes can otherwise round differently.
        fmt = 'RGBA32F' if (engine.precise or channel == 'HEIGHT'
                           or gpu.texture.from_image(image).format == 'RGBA32F') else 'RGBA16F'
        chain = self._spares.pop(channel, [])
        if chain and chain[0][1:] != (n, fmt):
            self._release_chain(chain)
            chain = []
        texture = chain[0][0] if chain else dev.texture(n, fmt)
        self.serial += 1
        self.resources[self.serial] = texture, n, fmt
        self._channels[self.serial] = channel
        self._mips[self.serial] = chain[1:]
        # Keep the evaluated source alive until C has accepted or rolled back
        # the complete batch. Finish/pad and publish its base in a single MRT
        # pass, just as for subsequent mips; no staging copy is necessary.
        ints = {'op_mode': int(channel == 'NORMAL'), 'op_pad': int(donor is not None),
                'op_scalar': int(getattr(result, 'format', '') in {'RG16F', 'RG32F'})}
        if self._pin_sources:
            final = engine.finals[channel]
            if final[0] == 'unit':
                self._pins.append((engine, final[1], n))
                engine._in_use[final[1]] += 1
            self._sources[self.serial] = result, donor, ints
        else:
            # Large inputs cannot all remain pinned alongside two immutable
            # revisions within the device budget. Finish each source now so
            # its working textures can be reused by the next channel.
            dev.draw(dev.fixed('publish_pixels'), [texture], n,
                     [('op_source', result), ('op_donor', donor if donor is not None else result)], ints=ints)
            self._sources[self.serial] = None
        self.staged[channel] = image, self.serial

    def snapshot_chain(self, target, n, scratch):
        # Only a new/replaced target needs a snapshot. Match its storage rather
        # than widening every old channel to RGBA32F, and copy existing levels
        # exactly instead of assuming Blender generated them with our filter.
        fmt = 'RGBA32F' if target.format == 'RGBA32F' else 'RGBA16F'
        chain, level = [], 0
        while True:
            texture = self.dev.texture(n, fmt)
            scratch.append((texture, n, fmt))
            self.dev.draw(self.dev.fixed('copy_mip'), [texture], n,
                          [('op_source', target)], ints={'op_level': level})
            chain.append(texture)
            if n == 1:
                break
            n, level = max(1, n // 2), level + 1
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
        commands = []

        def prepare_commands():
            # Numeric edits can change the number of dirty channels. Compile
            # every bounded batch width up front, never on a warm subset edit.
            for name in ('publish', 'publish_pixels', 'mip', 'mip_2', 'mip_3'):
                dev.fixed(name)
            # Base finish passes retain channel-specific normal/padding rules.
            groups = {}
            for item in prepared.values():
                texture = item[5][0]
                target = {'texture': item[3], 'mip': 0}
                source = self._sources[item[2]]
                if source is None:
                    commands.append((shader, [target], texture.width, [('op_source', texture)], None))
                else:
                    source, donor, ints = source
                    commands.append((dev.fixed('publish'), [target, texture], texture.width,
                        [('op_source', source), ('op_donor', donor if donor is not None else source)], ints))
                groups.setdefault(texture.width, []).append(item)
            # Three independent channels share a draw. Blender's public
            # framebuffer constructor permits six color attachments.
            # Every lane performs the same exact four-texel reduction as before.
            for items in groups.values():
                for start in range(0, len(items), 3):
                    batch = items[start:start + 3]
                    count = len(batch)
                    mip_shader = dev.fixed('mip' if count == 1 else f'mip_{count}')
                    for level in range(1, len(batch[0][5])):
                        slots, samplers = [], []
                        for lane, item in enumerate(batch):
                            slots.extend(({'texture': item[3], 'mip': level}, item[5][level]))
                            samplers.append(('op_source' if count == 1 else f'op_source_{lane}', item[5][level - 1]))
                        commands.append((mip_shader, slots, batch[0][5][level].width, samplers, None))
            for _, slots, _, _, _ in commands:
                gpu.types.GPUFrameBuffer(color_slots=slots)

        def submit(action, index, level):
            if action == 0:  # Host resource preparation; native owns ordering and commit policy.
                channel, (image, token) = staged[index]
                texture, n, fmt = self.resources[token]
                target = gpu.texture.from_image(image)
                previous = self.current.get(channel)
                if previous is not None and previous[0] == image.as_pointer() and previous[2] == n:
                    old = self.resources[previous[1]][0]
                    before = [old, *(entry[0] for entry in self._mips[previous[1]])]
                else:
                    before = self.snapshot_chain(target, n, scratch)
                chain = self._mips[token]
                side = n >> len(chain)
                while side > 1:
                    side = max(1, side // 2)
                    chain.append((dev.texture(side, fmt), side, fmt))
                after = [texture, *(entry[0] for entry in chain)]
                prepared[index] = channel, image, token, target, before, after
                next_current[channel] = image.as_pointer(), token, n
                next_targets[channel] = image, target
                if index == len(staged) - 1:
                    prepare_commands()
            elif action == 2:
                item = prepared[index]
                target = {'texture': item[3], 'mip': level}
                texture = item[4][level]
                dev.draw(shader, [target], texture.width, [('op_source', texture)])
            else:  # C marks the entire batch touched before entering this call.
                for program, slots, size, samplers, ints in commands:
                    dev.draw(program, slots, size, samplers, ints=ints)

        if self.binding is not None:
            self.revision = self.binding.revisions_publish(self.handle, bytes(records), submit, True)
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
            self.lib.op_revisions_publish_batched.argtypes = [C.c_void_p, C.POINTER(Publication), C.c_uint32,
                                                      callback_type, C.c_void_p, C.POINTER(C.c_uint64)]
            self.lib.op_revisions_publish_batched.restype = C.c_int
            revision = C.c_uint64()
            status = self.lib.op_revisions_publish_batched(self.handle, records, len(records), callback, None, C.byref(revision))
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
                    chain = [texture, *(entry[0] for entry in self._mips[token])]
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
        from ..evaluation.engine.runtime import RETAIN_BYTES
        self._release_scratch()
        # Bound simultaneous final inputs by the result-cache allowance. At
        # the default budget 2K can fuse finishing, while 4K+ stages bases and
        # releases sources sequentially. Both use identical pixels/mip math.
        # Engines start a transaction only where the sources fit (runtime.revisions_fit); a
        # direct caller of a larger set still stages sequentially.
        self._pin_sources = engine.graph.n ** 2 * 16 * 8 <= RETAIN_BYTES
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
            self._sources.clear()
            pins, self._pins = self._pins, []
            for owner, key, n in pins:
                owner._in_use[key] -= 1
            for owner, key, n in pins:
                state = owner.states.get(key)
                if state is not None and state.textures:
                    # Final channel sources coexist during atomic publication.
                    # Account them in the bounded result cache instead of
                    # churning through the small, shared free-texture pool.
                    owner._retain(key, state, n)
                owner._settle(key, n)
            engine._trim_retained(engine.graph.n)
            self._release_scratch()
            self.collect()
