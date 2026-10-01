"""Target-build UDIM tile-access gate; creating a tiled image alone is insufficient.

Path 1 (historical): ``Image.pixels`` after ``tiles.active_index``. Blender's
RNA pixel accessor acquires the buffer with no image user, so it addresses the
first tile regardless of the active index.
Path 2: per-tile file round trip through a ``<UDIM>`` filepath: save, load each
tile file as a plain image, rewrite one tile file, reload the tiled image.
Path 3: ``gpu.texture.from_image`` on the tiled image, read back as an array.
Path 4: native image paint onto the second tile, then read it back through the
file path. Skipped when no window is available.
Path 5: pack both tiles, save the .blend, reopen it, and read both tiles back
through tile files.
Path 6: Cycles bake onto a plane whose UVs lie in tile 1002, then read both
tile files. Cycles CPU baking works without a window.

Run with a window for GPU and paint paths::

    BLENDER_USER_RESOURCES=/private/tmp/op-udim-user blender --factory-startup --python scripts/probe_udim.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

REPORT = Path(os.environ.get("OP_UDIM_REPORT", "/private/tmp/op-udim-probe.json"))
SIZE = 16
RED, GREEN, BLUE = (1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1)
report = dict(blender=bpy.app.version_string, build=bpy.app.build_hash.decode(),
              background=bpy.app.background, paths={})


def close(a, b):
    return bool(np.allclose(a, b, atol=1 / 255))


def make_tiled(name, directory):
    image = bpy.data.images.new(name, width=SIZE, height=SIZE, tiled=True, float_buffer=False)
    image.generated_color = RED
    with bpy.context.temp_override(**_image_editor(image)):
        bpy.ops.image.tile_add(number=1002, fill=True, color=GREEN, width=SIZE, height=SIZE)
    image.filepath_raw = str(directory / (name + ".<UDIM>.png"))
    image.file_format = "PNG"
    return image


def _image_editor(image):
    area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
    area.type = "IMAGE_EDITOR"
    area.spaces.active.image = image
    region = next(region for region in area.regions if region.type == "WINDOW")
    return dict(area=area, region=region)


def restore_area():
    for area in bpy.context.screen.areas:
        if area.type == "IMAGE_EDITOR":
            area.type = "VIEW_3D"


def _plane_on_second_tile():
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.active_object
    for loop in plane.data.uv_layers.active.data:
        loop.uv = (loop.uv[0] + 1, loop.uv[1])  # whole plane on tile 1002
    return plane


def read_plain(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        data = np.empty(image.size[0] * image.size[1] * 4, np.float32)
        image.pixels.foreach_get(data)
        return data.reshape(image.size[1], image.size[0], 4)
    finally:
        bpy.data.images.remove(image)


def write_plain(path, color):
    image = bpy.data.images.new("udim probe tile", width=SIZE, height=SIZE)
    try:
        image.pixels.foreach_set(np.tile(np.asarray(color, np.float32), SIZE * SIZE))
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


with tempfile.TemporaryDirectory(prefix="op-udim-") as folder:
    directory = Path(folder)
    image = make_tiled("UDIM addressing probe", directory)
    try:
        # Path 1: RNA pixels after switching the active tile.
        values = []
        for index, color in ((0, RED), (1, GREEN)):
            image.tiles.active_index = index
            data = np.empty(SIZE * SIZE * 4, np.float32)
            image.pixels.foreach_get(data)
            values.append(data.reshape(SIZE, SIZE, 4)[0, 0].tolist())
        report["paths"]["rna_pixels_active_index"] = dict(
            result="PASS" if close(values, [RED, GREEN]) else "FAILED", tile_pixels=values,
            note="Image.pixels acquires the buffer without an image user, so it addresses the first tile.")

        # Path 2: file round trip through the <UDIM> filepath.
        image.save()
        files = sorted(p.name for p in directory.iterdir())
        first = read_plain(directory / "UDIM addressing probe.1001.png")[0, 0]
        second = read_plain(directory / "UDIM addressing probe.1002.png")[0, 0]
        write_plain(directory / "UDIM addressing probe.1002.png", BLUE)
        image.reload()
        image.save()  # writes memory back; 1002 must now carry the reloaded blue
        rewritten = read_plain(directory / "UDIM addressing probe.1002.png")[0, 0]
        untouched = read_plain(directory / "UDIM addressing probe.1001.png")[0, 0]
        ok = close(first, RED) and close(second, GREEN) and close(rewritten, BLUE) and close(untouched, RED)
        report["paths"]["udim_file_round_trip"] = dict(
            result="PASS" if ok else "FAILED", files=files,
            read=[first.tolist(), second.tolist()], after_rewrite=[untouched.tolist(), rewritten.tolist()],
            note="Independent per-tile read and write through tile files; a disk round trip per tile, not in-memory access.")

        # Path 3: GPU tile array readback. Without a window this call aborts the
        # whole process (GPU_texture_create_2d with no context), not a Python error.
        try:
            if bpy.app.background:
                raise RuntimeError("gpu.texture.from_image crashes the background process; requires a window")
            import gpu
            texture = gpu.texture.from_image(image)
            buffer = texture.read()
            shape = (texture.height, texture.width, 4)
            array = np.asarray(buffer.to_list(), np.float32).reshape(-1)
            layers = array.size // (SIZE * SIZE * 4)
            samples = [array.reshape(layers, SIZE, SIZE, 4)[layer, 0, 0].tolist() for layer in range(layers)]
            colors = {tuple(np.round(s, 2)) for s in samples}
            report["paths"]["gpu_texture_array_read"] = dict(
                result="PASS" if len(samples) >= 2 and close(samples[:2], [RED, BLUE]) else "FAILED",
                texture=dict(width=texture.width, height=texture.height, format=texture.format, layers=layers),
                samples=samples, note="Read-only. A single layer means the Python API uploaded only the first tile, not a tile array.")
        except Exception as exc:  # no GPU context in background mode
            report["paths"]["gpu_texture_array_read"] = dict(result="UNAVAILABLE", reason=str(exc))

        # Path 4: native paint onto the second tile via UV > 1, read back through files.
        if not bpy.app.background:
            try:
                restore_area()  # the tile_add override turned the viewport into an image editor
                plane = _plane_on_second_tile()
                material = bpy.data.materials.new("udim probe"); material.use_nodes = True
                node = material.node_tree.nodes.new("ShaderNodeTexImage"); node.image = image
                material.node_tree.nodes.active = node
                plane.data.materials.append(material)
                bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
                settings = bpy.context.scene.tool_settings.image_paint
                settings.mode = "MATERIAL"; settings.canvas = image
                for owner in (settings.brush, settings.unified_paint_settings):
                    owner.color = (1, 1, 1); owner.strength = 1; owner.size = 120
                area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
                region = next(r for r in area.regions if r.type == "WINDOW")
                stroke = [dict(name="", location=(0, 0, 0), mouse=(region.width * .5 + i * 8, region.height * .5),
                               mouse_event=(region.width * .5 + i * 8, region.height * .5), pressure=1.0,
                               size=120, time=i * .1, is_start=i == 0, x_tilt=0.0, y_tilt=0.0) for i in range(4)]
                with bpy.context.temp_override(area=area, region=region):
                    bpy.ops.view3d.view_axis(type="TOP", align_active=True)
                    bpy.ops.view3d.view_selected()
                    bpy.ops.paint.image_paint(stroke=stroke, mode="NORMAL")
                bpy.ops.object.mode_set(mode="OBJECT")
                image.save()
                painted = read_plain(directory / "UDIM addressing probe.1002.png")
                other = read_plain(directory / "UDIM addressing probe.1001.png")
                changed_second = not close(painted, np.broadcast_to(np.asarray(BLUE, np.float32), painted.shape))
                same_first = close(other, np.broadcast_to(np.asarray(RED, np.float32), other.shape))
                report["paths"]["native_paint_second_tile"] = dict(
                    result="PASS" if changed_second and same_first else "FAILED",
                    second_tile_changed=changed_second, first_tile_unchanged=same_first,
                    painted_mean=painted.mean(axis=(0, 1)).tolist())
            except Exception as exc:
                import traceback
                report["paths"]["native_paint_second_tile"] = dict(result="UNAVAILABLE", reason=repr(exc),
                                                                   traceback=traceback.format_exc().splitlines()[-4:])
        else:
            report["paths"]["native_paint_second_tile"] = dict(result="NOT_RUN", reason="background mode has no viewport")
        # Path 6: Cycles bake to the second tile.
        try:
            bake_plane = _plane_on_second_tile()
            material = bpy.data.materials.new("udim bake probe"); material.use_nodes = True
            nodes = material.node_tree.nodes
            emission = nodes.new("ShaderNodeEmission"); emission.inputs["Color"].default_value = BLUE
            output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
            material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
            target = nodes.new("ShaderNodeTexImage"); target.image = image; nodes.active = target
            bake_plane.data.materials.append(material)
            scene = bpy.context.scene
            scene.render.engine = "CYCLES"; scene.cycles.device = "CPU"; scene.cycles.samples = 1
            scene.render.bake.use_selected_to_active = False
            scene.render.bake.margin = 0
            bpy.ops.object.select_all(action="DESELECT")
            bake_plane.select_set(True); bpy.context.view_layer.objects.active = bake_plane
            outcomes = {}
            for clear in (True, False):
                scene.render.bake.use_clear = clear
                write_plain(directory / "UDIM addressing probe.1001.png", RED)
                write_plain(directory / "UDIM addressing probe.1002.png", GREEN)
                image.reload()
                result = bpy.ops.object.bake(type="EMIT")
                image.save()
                baked = read_plain(directory / "UDIM addressing probe.1002.png")
                other = read_plain(directory / "UDIM addressing probe.1001.png")
                outcomes["use_clear" if clear else "no_clear"] = dict(
                    operator=sorted(result),
                    second_tile_baked=close(baked, np.broadcast_to(np.asarray(BLUE, np.float32), baked.shape)),
                    first_tile_unchanged=close(other, np.broadcast_to(np.asarray(RED, np.float32), other.shape)),
                    baked_mean=baked.mean(axis=(0, 1)).tolist(), other_mean=other.mean(axis=(0, 1)).tolist())
            kept = outcomes["no_clear"]
            report["paths"]["cycles_bake_second_tile"] = dict(
                result="PASS" if kept["second_tile_baked"] and kept["first_tile_unchanged"] else "FAILED",
                **{key: value for key, value in outcomes.items()},
                note="Emission bake of a constant color; the plane covers tile 1002 only. With use_clear the "
                     "bake clears every tile of the target image, so per-tile baking must disable clearing "
                     "or restore untouched tiles itself.")
            bpy.data.objects.remove(bake_plane, do_unlink=True)
        except Exception as exc:
            import traceback
            report["paths"]["cycles_bake_second_tile"] = dict(result="UNAVAILABLE", reason=repr(exc),
                                                             traceback=traceback.format_exc().splitlines()[-3:])

        # Path 5: pack, save, reopen, read back. Reopening replaces every datablock,
        # so this runs last and re-fetches the image by name.
        try:
            write_plain(directory / "UDIM addressing probe.1001.png", RED)
            write_plain(directory / "UDIM addressing probe.1002.png", GREEN)
            image.reload()
            image.pack()
            packed = bool(image.packed_file) or len(image.packed_files) >= 2
            for tile in directory.glob("UDIM addressing probe.*.png"):
                tile.unlink()  # the .blend must carry the pixels itself
            blend = directory / "udim-probe.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)
            if not bpy.app.background:
                raise RuntimeError("reopen skipped with a window: open_mainfile ends the startup script; "
                                   "see the background run")
            bpy.ops.wm.open_mainfile(filepath=str(blend))
            image = bpy.data.images["UDIM addressing probe"]
            tiles = [tile.number for tile in image.tiles]
            image.filepath_raw = str(directory / "UDIM reopened.<UDIM>.png")
            image.file_format = "PNG"
            image.save()
            first = read_plain(directory / "UDIM reopened.1001.png")[0, 0]
            second = read_plain(directory / "UDIM reopened.1002.png")[0, 0]
            ok = packed and tiles == [1001, 1002] and close(first, RED) and close(second, GREEN)
            report["paths"]["pack_save_reopen"] = dict(
                result="PASS" if ok else "FAILED", packed=packed, packed_files=len(image.packed_files),
                tiles_after_reopen=tiles, read=[first.tolist(), second.tolist()],
                note="Tile files were deleted before saving; pixels survive only through the packed .blend.")
        except Exception as exc:
            import traceback
            report["paths"]["pack_save_reopen"] = dict(result="NOT_RUN" if "reopen skipped" in str(exc) else "UNAVAILABLE", reason=repr(exc),
                                                      traceback=traceback.format_exc().splitlines()[-3:])
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        restore_area()
        if image and image.name in bpy.data.images:
            bpy.data.images.remove(image)

passing = [name for name, path in report["paths"].items() if path["result"] == "PASS"]
report["tile_addressing"] = "PASS" if passing else "FAILED"
report["passing_paths"] = passing
REPORT.parent.mkdir(parents=True, exist_ok=True)
REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("UDIM_PROBE", json.dumps(report), flush=True)
if "--quit" in sys.argv:
    bpy.ops.wm.quit_blender()
