"""Deterministic node placement: rows follow the stack, columns follow dependencies."""

ROW_GAP = 40
COLUMN_WIDTH = 300
INDENT = 40
HEADER_HEIGHT = 130     # node header plus the drawn controls, in node units
SOCKET_HEIGHT = 22


def ranks(nodes, links):
    """Dependency rank per node: 1 + the highest rank among its producers, 0 for sources.

    ``links`` is an iterable of (producer id, consumer id). Cycles are cut at the
    first repeated node so a malformed graph still lays out.
    """
    producers = {}
    for source, target in links:
        producers.setdefault(target, set()).add(source)
    memo = {}

    def rank(node, trail=()):
        if node in memo:
            return memo[node]
        if node in trail:
            return 0
        value = 0
        for producer in producers.get(node, ()):
            if producer in nodes:
                value = max(value, rank(producer, trail + (node,)) + 1)
        memo[node] = value
        return value

    return {node: rank(node) for node in nodes}


def height(sockets):
    """Estimated node height from its socket count (Blender sizes nodes after drawing)."""
    return HEADER_HEIGHT + SOCKET_HEIGHT * sockets


def positions(rows, links, output=None, sockets=None, subrows=None, inline=None):
    """{node id: (x, y)} for ``rows`` = [(id, depth)] listed top to bottom.

    Rows stack downwards by their estimated heights (``sockets`` = {id: socket
    count}); x is the dependency rank times the column width plus a nesting
    indent, so producers sit left of their consumers and folder contents read
    as indented rows. ``inline`` = {row id: [node id, ...]} puts a chain on its
    layer's own row (masks: ``Mask -> Layer``); ``subrows`` = {row id: [[node
    id, ...], ...]} draws further chains (per-channel filters) as indented rows
    under it. The output node, when given, sits one column right of everything.
    """
    subrows, inline = subrows or {}, inline or {}
    ids = [identifier for identifier, _ in rows]
    ids += [identifier for members in inline.values() for identifier in members]
    ids += [identifier for chains in subrows.values() for chain in chains for identifier in chain]
    by_rank = ranks(set(ids), links)
    sockets = sockets or {}
    result, y = {}, 0
    for identifier, depth in rows:
        members = [identifier, *inline.get(identifier, ())]
        occupied = {}
        for member in members:
            rank = by_rank.get(member, 0)
            offset = occupied.get(rank, 0)
            result[member] = (rank * COLUMN_WIDTH + depth * INDENT, y - offset)
            occupied[rank] = offset + height(sockets.get(member, 0)) + ROW_GAP
        y -= max(occupied.values())
        for chain in subrows.get(identifier, ()):
            for member in chain:
                result[member] = (by_rank.get(member, 0) * COLUMN_WIDTH + (depth + 1) * INDENT, y)
            y -= max((height(sockets.get(member, 0)) for member in chain), default=0) + ROW_GAP
    if output is not None:
        widest = max((rank for rank in by_rank.values()), default=0)
        result[output] = ((widest + 1) * COLUMN_WIDTH + INDENT, 0)
    return result
