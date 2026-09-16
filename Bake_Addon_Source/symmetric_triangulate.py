bl_info = {
    "name": "Bake Addon Canonical Symmetric Triangulate V6",
    "author": "",
    "version": (1, 6, 0),
    "blender": (4, 5, 0),
    "location": "View3D > N-Panel > Bake Addon",
    "description": "Auto-detects symmetry and triangulates quads in canonical bounding-box space while preserving normals.",
    "category": "Mesh",
}

import bpy
import math
from collections import Counter
from mathutils import Vector


EPS = 1e-9


# ------------------------------------------------------------
# Math helpers
# ------------------------------------------------------------

def safe_normalize(v):
    length = v.length
    if length < EPS:
        return Vector((0.0, 0.0, 0.0))
    return v / length


def newell_normal(points):
    n = Vector((0.0, 0.0, 0.0))
    count = len(points)

    for i in range(count):
        p0 = points[i]
        p1 = points[(i + 1) % count]

        n.x += (p0.y - p1.y) * (p0.z + p1.z)
        n.y += (p0.z - p1.z) * (p0.x + p1.x)
        n.z += (p0.x - p1.x) * (p0.y + p1.y)

    return safe_normalize(n)


def signed_area_2d(poly):
    area = 0.0
    count = len(poly)

    for i in range(count):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % count]
        area += x1 * y2 - x2 * y1

    return area * 0.5


def cross_2d(a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def dist_sq_2d(a, b):
    ax, ay = a
    bx, by = b
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy


def dist_sq_3d(a, b):
    d = a - b
    return d.length_squared


def triangle_area_abs(a, b, c):
    return abs(cross_2d(a, b, c)) * 0.5


def point_in_tri_2d(p, a, b, c):
    px, py = p
    ax, ay = a
    bx, by = b
    cx, cy = c

    v0x, v0y = cx - ax, cy - ay
    v1x, v1y = bx - ax, by - ay
    v2x, v2y = px - ax, py - ay

    dot00 = v0x * v0x + v0y * v0y
    dot01 = v0x * v1x + v0y * v1y
    dot02 = v0x * v2x + v0y * v2y
    dot11 = v1x * v1x + v1y * v1y
    dot12 = v1x * v2x + v1y * v2y

    denom = dot00 * dot11 - dot01 * dot01
    if abs(denom) < EPS:
        return False

    inv = 1.0 / denom
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv

    return u >= -EPS and v >= -EPS and (u + v) <= 1.0 + EPS


def quantize_vec(v, tolerance):
    return (
        round(v.x / tolerance),
        round(v.y / tolerance),
        round(v.z / tolerance),
    )


def quant2(p, precision=1000000.0):
    return (
        round(p[0] * precision) / precision,
        round(p[1] * precision) / precision,
    )


def edge_key_2d(a, b):
    qa = quant2(a)
    qb = quant2(b)
    return tuple(sorted((qa, qb)))


def get_axis_index(axis):
    if axis == 'X':
        return 0
    if axis == 'Y':
        return 1
    if axis == 'Z':
        return 2
    return 0


def reflect_point(p, axis_index, center):
    q = p.copy()
    q[axis_index] = (center * 2.0) - q[axis_index]
    return q


# ------------------------------------------------------------
# Stable projection for N-gons
# ------------------------------------------------------------

def stable_project_polygon(points):
    count = len(points)

    if count < 3:
        return None, None

    normal = newell_normal(points)

    if normal.length < EPS:
        return None, None

    centroid = Vector((0.0, 0.0, 0.0))
    for p in points:
        centroid += p
    centroid /= count

    candidates = []

    for i in range(count):
        p0 = points[i]
        p1 = points[(i + 1) % count]
        edge = p1 - p0
        length_sq = edge.length_squared

        if length_sq < EPS:
            continue

        midpoint = (p0 + p1) * 0.5
        rel = midpoint - centroid

        midpoint_key = (
            round(rel.x, 6),
            round(rel.y, 6),
            round(rel.z, 6),
        )

        candidates.append((length_sq, midpoint_key, edge))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: (-round(x[0], 9), x[1]))

    u = safe_normalize(candidates[0][2])
    u = safe_normalize(u - normal * u.dot(normal))

    if u.length < EPS:
        return None, None

    abs_components = [abs(u.x), abs(u.y), abs(u.z)]
    main_axis = abs_components.index(max(abs_components))

    if main_axis == 0 and u.x < 0:
        u.negate()
    elif main_axis == 1 and u.y < 0:
        u.negate()
    elif main_axis == 2 and u.z < 0:
        u.negate()

    v = safe_normalize(normal.cross(u))

    coords = []

    for p in points:
        d = p - centroid
        coords.append((d.dot(u), d.dot(v)))

    if signed_area_2d(coords) < 0.0:
        coords = [(x, -y) for x, y in coords]

    return coords, normal


