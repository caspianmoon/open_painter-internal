"""Each texture set's layer stack lives in a hidden Palette datablock, not on its Scene.

Blender copies a Scene into its evaluated copy after every edit of a property the Scene
owns, and the copy takes every property along: with the stacks on the Scene, each slider
tick of a drag copied the whole project (5 ms at 200 smart-material layers, growing with
the stack). A Palette is never copied for evaluation. It is saved, undone, appended and
linked with the Scene that points at it, and a name starting with a dot keeps it out of
Blender's lists.

Everything else of a set stays on the Scene, so keyframes on set settings (an animated
age) keep their paths. ``OP_TextureSet.layers`` reads the store; a file written before the
store existed carries the stack on the set, and ``ensure`` moves it over unchanged.
"""
import bpy

PREFIX = '.OP Layers · '
ATTRIBUTE = 'open_painter_layers'   # the store's group on bpy.types.Palette
LEGACY = 'layers'   # the set's own stack before the store (a raw property of old files)

_owners = {}   # store palette pointer -> (scene, set id)


def _uid(scene):
    return scene.session_uid & 0x7fffffff   # stored in an IntProperty


def group(palette):
    return getattr(palette, ATTRIBUTE)


def layers(ts):
    """``ts.layers``: the set's stack, from its store. A set without one gets it here, unless
    datablocks cannot be written now (a panel drawing): its stack then reads as empty until
    the scheduler's next tick creates it."""
    palette = ts.layer_store
    if palette is not None:
        store = getattr(palette, ATTRIBUTE)
        scene = ts.id_data
        if store.home != _uid(scene):
            store = _claim(ts, scene, palette, store)
        return store.layers
    if ts.id_data.library is not None:
        return ()   # a linked set is read-only: a store could not be attached to it
    try:
        return ensure(ts).layers
    except (AttributeError, RuntimeError):
        return ()


def _claim(ts, scene, palette, store):
    """A store whose home is another scene. When that scene's set still holds it, ``ts`` is
    the set of a copied scene (``scene.copy()``, Full or Linked Copy) sharing the original's
    stack: it takes a copy before anything reads or edits it, so the two projects stay
    independent as they were when the stacks lived on the Scenes. Otherwise the store only
    moved home (a load gives every scene a new session id). Drawing may not write: the next
    writable access claims it."""
    if scene.library is not None or palette.library is not None:
        return store
    home = next((item for item in bpy.data.scenes if _uid(item) == store.home), None)
    shared = (home is not None and home != scene and getattr(home, 'open_painter', None) is not None
              and any(item.layer_store == palette for item in home.open_painter.texture_sets))
    try:
        if shared:
            copy = palette.copy()
            copy.use_fake_user = False
            copy.name = PREFIX + (ts.id or ts.name)
            ts.layer_store = copy
            store = group(copy)
        store.home = _uid(scene)
    except (AttributeError, RuntimeError):
        pass
    return store


def rna_owner(ts):
    """The struct holding the set's stack as its ``layers`` RNA collection, for templates
    that take one; None until the set has a store."""
    palette = ts.layer_store
    return group(palette) if palette is not None else None


def ensure(ts):
    """The set's store group, created on first use; a stack an older file kept on the set
    moves into it as it is (a raw copy, a few milliseconds for hundreds of layers)."""
    palette = ts.layer_store
    if palette is None:
        palette = bpy.data.palettes.new(PREFIX + (ts.id or ts.name))
        palette.use_fake_user = False   # Blender gives palettes one; the set's pointer is the store's user
        ts.layer_store = palette
        _owners.pop(palette.as_pointer(), None)
    store = group(palette)
    _migrate(ts, store)
    if store.set_id != ts.id:
        store.set_id = ts.id
    if store.home != _uid(ts.id_data):
        store.home = _uid(ts.id_data)
    return store


def _migrate(ts, store):
    raw = ts.bl_system_properties_get()
    if raw is None or LEGACY not in raw:
        return
    if not len(store.layers):
        store.bl_system_properties_get(do_create=True)[LEGACY] = raw[LEGACY]
    del raw[LEGACY]


