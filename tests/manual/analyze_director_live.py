"""Measure real generated masks in saved live-run scenes; no model or proposal fixtures.

blender --background --factory-startup --python tests/manual/analyze_director_live.py -- FOLDER
Uses a 256-square UV sample grid. These measurements do not replace viewport inspection.
"""

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.generators.scene_fields import compute
from open_painter.painting.projection import geometry

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("folder", type=Path)
parser.add_argument("--resolution", type=int, choices=(256, 512, 1024), default=256)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
open_painter.register()
report = dict(resolution=args.resolution, cases=[], checks={})
previous = None

for path in sorted(args.folder.glob("[0-9][0-9]-*.blend")):
    bpy.ops.wm.open_mainfile(filepath=str(path.resolve()))
    project = bpy.context.scene.open_painter
    ts = project.texture_sets[project.active_texture_set]
    n = args.resolution
    pos, _, valid = geometry(ts, n, coverage=True)
    names = {g.name for ref in ts.assignments if ref.object for g in ref.object.vertex_groups}
    groups = {name: compute(ts, SimpleNamespace(kind="VERTEX_GROUP", attribute=name, resolution=str(n))) > .9999
              for name in ("Handling", "Recessed Screws") if name in names}
    regions = dict(valid=valid, bottom=valid & (pos[..., 2] < .18), upper=valid & (pos[..., 2] > .5), **groups)
    rows, planes, cache, resources = [], {}, {}, {}
    for layer in ts.layers:
        value = evaluate_masks(ts, layer, n, 0, n, cache, resources) * layer.opacity
        planes[layer.name] = value
        row = dict(layer=layer.name, opacity=layer.opacity,
                   enabled_channels=[c.channel for c in layer.channels if c.enabled],
                   coverage={name: dict(mean=float(value[mask].mean()), max=float(value[mask].max()),
                                        nonzero=float((value[mask] > .01).mean()))
                             for name, mask in regions.items() if mask.any()},
                   fields=[dict(name=m.name, kind=m.influence.kind, image=bool(m.influence.image), error=m.influence.error)
                           for m in layer.masks if m.source == "SCENE"])
        if "Handling" in groups:
            row["outside_handling_max"] = float(value[valid & ~groups["Handling"]].max())
        rows.append(row)
    report["cases"].append(dict(name=path.stem, layers=rows))
    if path.stem.endswith("less_rust") and previous:
        rust = next((name for name in previous if "rust" in name.lower() and name in planes), None)
        if rust:
            report["checks"].update(
                rust_was_visible=bool(previous[rust].max() > .01),
                rust_alpha_halved_everywhere=bool(np.allclose(planes[rust], previous[rust] * .5, atol=1e-7)),
                other_masks_unchanged_by_less_rust=all(name in planes and np.array_equal(previous[name], planes[name])
                                                     for name in previous if name != rust))
    previous = planes
    print("ANALYZED", path.stem, flush=True)

(args.folder / "mask-analysis.json").write_text(json.dumps(report, indent=2))
print("MASK_CHECKS", json.dumps(report["checks"]), flush=True)
