"""Mask stack structure (0.9.3): every entry is a source; groups nest contiguous members.

A layer's masks are one ordered list. An entry whose ``source`` is ``GROUP``
is followed by its members — the contiguous run of entries whose ``parent_id``
chain reaches it. Evaluators walk the list recursively, so a group's members
combine among themselves first and the group then combines with the outer
stack through its own operation, strength, levels and invert, exactly like
any other source. These helpers keep the runs contiguous when entries move.
"""


def parents(masks):
    return {mask.id: mask.parent_id for mask in masks}


def depth(masks, index):
    """Nesting depth of the entry at ``index`` (0 for a top-level entry)."""
    by_id = parents(masks)
    seen, level, cursor = set(), 0, masks[index].parent_id
    while cursor and cursor in by_id and cursor not in seen:
        seen.add(cursor)
        level += 1
        cursor = by_id[cursor]
    return level


def descends_from(masks, index, group_id):
    by_id = parents(masks)
    seen, cursor = set(), masks[index].parent_id
    while cursor and cursor not in seen:
        if cursor == group_id:
            return True
        seen.add(cursor)
        cursor = by_id.get(cursor, '')
    return False


def block(masks, index):
    """[start, stop) of the entry at ``index`` with its contiguous descendants."""
    stop = index + 1
    if masks[index].source == 'GROUP':
        group_id = masks[index].id
        while stop < len(masks) and descends_from(masks, stop, group_id):
            stop += 1
    return index, stop


def members(masks, index):
    """The contiguous member run of the group at ``index`` (empty for other sources)."""
    start, stop = block(masks, index)
    return list(range(start + 1, stop))


def rows(masks):
    """(index, depth) in list order, for indented drawing."""
    return [(index, depth(masks, index)) for index in range(len(masks))]


def _move_range(collection, start, stop, target):
    """Move the entries [start, stop) so they begin at ``target`` (index before the move)."""
    count = stop - start
    if target == start:
        return
    if target > start:
        for _ in range(count):
            collection.move(start, target + count - 1)
    else:
        for offset in range(count):
            collection.move(start + offset, target + offset)


def move(layer, index, direction):
    """Move an entry (with its descendants) past its neighbouring sibling block."""
    masks = layer.masks
    start, stop = block(masks, index)
    parent = masks[index].parent_id
    if direction < 0:
        cursor = start - 1
        while cursor >= 0 and masks[cursor].parent_id != parent and depth(masks, cursor) > depth(masks, index):
            cursor -= 1
        if cursor < 0 or masks[cursor].parent_id != parent:
            return index
        target = block(masks, cursor)[0]
        _move_range(masks, start, stop, target)
        return target
    if stop >= len(masks) or masks[stop].parent_id != parent:
        return index
    _, next_stop = block(masks, stop)
    _move_range(masks, start, stop, next_stop - (stop - start))
    return index + (next_stop - stop)


def into_group_above(layer, index):
    """Make the entry a member of the group whose run ends right above it; returns the group index or None."""
    masks = layer.masks
    if index <= 0:
        return None
    above = index - 1
    if masks[above].source == 'GROUP' and masks[above].parent_id == masks[index].parent_id:
        group = above
    elif masks[above].parent_id and masks[above].parent_id != masks[index].parent_id:
        group = next((i for i in range(above, -1, -1) if masks[i].id == masks[above].parent_id), None)
        if group is None:
            return None
    else:
        return None
    if masks[index].source == 'GROUP' and descends_from(masks, group, masks[index].id):
        return None
    start, stop = block(masks, index)
    group_id = masks[group].id
    for i in range(start, stop):
        if masks[i].parent_id == masks[index].parent_id and i == start:
            masks[i].parent_id = group_id
    return group


def out_of_group(layer, index):
    """Lift the entry (with descendants) out of its group, placing it after the group's run."""
    masks = layer.masks
    parent_id = masks[index].parent_id
    if not parent_id:
        return index
    group = next((i for i in range(len(masks)) if masks[i].id == parent_id), None)
    if group is None:
        masks[index].parent_id = ''
        return index
    grandparent = masks[group].parent_id
    start, stop = block(masks, index)
    _, group_stop = block(masks, group)
    masks[index].parent_id = grandparent
    target = group_stop - (stop - start)
    _move_range(masks, start, stop, target)
    return target


def dissolve(layer, index):
    """Remove a group entry, keeping its members in place at the group's level."""
    masks = layer.masks
    if masks[index].source != 'GROUP':
        return
    group_id, parent_id = masks[index].id, masks[index].parent_id
    for i in members(masks, index):
        if masks[i].parent_id == group_id:
            masks[i].parent_id = parent_id
    masks.remove(index)


def remove_block(layer, index):
    start, stop = block(layer.masks, index)
    for i in range(stop - 1, start - 1, -1):
        layer.masks.remove(i)
    return start


def adopt(layer, index, anchor):
    """Place the (new, last) entry at ``index`` into the group context of ``anchor``:
    at the end of the anchor group's run, or beside a member; returns the new index."""
    masks = layer.masks
    if anchor is None or not 0 <= anchor < len(masks) or anchor == index:
        return index
    if masks[anchor].source == 'GROUP':
        group = anchor
    elif masks[anchor].parent_id:
        group = next((i for i in range(len(masks)) if masks[i].id == masks[anchor].parent_id), None)
        if group is None:
            return index
    else:
        return index
    masks[index].parent_id = masks[group].id
    _, stop = block(masks, group)
    if stop == index + 1:
        return index   # already right after the run (the run absorbed it)
    _move_range(masks, index, index + 1, stop)
    return stop


def normalize(layer):
    """Repair a list whose members drifted away from their group: they join at the group's end."""
    masks = layer.masks
    changed = False
    for index in range(len(masks)):
        parent_id = masks[index].parent_id
        if not parent_id:
            continue
        group = next((i for i in range(len(masks)) if masks[i].id == parent_id), None)
        if group is None or index < group:
            masks[index].parent_id = ''
            changed = True
    return changed
