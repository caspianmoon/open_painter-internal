"""Surface-space generation without Blender: observation identity, fusion and gates."""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.authoring import schema, surface_field, surface_workflow
from open_painter.geometry import (surface_coverage as coverage, surface_fusion as fusion,
                                   surface_observation as observation, surface_seams as seams,
                                   surface_tangents as tangents)


def grid(n=9, z=0.):
    """A flat quad grid in the XY plane facing +Z, with a 1:1 UV layout."""
    xs, ys = np.meshgrid(np.linspace(-1, 1, n), np.linspace(-1, 1, n))
    positions = np.column_stack((xs.ravel(), ys.ravel(), np.full(n * n, float(z))))
    triangles = []
    for row in range(n - 1):
        for col in range(n - 1):
            base = row * n + col
            triangles += [[base, base + 1, base + n + 1], [base, base + n + 1, base + n]]
    triangles = np.array(triangles)
    uv = np.column_stack(((positions[:, 0] + 1) / 2 * .96 + .02, (positions[:, 1] + 1) / 2 * .96 + .02))
    return positions, triangles, uv


def observe(positions, triangles, side=96, direction=(0, 0, 1), extra=None, owner=None):
    """One registered-style observation view over the given geometry."""
    every = triangles if extra is None else np.concatenate((triangles, extra))
    labels = np.zeros(len(every), np.int32) if owner is None else owner
    frame = observation.frame(positions[np.unique(triangles)], direction, coverage.up_for(np.asarray(direction, float)))
    buffers = observation.rasterize(positions, every, labels, frame['matrix'], frame['projection'], side)
    target = (buffers['triangle'] >= 0) & (buffers['triangle'] < len(triangles))
    silhouette = observation.silhouette_distance(target, buffers['depth'])
    view = dict(matrix=frame['matrix'], projection=frame['projection'], depth=buffers['depth'],
                target=target, silhouette=silhouette, axis=np.asarray(frame['axes'][2], np.float32),
                pixel_world=2 * frame['extent'] / side, silhouette_limit=fusion.SILHOUETTE_RAMP, side=side)
    return frame, buffers, target, view


def coordinate_image(side):
    """每 pixel encodes its own normalized image coordinate, so reprojection is checkable."""
    image = np.zeros((side, side, 3), np.float32)
    image[..., 0] = (np.arange(side) + .5)[None, :] / side
    image[..., 1] = (np.arange(side) + .5)[:, None] / side
    return image


class ObservationTests(unittest.TestCase):
    def test_projection_is_orthographic_and_top_left(self):
        positions, triangles, _ = grid()
        frame = observation.frame(positions, [0, 0, 1], [0, 1, 0])
        coords, depth = observation.project(frame['matrix'], frame['projection'], positions)
        self.assertTrue(np.all(coords >= 0) and np.all(coords <= 1))
        # +X is image right and +Y is image up: the image origin is top-left.
        right = positions[:, 0] == positions[:, 0].max()
        top = positions[:, 1] == positions[:, 1].max()
        self.assertGreater(coords[right, 0].min(), coords[~right, 0].max())
        self.assertLess(coords[top, 1].max(), coords[~top, 1].min())
        # An orthographic camera keeps one pixel the same world size everywhere.
        self.assertAlmostEqual(float(depth.max() - depth.min()), 0., places=5)

    def test_occluders_win_the_depth_test_and_are_never_the_target(self):
        positions, triangles, _ = grid()
        blocker = np.array([[-.5, -.5, 1.], [.5, -.5, 1.], [.5, .5, 1.], [-.5, .5, 1.]])
        extra = np.array([[0, 1, 2], [0, 2, 3]]) + len(positions)
        merged = np.concatenate((positions, blocker))
        owner = np.concatenate((np.zeros(len(triangles), np.int32), np.full(2, -1, np.int32)))
        _, buffers, target, _ = observe(merged, triangles, extra=extra, owner=owner)
        hidden = buffers['owner'] == -1
        self.assertTrue(hidden.any())
        self.assertFalse((hidden & target).any())

    def test_silhouette_distance_is_zero_at_the_boundary(self):
        positions, triangles, _ = grid()
        _, buffers, target, _ = observe(positions, triangles)
        distance = observation.silhouette_distance(target, buffers['depth'])
        self.assertEqual(float(distance[~target].max()), 0.)
        rows, cols = np.nonzero(target)
        self.assertEqual(float(distance[rows.min(), cols[rows == rows.min()][0]]), 0.)
        self.assertGreater(float(distance[target].max()), 4.)


