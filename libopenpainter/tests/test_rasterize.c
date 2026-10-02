/* op_uv_rasterize against the version before linear-time deduplication and vectorized
 * coverage (rasterize_reference.inc): samples must be identical, in the same order, unique and
 * not, with and without clip boxes, over overlapping, sliver and degenerate triangles. */
#include "rasterize_reference.inc"
#include <stdio.h>
#include <string.h>

static uint64_t state = 0x853c49e6748fea9bu;
static float uniform(float lo, float hi) {
    state ^= state << 13;
    state ^= state >> 7;
    state ^= state << 17;
    return lo + (hi - lo) * (float)((state >> 11) * (1.0 / 9007199254740992.0));
}

/* op_uv_raster_interpolate against rasterizing (unique), interpolating each kept sample's corners
 * and scattering the values into maps step by step: float corners in float arithmetic, double
 * corners in double rounded once, uncovered texels untouched, coverage marked; overlapping,
 * sliver and degenerate triangles. */
static int interpolated_maps(void) {
    enum { TRIANGLES = 400, SIZE = 160 };
    static float uv[TRIANGLES * 6], single[TRIANGLES * 9], maps[2][SIZE * SIZE * 3], expected[2][SIZE * SIZE * 3];
    static double doubles[TRIANGLES * 6];
    static uint8_t covered[SIZE * SIZE], marked[SIZE * SIZE];
    uint64_t texels = 0;
    for (int trial = 0; trial < 60; trial++) {
        uint32_t triangles = 1 + (uint32_t)uniform(0, TRIANGLES - 1), size = 4 + (uint32_t)uniform(0, SIZE - 4);
        float scale = uniform(.01f, .4f);
        for (uint32_t t = 0; t < triangles; t++) {
            float u = uniform(-.05f, 1.05f), v = uniform(-.05f, 1.05f);
            for (int k = 0; k < 6; k++)
                uv[t * 6 + k] = (k % 2 ? v : u) + uniform(-scale, scale);
            if (t % 29 == 7) {   /* degenerate */
                uv[t * 6 + 4] = uv[t * 6];
                uv[t * 6 + 5] = uv[t * 6 + 1];
            }
        }
        for (uint32_t i = 0; i < triangles * 9; i++)
            single[i] = uniform(-3, 3);
        for (uint32_t i = 0; i < triangles * 6; i++)
            doubles[i] = (double)uniform(-50, 50) + (double)uniform(-1, 1) * 1e-9;   /* past float precision */
        const void *corners[2] = {single, doubles};
        const uint32_t components[2] = {3, 2}, bytes[2] = {4, 8};
        float *outs[2] = {maps[0], maps[1]};
        for (int a = 0; a < 2; a++)
            for (uint32_t i = 0; i < size * size * 3; i++)
                maps[a][i] = expected[a][i] = (float)(i % 7) - 100.0f;   /* uncovered texels keep these */
        memset(covered, 0, sizeof covered);
        memset(marked, 0, sizeof marked);
        if (op_uv_raster_interpolate(uv, triangles, size, 2, corners, components, bytes, outs,
                                     trial % 4 ? covered : NULL))
            return 1;
        op_uv_sample_t *samples = NULL;
        uint64_t count = 0;
        if (op_uv_rasterize(uv, triangles, size, 1, NULL, &samples, &count))
            return 1;
        for (uint64_t k = 0; k < count; k++) {
            const op_uv_sample_t *s = samples + k;
            uint64_t texel = (uint64_t)s->row * size + s->column;
            const float *w = s->weights, *f = single + (uint64_t)s->triangle * 9;
            const double *d = doubles + (uint64_t)s->triangle * 6;
            for (int j = 0; j < 3; j++) {
                float a = w[0] * f[j];
                a = a + w[1] * f[3 + j];
                expected[0][texel * 3 + j] = a + w[2] * f[6 + j];
            }
            for (int j = 0; j < 2; j++) {
                double a = (double)w[0] * d[j];
                a = a + (double)w[1] * d[2 + j];
                expected[1][texel * 2 + j] = (float)(a + (double)w[2] * d[4 + j]);
            }
            marked[texel] = 1;
        }
        texels += count;
        op_geometry_free(samples);
        if (memcmp(maps[0], expected[0], size * size * 3 * sizeof(float)) ||
            memcmp(maps[1], expected[1], size * size * 2 * sizeof(float)) ||
            (trial % 4 && memcmp(covered, marked, size * size))) {
            fprintf(stderr, "interpolated maps, trial %d: a map or the coverage differs\n", trial);
            return 1;
        }
    }
    const void *corners[1] = {single};
    float *outs[1] = {maps[0]};
    const uint32_t three[1] = {3}, seventeen[1] = {17}, four[1] = {4}, two[1] = {2};
    /* A non-finite UV anywhere fails the call before any texel is written. */
    for (uint32_t i = 0; i < 64 * 3; i++)
        maps[0][i] = -1.0f;
    uv[6 * 3 + 2] = NAN;
    if (op_uv_raster_interpolate(uv, 4, 8, 1, corners, three, four, outs, NULL) != OP_VALIDATION_FAILED)
        return 1;
    for (uint32_t i = 0; i < 64 * 3; i++)
        if (maps[0][i] != -1.0f)
            return 1;
    if (op_uv_raster_interpolate(uv, 1, 8, 0, corners, three, four, outs, NULL) != OP_INVALID_ARGUMENT ||
        op_uv_raster_interpolate(uv, 1, 8, 9, corners, three, four, outs, NULL) != OP_INVALID_ARGUMENT ||
        op_uv_raster_interpolate(uv, 1, 8, 1, corners, seventeen, four, outs, NULL) != OP_INVALID_ARGUMENT ||
        op_uv_raster_interpolate(uv, 1, 8, 1, corners, three, two, outs, NULL) != OP_INVALID_ARGUMENT)
        return 1;
    printf("interpolated raster maps match step-by-step interpolation over %llu texels\n", (unsigned long long)texels);
    return 0;
}

