"""Measure current engine publication and idle cost on a real Blender GPU backend.

Run from ordinary Python; each size uses a fresh, isolated Blender process::

    python scripts/benchmark_windows_viewport.py --blender C:/Blender/blender.exe \
      --backend vulkan --label after --output .blender-test/windows-vulkan/after.json

For a saved source tree, add --source-root PATH (containing src/open_painter).
Use --compare BEFORE.json to add before/after medians and checksum comparisons.
Use --paired-flag RUNTIME_BOOLEAN to alternate a real runtime boolean
within each process; both sides measure the same authored transition and check
matching full output pixels. This uses protocol 3 and cannot use --compare.
Direct workers require --profile PATH and explicit resource overrides.

Timings measure engine preparation/publication through one-pixel samples of all
published image textures, not viewport FPS, OS input delivery, or display presentation.
Full output checksums run separately outside the timed interaction samples. Idle measurements
drive the production scheduler callback without waiting its returned interval.
They report per-callback cost, not the frequency of native viewport redraws.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_test_profile import assert_blender_profile, isolated_environment
from benchmark_pairs import check_flag, measure_pairs, summarize_pairs
from native_fixtures import FIXTURES, add_source

STAGES = ("idle_engine_tick", "idle_scheduler_tick", "roughness", "mask_strength", "opacity", "republish")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--backend", choices=("vulkan", "opengl", "auto"), default="vulkan")
    parser.add_argument("--pixel-publish", choices=("auto", "force"), default="auto")
    parser.add_argument("--fixture", choices=tuple(FIXTURES), default="masked",
                        help="Versioned plain, cellular mask, image fill, or authored stroke fixture")
    parser.add_argument("--layers", type=int, default=2,
                        help="Total root fills; additional plain fills go beneath the edited coat (default: 2)")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--label", default="current")
    parser.add_argument("--sizes", nargs="+", type=int, default=[512, 1024, 2048])
    parser.add_argument("--repeat", type=int, default=6)
    parser.add_argument("--idle-repeat", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--paired-flag", metavar="RUNTIME_BOOLEAN",
                        help="Interleave an existing runtime flag off/on; --repeat counts pairs")
    parser.add_argument("--require-same-gpu-work", action="store_true",
                        help="For a CPU-only paired change, require equal GPU passes/readbacks/publications")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--profile", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--size", type=int, default=512, help=argparse.SUPPRESS)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.worker and args.profile is None:
        parser.error("--worker requires --profile and explicit isolated resource overrides")
    if args.paired_flag and args.compare:
        parser.error("Paired protocol 3 cannot be compared with protocol 2 sweeps")
    if args.require_same_gpu_work and not args.paired_flag:
        parser.error("--require-same-gpu-work requires --paired-flag")
    if not 2 <= args.layers <= 128:
        parser.error("--layers must be between 2 and 128")
    if args.fixture in {"image", "paint"} and args.layers != 2:
        parser.error("Versioned image/paint fixtures require --layers 2")
    if args.repeat < 2 or args.idle_repeat < 2:
        parser.error("--repeat and --idle-repeat must be at least 2")
    if any(size not in (512, 1024, 2048, 4096) for size in [args.size, *args.sizes]):
        parser.error("sizes must be 512, 1024, 2048, or 4096")
    args.source_root = args.source_root.resolve()
    args.output = args.output.resolve()
    return args


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def source_digest(source):
    digest = hashlib.sha256()
    for path in sorted((source / "src/open_painter").rglob("*.py")):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def summarize(samples):
    result = {"samples": len(samples)}
    for key in ("prepare_ms", "maximum_tick_ms", "completion_ms", "wall_ms"):
        values = sorted(sample[key] for sample in samples if key in sample)
        if values:
            result[key] = {"median": statistics.median(values),
                           "p95": values[max(0, math.ceil(.95 * len(values)) - 1)],
                           "maximum": max(values)}
    return result


def comparison(before, after):
    if any(row.get("measurement_version") != 2 for row in before["results"] + after["results"]):
        raise ValueError("Cannot compare different benchmark protocols; rerun both sources with version 2")
    previous = {row["size"]: row for row in before["results"]}
    result = {}
    for row in after["results"]:
        old = previous.get(row["size"])
        if old is None:
            continue
        compared = ("backend", "renderer", "driver", "blender", "os", "pixel_publish",
                    "precise_intermediates", "pixel_budget_ms", "repeat", "idle_repeat", "fixture")
        entry = {"same_environment": all(old.get(key) == row.get(key) for key in compared),
                 "same_protocol": old.get("measurement_version") == row.get("measurement_version"),
                 "stages": {}}
        for stage in STAGES:
            if stage not in old["summary"] or stage not in row["summary"]:
                continue
            timings = {}
            for metric in ("prepare_ms", "maximum_tick_ms", "completion_ms", "wall_ms"):
                if metric not in old["summary"][stage] or metric not in row["summary"][stage]:
                    continue
                a = old["summary"][stage][metric]["median"]
                b = row["summary"][stage][metric]["median"]
                timings[metric] = {"before_median_ms": a, "after_median_ms": b,
                                  "speedup": a / b if b and entry["same_protocol"] and entry["same_environment"] else None}
            entry["stages"][stage] = {"primary_metric": "prepare_ms" if "prepare_ms" in timings else "wall_ms",
                                      "timings": timings}
        entry["final_pixels_identical"] = old["final_pixels"] == row["final_pixels"]
        result[str(row["size"])] = entry
    return result


def launch(args):
    if args.blender is None:
        raise ValueError("--blender is required when launching outside Blender")
    report = {"label": args.label, "status": "RUNNING", "results": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for size in args.sizes:
        child_output = args.output.with_name(f"{args.output.stem}-{size}.json")
        log = child_output.with_suffix(".log")
        with tempfile.TemporaryDirectory(prefix="op-benchmark-", dir=args.output.parent) as profile:
            environment = isolated_environment(profile)
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            command = [str(args.blender), "--background", "--factory-startup",
                       "--python-exit-code", "1"]
            if args.backend != "auto":
                command += ["--gpu-backend", args.backend]
            command += ["--python", str(Path(__file__).resolve()), "--", "--worker",
                        "--profile", str(profile),
                        "--source-root", str(args.source_root), "--size", str(size),
                        "--label", args.label, "--backend", args.backend,
                        "--pixel-publish", args.pixel_publish, "--repeat", str(args.repeat),
                        "--fixture", args.fixture,
                        "--layers", str(args.layers),
                        "--idle-repeat", str(args.idle_repeat), "--timeout", str(args.timeout),
                        "--output", str(child_output)]
            if args.paired_flag:
                command += ["--paired-flag", args.paired_flag]
            if args.require_same_gpu_work:
                command += ["--require-same-gpu-work"]
            print(f"BENCHMARK {args.label}: {size} on {args.backend}; log: {log}", flush=True)
            # No console window is needed for a background benchmark on Windows.
            with log.open("w", encoding="utf-8") as stream:
                completed = subprocess.run(command, cwd=args.source_root, env=environment,
                    stdout=stream, stderr=subprocess.STDOUT, timeout=args.timeout * 4,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if completed.returncode:
                report.update(status="FAIL", failed_size=size, log=str(log))
                write_report(args.output, report)
                raise RuntimeError(f"Blender exited {completed.returncode}; see {log}")
            child = json.loads(child_output.read_text(encoding="utf-8"))
            if child["status"] != "PASS":
                raise RuntimeError(f"Benchmark did not pass; see {child_output}")
            report["results"].append(child)
            write_report(args.output, report)
    report["status"] = "PASS"
    if args.compare:
        report["comparison"] = comparison(json.loads(args.compare.read_text(encoding="utf-8")), report)
    write_report(args.output, report)
    print("WINDOWS_ENGINE_BENCHMARK_PASS", args.output, flush=True)


def worker(args):
    import bpy
    profile = assert_blender_profile(bpy, args.profile)
    import gpu
    import numpy as np

    sys.path.insert(0, str(args.source_root / "src"))
    import open_painter
    from open_painter.constants import CHANNELS
    from open_painter.core import layer_stack as stack, material_manager, texture_set_manager as sets
    from open_painter.evaluation import cache, metrics, shader_preview
    from open_painter.evaluation.engine import runtime
    from open_painter.utils.ids import new_id
    # Gate 0 artifact identity (stdlib only; safe inside Blender). source_sha256
    # below keeps its exact legacy semantics; artifact_digests extends it with
    # the shader bundle, build files, and (absent until P1/P2) adapter hashes.
    from collect_gate0_baseline import artifact_digests
    if args.paired_flag:
        check_flag(runtime, args.paired_flag)

    report = {"status": "RUNNING", "label": args.label, "size": args.size,
              "repeat": args.repeat, "idle_repeat": args.idle_repeat,
              "layers": args.layers,
              "source_root": str(args.source_root), "source_sha256": source_digest(args.source_root),
              "artifact_digests": artifact_digests(args.source_root),
              "blender": bpy.app.version_string, "os": platform.platform(),
              "profile_isolation": profile,
              "fixture": (FIXTURES[args.fixture]
                          if args.layers == 2 else
                          f"root_fills_{args.layers}_{'cellular_coat_mask' if args.fixture == 'masked' else 'plain'}_v1"),
              "measurement_version": 3 if args.paired_flag else 2,
              "samples": {}, "validation": {},
              "measurement": "engine evaluation then one sampled texel per image in an atlas and a single read; not viewport FPS",
              "primary_metric": "prepare_ms (addon callback work); completion_ms includes image GPU upload and fences",
              "readback_accounting": "engine metrics exclude benchmark completion and validation; both are reported",
              "scheduler": "timer disabled; explicit callback timing excludes its requested sleep"}
    if args.paired_flag:
        report["paired"] = {"flag": args.paired_flag, "pairs_per_stage": args.repeat,
                            "initial_flag_value": getattr(runtime, args.paired_flag),
                            "order": "off/on on even pairs; on/off on odd pairs",
                            "reset": "untimed completed target then initial edits before each variant; same measured transition",
                            "require_same_gpu_work": args.require_same_gpu_work,
                            "require_fewer_signatures": args.paired_flag == "CACHE_EVALUATION_STATES",
                            "validation": "atlas equality per pair; full output equality after timing each stage"}
        report["paired_summary"] = {}
        report["paired_validation"] = {}
    registered = False
    fence_texture = None
    try:
        if not bpy.app.background or "--factory-startup" not in sys.argv:
            raise RuntimeError("Use --background --factory-startup in an isolated process")
        runtime.ensure_gpu()
        report.update(backend=gpu.platform.backend_type_get(), renderer=gpu.platform.renderer_get(),
                      vendor=gpu.platform.vendor_get(), driver=gpu.platform.version_get())
        if args.backend != "auto" and report["backend"].lower() != args.backend:
            raise RuntimeError(f"Requested {args.backend}, actual backend is {report['backend']}")
        runtime.PIXEL_PUBLISH = True if args.pixel_publish == "force" else None
        report["pixel_publish"] = runtime.device().pixel_publish
        report["pixel_publish_override"] = args.pixel_publish
        report["pixel_budget_ms"] = runtime.PIXEL_BUDGET_MS
        report["precise_intermediates"] = runtime.PRECISE
        report["publication_optimizations"] = {
            "fused": getattr(runtime, 'FUSE_PIXEL_PUBLISH', False),
            "fused_max_size": getattr(runtime, 'FUSE_PIXEL_PUBLISH_MAX_SIZE', None),
            "constant_fills": getattr(runtime, 'CONSTANT_OUTPUTS', False),
        }
        open_painter.register()
        registered = True
        from open_painter.native_bridge.loader import find_library
        report['artifact_digests'] = artifact_digests(args.source_root, find_library())
        if bpy.app.timers.is_registered(cache.tick):
            bpy.app.timers.unregister(cache.tick)
        scene = bpy.context.scene
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        if args.fixture in {"image", "paint"}:
            bpy.ops.mesh.primitive_cube_add(size=2)
        else:
            bpy.ops.mesh.primitive_plane_add(size=2)
        ts = sets.create(scene, "Windows engine benchmark", args.size)
        sets.assign(scene, ts, bpy.context.object)
        base = stack.add(ts, "FILL", "Base")
        stack.channel_for(base, "BASE_COLOR").color = (.3, .35, .4, 1.)
        for index in range(args.layers - 2):
            fill = stack.add(ts, "FILL", f"Underlying fill {index + 1}")
            fill.opacity = .4 + .05 * (index % 5)
            for channel, value in (("BASE_COLOR", (.15 + .02 * (index % 9), .3, .45, 1.)),
                                   ("ROUGHNESS", .2 + .025 * (index % 12)),
                                   ("METALLIC", .05 + .04 * (index % 10))):
                prop = stack.channel_for(fill, channel)
                prop.enabled = True
                if channel == "BASE_COLOR":
                    prop.color = value
                else:
                    prop.value = value
        coat_id = stack.add(ts, "FILL", "Coat").id
        coat = next(layer for layer in ts.layers if layer.id == coat_id)
        for channel, value in (("BASE_COLOR", (.7, .2, .1, 1.)), ("ROUGHNESS", .3), ("METALLIC", .8)):
            prop = stack.channel_for(coat, channel)
            prop.enabled = True
            if channel == "BASE_COLOR":
                prop.color = value
            else:
                prop.value = value
        if args.fixture == "masked":
            mask = coat.masks.add()
            mask.id, mask.source, mask.procedural = new_id(), "PROCEDURAL", "CELLULAR"
        else:
            coat.opacity = .6
        add_source(args.fixture, scene, ts, coat, args.size)
        material_manager.repair(ts)
        bpy.context.view_layer.update()
        dev = runtime.device()
        fence_shader = dev.fixed("copy")
        # Keep harness memory separate from the device readback cache. Reading
        # through Device.readback(..., 1) would discard its full-sized publication
        # buffer and force a new allocation in every following measured edit.
        fence_texture = gpu.types.GPUTexture((len(CHANNELS), 1), format="RGBA32F")
        fence_framebuffer = gpu.types.GPUFrameBuffer(color_slots=(fence_texture,))
        fence_buffer = gpu.types.Buffer("FLOAT", len(CHANNELS) * 4)

        def ready():
            deadline, durations = perf_counter() + args.timeout, []
            rendered, published = 0, []
            while perf_counter() < deadline:
                engine = shader_preview.session_engine(scene, ts)
                previous_report = engine.report if engine else None
                started = perf_counter()
                shader_preview.tick(scene, ts)
                durations.append((perf_counter() - started) * 1000)
                engine = shader_preview.session_engine(scene, ts)
                if engine is not None and engine.report is not previous_report:
                    rendered += engine.report.get("units_rendered", 0)
                    published += engine.report.get("channels", [])
                info = shader_preview.diagnostics(scene, ts)
                if info.get("error"):
                    raise RuntimeError(info["error"])
                if shader_preview.current(scene, ts):
                    return {"ticks": len(durations), "maximum_tick_ms": max(durations),
                            "units_rendered": rendered, "published": published}
            raise RuntimeError(f"Engine did not become current: {shader_preview.diagnostics(scene, ts)}")

        def validate_pixels(label, completion_samples=None):
            result = {}
            started = perf_counter()
            acquisition_ms = read_ms = view_ms = checksum_ms = 0.0
            total_bytes = 0
            # Read the real published storage texture. Attaching Blender image
            # textures to an FBO is not supported by the Vulkan image path.
            for index, channel in enumerate(CHANNELS):
                output = next(item.image for item in ts.outputs if item.channel == channel)
                mark = perf_counter()
                texture = gpu.texture.from_image(output)
                acquired = perf_counter()
                buffer = texture.read()
                read = perf_counter()
                if memoryview(buffer).format != "f":
                    raise AssertionError(f"Unexpected readback format: {channel}: {memoryview(buffer).format}")
                values = np.frombuffer(buffer, dtype=np.float32)
                if values.size != args.size * args.size * 4:
                    raise AssertionError(f"Unexpected component count: {channel}: {values.size}")
                viewed = perf_counter()
                if not np.isfinite(values).all():
                    raise AssertionError(f"Non-finite {channel} output")
                if completion_samples is not None:
                    expected_sample = values.reshape(args.size, args.size, 4)[0, index]
                    if not np.array_equal(expected_sample, completion_samples[channel]):
                        raise AssertionError(f"Atlas sample differs from the published {channel} texture")
                # Consume immediately; no NumPy views of borrowed buffers survive
                # another channel read. Hash without allocating a bytes copy.
                result[channel] = {"sha256": hashlib.sha256(memoryview(values)).hexdigest(),
                                   "sum": float(values.sum(dtype=np.float64)),
                                   "minimum": float(values.min()), "maximum": float(values.max())}
                if args.fixture in {"image", "paint"} and channel == "BASE_COLOR":
                    red_range = float(np.ptp(values.reshape(args.size, args.size, 4)[..., 0]))
                    result[channel]["spatial_red_range"] = red_range
                    if red_range <= 1e-4:
                        raise AssertionError(f"{args.fixture} did not produce nonuniform texels")
                checked = perf_counter()
                acquisition_ms += (acquired - mark) * 1000
                read_ms += (read - acquired) * 1000
                view_ms += (viewed - read) * 1000
                checksum_ms += (checked - viewed) * 1000
                total_bytes += values.nbytes
                del values, buffer
            report["validation"][label] = {"wall_ms": (perf_counter() - started) * 1000,
                "texture_acquisition_ms": acquisition_ms, "texture_read_ms": read_ms,
                "numpy_view_ms": view_ms, "checksum_ms": checksum_ms,
                "readbacks": len(CHANNELS), "readback_bytes": total_bytes, "pixels": result}
            return result

        completion_components = {}

        def complete():
            # Each output contributes its own texel to the atlas. A single read
            # waits for all eight writes and any lazy image uploads they require.
            # Never attach a Vulkan image texture to an FBO.
            acquisition_ms = submission_ms = 0.0
            with dev.state():
                for index, channel in enumerate(CHANNELS):
                    output = next(item.image for item in ts.outputs if item.channel == channel)
                    if args.paired_flag:
                        mark = perf_counter()
                    texture = gpu.texture.from_image(output)
                    if args.paired_flag:
                        acquired = perf_counter()
                    dev.draw(fence_shader, [fence_texture], 1, [("op_source", texture)],
                             region=(index, 0, 1, 1))
                    if args.paired_flag:
                        submitted = perf_counter()
                        acquisition_ms += (acquired - mark) * 1000
                        submission_ms += (submitted - acquired) * 1000
                if args.paired_flag:
                    read_started = perf_counter()
                with fence_framebuffer.bind():
                    fence_framebuffer.read_color(0, 0, len(CHANNELS), 1, 4, 0, "FLOAT", data=fence_buffer)
                if args.paired_flag:
                    completion_components.update(texture_acquisition_ms=acquisition_ms,
                                                 draw_submission_ms=submission_ms,
                                                 fence_read_ms=(perf_counter() - read_started) * 1000)
                values = np.frombuffer(fence_buffer, dtype=np.float32).reshape(len(CHANNELS), 4)
                if not np.isfinite(values).all():
                    raise AssertionError("Non-finite completion sample")
                # Own Python values before the next completion reuses the buffer.
                return {channel: values[index].tolist() for index, channel in enumerate(CHANNELS)}

        event_sequence = 0

        def measure(operation):
            nonlocal event_sequence
            event_sequence += 1
            passes, compiles = dev.passes, dev.compiles
            with metrics.Metrics() as observed:
                started = perf_counter()
                operation()
                callback_finished = perf_counter()
                result = ready()
                prepared = perf_counter()
            result.update(event_id=event_sequence, callback_received_s=started,
                          callback_finished_s=callback_finished,
                          callback_ms=(callback_finished - started) * 1000,
                          accepted_recipe_serial=ts.edit_serial, display_presented_s=None,
                          gpu_passes=dev.passes - passes, compiles=dev.compiles - compiles,
                          engine_counters=dict(observed.counts))
            fence_passes, fence_compiles = dev.passes, dev.compiles
            result["completion_pixels"] = complete()
            completed = perf_counter()
            result.update(wall_ms=(completed - started) * 1000,
                          prepare_ms=(prepared - started) * 1000,
                          completion_ms=(completed - prepared) * 1000,
                          completion_readbacks=1,
                          completion_readback_bytes=len(CHANNELS) * 16,
                          completion_gpu_passes=dev.passes - fence_passes,
                          completion_compiles=dev.compiles - fence_compiles)
            if args.paired_flag:
                result["completion_components"] = dict(completion_components)
            return result

        report["cold"] = measure(lambda: None)
        initial_pixels = validate_pixels("cold", report["cold"]["completion_pixels"])
        # Settle dependency bookkeeping outside the warmed idle samples.
        cache.tick()
        ready()
        for label, callback in (("idle_engine_tick", lambda: shader_preview.tick(scene, ts)),
                                ("idle_scheduler_tick", cache.tick)):
            rows = []
            for _ in range(args.idle_repeat):
                passes, compiles = dev.passes, dev.compiles
                engine = shader_preview.session_engine(scene, ts)
                previous_report = engine.report
                with metrics.Metrics() as observed:
                    started = perf_counter()
                    interval = callback()
                    elapsed = (perf_counter() - started) * 1000
                rows.append({"wall_ms": elapsed, "gpu_passes": dev.passes - passes,
                             "compiles": dev.compiles - compiles, "engine_counters": dict(observed.counts),
                             "units_rendered": (engine.report.get("units_rendered", 0)
                                                if engine.report is not previous_report else 0),
                             "returned_interval": interval})
            report["samples"][label] = rows
        report["idle_pixels_unchanged"] = validate_pixels("idle") == initial_pixels
        if not report["idle_pixels_unchanged"]:
            raise AssertionError("Idle callbacks changed the output pixels")

        coat = next(layer for layer in ts.layers if layer.id == coat_id)
        roughness = stack.channel_for(coat, "ROUGHNESS")

        def republish(_):
            engine = shader_preview.session_engine(scene, ts)
            engine.published.clear()
            shader_preview._sessions[shader_preview.key_for(scene, ts)]["dirty"] = True

        previous_pixels = initial_pixels
        coverage = (("mask_strength", lambda i: setattr(coat.masks[0], "strength", .3 + (i % 2) * .6))
                    if args.fixture == "masked" else
                    ("opacity", lambda i: setattr(coat, "opacity", .3 + (i % 2) * .6)))
        for label, change in (("roughness", lambda i: setattr(roughness, "value", .25 + (i % 2) * .5)),
                              coverage,
                              ("republish", republish)):
            if args.paired_flag:
                paired = measure_pairs(runtime, args.paired_flag, measure, change,
                                       validate_pixels, label, args.repeat,
                                       require_same_gpu_work=args.require_same_gpu_work,
                                       require_fewer_signatures=args.paired_flag == "CACHE_EVALUATION_STATES")
                rows, stage_pixels = paired["samples"], paired["final_pixels"]
                report["paired_summary"][label] = summarize_pairs(rows, summarize)
                report["paired_validation"][label] = {
                    key: value for key, value in paired.items() if key not in {"samples", "final_pixels"}}
            else:
                # One unreported edit warms paths specific to this interaction.
                measure(lambda: change(0))
                rows = [measure(lambda index=i: change(index)) for i in range(1, args.repeat + 1)]
                stage_pixels = validate_pixels(label, rows[-1]["completion_pixels"])
            if label != "republish":
                if stage_pixels["ROUGHNESS"]["sha256"] == previous_pixels["ROUGHNESS"]["sha256"]:
                    raise AssertionError(f"{label} did not change published pixels")
                if any("ROUGHNESS" not in row["published"] for row in rows):
                    raise AssertionError(f"{label} edit did not publish roughness")
            elif stage_pixels != previous_pixels:
                raise AssertionError("Republishing unchanged material altered output pixels")
            previous_pixels = stage_pixels
            report["samples"][label] = rows
            print("BENCHMARK_STAGE", args.size, label,
                  report["paired_summary"][label] if args.paired_flag else summarize(rows), flush=True)
            write_report(args.output, report)
        report["summary"] = {key: summarize(value) for key, value in report["samples"].items()}
        report["final_pixels"] = previous_pixels
        report["memory"] = shader_preview.session_engine(scene, ts).memory()
        report['execution_backend'] = shader_preview.session_engine(scene, ts).report.get('execution_backend')
        report["source_sha256_at_end"] = source_digest(args.source_root)
        if report["source_sha256"] != report["source_sha256_at_end"]:
            raise RuntimeError("Source changed during measurement; discard this run")
        report["status"] = "PASS"
    except Exception:
        report.update(status="FAIL", error=traceback.format_exc())
        raise
    finally:
        write_report(args.output, report)
        if fence_texture is not None:
            # GPUTexture owns its storage; removing framebuffer references before
            # dropping it releases the isolated harness allocation.
            fence_framebuffer = None
            fence_texture = None
        if registered:
            open_painter.unregister()
    print("WINDOWS_ENGINE_BENCHMARK_PASS", args.output, flush=True)


if __name__ == "__main__":
    options = arguments()
    (worker if options.worker else launch)(options)