class FusionTests(unittest.TestCase):
    def setUp(self):
        self.positions, self.triangles, self.uv = grid()
        self.corner_uv = self.uv[self.triangles]
        self.normals = np.tile([0, 0, 1.], (len(self.triangles), 3, 1)).astype(np.float32)

    def fuse(self, views, images, resolution=128):
        texels = fusion.texels(self.corner_uv, self.positions, self.triangles, self.normals, resolution)
        world = fusion.texel_world_size(texels, self.positions, self.triangles, self.corner_uv)
        return texels, fusion.fuse(texels, views, images, texel_world=world)

    def test_every_texel_takes_the_pixel_that_actually_shows_it(self):
        frame, _, _, view = observe(self.positions, self.triangles, side=256)
        side = view['side']
        texels, fused = self.fuse([view], dict(BASE_COLOR=[coordinate_image(side)]))
        expected, _ = observation.project(view['matrix'], view['projection'], texels['position'])
        chosen = fused['weight'] > 0
        error = np.abs(fused['values']['BASE_COLOR'][chosen][:, :2] - expected[chosen])
        # Bilinear sampling of an exact coordinate ramp reproduces the coordinate.
        self.assertLess(float(error.max()), 1.5 / side)

    def test_an_occluded_observation_contributes_nothing(self):
        blocker = np.array([[-2., -2., 1.], [2., -2., 1.], [2., 2., 1.], [-2., 2., 1.]])
        extra = np.array([[0, 1, 2], [0, 2, 3]]) + len(self.positions)
        merged = np.concatenate((self.positions, blocker))
        owner = np.concatenate((np.zeros(len(self.triangles), np.int32), np.full(2, -1, np.int32)))
        _, _, _, view = observe(merged, self.triangles, extra=extra, owner=owner)
        _, fused = self.fuse([view], dict(BASE_COLOR=[coordinate_image(view['side'])]))
        self.assertEqual(float(fused['weight'].max()), 0.)

    def test_edge_on_coverage_weighs_less_than_a_facing_view(self):
        _, _, _, straight = observe(self.positions, self.triangles, side=256)
        angled_direction = np.array([math.sin(1.35), 0., math.cos(1.35)])
        _, _, _, angled = observe(self.positions, self.triangles, side=256, direction=angled_direction)
        _, facing = self.fuse([straight], dict(BASE_COLOR=[coordinate_image(256)]))
        _, oblique = self.fuse([angled], dict(BASE_COLOR=[coordinate_image(256)]))
        self.assertGreater(float(facing['best_weight'].max()), float(oblique['best_weight'].max()) * 2)

    def test_disagreeing_observations_are_reported_not_hidden(self):
        _, _, _, first = observe(self.positions, self.triangles, side=128)
        _, _, _, second = observe(self.positions, self.triangles, side=128)
        black = np.zeros((128, 128, 3), np.float32)
        white = np.ones((128, 128, 3), np.float32)
        texels, fused = self.fuse([first, second], dict(BASE_COLOR=[black, white]))
        state = fusion.classify(fused)
        centre = fused['weight'] > 0
        self.assertAlmostEqual(float(fused['values']['BASE_COLOR'][centre].mean()), .5, places=2)
        self.assertGreater(float(fused['deviation']['BASE_COLOR'][centre].max()), fusion.CONFLICT_DEVIATION)
        self.assertIn(fusion.CONFLICTED, set(state.tolist()))

    def test_uncovered_surface_is_reported_rather_than_stretched(self):
        half = self.positions.copy()
        # Push one half far behind an opaque blocker so no camera can see it.
        blocker = np.array([[-2., 0., 1.], [2., 0., 1.], [2., 2., 1.], [-2., 2., 1.]])
        extra = np.array([[0, 1, 2], [0, 2, 3]]) + len(half)
        merged = np.concatenate((half, blocker))
        owner = np.concatenate((np.zeros(len(self.triangles), np.int32), np.full(2, -1, np.int32)))
        _, _, _, view = observe(merged, self.triangles, extra=extra, owner=owner)
        texels, fused = self.fuse([view], dict(BASE_COLOR=[coordinate_image(view['side'])]))
        state = fusion.classify(fused)
        report = fusion.report(state, resolution=128)
        self.assertGreater(report['classes'][fusion.MISSING]['fraction'], .3)
        clusters = fusion.hole_clusters(texels, state)
        self.assertTrue(clusters)
        self.assertGreater(max(item['texels'] for item in clusters), 16)

    def test_a_region_limits_which_triangles_can_receive_appearance(self):
        chosen = np.zeros(len(self.triangles), bool)
        chosen[:8] = True
        texels = fusion.texels(self.corner_uv, self.positions, self.triangles, self.normals, 128, chosen=chosen)
        self.assertEqual(set(np.unique(texels['triangle']).tolist()), set(np.flatnonzero(chosen).tolist()))


