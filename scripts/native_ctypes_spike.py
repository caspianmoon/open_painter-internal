"""ctypes spike: drive libopenpainter through the real ABI (no Blender needed).

Two spatially constant fills, a color and scalar edit,
opacity commit, snapshot round-trip, generation barrier, and error paths::

    python3 scripts/native_ctypes_spike.py --lib /tmp/op-libopenpainter-build/libopenpainter.dylib

Exit 0 with NATIVE_CTYPES_SPIKE_PASS on success. This exercises the
Python->C boundary only; texture values still live in the CPU core until
the compositing slice lands with its parity fixtures.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from open_painter.native_bridge import (  # noqa: E402
    Channel,
    NativeCore,
    NativeError,
    OP_ABI_VERSION,
    Status,
    load_library,
)


def expect_error(status, func, *args):
    try:
        func(*args)
    except NativeError as exc:
        assert exc.status == status, f"expected {status}, got {exc.status}"
        return
    raise AssertionError(f"expected NativeError({status}) from {func.__name__}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lib", type=Path, default=None)
    args = parser.parse_args()

    lib, found = load_library(args.lib)
    print(f"loaded {found}")
    with NativeCore(lib) as core:
        caps = core.capabilities()
        assert caps.abi_version == OP_ABI_VERSION and caps.has_gpu == 0, caps
        print(f"abi={caps.abi_version} max_tex={caps.max_texture_size} gpu={caps.has_gpu}")

        doc = core.document_create(1)
        lease = core.acquire(doc)
        assert (lease.document_id, lease.generation, lease.revision) == (1, 1, 0)
        assert lease.channel_mask == 0xFF
        core.release(doc, lease)

        # Two fills: base constant + red coat with roughness.
        tx = core.begin(doc)
        expect_error(Status.INVALID_ARGUMENT, core.begin, doc)  # second tx open
        core.layer_add(doc, tx, 101)
        core.layer_add(doc, tx, 102)
        core.set_color(doc, tx, 102, Channel.BASE_COLOR, (0.7, 0.2, 0.1, 1.0))
        core.set_scalar(doc, tx, 102, Channel.ROUGHNESS, 0.3)
        expect_error(Status.INVALID_ARGUMENT, core.set_scalar,
                     doc, tx, 102, 99, 0.3)  # bad channel
        expect_error(Status.STALE_HANDLE, core.commit, doc, tx + 1)  # wrong tx
        core.commit(doc, tx)
        core.evaluate(doc)
        coat = core.acquire(doc)
        assert coat.revision == 1 and coat.channel_mask == 0xFF
        print(f"coat revision={coat.revision} mask=0x{coat.channel_mask:02x}")

        # Opacity drag step, then snapshot round-trip.
        tx = core.begin(doc)
        core.set_opacity(doc, tx, 102, 0.6)
        core.commit(doc, tx)
        core.evaluate(doc)
        data = core.snapshot(doc)
        print(f"snapshot_bytes={len(data)}")
        restored = core.restore(data)
        after = core.acquire(restored)
        assert after.revision == coat.revision + 1
        assert after.generation == coat.generation + 1
        expect_error(Status.STALE_HANDLE, core.release, restored, coat)
        core.release(restored, after)
        core.release(doc, core.acquire(doc))

        # Tampered snapshot rejected.
        tampered = bytearray(data)
        tampered[20] ^= 0xFF
        expect_error(Status.VALIDATION_FAILED, core.restore, bytes(tampered))

        # Generation barrier on the live doc stale-dates outstanding leases.
        live = core.acquire(doc)
        core.new_generation(doc)
        expect_error(Status.STALE_HANDLE, core.release, doc, live)
        fresh = core.acquire(doc)
        assert fresh.generation > live.generation
        core.release(doc, fresh)

        metrics = core.metrics()
        print(f"commits={metrics.commits} cancels={metrics.cancels} "
              f"evals={metrics.evaluations} acq={metrics.frames_acquired} "
              f"rel={metrics.frames_released}")
        assert metrics.commits == 2 and metrics.evaluations == 2
        core.document_destroy(doc)
        core.document_destroy(restored)
    print("NATIVE_CTYPES_SPIKE_PASS")


if __name__ == "__main__":
    main()
