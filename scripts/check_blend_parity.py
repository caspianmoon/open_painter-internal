"""Blend parity: Python blend_modes.blend vs libopenpainter composite_read.

Loads the real reference implementation (no bpy needed, but numpy is, so
run with Blender's bundled Python)::

    /Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 \\
        scripts/check_blend_parity.py --lib /tmp/op-libopenpainter-build/libopenpainter.dylib

For opaque fills the C slice must match blend(base, src, opacity, mode) to
1e-5. Reports the maximum absolute difference; fails otherwise.
"""

import argparse
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TOLERANCE = 1e-5


def load_reference():
    """Import blend_modes.py with a stub parent package (avoids bpy)."""
    parent = types.ModuleType("parity_ref")
    parent.__path__ = [str(ROOT / "src" / "open_painter" / "evaluation")]
    sys.modules["parity_ref"] = parent
    for name in ("metrics", "blend_modes"):
        spec = importlib.util.spec_from_file_location(
            f"parity_ref.{name}",
            ROOT / "src" / "open_painter" / "evaluation" / f"{name}.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"parity_ref.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["parity_ref.blend_modes"]


def main():
    import numpy as np

    from open_painter.native_bridge import Channel, NativeCore, load_library

    parser = argparse.ArgumentParser()
    parser.add_argument("--lib", type=Path, default=None)
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = parser.parse_args()

    ref = load_reference()
    modes = {"NORMAL": 0, "MULTIPLY": 1, "OVERLAY": 2}
    bases = [(0.5, 0.5, 0.5), (0.8, 0.3, 0.5), (0.0, 0.0, 0.0),
             (1.0, 1.0, 1.0), (0.2, 0.7, 0.4)]
    sources = [(0.7, 0.2, 0.1), (0.6, 0.6, 0.6), (1.0, 1.0, 1.0),
               (0.0, 0.0, 0.0)]
    opacities = (1.0, 0.6, 0.0)

    lib, found = load_library(args.lib)
    worst = 0.0
    worst_case = None
    count = 0
    with NativeCore(lib) as core:
        doc_id = 0
        for mode_name, mode_id in modes.items():
            for base in bases:
                for src in sources:
                    for opacity in opacities:
                        doc_id += 1
                        doc = core.document_create(doc_id)
                        tx = core.begin(doc)
                        core.layer_add(doc, tx, 1)
                        core.layer_add(doc, tx, 2)
                        core.set_color(doc, tx, 1, Channel.BASE_COLOR,
                                       (*base, 1.0))
                        core.set_color(doc, tx, 2, Channel.BASE_COLOR,
                                       (*src, 1.0))
                        core.set_blend(doc, tx, 2, mode_id)
                        core.set_opacity(doc, tx, 2, opacity)
                        core.commit(doc, tx)
                        rev = core.evaluate(doc)
                        got = core.composite_read(doc, rev, Channel.BASE_COLOR, 2)
                        want = ref.blend(np.array(base, dtype=np.float64),
                                         np.array(src, dtype=np.float64),
                                         opacity, mode_name)
                        diff = max(abs(g - w) for g, w in
                                   zip(got[:3], want.tolist()))
                        # All texels identical for fills; check one more.
                        assert got[4:7] == got[0:3]
                        count += 1
                        if diff > worst:
                            worst, worst_case = diff, (mode_name, base, src,
                                                      opacity)
                        core.document_destroy(doc)
    print(f"cases={count} max_abs_diff={worst:.3e} worst={worst_case} lib={found}")
    if worst > args.tolerance:
        raise SystemExit(f"BLEND_PARITY_FAIL: {worst:.3e} > {args.tolerance:.3e}")
    print("BLEND_PARITY_PASS")


if __name__ == "__main__":
    main()
