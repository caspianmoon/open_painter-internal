"""Generic local surface directions: a tangent field, not a semantic anatomy.

Directional appearance — grain, scales, scratches, fibres, brushing, stripes, wear
flow — needs a consistent local frame on the surface. These frames come only from
the mesh: the render UV parametrization, the geometric shape operator, and the
principal axis of the connected component a triangle belongs to. No object,
material or part specific rule is applied anywhere in this module.
"""
import numpy as np

STABLE_ANISOTROPY = .25  # below this the two principal curvatures are indistinguishable


def _unit(values, axis=-1):
    return values / np.maximum(np.linalg.norm(values, axis=axis, keepdims=True), 1e-12)


def uv_tangents(positions, triangles, uv, normals):
    """dP/du and dP/dv projected onto the surface, with a per-triangle stability flag.

    A degenerate or mirrored UV triangle yields no usable direction; it is reported
    as unstable rather than silently producing an arbitrary axis.
    """
    p = np.asarray(positions, np.float64)[np.asarray(triangles, np.int64)]
    t = np.asarray(uv, np.float64)
    e1, e2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    d1, d2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    determinant = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    scale = np.abs(determinant)
    usable = scale > 1e-12
    safe = np.where(usable, determinant, 1.)[:, None]
    tangent = (e1 * d2[:, 1, None] - e2 * d1[:, 1, None]) / safe
    normals = np.asarray(normals, np.float64)
    tangent = tangent - normals * (tangent * normals).sum(axis=1, keepdims=True)
    length = np.linalg.norm(tangent, axis=1)
    usable &= length > 1e-9
    tangent = np.where(usable[:, None], _unit(tangent), 0.)
    return tangent, np.cross(normals, tangent), usable, np.where(usable, scale, 0.)


def curvature_directions(positions, triangles, vertex_normals):
    """Per-vertex principal directions and an anisotropy measure of the shape operator.

    The operator is fitted from how the surface normal varies along incident edges;
    where the two principal curvatures are close, the direction is meaningless and
    the anisotropy reports that instead of returning an arbitrary eigenvector.
    """
    positions = np.asarray(positions, np.float64)
    normals = _unit(np.asarray(vertex_normals, np.float64))
    triangles = np.asarray(triangles, np.int64)
    count = len(positions)
    # A stable tangent basis per vertex, chosen from the least parallel world axis.
    reference = np.eye(3)[np.argmin(np.abs(normals), axis=1)]
    u = _unit(np.cross(normals, reference))
    v = np.cross(normals, u)
    matrix = np.zeros((count, 3))   # [xx, xy, yy] moments of the incident edges
    right = np.zeros((count, 3))    # normal variation projected onto the same basis
    pairs = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    pairs = np.concatenate([pairs, pairs[:, ::-1]])
    a, b = pairs[:, 0], pairs[:, 1]
    edge = positions[b] - positions[a]
    edge = edge - normals[a] * (edge * normals[a]).sum(axis=1, keepdims=True)
    delta = normals[b] - normals[a]
    length = np.linalg.norm(edge, axis=1)
    usable = length > 1e-12
    x = (edge * u[a]).sum(axis=1)
    y = (edge * v[a]).sum(axis=1)
    du = (delta * u[a]).sum(axis=1)
    dv = (delta * v[a]).sum(axis=1)
    weight = np.where(usable, 1 / np.maximum(length * length, 1e-20), 0.)
    for index, value in enumerate((x * x, x * y, y * y)):
        np.add.at(matrix, (a, index), weight * value)
    np.add.at(right, (a, 0), weight * x * du)
    np.add.at(right, (a, 1), weight * (y * du + x * dv))
    np.add.at(right, (a, 2), weight * y * dv)
    # The complete symmetric normal equations of the shape operator, per vertex:
    # [[p, q, 0], [q, p + r, q], [0, q, r]] @ [suu, suv, svv] = right, whose inverse
    # has this closed form. A rank-deficient one-ring is reported as unstable.
    p, q, r = matrix.T
    determinant = (p + r) * (p * r - q * q)
    solvable = np.abs(determinant) > 1e-18
    safe = np.where(solvable, determinant, 1.)
    r0, r1, r2 = right.T
    suu = (((p + r) * r - q * q) * r0 - q * r * r1 + q * q * r2) / safe
    suv = (-q * r * r0 + p * r * r1 - p * q * r2) / safe
    svv = (q * q * r0 - p * q * r1 + (p * (p + r) - q * q) * r2) / safe
    trace, difference = (suu + svv) / 2, (suu - svv) / 2
    radius = np.sqrt(np.maximum(difference * difference + suv * suv, 0.))
    angle = .5 * np.arctan2(2 * suv, np.where(np.abs(suu - svv) < 1e-20, 1e-20, suu - svv))
    primary = u * np.cos(angle)[:, None] + v * np.sin(angle)[:, None]
    anisotropy = np.where(solvable, radius / np.maximum(np.abs(trace) + radius, 1e-20), 0.)
    stable = solvable & (anisotropy >= STABLE_ANISOTROPY)
    return dict(primary=np.where(stable[:, None], _unit(primary), 0.),
                secondary=np.where(stable[:, None], np.cross(normals, _unit(primary)), 0.),
                curvature_max=trace + radius, curvature_min=trace - radius,
                anisotropy=anisotropy, stable=stable)


def field(positions, triangles, uv, vertex_normals, face_normals):
    """The complete generic direction evidence for every triangle of a surface."""
    from ..evaluation.material_space import component_frames
    tangent, bitangent, usable, density = uv_tangents(positions, triangles, uv, face_normals)
    curvature = curvature_directions(positions, triangles, vertex_normals)
    corners = np.asarray(triangles, np.int64)
    stable = curvature['stable'][corners].all(axis=1)
    principal = _unit(curvature['primary'][corners].sum(axis=1))
    principal = principal - face_normals * (principal * face_normals).sum(axis=1, keepdims=True)
    principal = np.where(stable[:, None] & (np.linalg.norm(principal, axis=1, keepdims=True) > 1e-9),
                         _unit(principal), 0.)
    frames = component_frames(positions, corners)
    along = np.array([frame[1][1] for frame in frames], np.float64)
    along = along - face_normals * (along * face_normals).sum(axis=1, keepdims=True)
    along = np.where(np.linalg.norm(along, axis=1, keepdims=True) > 1e-9, _unit(along), 0.)
    return dict(normal=face_normals, primary_tangent=tangent, secondary_tangent=bitangent,
                tangent_stable=usable, uv_density=density, curvature_primary=principal,
                curvature_stable=stable, curvature_anisotropy=curvature['anisotropy'][corners].mean(axis=1),
                component_direction=along,
                meaning='Geometric direction evidence only: UV parametrization tangents, shape-operator '
                        'principal directions where anisotropy is measurable, and connected-component '
                        'principal axes. These are not semantic grain, growth or wear directions.')