# ------------------------------------------------------------
# Symmetry detection from evaluated mesh vertices
# ------------------------------------------------------------

def detect_symmetry_axes(verts, tolerance=0.001, min_score=0.85):
    if not verts:
        return []

    xs = [v.x for v in verts]
    ys = [v.y for v in verts]
    zs = [v.z for v in verts]

    bounds = {
        'X': ((min(xs) + max(xs)) * 0.5, max(xs) - min(xs)),
        'Y': ((min(ys) + max(ys)) * 0.5, max(ys) - min(ys)),
        'Z': ((min(zs) + max(zs)) * 0.5, max(zs) - min(zs)),
    }

    axes = []

    for axis in ('X', 'Y', 'Z'):
        axis_index = get_axis_index(axis)
        center, span = bounds[axis]

        if span < tolerance:
            continue

        counts = Counter(quantize_vec(v, tolerance) for v in verts)

        matched = 0
        total = len(verts)

        for v in verts:
            reflected = reflect_point(v, axis_index, center)
            key = quantize_vec(reflected, tolerance)

            if counts.get(key, 0) > 0:
                matched += 1

        score = matched / max(total, 1)

        if score >= min_score:
            axes.append({
                "axis": axis,
                "axis_index": axis_index,
                "center": center,
                "score": score,
                "span": span,
            })

    axes.sort(key=lambda item: (item["score"], item["span"]), reverse=True)
    return axes


# ------------------------------------------------------------
# New V6 quad logic:
# canonical bounding-box side diagonal choice
# ------------------------------------------------------------

def face_center(points):
    c = Vector((0.0, 0.0, 0.0))
    for p in points:
        c += p
    return c / max(len(points), 1)


def canonicalize_points_by_symmetry(points, detected_axes, tolerance):
    """
    Reflects a face into a shared canonical side of the object.

    Example:
    If X symmetry is detected, faces on negative X are temporarily reflected
    to positive X before choosing the diagonal.

    This avoids needing to find/pair the opposite face.
    """

    if not detected_axes:
        return [p.copy() for p in points]

    c = face_center(points)
    result = [p.copy() for p in points]

    for axis_info in detected_axes:
        axis_index = axis_info["axis_index"]
        center = axis_info["center"]

        # Center-plane faces are left unchanged for that axis.
        if c[axis_index] < center - tolerance:
            result = [
                reflect_point(p, axis_index, center)
                for p in result
            ]

    return result


def project_polygon_to_global_2d(points):
    """
    Project a polygon to a stable global/object-axis 2D plane.

    Instead of face loop order or local arbitrary basis,
    use object/local bounding axes.

    Drop the axis most aligned with the face normal.
    """

    normal = newell_normal(points)

    if normal.length < EPS:
        return None

    ax = abs(normal.x)
    ay = abs(normal.y)
    az = abs(normal.z)

    # Drop dominant normal axis.
    # If face normal mostly Y, use X/Z.
    # If face normal mostly Z, use X/Y.
    # If face normal mostly X, use Y/Z.
    if ax >= ay and ax >= az:
        coords = [(p.y, p.z) for p in points]

    elif ay >= ax and ay >= az:
        coords = [(p.x, p.z) for p in points]

    else:
        coords = [(p.x, p.y) for p in points]

    return coords


def project_quad_to_global_2d(points):
    coords = project_polygon_to_global_2d(points)

    if coords is None:
        return [(p.x, p.z) for p in points]

    return coords


