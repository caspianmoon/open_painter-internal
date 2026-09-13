# --- START OF FILE operators_unbevel.py --- [MODIFIED]

# gready/operators_unbevel.py
# Contains code originally from unchamfer.py (v5.7.12) integrated into G-Ready

import blf
import bpy
import bmesh
import math
import mathutils
from mathutils import Vector
from mathutils.geometry import intersect_line_line

from . import shared_state

# --- Constants and Helpers ---
EPSILON = 1e-6
HUD_OFFSET_X = 76
HUD_OFFSET_Y = 38


def set_blf_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, bpy.context.preferences.system.dpi or 72)


def draw_shadowed_text(font_id, x, y, text, size, color=(1.0, 1.0, 1.0, 1.0)):
    set_blf_size(font_id, size)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.62)
    blf.position(font_id, x + 1, y - 1, 0)
    blf.draw(font_id, text)
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


def update_mesh_viewport(bm, me):
    # --- (Unchanged) ---
    try:
        bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
        return True
    except Exception as e:
        print(f"Error in update_mesh_viewport: {e}"); return False

def finalize_mesh_update(bm, me):
    # --- (Unchanged) ---
    try:
        if bm and bm.is_valid:
            bmesh.update_edit_mesh(me)
            if not bm.is_valid:
                 print("Warning: BMesh became invalid after update_edit_mesh.")
            return True
        elif not bm:
             print("Warning: finalize_mesh_update called with None BMesh.")
             return False
        elif not bm.is_valid:
             print("Warning: finalize_mesh_update called with invalid BMesh.")
             return False
    except ReferenceError as re:
         print(f"Error in finalize_mesh_update (ReferenceError likely mesh/object deleted): {re}")
         return False
    except Exception as e:
        print(f"Error in finalize_mesh_update: {e}")
        return False
    return False


def find_best_neighbor_edge(bm, boundary_vert, segment_edge, excluded_edges):
    # --- (Unchanged) ---
    if not boundary_vert or not segment_edge: return None
    other_v_segment = segment_edge.other_vert(boundary_vert)
    if not other_v_segment: return None
    try:
        segment_vec = (boundary_vert.co - other_v_segment.co).normalized()
    except ValueError:
        return None
    best_neighbor = None
    max_dot = -2.0
    for linked_edge in boundary_vert.link_edges:
        if linked_edge == segment_edge or linked_edge in excluded_edges:
            continue
        other_v_neighbor = linked_edge.other_vert(boundary_vert)
        if not other_v_neighbor:
            continue
        try:
            neighbor_vec = (other_v_neighbor.co - boundary_vert.co).normalized()
            dot = segment_vec.dot(neighbor_vec)
        except ValueError:
            continue
        if dot > max_dot:
            max_dot = dot
            best_neighbor = linked_edge
    return best_neighbor


def build_unbevel_groups_from_edges(bm, initial_selected_edges):
    processed_initial_edges = set()
    final_operation_groups = []
    found_chains_count = 0
    edges_in_found_chains = set()

    while True:
        remaining_initial = initial_selected_edges - processed_initial_edges
        if not remaining_initial:
            break

        start_edge = next(iter(remaining_initial))
        current_segment_edges = set()
        queue = {start_edge}
        visited_in_segment = set()

        while queue:
            edge = queue.pop()
            if edge in visited_in_segment or edge not in initial_selected_edges:
                continue

            visited_in_segment.add(edge)
            current_segment_edges.add(edge)

            for v in edge.verts:
                for linked_edge in v.link_edges:
                    if linked_edge in initial_selected_edges and linked_edge not in visited_in_segment:
                        queue.add(linked_edge)

        processed_initial_edges.update(current_segment_edges)
        segment_verts = {v for edge in current_segment_edges for v in edge.verts}
        boundary_verts = []

        for v in segment_verts:
            count = sum(1 for edge in v.link_edges if edge in current_segment_edges)
            if count == 1:
                boundary_verts.append(v)

        if len(boundary_verts) != 2:
            if len(current_segment_edges) == 1:
                boundary_verts = list(start_edge.verts)
                if len(boundary_verts) != 2:
                    continue
            else:
                continue

        boundary_vert1, boundary_vert2 = boundary_verts
        segment_edge1 = next((e for e in current_segment_edges if boundary_vert1 in e.verts), None)
        segment_edge2 = next((e for e in current_segment_edges if boundary_vert2 in e.verts), None)
        if not segment_edge1 or not segment_edge2:
            continue

        neighbor1 = find_best_neighbor_edge(bm, boundary_vert1, segment_edge1, initial_selected_edges)
        neighbor2 = find_best_neighbor_edge(bm, boundary_vert2, segment_edge2, initial_selected_edges)
        if neighbor1 is None or neighbor2 is None:
            continue

        full_chain_edges = current_segment_edges | {neighbor1, neighbor2}
        if len(full_chain_edges) < 3:
            continue

        v_outer1 = neighbor1.other_vert(boundary_vert1)
        v_outer3 = neighbor2.other_vert(boundary_vert2)
        if not v_outer1 or not v_outer3:
            continue

        ordered_verts = []
        curr_v = v_outer1
        prev_v = None
        chain_edges_temp = full_chain_edges.copy()
        visited_edges = set()
        valid_trace = True
        max_steps = len(chain_edges_temp) + 2
        steps = 0

        while curr_v != v_outer3 and steps < max_steps:
            ordered_verts.append(curr_v)
            found_next = False

            for edge in curr_v.link_edges:
                if edge in chain_edges_temp and edge not in visited_edges:
                    next_v = edge.other_vert(curr_v)
                    if next_v and next_v != prev_v:
                        prev_v = curr_v
                        curr_v = next_v
                        visited_edges.add(edge)
                        found_next = True
                        break

            if not found_next:
                valid_trace = False
                break

            steps += 1

        if not valid_trace or steps >= max_steps:
            continue

        ordered_verts.append(v_outer3)
        if len(ordered_verts) < 3:
            continue

        v_move_verts = ordered_verts[1:-1]
        if not v_move_verts:
            continue

        p1 = v_outer1.co
        dir1_vec = (v_move_verts[0].co - p1)
        dir1 = dir1_vec.normalized() if dir1_vec.length > EPSILON else None
        p3 = v_outer3.co
        dir3_vec = (v_move_verts[-1].co - p3)
        dir3 = dir3_vec.normalized() if dir3_vec.length > EPSILON else None

        if dir1 is None or dir3 is None:
            continue
        if dir1.dot(dir3) > (1.0 - EPSILON) or dir1.dot(dir3) < (-1.0 + EPSILON):
            continue

        intersect_result = intersect_line_line(p1, p1 + dir1, p3, p3 + dir3)
        if intersect_result is None:
            continue

        closest_p1, closest_p3 = intersect_result
        pivot_local = (closest_p1 + closest_p3) / 2.0
        chain_data = {
            'v_outer1_idx': v_outer1.index,
            'v_outer3_idx': v_outer3.index,
            'v_move_indices': [v.index for v in v_move_verts],
            'initial_move_coords': [v.co.copy() for v in v_move_verts],
            'pivot_local': pivot_local,
            'v_outer1_co': p1.copy(),
            'v_outer3_co': p3.copy(),
            'dir1': dir1.copy(),
            'dir3': dir3.copy(),
        }

        final_operation_groups.append(chain_data)
        edges_in_found_chains.update(full_chain_edges)
        found_chains_count += 1

    return final_operation_groups, edges_in_found_chains, found_chains_count


def _selected_face_islands(selected_faces):
    remaining = set(selected_faces)
    islands = []

    while remaining:
        start_face = remaining.pop()
        island = {start_face}
        stack = [start_face]

        while stack:
            face = stack.pop()
            for edge in face.edges:
                for linked_face in edge.link_faces:
                    if linked_face in remaining:
                        remaining.remove(linked_face)
                        island.add(linked_face)
                        stack.append(linked_face)

        islands.append(island)

    return islands


def _selected_edge_components(selected_edges):
    remaining = set(selected_edges)
    components = []

    while remaining:
        start_edge = remaining.pop()
        component = {start_edge}
        stack = [start_edge]

        while stack:
            edge = stack.pop()
            for vert in edge.verts:
                for linked_edge in vert.link_edges:
                    if linked_edge in remaining:
                        remaining.remove(linked_edge)
                        component.add(linked_edge)
                        stack.append(linked_edge)

        components.append(component)

    return components


def _open_edge_chain_components(selected_edges):
    open_components = []

    for component in _selected_edge_components(selected_edges):
        if len(component) == 1:
            open_components.append(component)
            continue

        verts = {vert for edge in component for vert in edge.verts if vert.is_valid}
        endpoint_count = 0
        branched = False

        for vert in verts:
            degree = sum(1 for edge in vert.link_edges if edge in component)
            if degree == 1:
                endpoint_count += 1
            elif degree != 2:
                branched = True
                break

        if not branched and endpoint_count == 2:
            open_components.append(component)

    return open_components


def _ordered_open_edge_chain(chain_edges):
    if not chain_edges:
        return None

    verts = {vert for edge in chain_edges for vert in edge.verts if vert.is_valid}
    endpoints = []
    for vert in verts:
        degree = sum(1 for edge in vert.link_edges if edge in chain_edges)
        if degree == 1:
            endpoints.append(vert)
        elif degree != 2:
            return None

    if len(chain_edges) == 1:
        edge = next(iter(chain_edges))
        return [edge.verts[0], edge.verts[1]], [edge]

    if len(endpoints) != 2:
        return None

    ordered_verts = [endpoints[0]]
    ordered_edges = []
    current_vert = endpoints[0]
    previous_edge = None

    for _step in range(len(chain_edges)):
        next_edges = [
            edge for edge in current_vert.link_edges
            if edge in chain_edges and edge != previous_edge
        ]
        if len(next_edges) != 1:
            return None

        next_edge = next_edges[0]
        next_vert = next_edge.other_vert(current_vert)
        if not next_vert or next_vert in ordered_verts:
            return None

        ordered_edges.append(next_edge)
        ordered_verts.append(next_vert)
        previous_edge = next_edge
        current_vert = next_vert

        if current_vert == endpoints[1]:
            break

    if len(ordered_edges) != len(chain_edges) or ordered_verts[-1] != endpoints[1]:
        return None

    return ordered_verts, ordered_edges


def _edge_length(edge):
    return (edge.verts[0].co - edge.verts[1].co).length


def _chain_length(edges):
    return sum(_edge_length(edge) for edge in edges)