class CoverageTests(unittest.TestCase):
    def test_coverage_classifies_and_scores_what_each_camera_shows(self):
        positions, triangles, _ = grid()
        normals, areas = coverage.triangle_normals(positions, triangles)
        frame, buffers, target, view = observe(positions, triangles, side=256)
        measured = coverage.observe(buffers, observation.silhouette_distance(target, buffers['depth']),
                                    count=len(triangles), normals=normals, areas=areas,
                                    axis=frame['axes'][2], pixel_world=view['pixel_world'])
        self.assertTrue(measured['visible'].all())
        self.assertLess(float(measured['occlusion'].max()), .3)
        state = coverage.accumulate([measured], ['front'], count=len(triangles))
        summary = coverage.summary(state, areas)
        self.assertAlmostEqual(sum(item['area_fraction'] for item in summary['classes'].values()), 1., places=5)
        self.assertGreater(summary['classes'][coverage.GOOD]['area_fraction'], .5)

    def test_camera_ranking_prefers_the_direction_uncovered_geometry_faces(self):
        normals = np.array([[0., 0., 1.], [1., 0., 0.]])
        areas = np.array([1., 1.])
        needed = np.array([False, True])
        ranked = coverage.rank_directions(normals, areas, needed, [np.array([0., 0., 1.])], limit=3)
        self.assertTrue(ranked)
        self.assertGreater(float(ranked[0][1] @ np.array([1., 0., 0.])), .8)

    def test_a_duplicate_direction_is_penalised(self):
        normals = np.array([[0., 0., 1.]])
        areas = np.array([1.])
        needed = np.array([True])
        alone = coverage.rank_directions(normals, areas, needed, [], limit=1)[0][0]
        repeated = coverage.rank_directions(normals, areas, needed, [np.array([0., 0., 1.])], limit=1)[0][0]
        self.assertLess(repeated, alone)


