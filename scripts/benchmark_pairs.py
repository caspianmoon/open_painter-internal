"""Matched off/on transitions for a real-engine publication benchmark.

Resets and full pixel validation are outside measured interactions. Each pair
measures the same authored transition under both settings, with alternating
order. This avoids confusing a flag's effect with different parameter values
or continuously increasing driver/thermal load in separate sweeps.
"""

from statistics import median


TIMINGS = ("prepare_ms", "maximum_tick_ms", "completion_ms", "wall_ms")


def check_flag(runtime, flag):
    if not flag or not flag.isidentifier() or not flag.isupper():
        raise ValueError("The paired flag must name an uppercase runtime boolean")
    if type(getattr(runtime, flag, None)) is not bool:
        raise ValueError(f"Runtime has no boolean optimization flag {flag!r}")


def measure_pairs(runtime, flag, measure, change, validate, label, repeat, *,
                  require_same_gpu_work=False, require_fewer_signatures=False):
    """Return matched timing rows and separately verified full output pixels.

    ``measure`` must complete publication and a GPU fence, not just enqueue
    commands. ``change(0)`` and ``change(1)`` select the two authored values;
    for ``republish`` either index forces publication without changing values.
    ``validate(label, completion_pixels)`` reads/hashes actual published images.
    """
    check_flag(runtime, flag)
    if repeat < 2:
        raise ValueError("At least two pairs are required")
    original = getattr(runtime, flag)
    forced = label == "republish"
    rows = []
    resets = 0

    def sample(enabled, initial, target):
        nonlocal resets
        setattr(runtime, flag, enabled)
        if not forced:
            # Seed the target first so the immediately preceding reset is an
            # actual edit for both variants, even at a pair-order boundary.
            measure(lambda: change(target))
            measure(lambda: change(initial))
            resets += 2
        return measure(lambda: change(target))

    try:
        # Warm both alternatives, including their publication allocations.
        for enabled in (False, True):
            sample(enabled, 0, 1)
        for pair in range(repeat):
            initial = pair % 2
            target = 1 - initial
            order = (False, True) if pair % 2 == 0 else (True, False)
            current = []
            for enabled in order:
                row = sample(enabled, initial, target)
                row.update(pair=pair, variant_enabled=enabled,
                           transition={"initial_index": initial, "target_index": target})
                rows.append(row)
                current.append(row)
            if current[0]["completion_pixels"] != current[1]["completion_pixels"]:
                raise AssertionError(f"{label} pair {pair}: completion pixels differ across variants")
            off, on = (current[1], current[0]) if current[0]["variant_enabled"] else current
            if require_same_gpu_work:
                for key in ("gpu_passes", "units_rendered"):
                    if off[key] != on[key]:
                        raise AssertionError(f"{label} pair {pair}: {key} differs across variants")
                for key in ("engine_texture_readbacks", "engine_texture_readback_bytes",
                            "engine_channel_publications"):
                    if off["engine_counters"].get(key, 0) != on["engine_counters"].get(key, 0):
                        raise AssertionError(f"{label} pair {pair}: {key} differs across variants")
            if require_fewer_signatures:
                previous = off["engine_counters"].get("engine_state_signature_checks", 0)
                actual = on["engine_counters"].get("engine_state_signature_checks", 0)
                hits = on["engine_counters"].get("engine_state_cache_hits", 0)
                if not 0 < actual < previous or hits <= 0:
                    raise AssertionError(f"{label} pair {pair}: signature checks did not decrease with cache hits")

        # Validate each full output at the same transition after all timings.
        # Interleaving large validation reads with one timed variant would skew
        # that variant's following sample, especially at 2048 and above.
        checks = {}
        for enabled in (False, True):
            row = sample(enabled, initial, target)
            variant = "enabled" if enabled else "disabled"
            checks[variant] = validate(f"{label}_{variant}", row["completion_pixels"])
        if checks["disabled"] != checks["enabled"]:
            raise AssertionError(f"{label}: full published pixels differ across variants")
        return {"samples": rows, "final_pixels": checks["enabled"],
                "pixels_identical": True, "untimed_reset_interactions": resets,
                "same_gpu_work_verified": require_same_gpu_work,
                "fewer_signature_checks_verified": require_fewer_signatures,
                "validation": checks}
    finally:
        setattr(runtime, flag, original)


def summarize_pairs(rows, summarize):
    disabled = [row for row in rows if not row["variant_enabled"]]
    enabled = [row for row in rows if row["variant_enabled"]]
    deltas = {key: [] for key in TIMINGS}
    for index in range(0, len(rows), 2):
        a, b = rows[index:index + 2]
        if a["pair"] != b["pair"] or a["variant_enabled"] == b["variant_enabled"]:
            raise ValueError("Timing rows do not contain adjacent off/on pairs")
        off, on = (b, a) if a["variant_enabled"] else (a, b)
        for key in TIMINGS:
            deltas[key].append(on[key] - off[key])
    return {"disabled": summarize(disabled), "enabled": summarize(enabled),
            "enabled_minus_disabled_ms": deltas,
            "paired_delta_median_ms": {key: median(values) for key, values in deltas.items()},
            "enabled_faster_wall_pairs": sum(value < 0 for value in deltas["wall_ms"])}