def _normalize_or_none(vec):
    if vec.length <= EPSILON:
        return None
    return vec.normalized()


def _boundary_cycle_from_faces(face_island):
    cycles, error = _boundary_cycles_from_faces(face_island)
    if error:
        return None, error
    if len(cycles) != 1:
        return None, "Selected face patch contains multiple boundary loops."
    return cycles[0], None


def _boundary_cycles_from_faces(face_island):
    boundary_edges = []

    for face in face_island:
        for edge in face.edges:
            selected_link_count = sum(1 for linked_face in edge.link_faces if linked_face in face_island)
            if selected_link_count == 1:
                boundary_edges.append(edge)

    if len(boundary_edges) < 4:
        return None, "Selected face patch needs a closed quad-strip boundary."

    vertex_edges = {}
    for edge in boundary_edges:
        for vert in edge.verts:
            vertex_edges.setdefault(vert, []).append(edge)

    if any(len(edges) != 2 for edges in vertex_edges.values()):
        return None, "Selected face patch boundary is branched or open."

    remaining_edges = set(boundary_edges)
    cycles = []

    while remaining_edges:
        start_edge = next(iter(remaining_edges))
        start_vert = start_edge.verts[0]
        current_edge = start_edge
        current_vert = start_vert
        ordered_edges = []
        ordered_verts = [start_vert]
        visited_edges = set()

        while True:
            if current_edge in visited_edges:
                return None, "Selected face patch boundary could not be walked cleanly."

            visited_edges.add(current_edge)
            remaining_edges.discard(current_edge)
            ordered_edges.append(current_edge)
            next_vert = current_edge.other_vert(current_vert)
            ordered_verts.append(next_vert)

            if next_vert == start_vert:
                break

            next_edges = [edge for edge in vertex_edges[next_vert] if edge != current_edge]
            if len(next_edges) != 1:
                return None, "Selected face patch boundary is not a simple loop."

            current_vert = next_vert
            current_edge = next_edges[0]

        cycles.append((ordered_edges, ordered_verts[:-1]))

    return cycles, None


def _split_boundary_into_sides(ordered_edges, ordered_verts):
    vert_count = len(ordered_verts)
    corner_indices = []

    for idx, vert in enumerate(ordered_verts):
        prev_vert = ordered_verts[(idx - 1) % vert_count]
        next_vert = ordered_verts[(idx + 1) % vert_count]
        incoming = _normalize_or_none(vert.co - prev_vert.co)
        outgoing = _normalize_or_none(next_vert.co - vert.co)
        if incoming is None or outgoing is None:
            continue

        angle = math.degrees(math.acos(max(-1.0, min(1.0, incoming.dot(outgoing)))))
        if angle > 45.0:
            corner_indices.append(idx)

    if len(corner_indices) != 4:
        return None

    sides = []
    for side_idx, start_corner in enumerate(corner_indices):
        end_corner = corner_indices[(side_idx + 1) % len(corner_indices)]
        side_edges = []
        edge_idx = start_corner

        while edge_idx != end_corner:
            side_edges.append(ordered_edges[edge_idx])
            edge_idx = (edge_idx + 1) % len(ordered_edges)

        if not side_edges:
            return None
        sides.append(side_edges)

    return sides


def _edge_direction(edge):
    return _normalize_or_none(edge.verts[1].co - edge.verts[0].co)


def _edge_matches_directions(edge, directions, min_dot=0.70):
    direction = _edge_direction(edge)
    if direction is None:
        return False

    return any(abs(direction.dot(candidate)) >= min_dot for candidate in directions if candidate is not None)


def _loop_tangent_at_vert(loop_data, vert):
    _ordered_edges, ordered_verts = loop_data
    if vert not in ordered_verts or len(ordered_verts) < 3:
        return None
    index = ordered_verts.index(vert)
    prev_vert = ordered_verts[(index - 1) % len(ordered_verts)]
    next_vert = ordered_verts[(index + 1) % len(ordered_verts)]
    return _normalize_or_none(next_vert.co - prev_vert.co)


def _unselected_boundary_normal(face_island, loop_data, vert):
    loop_edges, _ordered_verts = loop_data
    normals = []
    for edge in loop_edges:
        if vert not in edge.verts:
            continue
        for linked_face in edge.link_faces:
            if linked_face not in face_island and linked_face.is_valid:
                normals.append(linked_face.normal.copy())
    if not normals:
        return None

    normal = Vector((0.0, 0.0, 0.0))
    for item in normals:
        normal += item
    return _normalize_or_none(normal)


def _support_direction_from_boundary(face_island, loop_data, vert, toward_patch):
    tangent = _loop_tangent_at_vert(loop_data, vert)
    normal = _unselected_boundary_normal(face_island, loop_data, vert)
    toward = _normalize_or_none(toward_patch)
    if tangent is None or normal is None or toward is None:
        return toward

    candidates = []
    for candidate in (normal.cross(tangent), tangent.cross(normal)):
        candidate = _normalize_or_none(candidate)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return toward

    return max(candidates, key=lambda candidate: abs(candidate.dot(toward)))


def _trace_profile_chain(start_vert, start_edge, target_loop_verts, patch_edges, boundary_edges, max_steps):
    chain_verts = [start_vert]
    chain_edges = []
    current_vert = start_vert
    current_edge = start_edge
    visited_edges = set()

    for _step in range(max_steps):
        if current_edge in visited_edges or current_edge not in patch_edges or current_edge in boundary_edges:
            return None
        visited_edges.add(current_edge)
        chain_edges.append(current_edge)

        next_vert = current_edge.other_vert(current_vert)
        if not next_vert:
            return None
        chain_verts.append(next_vert)

        if next_vert in target_loop_verts:
            return chain_verts, chain_edges

        incoming = _normalize_or_none(next_vert.co - current_vert.co)
        if incoming is None:
            return None

        candidates = [
            edge for edge in next_vert.link_edges
            if edge in patch_edges and edge not in boundary_edges and edge != current_edge and edge not in visited_edges
        ]
        if not candidates:
            return None

        def continuation_score(edge):
            other = edge.other_vert(next_vert)
            if not other:
                return -2.0
            direction = _normalize_or_none(other.co - next_vert.co)
            if direction is None:
                return -2.0
            return incoming.dot(direction)

        best_edge = max(candidates, key=continuation_score)
        if continuation_score(best_edge) < -0.1:
            return None

        current_vert = next_vert
        current_edge = best_edge

    return None


def _annular_group_from_profile_chain(face_island, source_loop, target_loop, chain_verts, chain_edges):
    if len(chain_verts) < 2:
        return None

    start_vert = chain_verts[0]
    end_vert = chain_verts[-1]
    start_toward_patch = chain_verts[1].co - start_vert.co
    end_toward_patch = chain_verts[-2].co - end_vert.co

    support_dir_start = _support_direction_from_boundary(face_island, source_loop, start_vert, start_toward_patch)
    support_dir_end = _support_direction_from_boundary(face_island, target_loop, end_vert, end_toward_patch)
    if support_dir_start is None:
        support_dir_start = _normalize_or_none(start_toward_patch)
    if support_dir_end is None:
        support_dir_end = _normalize_or_none(end_toward_patch)
    if support_dir_start is None or support_dir_end is None:
        return None

    intersect_result = intersect_line_line(
        start_vert.co,
        start_vert.co + support_dir_start,
        end_vert.co,
        end_vert.co + support_dir_end,
    )
    if intersect_result is None:
        return None

    closest_start, closest_end = intersect_result
    pivot_local = (closest_start + closest_end) / 2.0
    dir1 = _normalize_or_none(pivot_local - start_vert.co) or support_dir_start.copy()
    dir3 = _normalize_or_none(pivot_local - end_vert.co) or support_dir_end.copy()

    return {
        'v_outer1_idx': start_vert.index,
        'v_outer3_idx': end_vert.index,
        'v_move_indices': [vert.index for vert in chain_verts],
        'initial_move_coords': [vert.co.copy() for vert in chain_verts],
        'pivot_local': pivot_local,
        'v_outer1_co': start_vert.co.copy(),
        'v_outer3_co': end_vert.co.copy(),
        'dir1': dir1.copy(),
        'dir3': dir3.copy(),
        'profile_edge_indices': [edge.index for edge in chain_edges if edge.is_valid],
    }


def _annular_face_unbevel_candidates_from_island(bm, face_island, boundary_cycles):
    if len(boundary_cycles) != 2:
        return None, "Selected annular face patch must have exactly two boundary loops."

    patch_edges = {edge for face in face_island for edge in face.edges if edge.is_valid}
    boundary_edges = {edge for cycle_edges, _cycle_verts in boundary_cycles for edge in cycle_edges}
    if not patch_edges or not boundary_edges:
        return None, "Selected annular face patch has invalid topology."

    candidates = []
    seen_signatures = set()
    loop_pairs = ((boundary_cycles[0], boundary_cycles[1]), (boundary_cycles[1], boundary_cycles[0]))

    for source_loop, target_loop in loop_pairs:
        source_verts = list(source_loop[1])
        target_verts = set(target_loop[1])
        used_edges = set()
        profile_edges = set()
        groups = []

        for vert in source_verts:
            start_edges = [
                edge for edge in vert.link_edges
                if edge in patch_edges and edge not in boundary_edges and edge not in used_edges
            ]
            start_edges.sort(key=lambda edge: edge.index)

            best_trace = None
            for start_edge in start_edges:
                trace = _trace_profile_chain(
                    vert,
                    start_edge,
                    target_verts,
                    patch_edges,
                    boundary_edges,
                    max_steps=len(patch_edges) + 2,
                )
                if trace:
                    best_trace = trace
                    break

            if not best_trace:
                continue

            chain_verts, chain_edges = best_trace
            group = _annular_group_from_profile_chain(face_island, source_loop, target_loop, chain_verts, chain_edges)
            if not group:
                continue

            groups.append(group)
            used_edges.update(chain_edges)
            profile_edges.update(chain_edges)

        if not groups:
            continue

        signature = tuple(sorted(edge.index for edge in profile_edges if edge.is_valid))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        candidates.append({
            "pair": ("ANNULAR", len(candidates)),
            "edges": profile_edges,
            "groups": groups,
            "found_edges": profile_edges,
            "found_count": len(groups),
            "direct_groups": True,
            "method_label": "Patch",
            "sort_key": (
                0,
                sum(len(group["v_move_indices"]) for group in groups) / max(1, len(groups)),
                -len(groups),
            ),
        })

    if not candidates:
        return None, "No valid cylindrical/annular UnBevel profile chains could be inferred from the selected faces."

    candidates.sort(key=lambda item: item["sort_key"])
    return candidates, None