def diagonal_positive_slope_score(p1, p2):
    """
    Gives a stable orientation score for a diagonal in canonical 2D space.

    For rectangles/squares:
    positive slope diagonal is preferred.
    Mirrored side was already canonicalized, so the original side will mirror correctly.
    """

    x1, y1 = p1
    x2, y2 = p2

    # Sort endpoints left-to-right. If almost vertical, sort bottom-to-top.
    if abs(x2 - x1) > EPS:
        if x2 < x1:
            x1, y1, x2, y2 = x2, y2, x1, y1
    else:
        if y2 < y1:
            x1, y1, x2, y2 = x2, y2, x1, y1

    dx = x2 - x1
    dy = y2 - y1

    # Prefer positive slope.
    sign_score = 1.0 if dy >= -EPS else 0.0

    # Prefer diagonals closer to +45 degrees as a tie-break.
    # This is scale-independent enough for quad tie cases.
    if abs(dx) < EPS and abs(dy) < EPS:
        angle_score = 0.0
    elif abs(dx) < EPS:
        angle_score = -999.0
    else:
        slope = dy / dx
        angle_score = -abs(slope - 1.0)

    # Stable tiny tie-break from endpoint coordinates.
    key = tuple(sorted((quant2((x1, y1)), quant2((x2, y2)))))

    return (sign_score, angle_score, key)


def choose_quad_diagonal_v6(points, detected_axes, tolerance):
    """
    Main quad algorithm.

    1. Canonicalize face using detected symmetry axes.
    2. If diagonals have different lengths, use shortest.
    3. If diagonals are equal/near-equal, use canonical positive-slope rule.

    Returns:
        True  = use AC diagonal
        False = use BD diagonal
    """

    canonical = canonicalize_points_by_symmetry(
        points=points,
        detected_axes=detected_axes,
        tolerance=tolerance,
    )

    p0, p1, p2, p3 = canonical

    ac_len = dist_sq_3d(p0, p2)
    bd_len = dist_sq_3d(p1, p3)

    # If one diagonal is clearly shorter, this is not ambiguous.
    if abs(ac_len - bd_len) > max(EPS, tolerance * tolerance * 0.01):
        return ac_len < bd_len

    coords = project_quad_to_global_2d(canonical)

    ac_score = diagonal_positive_slope_score(coords[0], coords[2])
    bd_score = diagonal_positive_slope_score(coords[1], coords[3])

    if ac_score != bd_score:
        return ac_score > bd_score

    # Last fallback, still geometry based.
    key_ac = edge_key_2d(coords[0], coords[2])
    key_bd = edge_key_2d(coords[1], coords[3])

    return key_ac <= key_bd


# ------------------------------------------------------------
# Triangulation
# ------------------------------------------------------------

def deterministic_quad(loop_indices, use_ac):
    if use_ac:
        return [
            (loop_indices[0], loop_indices[1], loop_indices[2]),
            (loop_indices[0], loop_indices[2], loop_indices[3]),
        ]

    return [
        (loop_indices[1], loop_indices[2], loop_indices[3]),
        (loop_indices[1], loop_indices[3], loop_indices[0]),
    ]


def triangle_quality(a, b, c):
    area = triangle_area_abs(a, b, c)

    ab = math.sqrt(max(dist_sq_2d(a, b), EPS))
    bc = math.sqrt(max(dist_sq_2d(b, c), EPS))
    ca = math.sqrt(max(dist_sq_2d(c, a), EPS))

    perimeter = ab + bc + ca

    if perimeter < EPS:
        return 0.0

    return area / (perimeter * perimeter)