/* op_uv_raster_ownership against padding.build_ownership's steps, batch by batch: the batch's
 * samples (op_uv_rasterize, not unique, in triangle order), each texel's island the last writer's
 * owner, coverage, and a conflict wherever a writer differs from the batch's first writer of the
 * texel. Owners include -1 (faces outside every island); batches split overlapping triangles. */
static int ownership_maps(void) {
    enum { TRIANGLES = 400, SIZE = 128 };
    static float uv[TRIANGLES * 6];
    static int32_t owners[TRIANGLES], island[SIZE * SIZE], expected_island[SIZE * SIZE], first[SIZE * SIZE];
    static uint8_t covered[SIZE * SIZE], conflicts[SIZE * SIZE], expected_covered[SIZE * SIZE],
        expected_conflicts[SIZE * SIZE], seen[SIZE * SIZE];
    uint32_t starts[8];
    uint64_t texels = 0, conflicted = 0;
    for (int trial = 0; trial < 80; trial++) {
        uint32_t triangles = 1 + (uint32_t)uniform(0, TRIANGLES - 1), size = 4 + (uint32_t)uniform(0, SIZE - 4);
        float scale = uniform(.02f, .4f);
        for (uint32_t t = 0; t < triangles; t++) {
            float u = uniform(-.05f, 1.05f), v = uniform(-.05f, 1.05f);
            for (int k = 0; k < 6; k++)
                uv[t * 6 + k] = (k % 2 ? v : u) + uniform(-scale, scale);
            owners[t] = (int32_t)uniform(0, 6) - 1;   /* -1 to 4 */
        }
        uint32_t batches = 1;
        starts[0] = 0;
        while (batches < 8 && trial % 3) {   /* ascending batch starts inside the triangle range */
            uint32_t next = starts[batches - 1] + 1 + (uint32_t)uniform(0, (float)(triangles / 4 + 1));
            if (next >= triangles)
                break;
            starts[batches++] = next;
        }
        for (uint32_t i = 0; i < size * size; i++) {
            island[i] = expected_island[i] = -7;
            covered[i] = expected_covered[i] = conflicts[i] = expected_conflicts[i] = 0;
        }
        if (op_uv_raster_ownership(uv, triangles, size, owners, starts, batches, island, covered, conflicts))
            return 1;
        for (uint32_t b = 0; b < batches; b++) {
            uint32_t begin = starts[b], end = b + 1 < batches ? starts[b + 1] : triangles;
            op_uv_sample_t *samples = NULL;
            uint64_t count = 0;
            if (op_uv_rasterize(uv + (uint64_t)begin * 6, end - begin, size, 0, NULL, &samples, &count))
                return 1;
            memset(seen, 0, sizeof seen);
            for (uint64_t k = 0; k < count; k++) {
                uint64_t texel = (uint64_t)samples[k].row * size + samples[k].column;
                int32_t owner = owners[begin + samples[k].triangle];
                if (!seen[texel]) {
                    seen[texel] = 1;
                    first[texel] = owner;
                }
                expected_island[texel] = owner;
                expected_covered[texel] = 1;
                if (first[texel] != owner)
                    expected_conflicts[texel] = 1;
            }
            texels += count;
            op_geometry_free(samples);
        }
        for (uint32_t i = 0; i < size * size; i++)
            conflicted += expected_conflicts[i];
        if (memcmp(island, expected_island, size * size * sizeof(int32_t)) ||
            memcmp(covered, expected_covered, size * size) || memcmp(conflicts, expected_conflicts, size * size)) {
            fprintf(stderr, "ownership maps, trial %d: islands, coverage or conflicts differ\n", trial);
            return 1;
        }
    }
    const uint32_t backwards[2] = {0, 0}, late[1] = {1};
    if (op_uv_raster_ownership(uv, 4, 8, owners, backwards, 2, island, covered, conflicts) != OP_INVALID_ARGUMENT ||
        op_uv_raster_ownership(uv, 4, 8, owners, late, 1, island, covered, conflicts) != OP_INVALID_ARGUMENT ||
        op_uv_raster_ownership(uv, 4, 8, owners, starts, 0, island, covered, conflicts) != OP_INVALID_ARGUMENT)
        return 1;
    printf("ownership maps match the per-batch steps over %llu samples (%llu conflicting texels)\n",
           (unsigned long long)texels, (unsigned long long)conflicted);
    return 0;
}

