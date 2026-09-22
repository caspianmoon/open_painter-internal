"""Numerical surface evidence, never an aesthetic score or approval decision."""
import numpy as np


def _mean_window(values, valid, radius):
    radius = min(radius, max(0, min(valid.shape) - 1))
    total = np.zeros_like(values, dtype=np.float32)
    count = np.zeros(valid.shape, np.float32)
    # No wrapping across the atlas boundary; exclude uncovered texels.
    for axis in (0, 1):
        source = values if axis == 0 else total / np.maximum(count[..., None], 1)
        weight = valid.astype(np.float32) if axis == 0 else (count > 0).astype(np.float32)
        total = np.zeros_like(values, dtype=np.float32)
        count = np.zeros(valid.shape, np.float32)
        for offset in range(-radius, radius + 1):
            a = [slice(None), slice(None)]
            b = [slice(None), slice(None)]
            a[axis] = slice(max(0, offset), min(valid.shape[axis], valid.shape[axis] + offset))
            b[axis] = slice(max(0, -offset), min(valid.shape[axis], valid.shape[axis] - offset))
            total[tuple(a)] += source[tuple(b)] * weight[tuple(b)][..., None]
            count[tuple(a)] += weight[tuple(b)]
    return total / np.maximum(count[..., None], 1)


def variation(values, valid):
    values = np.asarray(values, np.float32)[..., :3]
    valid = np.asarray(valid, bool)
    if values.shape[:2] != valid.shape or not valid.any() or not np.isfinite(values[valid]).all():
        raise ValueError("Surface diagnostics need matching finite covered samples")
    # Keep cost bounded; these are UV-frequency proxies, not measured millimeters.
    stride = max(1, int(np.ceil(max(valid.shape) / 256)))
    sample, coverage = values[::stride, ::stride], valid[::stride, ::stride]
    if not coverage.any():
        # Small islands may fall entirely between the sampling grid points.
        y, x = np.argwhere(valid)[0]
        sample = values[y % stride::stride, x % stride::stride]
        coverage = valid[y % stride::stride, x % stride::stride]
    fine = _mean_window(sample, coverage, 1)
    broad = _mean_window(sample, coverage, 8)
    def rms(value):
        return float(np.sqrt(np.mean(np.square(value[coverage]))))
    spread = values[valid].std(axis=0)
    bands = dict(macro=float(broad[coverage].std(axis=0).mean()),
                 meso=rms(fine - broad), micro=rms(sample - fine))
    repeated = []
    for axis in (0, 1):
        for lag in (8, 16, 32, 64):
            if sample.shape[axis] <= lag:
                continue
            left, right = [slice(None)] * 2, [slice(None)] * 2
            left[axis], right[axis] = slice(None, -lag), slice(lag, None)
            mask = coverage[tuple(left)] & coverage[tuple(right)]
            a, b = sample[tuple(left)][mask], sample[tuple(right)][mask]
            if len(a) < 32:
                continue
            a, b = a - a.mean(axis=0), b - b.mean(axis=0)
            denominator = float(np.sqrt(np.sum(a*a) * np.sum(b*b)))
            if denominator > 1e-9:
                repeated.append(dict(axis=axis, lag_texels=lag*stride,
                                     correlation=float(np.sum(a*b) / denominator)))
    return dict(standard_deviation=spread.tolist(), frequency_band_rms=bands,
                repetition_probes=repeated, sampled_stride=stride,
                warnings=["Near-constant evaluated channel; inspect whether this surface needs detail"]
                         if float(spread.max()) < .001 else [],
                interpretation="UV-space frequency proxies; chart seams, UV scale and intentional smooth finishes affect measurements. No artistic pass/fail.")


def contribution(normal, bypass, valid, threshold=1/255):
    a, b = np.asarray(normal, np.float32)[..., :3], np.asarray(bypass, np.float32)[..., :3]
    mask = np.asarray(valid, bool)
    if a.shape != b.shape or a.shape[:2] != mask.shape or not mask.any():
        raise ValueError("Contribution requires matching covered buffers")
    diff = np.abs(a[mask] - b[mask])
    if not np.isfinite(diff).all():
        raise ValueError("Non-finite contribution buffers")
    fraction = float((diff.max(axis=-1) > threshold).mean())
    return dict(mean_absolute_difference=float(diff.mean()), maximum_difference=float(diff.max()),
                changed_texel_fraction=fraction, threshold=threshold,
                warnings=["Generated sources have negligible evaluated spatial influence here"]
                         if fraction < .01 else [],
                interpretation="Fraction of covered texels above the disclosed difference threshold, not percent artistic quality")
