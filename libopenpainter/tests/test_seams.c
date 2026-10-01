#include "openpainter/op_seam.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
int main(void) {
    uint8_t covered[256] = {0};
    covered[5 * 16 + 5] = 1;
    float sides[] = {4, 4, 4, 8, 3, 6, 10, 4, 10, 8, 11, 6};
    int64_t islands[] = {2};
    op_seam_sample_t *samples;
    uint64_t count;
    assert(op_seam_bands(covered, 16, 2, sides, islands, 1, &samples, &count) == OP_OK &&
           count > 0);
    unsigned found = 0;
    for (uint64_t i = 0; i < count; i++) {
        assert(samples[i].nearest == 1);
        assert(samples[i].island == 2 && !covered[samples[i].row * 16 + samples[i].column]);
        if (samples[i].row == 5 && samples[i].column == 4) {
            assert(samples[i].x == 10.25f && samples[i].y == 5 && samples[i].normal[0] == 1);
            found++;
        }
    }
    assert(found == 1);
    op_geometry_free(samples);
    float duplicates[24];
    for (unsigned i = 0; i < 24; i++)
        duplicates[i] = sides[i % 12];
    int64_t owners[] = {7, 2};
    assert(op_seam_bands(covered, 16, 2, duplicates, owners, 2, &samples, &count) == OP_OK);
    for (uint64_t i = 0; i < count; i++)
        assert(samples[i].nearest == (uint32_t)(samples[i].island == 2));
    op_geometry_free(samples);
    sides[0] = NAN;
    assert(op_seam_bands(covered, 16, 2, sides, islands, 1, &samples, &count) ==
               OP_VALIDATION_FAILED &&
           !samples && !count);
    assert(op_seam_bands(covered, 16, 0, NULL, NULL, 0, &samples, &count) == OP_INVALID_ARGUMENT);
    assert(op_seam_bands(covered, 16, 2, NULL, NULL, 0, &samples, &count) == OP_OK && !count);
    op_geometry_free(samples);
    puts("PASS test_seams");
    return 0;
}