def _build_face_unbevel_candidate(bm, face_island, sides, pair):
    pair_edges = set(sides[pair[0]] + sides[pair[1]])
    pair_groups, pair_found_edges, pair_found_count = build_unbevel_groups_from_edges(bm, pair_edges)
    if not pair_groups:
        return None

    target_directions = [_edge_direction(edge) for edge in sides[pair[0]] + sides[pair[1]]]
    patch_edges = {edge for face in face_island for edge in face.edges}
    inferred_edges = {
        edge for edge in patch_edges
        if _edge_matches_directions(edge, target_directions)
    }

    candidate_edges = pair_edges
    candidate_groups = pair_groups
    candidate_found_edges = pair_found_edges
    candidate_found_count = pair_found_count

    inferred_groups, inferred_found_edges, inferred_count = build_unbevel_groups_from_edges(bm, inferred_edges)
    if inferred_groups and len(inferred_groups) >= len(pair_groups):
        candidate_edges = inferred_edges
        candidate_groups = inferred_groups
        candidate_found_edges = inferred_found_edges
        candidate_found_count = inferred_count

    return {
        "pair": pair,
        "edges": candidate_edges,
        "groups": candidate_groups,
        "found_edges": candidate_found_edges,
        "found_count": candidate_found_count,
        "method_label": "Classic Face Rings",
        "sort_key": (
            len(pair_edges - pair_found_edges),
            (_chain_length(sides[pair[0]]) + _chain_length(sides[pair[1]])) / 2.0,
            -len(pair_groups),
        ),
    }


def _face_unbevel_candidates_from_island(bm, face_island):
    if any(len(face.verts) != 4 for face in face_island):
        return None, "Face-mode UnBevel requires quad bevel face patches."

    boundary_cycles, boundary_error = _boundary_cycles_from_faces(face_island)
    if boundary_error:
        return None, boundary_error

    if len(boundary_cycles) == 2:
        return _annular_face_unbevel_candidates_from_island(bm, face_island, boundary_cycles)

    if len(boundary_cycles) != 1:
        return None, "Selected face patch contains multiple boundary loops."

    ordered_edges, ordered_verts = boundary_cycles[0]
    sides = _split_boundary_into_sides(ordered_edges, ordered_verts)
    if not sides or len(sides) != 4:
        return None, "Selected face patch must look like a rectangular bevel strip."

    candidates = []
    seen_signatures = set()
    for pair in ((0, 2), (1, 3)):
        candidate = _build_face_unbevel_candidate(bm, face_island, sides, pair)
        if not candidate:
            continue

        signature = tuple(sorted(edge.index for edge in candidate["edges"] if edge.is_valid))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        candidates.append(candidate)

    if not candidates:
        return None, "No valid UnBevel edge rings could be inferred from the selected faces."

    candidates.sort(key=lambda item: item["sort_key"])
    return candidates, None


def _combine_face_unbevel_candidate_set(bm, candidate_sets, candidate_index):
    combined_edges = set()
    island_pairs = []
    direct_groups = []
    use_direct_groups = False
    method_labels = set()

    for island_candidates in candidate_sets:
        candidate = island_candidates[candidate_index]
        combined_edges.update(candidate["edges"])
        island_pairs.append(candidate["pair"])
        if candidate.get("direct_groups"):
            use_direct_groups = True
        direct_groups.extend(candidate.get("groups", []))
        if candidate.get("method_label"):
            method_labels.add(candidate["method_label"])

    method_label = method_labels.pop() if len(method_labels) == 1 else "Mixed"

    if use_direct_groups:
        if not direct_groups:
            return None
        return {
            "edges": combined_edges,
            "groups": direct_groups,
            "found_edges": combined_edges,
            "found_count": len(direct_groups),
            "pairs": island_pairs,
            "unprocessed": 0,
            "method_label": method_label,
        }

    groups, found_edges, found_count = build_unbevel_groups_from_edges(bm, combined_edges)
    if not groups:
        return None

    return {
        "edges": combined_edges,
        "groups": groups,
        "found_edges": found_edges,
        "found_count": found_count,
        "pairs": island_pairs,
        "unprocessed": len(combined_edges - found_edges),
        "method_label": method_label or "Classic Face Rings",
    }


def _classic_face_candidate_from_inferred_edges(bm, candidate):
    inferred_edges = {edge for edge in candidate.get("edges", set()) if edge.is_valid}
    if not inferred_edges:
        return None

    groups, found_edges, found_count = build_unbevel_groups_from_edges(bm, inferred_edges)
    if not groups:
        return None

    classic_candidate = dict(candidate)
    classic_candidate["groups"] = groups
    classic_candidate["found_edges"] = found_edges
    classic_candidate["found_count"] = found_count
    classic_candidate["method_label"] = "Classic Face Rings"
    classic_candidate.pop("direct_groups", None)
    return classic_candidate


def infer_unbevel_edge_candidates_from_faces(bm, selected_faces, prefer_classic=False):
    candidate_sets = []
    errors = []

    for island in _selected_face_islands(selected_faces):
        island_candidates, error = _face_unbevel_candidates_from_island(bm, island)
        if error:
            errors.append(error)
            continue

        if prefer_classic:
            classic_candidates = []
            for candidate in island_candidates:
                classic_candidate = _classic_face_candidate_from_inferred_edges(bm, candidate)
                if classic_candidate:
                    classic_candidates.append(classic_candidate)
            if classic_candidates:
                island_candidates = classic_candidates

        candidate_sets.append(island_candidates)

    if errors:
        return [], errors[0]

    if not candidate_sets:
        return [], "No selected bevel face patch found."

    candidate_count = min(2, min(len(candidates) for candidates in candidate_sets))
    combined_candidates = []

    for candidate_index in range(candidate_count):
        candidate = _combine_face_unbevel_candidate_set(bm, candidate_sets, candidate_index)
        if candidate:
            combined_candidates.append(candidate)

    if not combined_candidates:
        return [], "No valid UnBevel edge rings could be inferred from the selected faces."

    return combined_candidates, None


def _profile_chain_endpoint_support_direction(endpoint_vert, toward_vert, chain_faces, chain_edges):
    toward_patch = _normalize_or_none(toward_vert.co - endpoint_vert.co)
    if toward_patch is None:
        return None

    boundary_support_faces = set()
    for edge in endpoint_vert.link_edges:
        if edge in chain_edges or not edge.is_valid:
            continue

        if any(face in chain_faces for face in edge.link_faces):
            boundary_support_faces.update(
                face for face in edge.link_faces if face not in chain_faces and face.is_valid
            )

    continuation_edges = [
        edge
        for edge in endpoint_vert.link_edges
        if (
            edge not in chain_edges
            and edge.is_valid
            and not any(face in chain_faces for face in edge.link_faces)
            and any(face in boundary_support_faces for face in edge.link_faces)
        )
    ]
    if continuation_edges:
        def continuation_score(edge):
            other_vert = edge.other_vert(endpoint_vert)
            if not other_vert:
                return -1.0
            direction = _normalize_or_none(other_vert.co - endpoint_vert.co)
            if direction is None:
                return -1.0
            return abs(direction.dot(toward_patch))

        best_edge = max(continuation_edges, key=continuation_score)
        other_vert = best_edge.other_vert(endpoint_vert)
        if other_vert:
            support_direction = _normalize_or_none(other_vert.co - endpoint_vert.co)
            if support_direction is not None:
                return support_direction

    best_direction = None
    best_score = -1.0

    for edge in endpoint_vert.link_edges:
        if edge in chain_edges or not edge.is_valid:
            continue

        chain_link_faces = [face for face in edge.link_faces if face in chain_faces]
        support_faces = [face for face in edge.link_faces if face not in chain_faces and face.is_valid]
        if not chain_link_faces or not support_faces:
            continue

        other_vert = edge.other_vert(endpoint_vert)
        if not other_vert:
            continue

        boundary_tangent = _normalize_or_none(other_vert.co - endpoint_vert.co)
        if boundary_tangent is None:
            continue

        for support_face in support_faces:
            support_normal = _normalize_or_none(support_face.normal.copy())
            if support_normal is None:
                continue

            for candidate in (support_normal.cross(boundary_tangent), boundary_tangent.cross(support_normal)):
                candidate = _normalize_or_none(candidate)
                if candidate is None:
                    continue

                score = abs(candidate.dot(toward_patch))
                if score > best_score:
                    best_score = score
                    best_direction = candidate

    return best_direction


def _profile_chain_group_from_edges(chain_edges):
    ordered_chain = _ordered_open_edge_chain(chain_edges)
    if not ordered_chain:
        return None

    ordered_verts, ordered_edges = ordered_chain
    if len(ordered_verts) < 2:
        return None

    chain_faces = {
        face
        for edge in ordered_edges
        for face in edge.link_faces
        if face.is_valid
    }
    if not chain_faces:
        return None

    start_vert = ordered_verts[0]
    end_vert = ordered_verts[-1]
    start_support_dir = _profile_chain_endpoint_support_direction(
        start_vert,
        ordered_verts[1],
        chain_faces,
        set(ordered_edges),
    )
    end_support_dir = _profile_chain_endpoint_support_direction(
        end_vert,
        ordered_verts[-2],
        chain_faces,
        set(ordered_edges),
    )
    if start_support_dir is None or end_support_dir is None:
        return None

    intersect_result = intersect_line_line(
        start_vert.co,
        start_vert.co + start_support_dir,
        end_vert.co,
        end_vert.co + end_support_dir,
    )
    if intersect_result is None:
        return None

    closest_start, closest_end = intersect_result
    pivot_local = (closest_start + closest_end) / 2.0
    dir1 = _normalize_or_none(pivot_local - start_vert.co) or start_support_dir.copy()
    dir3 = _normalize_or_none(pivot_local - end_vert.co) or end_support_dir.copy()

    return {
        'v_outer1_idx': start_vert.index,
        'v_outer3_idx': end_vert.index,
        'v_move_indices': [vert.index for vert in ordered_verts],
        'initial_move_coords': [vert.co.copy() for vert in ordered_verts],
        'pivot_local': pivot_local,
        'v_outer1_co': start_vert.co.copy(),
        'v_outer3_co': end_vert.co.copy(),
        'dir1': dir1.copy(),
        'dir3': dir3.copy(),
        'constrain_profile_endpoints': True,
        'profile_start_anchor': start_vert.co.copy(),
        'profile_start_dir': start_support_dir.copy(),
        'profile_end_anchor': end_vert.co.copy(),
        'profile_end_dir': end_support_dir.copy(),
        'profile_edge_indices': [edge.index for edge in ordered_edges if edge.is_valid],
    }


