"""What the last bake did, per texture set: the Results section reads it, the queue writes it.

Runtime only (never saved). A map that fails keeps its previous image, so the record
names the maps that landed and the ones that did not, with the error text.
"""
from time import strftime

_records = {}
_running = {}


def start(ts, kinds):
    _running[ts.id] = dict(kinds=list(kinds), done=[], current=None)


def step(ts, kind):
    entry = _running.setdefault(ts.id, dict(kinds=[], done=[], current=None))
    entry["current"] = kind


def landed(ts, kind):
    entry = _running.setdefault(ts.id, dict(kinds=[], done=[], current=None))
    entry["done"].append(kind)
    entry["current"] = None


def finish(ts, *, error="", stopped=False):
    entry = _running.pop(ts.id, None) or dict(kinds=[], done=[], current=None)
    failed = entry["current"] if error else None
    _records[ts.id] = dict(
        when=strftime("%H:%M:%S"), kinds=entry["kinds"], done=entry["done"], failed=failed,
        error=error, stopped=stopped,
        skipped=[kind for kind in entry["kinds"] if kind not in entry["done"] and kind != failed])


def running(ts):
    return _running.get(ts.id)


def last(ts):
    return _records.get(ts.id)


def clear():
    _records.clear()
    _running.clear()
