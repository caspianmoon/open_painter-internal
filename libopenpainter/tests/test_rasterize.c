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
    return 0;
}