def _profile_chain_unbevel_candidates_from_edges(selected_edges):
    groups = []
    found_edges = set()

    for component in _open_edge_chain_components(selected_edges):
        group = _profile_chain_group_from_edges(component)
        if not group:
            continue

        groups.append(group)
        found_edges.update(component)

    if not groups:
        return [], "No selected bevel profile chains could be solved from their support faces."

    return [{
        "pair": ("PROFILE_CHAIN", 0),
        "edges": found_edges,
        "groups": groups,
        "found_edges": found_edges,
        "found_count": len(groups),
        "direct_groups": True,
        "sort_key": (
            0,
            sum(len(group["v_move_indices"]) for group in groups) / max(1, len(groups)),
            -len(groups),
        ),
    }], None


def infer_annular_unbevel_candidates_from_edges(bm, selected_edges):
    profile_seed_edges = {
        edge
        for component in _open_edge_chain_components(selected_edges)
        for edge in component
        if edge.is_valid
    }
    coverage_seed_edges = profile_seed_edges or {
        edge for edge in selected_edges if edge.is_valid
    }

    profile_seed_faces = {
        face
        for edge in profile_seed_edges
        for face in edge.link_faces
        if face.is_valid and len(face.verts) == 4
    }
    grid_seed_faces = {
        face
        for edge in selected_edges
        for face in edge.link_faces
        if (
            face.is_valid
            and len(face.verts) == 4
            and sum(1 for face_edge in face.edges if face_edge in selected_edges) >= 2
        )
    }
    seed_faces = profile_seed_faces | grid_seed_faces
    if not seed_faces:
        return [], "No quad bevel faces could be inferred from the selected edge rings."

    candidate_sets = []
    errors = []

    for island in _selected_face_islands(seed_faces):
        island_seed_edges = {
            edge
            for edge in coverage_seed_edges
            if any(face in island for face in edge.link_faces)
        }
        if not island_seed_edges:
            continue

        boundary_cycles, boundary_error = _boundary_cycles_from_faces(island)
        if boundary_error:
            errors.append(boundary_error)
            continue
        if len(boundary_cycles) != 2:
            continue

        island_candidates, inference_error = _annular_face_unbevel_candidates_from_island(
            bm,
            island,
            boundary_cycles,
        )
        if inference_error:
            errors.append(inference_error)
            continue

        filtered_candidates = []
        for candidate in island_candidates:
            candidate_edges = set(candidate["edges"])
            overlap_edges = candidate_edges & island_seed_edges
            if not overlap_edges:
                continue

            selected_coverage = len(overlap_edges) / max(1, len(island_seed_edges))
            candidate_coverage = len(overlap_edges) / max(1, len(candidate_edges))
            if selected_coverage < 0.50 and candidate_coverage < 0.70:
                continue

            filtered_candidates.append(candidate)

        if filtered_candidates:
            candidate_sets.append(filtered_candidates)

    if not candidate_sets:
        profile_candidates, _profile_error = _profile_chain_unbevel_candidates_from_edges(profile_seed_edges)
        if profile_candidates:
            return profile_candidates, None
        if errors:
            return [], errors[0]
        return [], "No cylindrical/annular bevel patch could be inferred from the selected edge rings."

    candidate_count = min(len(candidates) for candidates in candidate_sets)
    combined_candidates = []

    for candidate_index in range(candidate_count):
        candidate = _combine_face_unbevel_candidate_set(bm, candidate_sets, candidate_index)
        if candidate:
            combined_candidates.append(candidate)

    if not combined_candidates:
        return [], "No valid cylindrical/annular UnBevel edge rings could be inferred from the edge selection."

    return combined_candidates, None


def infer_unbevel_edges_from_faces(bm, selected_faces):
    candidates, error = infer_unbevel_edge_candidates_from_faces(bm, selected_faces)
    if error:
        return set(), error
    return set(candidates[0]["edges"]), None