class SeamTests(unittest.TestCase):
    def seam(self, edge_b, center_b):
        return dict(side_a=dict(uv_edge=[[.1, .5], [.4, .5]], uv_center=[.25, .6]),
                    side_b=dict(uv_edge=edge_b, uv_center=center_b))

    def test_continuation_enters_the_paired_island(self):
        for edge, center in (([[.6, .5], [.9, .5]], [.75, .4]),      # translated, mirrored across the edge
                             ([[.9, .5], [.6, .5]], [.75, .6]),      # reversed endpoints
                             ([[.6, .2], [.6, .5]], [.7, .35])):     # rotated island
            transform = seams.continuation(self.seam(edge, center))
            outside = np.array([.25, .45])   # A's exterior: away from its face centre
            mapped = transform['b0'] + transform['forward'] @ (outside - transform['a0'])
            inward = np.asarray(center) - np.asarray(edge[0])
            edge_direction = np.asarray(edge[1]) - np.asarray(edge[0])
            perpendicular = np.array([-edge_direction[1], edge_direction[0]])
            if perpendicular @ inward < 0:
                perpendicular = -perpendicular
            self.assertGreater(float(perpendicular @ (mapped - np.asarray(edge[0]))), 0,
                               'A point past the shared edge must land inside the paired island')

    def test_carry_fills_only_uncovered_texels_from_covered_donors(self):
        resolution = 64
        covered = np.zeros((resolution, resolution), bool)
        covered[:, resolution // 2:] = True      # island B occupies the right half
        values = [np.zeros((resolution, resolution, 3), np.float32)]
        values[0][covered] = [.25, .5, .75]
        seam = self.seam([[.6, .5], [.9, .5]], [.75, .4])
        carried = seams.carry(values, covered, [seam], resolution)
        self.assertGreater(carried, 0)
        filled = np.all(np.isclose(values[0], [.25, .5, .75]), axis=2) & ~np.zeros((resolution, resolution), bool)
        self.assertGreater(int((filled & (np.arange(resolution) < resolution // 2)[None, :]).sum()), 0)


class TangentTests(unittest.TestCase):
    def test_uv_tangents_follow_the_parametrization_and_flag_degenerate_charts(self):
        positions, triangles, uv = grid()
        normals, _ = coverage.triangle_normals(positions, triangles)
        tangent, bitangent, usable, _ = tangents.uv_tangents(positions, triangles, uv[triangles], normals)
        self.assertTrue(usable.all())
        self.assertTrue(np.allclose(np.abs(tangent @ np.array([1., 0., 0.])), 1., atol=1e-6))
        collapsed = uv[triangles].copy()
        collapsed[0] = collapsed[0][0]
        _, _, degenerate, _ = tangents.uv_tangents(positions, triangles, collapsed, normals)
        self.assertFalse(degenerate[0])

    def test_curvature_is_stable_on_a_cylinder_and_unstable_on_a_plane(self):
        angle = np.linspace(0, 2 * math.pi, 33)[:-1]
        height = np.linspace(-1, 1, 9)
        points, quads = [], []
        for row, z in enumerate(height):
            for column, a in enumerate(angle):
                points.append([math.cos(a), math.sin(a), z])
        count = len(angle)
        for row in range(len(height) - 1):
            for column in range(count):
                nxt = (column + 1) % count
                base = row * count
                quads += [[base + column, base + nxt, base + count + nxt],
                          [base + column, base + count + nxt, base + count + column]]
        points = np.asarray(points)
        quads = np.asarray(quads)
        vertex_normals = points / np.maximum(np.linalg.norm(points[:, :2], axis=1, keepdims=True), 1e-9)
        vertex_normals[:, 2] = 0
        curvature = tangents.curvature_directions(points, quads, vertex_normals)
        interior = np.abs(points[:, 2]) < .9
        self.assertGreater(float(curvature['stable'][interior].mean()), .8)
        # A unit cylinder bends by 1 around its axis and not at all along it, and the
        # primary direction is the one that actually bends.
        self.assertAlmostEqual(float(np.median(curvature['curvature_max'][interior])), 1., places=3)
        self.assertAlmostEqual(float(np.median(curvature['curvature_min'][interior])), 0., places=3)
        axis = np.abs(curvature['primary'][interior & curvature['stable']] @ np.array([0., 0., 1.]))
        self.assertLess(float(axis.max()), .05)
        positions, triangles, _ = grid()
        flat = tangents.curvature_directions(positions, triangles, np.tile([0., 0., 1.], (len(positions), 1)))
        self.assertFalse(flat['stable'].any())


class CommandTests(unittest.TestCase):
    def command(self, **extra):
        return dict(version=1, summary='Fused surface', commands=[dict(op='generate_surface_field',
            layer_id='layer', name='Surface', purpose='Observed appearance',
            observations=[dict(observation_id='observation:a', base_color='generated:' + 'a' * 64)], **extra)])

    def test_the_command_validates_and_requires_an_observation_identity(self):
        schema.validate(self.command())
        broken = self.command()
        broken['commands'][0]['observations'] = [dict(base_color='generated:' + 'a' * 64)]
        with self.assertRaises(schema.AuthoringError):
            schema.validate(broken)
        broken = self.command()
        broken['commands'][0]['observations'] = [dict(observation_id='o%d' % i, base_color='g%d' % i) for i in range(9)]
        with self.assertRaises(schema.AuthoringError):
            schema.validate(broken)

    def test_generated_channels_win_over_the_luminance_fallback(self):
        cmd = dict(observations=[dict(observation_id='a', base_color='colour')], roughness_range=[.2, .5],
                   height_amplitude=.01)
        derived = surface_field.channels_for(cmd)
        self.assertEqual(derived['ROUGHNESS'], surface_field.DERIVATION)
        self.assertEqual(derived['HEIGHT'], surface_field.DERIVATION)
        cmd['observations'][0].update(roughness='rough', height='tall', metallic='metal')
        explicit = surface_field.channels_for(cmd)
        self.assertEqual({key: value for key, value in explicit.items()},
                         dict(BASE_COLOR='', ROUGHNESS='', HEIGHT='', METALLIC=''))
        with self.assertRaises(schema.AuthoringError):
            surface_field.channels_for(dict(observations=[dict(observation_id='a')]))

    def test_every_channel_of_a_field_shares_one_correspondence(self):
        ts = type('Set', (), dict(id='set', resolution='1024'))()
        cmd = dict(observations=[dict(observation_id='a', base_color='colour', roughness='rough')],
                   name='Field', purpose='Test')
        field = surface_field.field_id(cmd['observations'])
        values = [surface_field.descriptor(ts, cmd, None, cmd['observations'], channel, field)
                  for channel in ('BASE_COLOR', 'ROUGHNESS', 'COVERAGE')]
        self.assertEqual(len({surface_field._key(value) for value in values}), 1)
        self.assertEqual({value['field'] for value in values}, {field})


class CritiqueTests(unittest.TestCase):
    def captured(self):
        views = ['front', 'back', 'left', 'right', 'top', 'bottom', 'close']
        return dict(mode='CREATE', context=dict(image_generation=dict(enabled=True)), preview_count=2,
                    candidate=dict(digest='a' * 64, result=dict(remaining_corrections=1, correspondence_available=True,
                        review_views=views, images=[dict(id='candidate:close')])))

    def weakness(self, **extra):
        return dict(candidate_digest='a' * 64, reviewed_views=['front', 'back', 'left', 'right', 'top', 'bottom', 'close'],
                    generated_contribution='The bypass loses observed surface variation',
                    preserved_decisions='Preserve the component regions',
                    weaknesses=[dict(component='Barrel', observation='The underside is untextured',
                                     correction='Observe the underside and refuse the field',
                                     evidence_image_id='candidate:close', scope='LOCAL', **extra)])

    def test_a_weakness_must_name_its_correction_type(self):
        captured = self.captured()
        with self.assertRaisesRegex(schema.AuthoringError, 'SURFACE_OBSERVATION'):
            surface_workflow.critique(captured, self.weakness(category='APPEARANCE'))
        surface_workflow.critique(captured, self.weakness(category='MISSING_COVERAGE',
                                                          correction_type='SURFACE_OBSERVATION'))

    def test_a_surface_defect_cannot_be_called_a_texture_source_defect(self):
        with self.assertRaisesRegex(schema.AuthoringError, 'corrective observation'):
            surface_workflow.critique(self.captured(), self.weakness(category='WEAK_COVERAGE',
                                                                     correction_type='TEXTURE_SOURCE'))


if __name__ == '__main__':
    unittest.main()