def deterministic_earclip(loop_indices, coords_2d):
    count_original = len(loop_indices)

    if count_original < 3:
        return []

    if count_original == 3:
        return [tuple(loop_indices)]

    work = list(range(count_original))

    if signed_area_2d(coords_2d) < 0.0:
        work.reverse()

    triangles = []
    safety = 0

    while len(work) > 3 and safety < count_original * count_original:
        safety += 1

        best_local_i = None
        best_score = None
        count = len(work)

        for local_i in range(count):
            prev_i = work[(local_i - 1) % count]
            curr_i = work[local_i]
            next_i = work[(local_i + 1) % count]

            a = coords_2d[prev_i]
            b = coords_2d[curr_i]
            c = coords_2d[next_i]

            if cross_2d(a, b, c) <= EPS:
                continue

            blocked = False

            for other_i in work:
                if other_i in (prev_i, curr_i, next_i):
                    continue

                if point_in_tri_2d(coords_2d[other_i], a, b, c):
                    blocked = True
                    break

            if blocked:
                continue

            quality = triangle_quality(a, b, c)
            area = triangle_area_abs(a, b, c)
            tri_coords = sorted([quant2(a), quant2(b), quant2(c)])

            score = (
                round(quality, 12),
                round(area, 12),
                tri_coords,
            )

            if best_score is None or score > best_score:
                best_score = score
                best_local_i = local_i

        if best_local_i is None:
            canonical = min(
                range(len(work)),
                key=lambda k: quant2(coords_2d[work[k]])
            )

            ordered = work[canonical:] + work[:canonical]
            base = ordered[0]

            for i in range(1, len(ordered) - 1):
                triangles.append((
                    loop_indices[base],
                    loop_indices[ordered[i]],
                    loop_indices[ordered[i + 1]],
                ))

            return triangles

        prev_i = work[(best_local_i - 1) % len(work)]
        curr_i = work[best_local_i]
        next_i = work[(best_local_i + 1) % len(work)]

        triangles.append((
            loop_indices[prev_i],
            loop_indices[curr_i],
            loop_indices[next_i],
        ))

        del work[best_local_i]

    if len(work) == 3:
        triangles.append((
            loop_indices[work[0]],
            loop_indices[work[1]],
            loop_indices[work[2]],
        ))

    return triangles


def deterministic_fan(loop_indices, coords_2d=None):
    if len(loop_indices) < 3:
        return []

    order = list(range(len(loop_indices)))

    if coords_2d and len(coords_2d) == len(loop_indices):
        base_i = min(
            range(len(loop_indices)),
            key=lambda i: quant2(coords_2d[i]),
        )
        order = order[base_i:] + order[:base_i]

    base = order[0]
    triangles = []

    for i in range(1, len(order) - 1):
        triangles.append((
            loop_indices[base],
            loop_indices[order[i]],
            loop_indices[order[i + 1]],
        ))

    return triangles


def canonical_polygon_order(coords_2d):
    order = list(range(len(coords_2d)))

    if signed_area_2d(coords_2d) < 0.0:
        order.reverse()

    start = min(
        range(len(order)),
        key=lambda i: (
            quant2(coords_2d[order[i]]),
            i,
        ),
    )

    return order[start:] + order[:start]


def deterministic_symmetric_ngon(loop_indices, points, detected_axes, tolerance):
    canonical = canonicalize_points_by_symmetry(
        points=points,
        detected_axes=detected_axes,
        tolerance=tolerance,
    )

    coords_2d = project_polygon_to_global_2d(canonical)

    if coords_2d is None:
        coords_2d, _normal = stable_project_polygon(canonical)

        if coords_2d is None:
            return deterministic_fan(loop_indices)

    order = canonical_polygon_order(coords_2d)
    ordered_loop_indices = [loop_indices[i] for i in order]
    ordered_coords_2d = [coords_2d[i] for i in order]

    return deterministic_earclip(
        loop_indices=ordered_loop_indices,
        coords_2d=ordered_coords_2d,
    )


def orient_triangle_to_normal(tri, old_loop_to_vertex, verts, target_normal):
    if target_normal.length < EPS:
        return tri

    a = verts[old_loop_to_vertex[tri[0]]]
    b = verts[old_loop_to_vertex[tri[1]]]
    c = verts[old_loop_to_vertex[tri[2]]]

    tri_normal = (b - a).cross(c - a)

    if tri_normal.length < EPS:
        return tri

    if tri_normal.dot(target_normal) < 0.0:
        return (tri[0], tri[2], tri[1])

    return tri


# ------------------------------------------------------------
# Mesh helpers
# ------------------------------------------------------------