# --- Base Class for Modal Logic (UnBevel) ---
class UnbevelBaseOperator(bpy.types.Operator):
    # --- (Unchanged) ---
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'} # BLOCKING for modal

    final_scale: bpy.props.FloatProperty( name="Scale", description="Final scale factor", default=1.0, min=0.0, soft_max=2.0, subtype='FACTOR', unit='NONE', precision=4 )
    unbevel_method: bpy.props.EnumProperty(
        name="Method",
        description="Solver priority for UnBevel",
        items=[
            ('AUTO', "Auto", "Use the combined solver: Classic edge-ring solving first, Patch fallback when needed"),
            ('CLASSIC', "Classic", "Prefer the older edge-chain solver, with Patch fallback when needed"),
            ('PATCH', "Patch", "Prefer the newer annular/profile patch solver, with Classic fallback when needed"),
        ],
        default='AUTO',
    )
    edge_slide: bpy.props.BoolProperty(name="Edge Slide", description="Slide end vertices along neighboring edges while unbeveling", default=False)
    unbevel_alternate_ring: bpy.props.BoolProperty(
        name="Direction",
        description="Use the alternate inferred UnBevel ring set, matching the modal mouse-wheel switch",
        default=False,
    )
    initial_edge_indices_csv: bpy.props.StringProperty(options={'HIDDEN'}, default="")
    initial_face_indices_csv: bpy.props.StringProperty(options={'HIDDEN'}, default="")
    initial_mesh_select_mode_csv: bpy.props.StringProperty(options={'HIDDEN'}, default="")
    operation_groups: list = []
    bm: object = None; obj: bpy.types.Object = None; me: bpy.types.Mesh = None
    last_mouse_region_x: int = 0
    total_delta_x: float = 0.0
    sensitivity: float = 0.0056
    current_scale_preview: float = 1.0
    finished_successfully: bool = False
    initial_selected_edge_indices: set = set()
    initial_selected_face_indices: set = set()
    selection_source: str = "EDGE"
    active_unbevel_method_label: str = "Classic"
    face_unbevel_candidates: list = []
    face_unbevel_candidate_index: int = 0
    original_mesh_select_mode: tuple = None
    _handle_draw_2d = None
    _ignore_initial_left_release: bool = False
    _initial_run: bool = True

    def _encode_indices(self, indices):
        return ",".join(str(idx) for idx in sorted(indices))

    def _decode_indices(self, indices_csv):
        if not indices_csv:
            return set()

        indices = set()
        for item in indices_csv.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                indices.add(int(item))
            except ValueError:
                pass
        return indices

    def _encode_select_mode(self, select_mode):
        return ",".join("1" if enabled else "0" for enabled in select_mode)

    def _decode_select_mode(self, select_mode_csv):
        if not select_mode_csv:
            return None

        parts = select_mode_csv.split(",")
        if len(parts) != 3:
            return None

        return tuple(part == "1" for part in parts)

    def _store_initial_selection_snapshot(self, select_mode, edge_indices, face_indices):
        self.initial_mesh_select_mode_csv = self._encode_select_mode(select_mode)
        self.initial_edge_indices_csv = self._encode_indices(edge_indices)
        self.initial_face_indices_csv = self._encode_indices(face_indices)

    def _method_report_label(self, raw_label):
        if self.unbevel_method == 'AUTO':
            if raw_label.startswith("Patch"):
                return "Auto: Patch Fallback"
            return f"Auto: {raw_label}"

        if self.unbevel_method == 'CLASSIC':
            if raw_label.startswith("Patch"):
                return "Classic: Patch Fallback"
            return raw_label

        if self.unbevel_method == 'PATCH':
            if raw_label.startswith("Classic"):
                return "Patch: Classic Fallback"
            return raw_label

        return raw_label

    def _restore_initial_selection_snapshot_for_redo(self, context):
        stored_select_mode = self._decode_select_mode(self.initial_mesh_select_mode_csv)
        stored_edge_indices = self._decode_indices(self.initial_edge_indices_csv)
        stored_face_indices = self._decode_indices(self.initial_face_indices_csv)

        if not stored_select_mode or (not stored_edge_indices and not stored_face_indices):
            return False

        if context.mode != 'EDIT_MESH':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except RuntimeError:
                print("Warning: Could not switch to Edit Mode to restore redo UnBevel selection.")
                return False

        obj = context.edit_object
        if not obj or not obj.data:
            return False

        bm = None
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()

            for v in bm.verts: v.select = False
            for e in bm.edges: e.select = False
            for f in bm.faces: f.select = False

            if stored_select_mode[2]:
                for face_idx in stored_face_indices:
                    if 0 <= face_idx < len(bm.faces):
                        face = bm.faces[face_idx]
                        if face.is_valid:
                            face.select_set(True)
            elif stored_select_mode[1]:
                for edge_idx in stored_edge_indices:
                    if 0 <= edge_idx < len(bm.edges):
                        edge = bm.edges[edge_idx]
                        if edge.is_valid:
                            edge.select_set(True)

            context.tool_settings.mesh_select_mode = stored_select_mode
            bmesh.update_edit_mesh(obj.data)
            return True
        except Exception as e:
            print(f"Warning: Could not restore redo UnBevel selection snapshot: {e}")
            return False
        finally:
            if bm and bm.is_valid:
                bm.free()

    # --- Helper to Apply Geometry (unchanged) ---
    def _apply_geometry(self, bm_instance, scale_value, is_slide_active):
        """Applies scaling or sliding to the geometry in the provided bmesh."""
        try:
            if not bm_instance or not bm_instance.is_valid:
                print("Error in _apply_geometry: Invalid BMesh instance.")
                return False
            bm_instance.verts.ensure_lookup_table() # Ensure indices valid

            for group in self.operation_groups:
                v_move_indices = group['v_move_indices']; initial_coords = group['initial_move_coords']
                num_move_verts = len(v_move_indices)
                if num_move_verts == 0: continue

                # Get verts directly from the passed bm_instance, checking validity
                v_move_verts = []
                for idx in v_move_indices:
                    if 0 <= idx < len(bm_instance.verts):
                        v = bm_instance.verts[idx]
                        if v.is_valid:
                            v_move_verts.append(v)
                        else:
                            print(f"Warning in _apply_geometry: Skipping invalid move vert index {idx}")
                    else:
                        print(f"Warning in _apply_geometry: Move vert index {idx} out of bounds.")
                if len(v_move_verts) != num_move_verts:
                     print("Warning: Mismatch in valid move verts found.")

                if not v_move_verts: continue # Skip if no valid verts were found for this group


                pivot = group['pivot_local']

                if group.get('constrain_profile_endpoints'):
                    if len(initial_coords) != len(v_move_verts):
                         print("Warning: Mismatch between initial_coords and valid v_move_verts in profile-chain scale.")
                         continue

                    if len(v_move_verts) == 1:
                        v_move_verts[0].co = pivot + (initial_coords[0] - pivot) * scale_value
                        continue

                    def constrained_endpoint(anchor_key, direction_key, initial_co):
                        anchor = group.get(anchor_key)
                        direction = group.get(direction_key)
                        if anchor is None or direction is None:
                            return pivot + (initial_co - pivot) * scale_value

                        direction = _normalize_or_none(direction.copy())
                        if direction is None:
                            return pivot + (initial_co - pivot) * scale_value

                        pivot_dist = (pivot - anchor).dot(direction)
                        initial_dist = (initial_co - anchor).dot(direction)
                        target_dist = pivot_dist + (initial_dist - pivot_dist) * scale_value
                        return anchor + direction * target_dist

                    target_start = constrained_endpoint(
                        'profile_start_anchor',
                        'profile_start_dir',
                        initial_coords[0],
                    )
                    target_end = constrained_endpoint(
                        'profile_end_anchor',
                        'profile_end_dir',
                        initial_coords[-1],
                    )

                    scaled_start = pivot + (initial_coords[0] - pivot) * scale_value
                    scaled_end = pivot + (initial_coords[-1] - pivot) * scale_value
                    correction_start = target_start - scaled_start
                    correction_end = target_end - scaled_end

                    for i, v_move in enumerate(v_move_verts):
                        factor = i / (len(v_move_verts) - 1)
                        base_co = pivot + (initial_coords[i] - pivot) * scale_value
                        v_move.co = base_co + correction_start.lerp(correction_end, factor)
                    continue

                if is_slide_active:
                    # --- Edge Slide ---
                    v_outer1_co = group['v_outer1_co']; dir1 = group['dir1']; v_outer3_co = group['v_outer3_co']; dir3 = group['dir3']
                    pivot_dist1 = (pivot - v_outer1_co).dot(dir1); pivot_dist3 = (pivot - v_outer3_co).dot(dir3)
                    if len(initial_coords) < 1: continue

                    if len(initial_coords) != len(v_move_verts):
                         print("Warning: Mismatch between initial_coords and valid v_move_verts in slide.")
                         continue # Skip this group

                    orig_endpoint1 = initial_coords[0]
                    orig_endpointN = initial_coords[-1]

                    initial_dist1 = (orig_endpoint1 - v_outer1_co).length; initial_dist3 = (orig_endpointN - v_outer3_co).length
                    span1 = initial_dist1 - pivot_dist1; span3 = initial_dist3 - pivot_dist3
                    target_dist1 = pivot_dist1 + span1 * scale_value; target_dist3 = pivot_dist3 + span3 * scale_value
                    target_endpoint1_co = v_outer1_co + dir1 * target_dist1; target_endpoint3_co = v_outer3_co + dir3 * target_dist3

                    v_move_verts[0].co = target_endpoint1_co

                    if len(v_move_verts) > 1:
                        v_move_verts[-1].co = target_endpoint3_co
                        if len(v_move_verts) > 2:
                            for i in range(1, len(v_move_verts) - 1):
                                factor = i / (len(v_move_verts) - 1)
                                orig_lerp_point = orig_endpoint1.lerp(orig_endpointN, factor)
                                offset_vec = initial_coords[i] - orig_lerp_point
                                new_lerp_point = target_endpoint1_co.lerp(target_endpoint3_co, factor)
                                v_move_verts[i].co = new_lerp_point + offset_vec
                    # --- End Edge Slide ---
                else:
                    # --- Pivot Scaling (Linear) ---
                    if len(initial_coords) != len(v_move_verts):
                         print("Warning: Mismatch between initial_coords and valid v_move_verts in scale.")
                         continue # Skip this group

                    for i, v_move in enumerate(v_move_verts):
                        initial_co = initial_coords[i]; vec = initial_co - pivot; v_move.co = pivot + vec * scale_value
                    # --- End Pivot Scaling ---
            return True
        except IndexError as ie:
             self.report({'ERROR'}, f"Error applying geometry (IndexError likely from group data): {ie}")
             return False
        except ZeroDivisionError:
             self.report({'ERROR'}, f"Error applying geometry: Division by zero encountered.")
             return False
        except ReferenceError as re:
             self.report({'ERROR'}, f"Error applying geometry (ReferenceError, likely deleted element): {re}")
             return False
        except Exception as e:
            self.report({'ERROR'}, f"Error applying geometry: {e}")
            return False

    # --- Restore Initial State Method (Corrected) ---
    def restore_initial_state(self):
        needs_free = False
        bm_restore = None
        try:
            if not self.obj or not self.me:
                print("Warning: Cannot restore initial state, obj or me missing.")
                return
            # Ensure we are in edit mode before getting bmesh
            if bpy.context.mode != 'EDIT_MESH':
                try:
                    # Attempt to switch, but don't fail restore if it doesn't work
                    bpy.ops.object.mode_set(mode='EDIT')
                    print("Switched to Edit Mode for restore_initial_state.")
                except RuntimeError:
                    print("Warning: restore_initial_state called outside Edit Mode and couldn't switch.")
                    return # Cannot get bmesh if not in edit mode

            # Get a fresh BMesh representing the current state
            bm_restore = bmesh.from_edit_mesh(self.me)
            needs_free = True
        except Exception as e:
             print(f"Error getting BMesh in restore_initial_state: {e}")
             # Ensure we don't leave bm_restore dangling if creation failed partially
             if bm_restore and needs_free:
                 try: bm_restore.free()
                 except: pass
             return

        print("Restoring initial state (Scale 1.0)...") # Debug
        # Apply scale 1.0 (original state) without sliding
        if self._apply_geometry(bm_restore, 1.0, False):
            # Pass the original mesh data (self.me) to finalize
            if not finalize_mesh_update(bm_restore, self.me): # CORRECTED LINE
                 print("Error finalizing mesh update during restore_initial_state.")
        else:
             print("Error applying geometry during restore_initial_state.")

        # Free BMesh if we created it, regardless of success/failure of apply/finalize
        if needs_free and bm_restore and bm_restore.is_valid:
             try:
                 bm_restore.free()
                 print("Freed BMesh from restore_initial_state.") # Debug
             except Exception as e_free:
                  print(f"Error freeing BMesh in restore_initial_state: {e_free}")
        elif needs_free and bm_restore and not bm_restore.is_valid:
             print("Warning: BMesh in restore_initial_state became invalid before explicit free.")


    # --- Restore Selection Method ---
    def restore_selection(self, context, original_mesh_select_mode=None):
        """Restores the initial USER edge selection and the original mesh select mode"""
        current_object_mode = context.mode
        edit_mode_switched_here = False
        restored_count = 0

        if current_object_mode != 'EDIT_MESH':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                edit_mode_switched_here = True
            except RuntimeError:
                print("Error: Cannot switch to Edit mode to restore selection."); return 0

        if (
            original_mesh_select_mode
            and original_mesh_select_mode[2]
            and hasattr(self, 'initial_selected_face_indices')
            and self.initial_selected_face_indices
        ):
            bm_final = None
            try:
                obj_final = context.edit_object
                if not obj_final or not obj_final.data:
                    print("Error: Cannot get edit object/mesh for face selection restore.")
                    return 0

                me_final = obj_final.data
                bm_final = bmesh.from_edit_mesh(me_final)
                bm_final.faces.ensure_lookup_table()

                for v in bm_final.verts: v.select = False
                for e in bm_final.edges: e.select = False
                for f in bm_final.faces: f.select = False

                selected_count = 0
                for face_idx in self.initial_selected_face_indices:
                    try:
                        if 0 <= face_idx < len(bm_final.faces):
                            face = bm_final.faces[face_idx]
                            if face.is_valid:
                                face.select_set(True)
                                selected_count += 1
                                restored_count += 1
                    except (IndexError, ReferenceError):
                        pass
                    except Exception as e_sel:
                        print(f"Warning: Error selecting face index {face_idx}: {e_sel}")

                bmesh.update_edit_mesh(me_final)
                print(f"Attempted to restore {selected_count} initially selected face(s).")

            except Exception as e:
                print(f"Error during face selection restore: {e}")
            finally:
                if bm_final and bm_final.is_valid:
                    bm_final.free()

        elif hasattr(self, 'initial_selected_edge_indices') and self.initial_selected_edge_indices:
            bm_final = None
            try:
                # Ensure we have the latest mesh data after potential merge
                obj_final = context.edit_object
                if not obj_final or not obj_final.data:
                    print("Error: Cannot get edit object/mesh for selection restore.")
                    return 0
                me_final = obj_final.data
                bm_final = bmesh.from_edit_mesh(me_final)
                bm_final.edges.ensure_lookup_table()
                bm_final.verts.ensure_lookup_table()

                # Deselect everything first (this will be overridden if scale != 0)
                for v in bm_final.verts: v.select = False
                for e in bm_final.edges: e.select = False
                for f in bm_final.faces: f.select = False

                selected_count = 0
                # Reselect the originally selected edges (indices might be less reliable after merge)
                for edge_idx in self.initial_selected_edge_indices:
                    try:
                        if 0 <= edge_idx < len(bm_final.edges): # Check bounds
                             edge = bm_final.edges[edge_idx]
                             # Basic validity checks
                             if edge.is_valid and edge.verts[0].is_valid and edge.verts[1].is_valid and edge.verts[0].index != edge.verts[1].index:
                                  edge.select_set(True)
                                  # Also select vertices if original mode included vertex selection
                                  if original_mesh_select_mode and original_mesh_select_mode[0]: # Check if vertex mode was active
                                       edge.verts[0].select_set(True)
                                       edge.verts[1].select_set(True)
                                  selected_count += 1
                                  restored_count += 1
                        # else: print(f"Debug: Initial edge index {edge_idx} out of bounds ({len(bm_final.edges)}).") # Debug
                    except IndexError: pass # Edge index might be out of bounds after operations
                    except ReferenceError: pass # Edge might have been deleted
                    except Exception as e_sel: print(f"Warning: Error selecting edge index {edge_idx}: {e_sel}")

                bmesh.update_edit_mesh(me_final) # Update selection in viewport
                print(f"Attempted to restore selection based on {len(self.initial_selected_edge_indices)} initial edge indices, {selected_count} edges selected.")

            except Exception as e:
                print(f"Error during selection restore: {e}")
            finally:
                if bm_final and bm_final.is_valid: bm_final.free() # Free the bmesh used for selection

        # Restore original selection mode (Vert/Edge/Face)
        try:
            if original_mesh_select_mode and tuple(context.tool_settings.mesh_select_mode) != original_mesh_select_mode:
                print(f"Restoring original mesh select mode: {original_mesh_select_mode}")
                context.tool_settings.mesh_select_mode = original_mesh_select_mode
        except Exception as e_mode:
            print(f"Warning: Could not restore original mesh select mode: {e_mode}")

        # Switch back to original object mode if we changed it
        if edit_mode_switched_here and current_object_mode != 'EDIT_MESH':
             try: bpy.ops.object.mode_set(mode=current_object_mode)
             except RuntimeError: pass

        return restored_count

    def select_operation_result(self, context, original_mesh_select_mode=None):
        """Fallback selection when original face/edge indices were removed by the merge."""
        if context.mode != 'EDIT_MESH':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except RuntimeError:
                print("Error: Cannot switch to Edit mode to select UnBevel result.")
                return 0

        obj_final = context.edit_object
        if not obj_final or not obj_final.data:
            print("Error: Cannot get edit object/mesh to select UnBevel result.")
            return 0

        bm_final = None
        selected_count = 0
        try:
            bm_final = bmesh.from_edit_mesh(obj_final.data)
            bm_final.verts.ensure_lookup_table()
            bm_final.edges.ensure_lookup_table()
            bm_final.faces.ensure_lookup_table()
            bm_final.verts.index_update()
            bm_final.edges.index_update()
            bm_final.faces.index_update()

            for v in bm_final.verts: v.select = False
            for e in bm_final.edges: e.select = False
            for f in bm_final.faces: f.select = False

            affected_vert_indices = set()
            for group in self.operation_groups:
                affected_vert_indices.update(group.get('v_move_indices', ()))
                affected_vert_indices.add(group.get('v_outer1_idx'))
                affected_vert_indices.add(group.get('v_outer3_idx'))
            affected_vert_indices.discard(None)

            valid_affected_verts = {
                bm_final.verts[idx]
                for idx in affected_vert_indices
                if 0 <= idx < len(bm_final.verts) and bm_final.verts[idx].is_valid
            }

            if not valid_affected_verts:
                return 0

            if original_mesh_select_mode and original_mesh_select_mode[2]:
                for face in bm_final.faces:
                    if face.is_valid and any(vert in valid_affected_verts for vert in face.verts):
                        face.select_set(True)
                        selected_count += 1
            elif original_mesh_select_mode and original_mesh_select_mode[1]:
                for edge in bm_final.edges:
                    if edge.is_valid and any(vert in valid_affected_verts for vert in edge.verts):
                        edge.select_set(True)
                        selected_count += 1
            else:
                for vert in valid_affected_verts:
                    vert.select_set(True)
                    selected_count += 1

            bmesh.update_edit_mesh(obj_final.data)
            print(f"Fallback selected {selected_count} UnBevel result element(s).")
            return selected_count
        except Exception as e:
            print(f"Error selecting UnBevel result: {e}")
            return 0
        finally:
            if bm_final and bm_final.is_valid:
                bm_final.free()


