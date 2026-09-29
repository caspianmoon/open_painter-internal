"""Independent-device capability report, separate from host GPU scheduling.

A C ABI match does not qualify an independent native GPU backend. lifecycle
selects the C17 host-Metal scheduler when its library and host are available.
"""

def select_backend(*, native_loaded=False, has_gpu=False, backend=None):
    return {
        'selected': 'legacy',
        'native_loaded': bool(native_loaded),
        'native_has_gpu': bool(has_gpu),
        'host_backend': backend,
        'qualified': False,
        'reason': 'Independent native GPU backend unavailable; host executor selected at evaluation',
    }