/* op_uv_raster_assign against rasterizing (unique) and scattering each kept sample's triangle
 * values step by step, with each kind's cast: float32, float64 rounded, int32, int64 wrapped to
 * int32 (values past the int32 range included); uncovered texels untouched, coverage marked. */
static int assigned_maps(void) {
    enum { TRIANGLES = 300, SIZE = 96 };
    static float uv[TRIANGLES * 6], f32[TRIANGLES * 3], maps_f[2][SIZE * SIZE * 3], expected_f[2][SIZE * SIZE * 3];
    static double f64[TRIANGLES * 2];
    static int32_t i32[TRIANGLES], maps_i[2][SIZE * SIZE], expected_i[2][SIZE * SIZE];
    static int64_t i64[TRIANGLES];
    static uint8_t covered[SIZE * SIZE], marked[SIZE * SIZE];
    uint64_t texels = 0;
    for (int trial = 0; trial < 50; trial++) {
        uint32_t triangles = 1 + (uint32_t)uniform(0, TRIANGLES - 1), size = 4 + (uint32_t)uniform(0, SIZE - 4);
        float scale = uniform(.02f, .4f);
        for (uint32_t t = 0; t < triangles; t++) {
            float u = uniform(-.05f, 1.05f), v = uniform(-.05f, 1.05f);
            for (int k = 0; k < 6; k++)
                uv[t * 6 + k] = (k % 2 ? v : u) + uniform(-scale, scale);
            for (int k = 0; k < 3; k++)
                f32[t * 3 + k] = uniform(-9, 9);
            for (int k = 0; k < 2; k++)
                f64[t * 2 + k] = (double)uniform(-1e3f, 1e3f) + (double)uniform(-1, 1) * 1e-10;
            i32[t] = (int32_t)uniform(-50, 50);
            i64[t] = (int64_t)uniform(-50, 50) + (t % 7 == 3 ? (int64_t)1 << 33 : 0);   /* past int32 */
        }
        const void *values[4] = {f32, f64, i32, i64};
        const uint32_t components[4] = {3, 2, 1, 1}, kinds[4] = {OP_ASSIGN_F32, OP_ASSIGN_F64, OP_ASSIGN_I32,
                                                                  OP_ASSIGN_I64};
        void *outs[4] = {maps_f[0], maps_f[1], maps_i[0], maps_i[1]};
        for (uint32_t i = 0; i < size * size * 3; i++)
            maps_f[0][i] = maps_f[1][i] = expected_f[0][i] = expected_f[1][i] = -5.0f;
        for (uint32_t i = 0; i < size * size; i++)
            maps_i[0][i] = maps_i[1][i] = expected_i[0][i] = expected_i[1][i] = -9;
        memset(covered, 0, sizeof covered);
        memset(marked, 0, sizeof marked);
        if (op_uv_raster_assign(uv, triangles, size, 4, values, components, kinds, outs, trial % 3 ? covered : NULL))
            return 1;
        op_uv_sample_t *samples = NULL;
        uint64_t count = 0;
        if (op_uv_rasterize(uv, triangles, size, 1, NULL, &samples, &count))
            return 1;
        for (uint64_t k = 0; k < count; k++) {
            uint64_t texel = (uint64_t)samples[k].row * size + samples[k].column;
            uint32_t t = samples[k].triangle;
            for (int j = 0; j < 3; j++)
                expected_f[0][texel * 3 + j] = f32[t * 3 + j];
            for (int j = 0; j < 2; j++)
                expected_f[1][texel * 2 + j] = (float)f64[t * 2 + j];
            expected_i[0][texel] = i32[t];
            expected_i[1][texel] = (int32_t)(i64[t] & 0xffffffff) ;
            marked[texel] = 1;
        }
        texels += count;
        op_geometry_free(samples);
        if (memcmp(maps_f[0], expected_f[0], size * size * 3 * sizeof(float)) ||
            memcmp(maps_f[1], expected_f[1], size * size * 2 * sizeof(float)) ||
            memcmp(maps_i[0], expected_i[0], size * size * sizeof(int32_t)) ||
            memcmp(maps_i[1], expected_i[1], size * size * sizeof(int32_t)) ||
            (trial % 3 && memcmp(covered, marked, size * size))) {
            fprintf(stderr, "assigned maps, trial %d: a map or the coverage differs\n", trial);
            return 1;
        }
    }
    const void *values[1] = {f32};
    void *outs[1] = {maps_f[0]};
    const uint32_t three[1] = {3}, bad[1] = {OP_ASSIGN_I64 + 1}, f32kind[1] = {OP_ASSIGN_F32};
    if (op_uv_raster_assign(uv, 1, 8, 1, values, three, bad, outs, NULL) != OP_INVALID_ARGUMENT ||
        op_uv_raster_assign(uv, 1, 8, 0, values, three, f32kind, outs, NULL) != OP_INVALID_ARGUMENT)
        return 1;
    printf("assigned raster maps match step-by-step scatters over %llu texels\n", (unsigned long long)texels);
    return 0;
}

