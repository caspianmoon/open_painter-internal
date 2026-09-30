#include "openpainter/op_geometry.h"
#define donor_key reference_key
#include <stdlib.h>

#define INF (UINT64_C(1) << 62)
static uint64_t donor_key(const int32_t *islands, uint32_t size, uint32_t row, uint32_t column,
                          int32_t source, uint32_t limit) {
    if (source < 0 || (uint32_t)source >= size)
        return INF;
    int64_t distance = (int64_t)row - source;
    if (distance < -(int64_t)limit || distance > limit)
        return INF;
    uint64_t index = (uint64_t)source * size + column;
    return ((uint64_t)(distance * distance) << 46) | ((uint64_t)islands[index] << 26) | index;
}
op_status_t reference_donors(const uint8_t *covered, const int32_t *islands, uint32_t size,
                              uint32_t limit, int32_t *donors, int16_t *distance2) {
    if (!covered || !islands || !donors || !distance2 || !size || size > 8192 || limit > 64)
        return OP_INVALID_ARGUMENT;
    uint64_t count = (uint64_t)size * size;
    for (uint64_t i = 0; i < count; i++)
        if (covered[i] && (islands[i] < 0 || islands[i] >= (1 << 20)))
            return OP_VALIDATION_FAILED;
    uint32_t band = (1u << 21) / size;
    if (band > size)
        band = size;
    uint64_t *keys = malloc((size_t)band * size * sizeof(*keys));
    if (!keys)
        return OP_RESOURCE_EXHAUSTED;
    for (uint32_t first = 0; first < size; first += band) {
        uint32_t last = first + band < size ? first + band : size;
        uint32_t low = first > limit ? first - limit : 0;
        uint32_t high = last + limit < size ? last + limit : size;
        for (uint32_t col = 0; col < size; col++) {
            int32_t source = -1;
            for (uint32_t row = low; row < last; row++) {
                if (covered[(uint64_t)row * size + col])
                    source = (int32_t)row;
                if (row >= first)
                    keys[(uint64_t)(row - first) * size + col] =
                        donor_key(islands, size, row, col, source, limit);
            }
            source = (int32_t)size;
            for (uint32_t end = high; end > first;) {
                uint32_t row = --end;
                if (covered[(uint64_t)row * size + col])
                    source = (int32_t)row;
                if (row < last) {
                    uint64_t *key = keys + (uint64_t)(row - first) * size + col;
                    uint64_t candidate = donor_key(islands, size, row, col, source, limit);
                    if (candidate < *key)
                        *key = candidate;
                }
            }
        }
        for (uint32_t row = first; row < last; row++) {
            const uint64_t *source = keys + (uint64_t)(row - first) * size;
            for (uint32_t col = 0; col < size; col++) {
                uint64_t index = (uint64_t)row * size + col;
                if (covered[index]) {
                    donors[index] = -1;
                    distance2[index] = 0;
                    continue;
                }
                uint64_t best = source[col];
                for (uint32_t dx = 1; dx <= limit; dx++) {
                    uint64_t shift = (uint64_t)(dx * dx) << 46;
                    if (col >= dx && source[col - dx] + shift < best)
                        best = source[col - dx] + shift;
                    if (col + dx < size && source[col + dx] + shift < best)
                        best = source[col + dx] + shift;
                }
                uint64_t distance = best >> 46;
                int found = best < INF && distance <= limit * limit;
                donors[index] = found ? (int32_t)(best & ((UINT64_C(1) << 26) - 1)) : -1;
                distance2[index] = found ? (int16_t)distance : -1;
            }
        }
    }
    free(keys);
    return OP_OK;
}
