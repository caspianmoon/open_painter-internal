"""Fair cooperative scheduling; full-resolution output stays authoritative."""


def fair_order(items, previous, identity, preferred=None):
    if not items:
        return []
    keys = [identity(item) for item in items]
    if previous in keys:
        start = (keys.index(previous) + 1) % len(items)
    elif preferred in keys:
        start = keys.index(preferred)
    else:
        start = 0
    return items[start:] + items[:start]