int main(void) {
    enum { MAX = 4000 };
    static float uv[MAX * 6], clip[MAX * 4];
    uint64_t compared = 0;
    for (int trial = 0; trial < 160; trial++) {
        /* Large triangles on small rasters, many small ones on large rasters (the samples stay
         * within what the sorting reference finishes quickly). */
        int large = trial % 5 == 0;
        uint32_t triangles = 1 + (uint32_t)uniform(0, (float)(large ? 120 : MAX - 1));
        uint32_t size = large ? 16 + (uint32_t)uniform(0, 300) : trial % 23 == 1 ? 4096 : 16 + (uint32_t)uniform(0, 700);
        float scale = large ? uniform(.05f, .6f) : uniform(.002f, size > 1024 ? .01f : .08f);
        for (uint32_t t = 0; t < triangles; t++) {
            float u = uniform(-.05f, 1.05f), v = uniform(-.05f, 1.05f);
            for (int k = 0; k < 3; k++) {
                uv[t * 6 + 2 * k] = u + uniform(-scale, scale);
                uv[t * 6 + 2 * k + 1] = v + uniform(-scale, scale);
            }
            if (t % 37 == 5) {   /* a degenerate triangle */
                uv[t * 6 + 4] = uv[t * 6];
                uv[t * 6 + 5] = uv[t * 6 + 1];
            }
            if (t % 11 == 3) {   /* a sliver */
                uv[t * 6 + 2] = uv[t * 6] + 1e-4f;
                uv[t * 6 + 3] = uv[t * 6 + 1] + scale;
            }
            clip[t * 4] = u - uniform(0, scale);
            clip[t * 4 + 1] = v - uniform(0, scale);
            clip[t * 4 + 2] = u + uniform(0, scale);
            clip[t * 4 + 3] = v + uniform(0, scale);
        }
        for (uint32_t unique = 0; unique < 2; unique++) {
            const float *box = trial % 3 == 1 ? clip : NULL;
            op_uv_sample_t *mine = NULL, *theirs = NULL;
            uint64_t a = 0, b = 0;
            op_status_t s1 = op_uv_rasterize(uv, triangles, size, unique, box, &mine, &a);
            op_status_t s2 = reference_rasterize(uv, triangles, size, unique, box, &theirs, &b);
            if (s1 != s2 || a != b || (a && memcmp(mine, theirs, a * sizeof(*mine)))) {
                fprintf(stderr, "trial %d unique %u: status %d/%d, %llu/%llu samples\n", trial, unique, s1, s2,
                        (unsigned long long)a, (unsigned long long)b);
                for (uint64_t i = 0; i < a && i < b; i++)
                    if (memcmp(mine + i, theirs + i, sizeof(*mine))) {
                        fprintf(stderr, "  first difference at %llu: (%u,%u,t%u) vs (%u,%u,t%u)\n",
                                (unsigned long long)i, mine[i].row, mine[i].column, mine[i].triangle,
                                theirs[i].row, theirs[i].column, theirs[i].triangle);
                        break;
                    }
                return 1;
            }
            compared += a;
            op_geometry_free(mine);
            op_geometry_free(theirs);
        }
    }
    printf("uv rasterize matches its reference over %llu samples\n", (unsigned long long)compared);
    return interpolated_maps() || ownership_maps() || assigned_maps();
}