def get_source_mesh(context, obj, use_evaluated):
    depsgraph = context.evaluated_depsgraph_get()

    if use_evaluated:
        eval_obj = obj.evaluated_get(depsgraph)
        src_mesh = bpy.data.meshes.new_from_object(
            eval_obj,
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        src_mesh.name = obj.name + "_SOURCE_EVAL"
        src_mesh.update()
        return src_mesh

    src_mesh = obj.data.copy()
    src_mesh.name = obj.name + "_SOURCE_COPY"
    src_mesh.update()
    return src_mesh


def get_corner_normals(src_mesh):
    src_mesh.update()
    normals = []

    try:
        for n in src_mesh.corner_normals:
            normals.append(n.vector.copy())
    except Exception:
        for loop in src_mesh.loops:
            normals.append(loop.normal.copy())

    return normals


def copy_materials(src_mesh, new_mesh):
    for mat in src_mesh.materials:
        new_mesh.materials.append(mat)


def copy_uvs(src_mesh, new_mesh, new_loop_sources):
    for uv_layer in src_mesh.uv_layers:
        new_uv = new_mesh.uv_layers.new(name=uv_layer.name)

        for poly_i, poly in enumerate(new_mesh.polygons):
            source_tri = new_loop_sources[poly_i]

            for corner_offset, new_loop_index in enumerate(poly.loop_indices):
                old_loop_index = source_tri[corner_offset]
                new_uv.data[new_loop_index].uv = uv_layer.data[old_loop_index].uv.copy()


def copy_corner_colors(src_mesh, new_mesh, new_loop_sources):
    if not hasattr(src_mesh, "color_attributes"):
        return

    if not hasattr(new_mesh, "color_attributes"):
        return

    for attr in src_mesh.color_attributes:
        if attr.domain != 'CORNER':
            continue

        if attr.data_type not in {'BYTE_COLOR', 'FLOAT_COLOR'}:
            continue

        new_attr = new_mesh.color_attributes.new(
            name=attr.name,
            type=attr.data_type,
            domain='CORNER',
        )

        for poly_i, poly in enumerate(new_mesh.polygons):
            source_tri = new_loop_sources[poly_i]

            for corner_offset, new_loop_index in enumerate(poly.loop_indices):
                old_loop_index = source_tri[corner_offset]
                new_attr.data[new_loop_index].color = attr.data[old_loop_index].color


def get_source_sharp_edge_keys(src_mesh):
    sharp_keys = set()
    sharp_attr_values = None

    if hasattr(src_mesh, "attributes"):
        sharp_attr = src_mesh.attributes.get("sharp_edge")

        if sharp_attr is not None and sharp_attr.domain == 'EDGE':
            sharp_attr_values = sharp_attr.data

    for edge in src_mesh.edges:
        is_sharp = False

        if sharp_attr_values is not None and edge.index < len(sharp_attr_values):
            is_sharp = bool(getattr(sharp_attr_values[edge.index], "value", False))
        elif hasattr(edge, "use_edge_sharp"):
            is_sharp = bool(edge.use_edge_sharp)

        if is_sharp:
            sharp_keys.add(tuple(sorted(edge.vertices)))

    return sharp_keys


def copy_sharp_edges(src_mesh, new_mesh):
    sharp_keys = get_source_sharp_edge_keys(src_mesh)

    if not sharp_keys:
        return

    sharp_attr = None

    if hasattr(new_mesh, "attributes"):
        sharp_attr = new_mesh.attributes.get("sharp_edge")

        if sharp_attr is None:
            try:
                sharp_attr = new_mesh.attributes.new(
                    name="sharp_edge",
                    type='BOOLEAN',
                    domain='EDGE',
                )
            except Exception:
                sharp_attr = None

    for edge in new_mesh.edges:
        if tuple(sorted(edge.vertices)) not in sharp_keys:
            continue

        if sharp_attr is not None and edge.index < len(sharp_attr.data):
            sharp_attr.data[edge.index].value = True
        elif hasattr(edge, "use_edge_sharp"):
            edge.use_edge_sharp = True
        elif hasattr(edge, "smooth"):
            edge.smooth = False

    new_mesh.update()


# ------------------------------------------------------------
# Main builder
# ------------------------------------------------------------

def build_triangulated_mesh(
    src_mesh,
    keep_normals=True,
    preserve_sharp_edges=True,
    symmetry_tolerance=0.001,
):
    src_mesh.update()

    verts = [v.co.copy() for v in src_mesh.vertices]
    source_corner_normals = get_corner_normals(src_mesh)

    old_loop_to_vertex = {}
    for loop in src_mesh.loops:
        old_loop_to_vertex[loop.index] = loop.vertex_index

    detected_axes = detect_symmetry_axes(
        verts=verts,
        tolerance=symmetry_tolerance,
        min_score=0.85,
    )

    new_faces = []
    new_face_materials = []
    new_face_smooths = []
    new_loop_sources = []

    quad_count = 0

    for poly in src_mesh.polygons:
        poly_loop_indices = list(poly.loop_indices)

        points = []
        for li in poly_loop_indices:
            vi = old_loop_to_vertex[li]
            points.append(verts[vi])

        if len(poly_loop_indices) == 4:
            use_ac = choose_quad_diagonal_v6(
                points=points,
                detected_axes=detected_axes,
                tolerance=symmetry_tolerance,
            )

            tri_loop_indices = deterministic_quad(
                loop_indices=poly_loop_indices,
                use_ac=use_ac,
            )

            quad_count += 1

        else:
            tri_loop_indices = deterministic_symmetric_ngon(
                loop_indices=poly_loop_indices,
                points=points,
                detected_axes=detected_axes,
                tolerance=symmetry_tolerance,
            )

        for tri in tri_loop_indices:
            tri = orient_triangle_to_normal(
                tri=tri,
                old_loop_to_vertex=old_loop_to_vertex,
                verts=verts,
                target_normal=poly.normal,
            )
            face_verts = [old_loop_to_vertex[li] for li in tri]

            if len(set(face_verts)) != 3:
                continue

            new_faces.append(face_verts)
            new_face_materials.append(poly.material_index)
            new_face_smooths.append(poly.use_smooth)
            new_loop_sources.append(tri)

    new_mesh = bpy.data.meshes.new(src_mesh.name + "_TRIANGULATED")
    new_mesh.from_pydata([v[:] for v in verts], [], new_faces)
    new_mesh.update()

    copy_materials(src_mesh, new_mesh)

    for i, poly in enumerate(new_mesh.polygons):
        poly.material_index = new_face_materials[i]

        if keep_normals:
            poly.use_smooth = new_face_smooths[i]
        else:
            poly.use_smooth = False

    copy_uvs(src_mesh, new_mesh, new_loop_sources)
    copy_corner_colors(src_mesh, new_mesh, new_loop_sources)
    if preserve_sharp_edges:
        copy_sharp_edges(src_mesh, new_mesh)

    if keep_normals:
        custom_normals = [Vector((0.0, 0.0, 1.0)) for _ in range(len(new_mesh.loops))]

        for poly_i, poly in enumerate(new_mesh.polygons):
            source_tri = new_loop_sources[poly_i]

            for corner_offset, new_loop_index in enumerate(poly.loop_indices):
                old_loop_index = source_tri[corner_offset]

                if old_loop_index < len(source_corner_normals):
                    custom_normals[new_loop_index] = source_corner_normals[old_loop_index]
                else:
                    custom_normals[new_loop_index] = poly.normal.copy()

        try:
            new_mesh.normals_split_custom_set(custom_normals)
            new_mesh.update()
        except Exception as e:
            print("[Bake Addon Canonical Sym Tri V6] Could not set custom normals:", e)

    return new_mesh, detected_axes, quad_count


# ------------------------------------------------------------
# Operator
# ------------------------------------------------------------

class BAKEADDON_OT_canonical_symmetric_triangulate_v6(bpy.types.Operator):
    bl_idname = "bakeaddon.canonical_symmetric_triangulate_v6"
    bl_label = "Canonical Symmetric Triangulate V6"
    bl_description = "Triangulate quads using auto symmetry and canonical bounding-box space while keeping normals"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene

        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if not selected:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        made = 0
        total_quads = 0
        axis_report = []

        for obj in selected:
            try:
                source_mesh = get_source_mesh(
                    context=context,
                    obj=obj,
                    use_evaluated=scene.bakeaddon_cansymtri_v6_use_evaluated,
                )

                tri_mesh, axes, quad_count = build_triangulated_mesh(
                    src_mesh=source_mesh,
                    keep_normals=scene.bakeaddon_cansymtri_v6_keep_normals,
                    symmetry_tolerance=scene.bakeaddon_cansymtri_v6_symmetry_tolerance,
                )

                if axes:
                    axis_names = ", ".join(
                        f"{a['axis']}({a['score']:.2f}) center={a['center']:.4f}"
                        for a in axes
                    )
                else:
                    axis_names = "none"

                axis_report.append(f"{obj.name}: {axis_names}")
                total_quads += quad_count

                if scene.bakeaddon_cansymtri_v6_duplicate_output:
                    tri_obj = bpy.data.objects.new(
                        obj.name + scene.bakeaddon_cansymtri_v6_suffix,
                        tri_mesh
                    )

                    tri_obj.matrix_world = obj.matrix_world.copy()

                    if obj.users_collection:
                        obj.users_collection[0].objects.link(tri_obj)
                    else:
                        context.collection.objects.link(tri_obj)

                    obj.select_set(False)
                    tri_obj.select_set(True)
                    context.view_layer.objects.active = tri_obj

                else:
                    old_mesh = obj.data
                    obj.data = tri_mesh

                    if scene.bakeaddon_cansymtri_v6_use_evaluated:
                        obj.modifiers.clear()

                    if old_mesh.users == 0:
                        bpy.data.meshes.remove(old_mesh)

                if scene.bakeaddon_cansymtri_v6_clean_temp_mesh:
                    if source_mesh.users == 0:
                        bpy.data.meshes.remove(source_mesh)

                made += 1

            except Exception as e:
                print("[Bake Addon Canonical Sym Tri V6] Failed on", obj.name, e)
                self.report({'WARNING'}, f"Failed on {obj.name}: {e}")

        print("[Bake Addon Canonical Sym Tri V6] Symmetry report:")
        for line in axis_report:
            print("  ", line)

        self.report(
            {'INFO'},
            f"Triangulated {made} object(s). Quads processed: {total_quads}."
        )

        return {'FINISHED'}


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------

class BAKEADDON_PT_canonical_symmetric_triangulate_v6_panel(bpy.types.Panel):
    bl_label = "Canonical Symmetric Triangulate V6"
    bl_idname = "BAKEADDON_PT_canonical_symmetric_triangulate_v6_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bake Addon"

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        col = layout.column(align=True)

        col.label(text="Canonical Symmetric Triangulation")
        col.separator()

        col.prop(scene, "bakeaddon_cansymtri_v6_use_evaluated")
        col.prop(scene, "bakeaddon_cansymtri_v6_keep_normals")
        col.prop(scene, "bakeaddon_cansymtri_v6_duplicate_output")
        col.prop(scene, "bakeaddon_cansymtri_v6_suffix")
        col.prop(scene, "bakeaddon_cansymtri_v6_clean_temp_mesh")

        col.separator()

        box = col.box()
        box.label(text="Auto Symmetry")
        box.prop(scene, "bakeaddon_cansymtri_v6_symmetry_tolerance")

        col.separator()

        col.operator(
            BAKEADDON_OT_canonical_symmetric_triangulate_v6.bl_idname,
            text="Triangulate Selected V6",
            icon='MOD_TRIANGULATE',
        )

        col.separator()

        info = col.box()
        info.label(text="V6 logic:")
        info.label(text="No face pairing")
        info.label(text="Uses bounding-box symmetry")
        info.label(text="Canonicalizes each quad")
        info.label(text="Then chooses diagonal")


# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

classes = (
    BAKEADDON_OT_canonical_symmetric_triangulate_v6,
    BAKEADDON_PT_canonical_symmetric_triangulate_v6_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.bakeaddon_cansymtri_v6_use_evaluated = bpy.props.BoolProperty(
        name="Use Evaluated Modifiers",
        description="Use final evaluated mesh including Mirror, Array, Bevel, Weighted Normal, etc.",
        default=True,
    )

    bpy.types.Scene.bakeaddon_cansymtri_v6_keep_normals = bpy.props.BoolProperty(
        name="Keep Normals",
        description="Preserve evaluated corner normals as custom split normals.",
        default=True,
    )

    bpy.types.Scene.bakeaddon_cansymtri_v6_duplicate_output = bpy.props.BoolProperty(
        name="Create Duplicate",
        description="Create triangulated duplicate and keep original object untouched.",
        default=True,
    )

    bpy.types.Scene.bakeaddon_cansymtri_v6_suffix = bpy.props.StringProperty(
        name="Suffix",
        description="Suffix for triangulated duplicate.",
        default="_TRI",
    )

    bpy.types.Scene.bakeaddon_cansymtri_v6_clean_temp_mesh = bpy.props.BoolProperty(
        name="Clean Temporary Source Mesh",
        description="Remove temporary evaluated source mesh after triangulation.",
        default=True,
    )

    bpy.types.Scene.bakeaddon_cansymtri_v6_symmetry_tolerance = bpy.props.FloatProperty(
        name="Symmetry Tolerance",
        description="Tolerance used to detect symmetry axes.",
        default=0.001,
        min=0.000001,
        max=0.1,
        precision=6,
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.bakeaddon_cansymtri_v6_use_evaluated
    del bpy.types.Scene.bakeaddon_cansymtri_v6_keep_normals
    del bpy.types.Scene.bakeaddon_cansymtri_v6_duplicate_output
    del bpy.types.Scene.bakeaddon_cansymtri_v6_suffix
    del bpy.types.Scene.bakeaddon_cansymtri_v6_clean_temp_mesh
    del bpy.types.Scene.bakeaddon_cansymtri_v6_symmetry_tolerance


if __name__ == "__main__":
    register()


def build_substance_triangulated_mesh(
    src_mesh,
    triangulate_quads=True,
    keep_normals=True,
    preserve_sharp_edges=True,
    symmetry_tolerance=0.001,
):
    if triangulate_quads:
        return build_triangulated_mesh(
            src_mesh=src_mesh,
            keep_normals=keep_normals,
            preserve_sharp_edges=preserve_sharp_edges,
            symmetry_tolerance=symmetry_tolerance,
        )

    src_mesh.update()

    verts = [v.co.copy() for v in src_mesh.vertices]
    source_corner_normals = get_corner_normals(src_mesh)

    old_loop_to_vertex = {}
    for loop in src_mesh.loops:
        old_loop_to_vertex[loop.index] = loop.vertex_index

    detected_axes = detect_symmetry_axes(
        verts=verts,
        tolerance=symmetry_tolerance,
        min_score=0.85,
    )

    new_faces = []
    new_face_materials = []
    new_face_smooths = []
    new_loop_sources = []

    for poly in src_mesh.polygons:
        poly_loop_indices = list(poly.loop_indices)

        points = []
        for li in poly_loop_indices:
            vi = old_loop_to_vertex[li]
            points.append(verts[vi])

        if len(poly_loop_indices) <= 4:
            face_verts = [old_loop_to_vertex[li] for li in poly_loop_indices]

            if len(set(face_verts)) != len(face_verts):
                continue

            new_faces.append(face_verts)
            new_face_materials.append(poly.material_index)
            new_face_smooths.append(poly.use_smooth)
            new_loop_sources.append(tuple(poly_loop_indices))
            continue

        tri_loop_indices = deterministic_symmetric_ngon(
            loop_indices=poly_loop_indices,
            points=points,
            detected_axes=detected_axes,
            tolerance=symmetry_tolerance,
        )

        for tri in tri_loop_indices:
            tri = orient_triangle_to_normal(
                tri=tri,
                old_loop_to_vertex=old_loop_to_vertex,
                verts=verts,
                target_normal=poly.normal,
            )
            face_verts = [old_loop_to_vertex[li] for li in tri]

            if len(set(face_verts)) != 3:
                continue

            new_faces.append(face_verts)
            new_face_materials.append(poly.material_index)
            new_face_smooths.append(poly.use_smooth)
            new_loop_sources.append(tri)

    new_mesh = bpy.data.meshes.new(src_mesh.name + "_TRIANGULATED")
    new_mesh.from_pydata([v[:] for v in verts], [], new_faces)
    new_mesh.update()

    copy_materials(src_mesh, new_mesh)

    for i, poly in enumerate(new_mesh.polygons):
        poly.material_index = new_face_materials[i]

        if keep_normals:
            poly.use_smooth = new_face_smooths[i]
        else:
            poly.use_smooth = False

    copy_uvs(src_mesh, new_mesh, new_loop_sources)
    copy_corner_colors(src_mesh, new_mesh, new_loop_sources)
    if preserve_sharp_edges:
        copy_sharp_edges(src_mesh, new_mesh)

    if keep_normals:
        custom_normals = [Vector((0.0, 0.0, 1.0)) for _ in range(len(new_mesh.loops))]

        for poly_i, poly in enumerate(new_mesh.polygons):
            source_poly = new_loop_sources[poly_i]

            for corner_offset, new_loop_index in enumerate(poly.loop_indices):
                old_loop_index = source_poly[corner_offset]

                if old_loop_index < len(source_corner_normals):
                    custom_normals[new_loop_index] = source_corner_normals[old_loop_index]
                else:
                    custom_normals[new_loop_index] = poly.normal.copy()

        try:
            new_mesh.normals_split_custom_set(custom_normals)
            new_mesh.update()
        except Exception as e:
            print("[Bake Addon Canonical Sym Tri V6] Could not set custom normals:", e)

    return new_mesh, detected_axes, 0
