"""Opt-in CPU phase counters. No timers, threads or image reads when disabled.

Times are nested inclusive/exclusive CPU and wall durations, not frame latency.
Allocation counters cover explicitly observed buffers, not every NumPy temporary.
RSS is the process lifetime high-water mark (including Blender).
"""
from collections import defaultdict
from contextlib import contextmanager
from functools import wraps
from time import perf_counter, process_time
import sys

_active = None


def count(name, amount=1):
    if _active is not None:
        _active.counts[name] += amount


def gauge(name, value):
    if _active is not None:
        _active.gauges[name] = value
        _active.peaks[name] = max(value, _active.peaks[name])


def sample(name,value):
    if _active is not None and len(_active.samples[name])<16384:
        _active.samples[name].append(float(value))


def measured(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if _active is None:
                return fn(*args, **kwargs)
            with _active.phase(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


class Metrics:
    def __init__(self):
        self.counts = defaultdict(int)
        self.gauges = {}
        self.peaks = defaultdict(int)
        self.samples = defaultdict(list)
        self.phases = {name: dict(calls=0, wall_s=0., cpu_s=0., exclusive_wall_s=0., exclusive_cpu_s=0.)
                       for name in ("source_generation", "masking", "filters", "blending", "geometry",
                                    "geometry_signature", "image_read", "image_write", "packing")}
        self.stack = []

    def __enter__(self):
        global _active
        self.previous, _active = _active, self
        return self

    def __exit__(self, *exc):
        global _active
        _active = self.previous

    @contextmanager
    def phase(self, name):
        start = perf_counter(), process_time()
        child = [0., 0.]
        self.stack.append(child)
        try:
            yield
        finally:
            wall, cpu = perf_counter() - start[0], process_time() - start[1]
            self.stack.pop()
            if self.stack:
                self.stack[-1][0] += wall
                self.stack[-1][1] += cpu
            entry = self.phases.setdefault(name, dict(calls=0, wall_s=0., cpu_s=0., exclusive_wall_s=0., exclusive_cpu_s=0.))
            entry['calls'] += 1
            entry['wall_s'] += wall
            entry['cpu_s'] += cpu
            entry['exclusive_wall_s'] += wall - child[0]
            entry['exclusive_cpu_s'] += cpu - child[1]

    def report(self):
        import numpy as np
        try:
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
        except ImportError:
            rss = None
        distributions={key:dict(samples=len(values),p50=float(np.percentile(values,50)),
                               p95=float(np.percentile(values,95)),maximum=max(values))
                       for key,values in self.samples.items() if values}
        return dict(phases=self.phases, counters=dict(self.counts), distributions=distributions, last_observed_bytes=self.gauges,
                    peak_observed_bytes=dict(self.peaks), process_peak_rss_bytes=rss)