def owner(palette):
    """(scene, texture set) whose stack a store holds, or (None, None)."""
    pointer = palette.as_pointer()
    cached = _owners.get(pointer)
    if cached is not None:
        try:
            scene, set_id = cached
            ts = next((item for item in scene.open_painter.texture_sets if item.id == set_id), None)
            if ts is not None and ts.layer_store == palette:
                return scene, ts
        except (ReferenceError, AttributeError):
            pass
    set_id = group(palette).set_id
    for scene in bpy.data.scenes:
        project = getattr(scene, 'open_painter', None)
        for ts in (project.texture_sets if project is not None else ()):
            if ts.layer_store == palette and (ts.id == set_id or not set_id):
                _owners[pointer] = (scene, ts.id)
                return scene, ts
    return None, None


def scene_of(struct):
    """The Scene a struct of the project belongs to: its own datablock, or for anything in
    a layer stack, the Scene of the set whose store holds it."""
    data = struct.id_data
    if isinstance(data, bpy.types.Palette):
        return owner(data)[0]
    return data


def project_of(struct):
    scene = scene_of(struct)
    return getattr(scene, 'open_painter', None) if scene is not None else None


def scene_path(struct):
    """``struct.path_from_id()`` spelled from the Scene, as before the store: a layer's path
    is ``open_painter.texture_sets[i].layers[j]...`` wherever its data lives. Paths name
    shader bindings and cache provenance, which must not change with the storage."""
    path = struct.path_from_id()
    data = struct.id_data
    if isinstance(data, bpy.types.Palette) and path.startswith(ATTRIBUTE + '.'):
        ts = owner(data)[1]
        if ts is not None:
            return ts.path_from_id() + path[len(ATTRIBUTE):]
    return path


def release(palette):
    """Remove a store no set points at any more (its set was removed)."""
    if palette is not None and palette.users - int(palette.use_fake_user) == 0 and palette.library is None:
        _owners.pop(palette.as_pointer(), None)
        bpy.data.palettes.remove(palette)


def ensure_all():
    """Give every set of every local scene its own store. A copied Scene (Full or Linked
    Copy, a Material Director candidate) points at the original's stores; the set that held
    a store keeps it and the others get copies, so the projects stay independent as they
    were when the stacks lived on the Scenes. A set whose store changed drops its sessions:
    they bound the structs of the store it left."""
    sharing = {}
    for scene in bpy.data.scenes:
        project = getattr(scene, 'open_painter', None)
        if project is None or scene.library is not None:
            continue
        for ts in project.texture_sets:
            palette = ts.layer_store
            if palette is not None:
                sharing.setdefault(palette.as_pointer(), []).append((scene, ts.id))
    moved = set()
    for pointer, holders in sharing.items():
        if len(holders) < 2:
            continue
        palette = next(item.layer_store for item in holders[0][0].open_painter.texture_sets
                       if item.id == holders[0][1])
        home = group(palette).home
        keeper = next((holder for holder in holders if _uid(holder[0]) == home), holders[0])
        for holder in holders:
            if holder is keeper:
                continue
            scene, set_id = holder
            ts = next(item for item in scene.open_painter.texture_sets if item.id == set_id)
            copy = ts.layer_store.copy()
            copy.use_fake_user = False
            copy.name = PREFIX + (ts.id or ts.name)
            ts.layer_store = copy
            moved.add(scene.as_pointer())
    for scene in bpy.data.scenes:
        project = getattr(scene, 'open_painter', None)
        if project is None or scene.library is not None:
            continue
        for ts in project.texture_sets:
            ensure(ts)   # homes every store in its scene
        if scene.as_pointer() in moved:
            from ..evaluation.shader_preview import texture_sets_changed
            from . import owners
            owners.clear()
            texture_sets_changed(scene)
    _owners.clear()


def maintain():
    """Per-tick check (a few reads per set): a set without a store (an append, a direct
    ``texture_sets.add()``) or two sets sharing one (a copied scene, an undo back to just
    after the copy) is put right."""
    stores = []
    try:
        for scene in bpy.data.scenes:
            project = getattr(scene, 'open_painter', None)
            if project is None or scene.library is not None:
                continue
            for ts in project.texture_sets:
                palette = ts.layer_store
                stores.append(palette.as_pointer() if palette is not None else None)
    except (AttributeError, ReferenceError):
        return
    if None in stores or len(set(stores)) != len(stores):
        ensure_all()


def clear():
    """Load, undo and redo: the structs these pointers named are gone."""
    _owners.clear()