# --- UnBevel Operator (Modal) ---
class OBJECT_OT_unbevel_modal_op(UnbevelBaseOperator):
    """Interactively unbevels edge chains based on inner edge selection"""
    bl_idname = "object.unbevel_modal_op"
    bl_label = "UnBevel" # Label used for Undo/Redo

    @classmethod
    def poll(cls, context):
        valid_mode = (
            context.mode == 'EDIT_MESH'
            and context.object is not None
            and context.object.type == 'MESH'
        )
        if not valid_mode:
            return False

        select_mode = tuple(context.tool_settings.mesh_select_mode)
        if not select_mode[1] and not select_mode[2]:
            return False

        try:
            bm = bmesh.from_edit_mesh(context.edit_object.data)
            if select_mode[1] and any(e.select for e in bm.edges):
                return True
            if select_mode[2] and any(f.select for f in bm.faces):
                return True
        except Exception:
            return False

        return False

    def _solve_classic_edge_selection(self, initial_selected_edges):
        final_operation_groups, edges_in_found_chains, found_chains_count = build_unbevel_groups_from_edges(
            self.bm,
            initial_selected_edges,
        )
        if not final_operation_groups:
            return None

        return {
            "groups": final_operation_groups,
            "found_edges": edges_in_found_chains,
            "found_count": found_chains_count,
            "selection_source": "EDGE",
            "method_label": self._method_report_label("Classic"),
        }

    def _solve_patch_edge_selection(self, initial_selected_edges):
        annular_candidates, _annular_error = infer_annular_unbevel_candidates_from_edges(
            self.bm,
            initial_selected_edges,
        )
        if not annular_candidates:
            return None

        active_candidate = annular_candidates[0]
        return {
            "groups": list(active_candidate["groups"]),
            "found_edges": set(active_candidate["found_edges"]) & initial_selected_edges,
            "found_count": active_candidate["found_count"],
            "selection_source": "EDGE_PATCH",
            "method_label": self._method_report_label("Patch"),
        }

    def _solve_edge_selection(self, initial_selected_edges):
        solvers = (
            (self._solve_patch_edge_selection, self._solve_classic_edge_selection)
            if self.unbevel_method == 'PATCH'
            else (self._solve_classic_edge_selection, self._solve_patch_edge_selection)
        )

        for solver in solvers:
            result = solver(initial_selected_edges)
            if result:
                return result

        return None

    def _prepare_operation_from_selection(self, context, store_selection_snapshot=False):
        if not self.bm or not self.bm.is_valid:
            self.report({'ERROR'}, "Could not prepare UnBevel: invalid BMesh.")
            return None

        self.bm.verts.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()
        self.bm.faces.ensure_lookup_table()
        self.bm.verts.index_update()
        self.bm.edges.index_update()
        self.bm.faces.index_update()

        self.original_mesh_select_mode = tuple(context.tool_settings.mesh_select_mode)
        self.initial_selected_face_indices = {
            f.index for f in self.bm.faces if f.select and f.is_valid
        }

        visible_selected_edges = {e for e in self.bm.edges if e.select and e.is_valid}
        selected_faces = {f for f in self.bm.faces if f.select and f.is_valid}
        if store_selection_snapshot:
            self._store_initial_selection_snapshot(
                self.original_mesh_select_mode,
                {e.index for e in visible_selected_edges},
                self.initial_selected_face_indices,
            )

        initial_selected_edges = set()
        self.selection_source = "EDGE"
        final_operation_groups = []
        edges_in_found_chains = set()
        found_chains_count = 0
        self.face_unbevel_candidates = []
        self.face_unbevel_candidate_index = 0

        if self.original_mesh_select_mode[1] and visible_selected_edges:
            initial_selected_edges = visible_selected_edges
            edge_solution = self._solve_edge_selection(initial_selected_edges)
            if edge_solution:
                self.selection_source = edge_solution["selection_source"]
                self.active_unbevel_method_label = edge_solution["method_label"]
                final_operation_groups = edge_solution["groups"]
                edges_in_found_chains = edge_solution["found_edges"]
                found_chains_count = edge_solution["found_count"]
        elif self.original_mesh_select_mode[2] and selected_faces:
            self.selection_source = "FACE"
            self.face_unbevel_candidates, inference_error = infer_unbevel_edge_candidates_from_faces(
                self.bm,
                selected_faces,
                prefer_classic=(self.unbevel_method in {'AUTO', 'CLASSIC'}),
            )
            if inference_error:
                self.report({'WARNING'}, inference_error)
            elif self.face_unbevel_candidates:
                if len(self.face_unbevel_candidates) > 1 and self.unbevel_alternate_ring:
                    self.face_unbevel_candidate_index = 1
                active_candidate = self.face_unbevel_candidates[self.face_unbevel_candidate_index]
                initial_selected_edges = set(active_candidate["edges"])
                final_operation_groups = list(active_candidate["groups"])
                edges_in_found_chains = set(active_candidate["found_edges"])
                found_chains_count = active_candidate["found_count"]
                self.active_unbevel_method_label = self._method_report_label(
                    active_candidate.get("method_label", "Face Patch")
                )
        else:
            self.report({'WARNING'}, "Requires selected edges or bevel faces in Edit Mode.")
            return None

        if not initial_selected_edges:
            self.report({'WARNING'}, "No valid UnBevel edge rings found.")
            return None

        self.initial_selected_edge_indices = {e.index for e in initial_selected_edges}
        self.operation_groups = final_operation_groups

        if not self.operation_groups:
            self.report({'WARNING'}, "No valid unbevel chains constructed.")
            return None

        unprocessed_selected_count = len(initial_selected_edges - edges_in_found_chains)
        if unprocessed_selected_count > 0:
             self.report({'WARNING'}, f"{unprocessed_selected_count} initially selected edges not part of a valid chain.")

        return found_chains_count

    # --- Invoke Method (MODIFIED) ---
    def invoke(self, context, event):
        self.operation_groups = []; self.finished_successfully = False; self._initial_run = True
        self.face_unbevel_candidates = []
        self.face_unbevel_candidate_index = 0
        self._ignore_initial_left_release = event.type == 'LEFTMOUSE' and event.value == 'PRESS'
        if not self.properties.is_property_set("edge_slide"):
            self.edge_slide = bool(getattr(context.scene, "unbevel_edge_slide", False))
        if not self.poll(context):
            self.report({'WARNING'}, "Requires selected edges or bevel faces in Edit Mode."); return {'CANCELLED'}
        self.obj = context.edit_object; self.me = self.obj.data
        try:
            # Keep a reference to the BMesh for the modal phase
            self.bm = bmesh.from_edit_mesh(self.me)
        except Exception as e:
            self.report({'ERROR'}, f"Could not get BMesh: {e}"); return {'CANCELLED'}
        found_chains_count = self._prepare_operation_from_selection(context, store_selection_snapshot=True)
        if found_chains_count is None:
            self.bm.free()
            self.bm = None
            return {'CANCELLED'}

        # --- Initialize Modal State ---
        self.last_mouse_region_x = event.mouse_region_x
        self.total_delta_x = 0.0
        self.current_scale_preview = 1.0

        # --- REMOVED CURSOR AND PIVOT CHANGES ---
        # last_pivot_local = self.operation_groups[-1]['pivot_local']
        # intersection_point_world = self.obj.matrix_world @ last_pivot_local
        # context.scene.cursor.location = intersection_point_world
        # context.scene.tool_settings.transform_pivot_point = 'CURSOR'
        # --- END REMOVED LINES ---

        context.window_manager.modal_handler_add(self)
        context.window.cursor_modal_set('SCROLL_X')
        self._add_draw_handler(context)
        self._set_header_text(context)

        self.report({'INFO'}, f"Modal UnBevel started for {found_chains_count} chain(s). Move mouse horizontally (wrap enabled).")
        return {'RUNNING_MODAL'}

    def _set_header_text(self, context):
        try:
            if not context.area:
                return
            if self.selection_source == "FACE" and len(self.face_unbevel_candidates) > 1:
                context.area.header_text_set(
                    f"UnBevel | Ring Set {self.face_unbevel_candidate_index + 1}/{len(self.face_unbevel_candidates)} | "
                    "Mouse L/R Scale | Wheel Switch Rings | LMB Apply | RMB/Esc Cancel"
                )
            else:
                context.area.header_text_set("UnBevel | Mouse L/R Scale | LMB Apply | RMB/Esc Cancel")
        except Exception:
            pass

    def _clear_header_text(self, context):
        try:
            if context.area:
                context.area.header_text_set(None)
        except Exception:
            pass

    def _add_draw_handler(self, context):
        if self._handle_draw_2d is None:
            self._handle_draw_2d = bpy.types.SpaceView3D.draw_handler_add(
                self.draw_hud,
                (context,),
                'WINDOW',
                'POST_PIXEL',
            )
            shared_state.unbevel_draw_handles.append(self._handle_draw_2d)

    def _remove_draw_handler(self):
        if not self._handle_draw_2d:
            return

        if self._handle_draw_2d in shared_state.unbevel_draw_handles:
            shared_state.unbevel_draw_handles.remove(self._handle_draw_2d)

        try:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_draw_2d, 'WINDOW')
        except ValueError:
            pass

        self._handle_draw_2d = None

    def _cleanup_modal_ui(self, context):
        context.window.cursor_modal_restore()
        self._clear_header_text(context)
        self._remove_draw_handler()
        if context.area:
            context.area.tag_redraw()

    def draw_hud(self, _context):
        font_id = 0
        x = 16 + HUD_OFFSET_X
        y = 18 + HUD_OFFSET_Y
        has_ring_switch = self.selection_source == "FACE" and len(self.face_unbevel_candidates) > 1

        draw_shadowed_text(font_id, x, y + 64, "UnBevel", 16, (0.96, 0.98, 1.0, 1.0))
        draw_shadowed_text(font_id, x, y + 42, f"Scale: {self.current_scale_preview:.3f}", 14, (0.98, 0.72, 0.78, 1.0))

        if has_ring_switch:
            draw_shadowed_text(
                font_id,
                x,
                y + 22,
                f"Ring Set: {self.face_unbevel_candidate_index + 1}/{len(self.face_unbevel_candidates)}",
                14,
                (0.64, 0.92, 1.0, 1.0),
            )
            footer = "Mouse L/R Scale | Wheel Switch Rings | LMB Apply | RMB/Esc Cancel"
        else:
            draw_shadowed_text(font_id, x, y + 22, "Mouse L/R Scale", 14, (0.64, 0.92, 1.0, 1.0))
            footer = "LMB Apply | RMB/Esc Cancel"

        draw_shadowed_text(font_id, x, y + 2, footer, 12, (0.78, 0.82, 0.86, 1.0))

    def _switch_face_unbevel_candidate(self, context, step):
        if self.selection_source != "FACE" or len(self.face_unbevel_candidates) < 2:
            return False
        if not self.bm or not self.bm.is_valid:
            self.report({'ERROR'}, "BMesh became invalid while switching UnBevel ring set.")
            return False

        old_index = self.face_unbevel_candidate_index
        old_groups = self.operation_groups
        old_edge_indices = set(self.initial_selected_edge_indices)

        if not self._apply_geometry(self.bm, 1.0, False):
            self.report({'ERROR'}, "Could not reset UnBevel preview before switching ring set.")
            return False

        new_index = (old_index + step) % len(self.face_unbevel_candidates)
        candidate = self.face_unbevel_candidates[new_index]
        self.face_unbevel_candidate_index = new_index
        self.unbevel_alternate_ring = new_index > 0
        self.active_unbevel_method_label = self._method_report_label(
            candidate.get("method_label", self.active_unbevel_method_label)
        )
        self.operation_groups = list(candidate["groups"])
        self.initial_selected_edge_indices = {edge.index for edge in candidate["edges"] if edge.is_valid}

        if not self._apply_geometry(self.bm, self.current_scale_preview, self.edge_slide):
            self.operation_groups = old_groups
            self.initial_selected_edge_indices = old_edge_indices
            self.face_unbevel_candidate_index = old_index
            self._apply_geometry(self.bm, self.current_scale_preview, self.edge_slide)
            update_mesh_viewport(self.bm, self.me)
            self.report({'ERROR'}, "Could not preview selected UnBevel ring set.")
            return False

        if not update_mesh_viewport(self.bm, self.me):
            self.report({'ERROR'}, "Viewport update failed while switching UnBevel ring set.")
            return False

        self._set_header_text(context)
        self.report({'INFO'}, f"UnBevel ring set {new_index + 1}/{len(self.face_unbevel_candidates)}")
        return True

    # --- Helper Function to Finalize Operation (Apply, Merge, Restore, Conditional Deselect) ---
    def _finalize_operation(self, context):
        """Applies final geometry, performs auto-merge, restores selection, then deselects if scale was 0."""
        print("Finalizing operation...") # Debug print

        original_object_mode = context.mode
        original_mesh_select_mode = None
        if original_object_mode == 'EDIT_MESH':
            original_mesh_select_mode = self.original_mesh_select_mode or tuple(context.tool_settings.mesh_select_mode)

        # --- Get BMesh if needed ---
        bm_finalize = self.bm
        needs_free = False
        mesh_to_update = self.me

        if not bm_finalize or not bm_finalize.is_valid:
            try:
                print("Re-acquiring BMesh for finalization...") # Debug
                current_obj = context.edit_object
                if not current_obj: raise ReferenceError("No edit object found for finalization")
                mesh_to_update = current_obj.data # Update the mesh we get the bmesh from
                bm_finalize = bmesh.from_edit_mesh(mesh_to_update)
                needs_free = True # We created this BMesh, so we need to free it
                # Store obj/me references if re-acquired for consistency
                self.obj = current_obj
                self.me = mesh_to_update
            except Exception as e:
                 self.report({'ERROR'}, f"Could not get BMesh for finalization: {e}")
                 self.restore_selection(context, original_mesh_select_mode=original_mesh_select_mode)
                 return False # Indicate failure


        # --- Apply Final UnBevel Geometry ---
        is_slide_active = self.edge_slide
        if not self._apply_geometry(bm_finalize, self.final_scale, is_slide_active):
             self.report({'ERROR'}, "Failed to apply final geometry state during finalization.")
             if needs_free and bm_finalize.is_valid: bm_finalize.free()
             self.restore_selection(context, original_mesh_select_mode=original_mesh_select_mode)
             return False # Indicate failure


        # --- Finalize Mesh Update (Write BMesh back) ---
        if not finalize_mesh_update(bm_finalize, mesh_to_update):
             self.report({'ERROR'}, "Final mesh update failed during finalization.")
             if needs_free and bm_finalize.is_valid: bm_finalize.free()
             self.restore_selection(context, original_mesh_select_mode=original_mesh_select_mode)
             return False # Indicate failure


        # Free BMesh if we created it here
        if needs_free and bm_finalize.is_valid:
            try:
                bm_finalize.free()
                print("Freed BMesh acquired for finalization.") # Debug
            except Exception as e_free:
                 print(f"Error freeing finalization BMesh: {e_free}")
        elif needs_free and not bm_finalize.is_valid:
             print("Warning: Finalization BMesh became invalid before explicit free.")

        self.bm = None # Ensure self.bm is cleared after finalization

        # --- Auto-Merge Sequence ---
        merge_successful = False
        original_automerge_state = context.scene.tool_settings.use_mesh_automerge
        original_automerge_threshold = context.scene.tool_settings.double_threshold
        mode_before_merge = tuple(context.tool_settings.mesh_select_mode)

        edit_mode_active_for_merge = False
        if context.mode != 'EDIT_MESH':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                edit_mode_active_for_merge = True
            except RuntimeError:
                 self.report({'ERROR'}, "Could not switch to Edit Mode for auto-merge sequence.")
                 if edit_mode_active_for_merge and original_object_mode != 'EDIT_MESH':
                     try: bpy.ops.object.mode_set(mode=original_object_mode)
                     except RuntimeError: pass
                 return False # Cannot proceed


        try:
            context.scene.tool_settings.use_mesh_automerge = True
            context.scene.tool_settings.double_threshold = 1e-06 # Use original small threshold (REVERTED)

            try:
                bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='VERT')
            except RuntimeError as e:
                print(f"Warning: Failed to switch to vertex mode for merge: {e}")

            try:
                obj_for_merge = context.edit_object
                me_for_merge = self.me
                if not me_for_merge:
                     raise ReferenceError("Mesh data reference missing for merge sequence.")

                bm_for_merge = bmesh.from_edit_mesh(me_for_merge)
                bm_for_merge.verts.ensure_lookup_table()

                affected_vert_indices = set()
                if self.operation_groups:
                   for group in self.operation_groups:
                       affected_vert_indices.update(group['v_move_indices'])
                       affected_vert_indices.add(group['v_outer1_idx'])
                       affected_vert_indices.add(group['v_outer3_idx'])

                bpy.ops.mesh.select_all(action='DESELECT')
                verts_to_select = []
                for v_idx in affected_vert_indices:
                    try:
                        if 0 <= v_idx < len(bm_for_merge.verts):
                            v = bm_for_merge.verts[v_idx]
                            if v.is_valid:
                                v.select_set(True)
                                verts_to_select.append(v)
                        else: print(f"Warning: Vertex index {v_idx} out of bounds for merge selection.")
                    except IndexError: print(f"Warning: IndexError accessing vertex {v_idx} for merge selection.")
                    except ReferenceError: print(f"Warning: ReferenceError accessing vertex {v_idx} (likely deleted).")

                bmesh.update_edit_mesh(me_for_merge)
                bm_for_merge.free()

                if not verts_to_select:
                     print("Warning: No affected vertices found/selected to trigger merge transform.")
                     merge_successful = True # Not technically failed
                else:
                    print(f"Triggering auto-merge via transform on {len(verts_to_select)} vertices.")
                    bpy.ops.transform.translate(value=(0, 0, 0), orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)), orient_matrix_type='GLOBAL', constraint_axis=(False, False, False), mirror=False, use_proportional_edit=False)
                    merge_successful = True

            except RuntimeError as e:
                self.report({'ERROR'}, f"Failed to run transform to trigger auto-merge: {e}")
                merge_successful = False
            except Exception as e:
                 self.report({'ERROR'}, f"Error during vertex selection or transform for merge trigger: {e}")
                 merge_successful = False

            try:
                # Restore selection mode that was active before merge sequence
                context.tool_settings.mesh_select_mode = mode_before_merge
            except RuntimeError as e:
                print(f"Warning: Failed to restore selection mode after merge: {e}")
                try: bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='EDGE')
                except RuntimeError: pass


        finally:
            context.scene.tool_settings.use_mesh_automerge = original_automerge_state
            context.scene.tool_settings.double_threshold = original_automerge_threshold
            print(f"Auto Merge restored: {original_automerge_state}, Threshold: {original_automerge_threshold}")
            if edit_mode_active_for_merge and original_object_mode != 'EDIT_MESH':
                try: bpy.ops.object.mode_set(mode=original_object_mode)
                except RuntimeError: pass

        # --- End Auto-Merge Sequence ---

        # --- Final Selection and Mode Restoration ---
        final_report_msg = f"{self.bl_label} confirmed (Scale: {self.final_scale:.4f}, Method: {self.active_unbevel_method_label}, Slide: {is_slide_active})"
        if merge_successful: final_report_msg += " - Auto-Merge applied"
        else: final_report_msg += " - Auto-Merge failed or skipped"

        # Ensure Edit mode for final selection restore
        final_edit_mode_active = False
        if context.mode != 'EDIT_MESH':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                final_edit_mode_active = True
            except RuntimeError:
                self.report({'ERROR'}, "Could not switch to Edit Mode for final selection restore.")
                if final_edit_mode_active and original_object_mode != 'EDIT_MESH':
                     try: bpy.ops.object.mode_set(mode=original_object_mode)
                     except RuntimeError: pass
                return False # Indicate failure


        try:
            # Restore the original user edge selection and the original mesh select mode
            restored_count = self.restore_selection(context, original_mesh_select_mode=original_mesh_select_mode)
            if restored_count == 0:
                self.select_operation_result(context, original_mesh_select_mode=original_mesh_select_mode)
        except Exception as e:
             self.report({'ERROR'}, f"Error during final selection restoration: {e}")
             # Don't necessarily fail the whole operation for this
        finally:
            # If we switched to Edit mode here for selection restore, switch back
            if final_edit_mode_active and original_object_mode != 'EDIT_MESH':
                 try: bpy.ops.object.mode_set(mode=original_object_mode)
                 except RuntimeError: pass

        self.report({'INFO'}, final_report_msg)
        self._initial_run = False # Mark that the initial run (invoke) is done
        return True # Indicate success


    # --- Modal Method (Updated to call _finalize_operation) ---
    def modal(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._ignore_initial_left_release:
            self._ignore_initial_left_release = False
            return {'RUNNING_MODAL'}

        if event.type == 'ESC' or (event.type == 'RIGHTMOUSE' and event.value == 'PRESS'):
            # Only restore if self.bm is valid (might have been cleared/freed)
            if self.bm and self.bm.is_valid:
                 self.restore_initial_state()
            else:
                 print("Skipping restore_initial_state on cancel: BMesh invalid or None.")
            self.restore_selection(context, original_mesh_select_mode=self.original_mesh_select_mode)
            self._cleanup_modal_ui(context)
            self.report({'INFO'}, f"{self.bl_label} cancelled.")
            self.finished_successfully = False
            # Clean up self.bm if it exists but wasn't handled by restore
            if self.bm and self.bm.is_valid:
                try: self.bm.free()
                except Exception as e_free: print(f"Error freeing self.bm on cancel: {e_free}")
            self.bm = None
            return {'CANCELLED'}

        if (
            (event.type == 'LEFTMOUSE' and event.value == 'PRESS')
            or (event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS')
        ):
            self._cleanup_modal_ui(context)
            self.final_scale = self.current_scale_preview # Store the final scale
            self.finished_successfully = True

            # --- Call the finalization helper directly ---
            if self._finalize_operation(context):
                # self.bm should be None now if finalize succeeded
                return {'FINISHED'}
            else:
                # Finalization failed, report would have been made by helper
                # Try to restore initial state as a fallback if possible
                # Note: self.bm might be None or invalid here already
                print("Finalization failed, attempting fallback restore...")
                self.restore_initial_state() # This might fail if mesh state is bad
                # Ensure self.bm is cleared after attempt
                if self.bm and self.bm.is_valid:
                    try: self.bm.free()
                    except Exception as e_free: print(f"Error freeing self.bm on finalize fail: {e_free}")
                self.bm = None
                return {'CANCELLED'}

        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            if self.selection_source == "FACE" and len(self.face_unbevel_candidates) > 1:
                step = 1 if event.type == 'WHEELUPMOUSE' else -1
                if self._switch_face_unbevel_candidate(context, step):
                    return {'RUNNING_MODAL'}

                self.restore_initial_state()
                self._cleanup_modal_ui(context)
                if self.bm and self.bm.is_valid:
                    try: self.bm.free()
                    except Exception as e_free: print(f"Error freeing self.bm on ring switch fail: {e_free}")
                self.bm = None
                return {'CANCELLED'}

            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            # Ensure self.bm is valid before proceeding
            if not self.bm or not self.bm.is_valid:
                 self.report({'ERROR'}, "BMesh became invalid during modal operation.")
                 self._cleanup_modal_ui(context)
                 # Ensure self.bm is cleared before cancelling
                 if self.bm and self.bm.is_valid:
                     try: self.bm.free()
                     except Exception as e_free: print(f"Error freeing self.bm on invalid modal: {e_free}")
                 self.bm = None
                 return {'CANCELLED'}

            region = context.region
            region_width = region.width
            region_height = region.height

            delta_x = event.mouse_region_x - self.last_mouse_region_x

            warp_threshold = region_width * 0.8
            if abs(delta_x) > warp_threshold:
                delta_x = 0
                self.last_mouse_region_x = event.mouse_region_x

            self.total_delta_x += delta_x

            raw_scale = 1.0 + (self.total_delta_x * self.sensitivity)
            self.current_scale_preview = max(0.0, raw_scale)

            is_slide_active = self.edge_slide
            # Apply preview using the modal's self.bm
            if self._apply_geometry(self.bm, self.current_scale_preview, is_slide_active):
                # Update viewport using the modal's self.me
                if not update_mesh_viewport(self.bm, self.me):
                     self.report({'ERROR'}, "Viewport update failed during modal.")
                     # Attempt restore before cancelling
                     self.restore_initial_state()
                     self._cleanup_modal_ui(context)
                     if self.bm and self.bm.is_valid: self.bm.free()
                     self.bm = None
                     return {'CANCELLED'}
            else:
                # Geometry application failed
                self.report({'ERROR'}, "Geometry application failed during modal preview.")
                self.restore_initial_state()
                self._cleanup_modal_ui(context)
                if self.bm and self.bm.is_valid: self.bm.free()
                self.bm = None
                return {'CANCELLED'}

            # --- Cursor Wrapping Logic (Unchanged) ---
            warp_margin = 2
            new_mouse_x = event.mouse_region_x
            new_mouse_y = event.mouse_region_y
            needs_warp = False
            if event.mouse_region_x <= warp_margin:
                new_mouse_x = region_width - (warp_margin + 1); needs_warp = True
            elif event.mouse_region_x >= region_width - warp_margin:
                new_mouse_x = warp_margin + 1; needs_warp = True
            if event.mouse_region_y <= warp_margin:
                new_mouse_y = region_height - (warp_margin + 1); needs_warp = True
            elif event.mouse_region_y >= region_height - warp_margin:
                new_mouse_y = warp_margin + 1; needs_warp = True
            if needs_warp:
                context.window.cursor_warp(new_mouse_x, new_mouse_y)
                self.last_mouse_region_x = new_mouse_x
            else:
                 self.last_mouse_region_x = event.mouse_region_x
            # --- End Wrapping Logic ---

            return {'RUNNING_MODAL'}

        elif event.type in {'MIDDLEMOUSE'}:
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    # --- Execute Method (Now primarily for Redo) ---
    def execute(self, context):
        """Handles Redo by calling the finalization helper."""
        print("Execute method called (likely for Redo)") # Debug print
        # The final_scale property should be set by Blender for Redo
        # Redo can run after Blender has restored/rebuilt edit mesh data; refresh pointers.
        current_obj = context.edit_object
        if not current_obj or not current_obj.data:
             self.report({'ERROR'}, "Missing object or mesh data for Redo.")
             return {'CANCELLED'}
        self.obj = current_obj
        self.me = current_obj.data

        self._restore_initial_selection_snapshot_for_redo(context)
        current_obj = context.edit_object
        if not current_obj or not current_obj.data:
             self.report({'ERROR'}, "Missing object or mesh data after restoring Redo selection.")
             return {'CANCELLED'}
        self.obj = current_obj
        self.me = current_obj.data

        try:
            self.bm = bmesh.from_edit_mesh(self.me)
        except Exception as e:
            self.report({'ERROR'}, f"Could not get BMesh for Redo: {e}")
            return {'CANCELLED'}

        if self._prepare_operation_from_selection(context) is None:
            if self.bm and self.bm.is_valid:
                try: self.bm.free()
                except Exception as e_free: print(f"Error freeing self.bm on Redo prepare fail: {e_free}")
            self.bm = None
            return {'CANCELLED'}

        if self._finalize_operation(context):
            return {'FINISHED'}
        else:
            # Finalization failed during Redo
            return {'CANCELLED'}

# --- END OF FILE operators_unbevel.py ---
