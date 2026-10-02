#include "openpainter/op_geometry.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

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
op_status_t op_padding_donors(const uint8_t *covered, const int32_t *islands, uint32_t size,
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
    uint32_t *nearest = malloc((size_t)size * sizeof(*nearest));
    int32_t *sources = malloc((size_t)size * sizeof(*sources));
    if (!keys || !nearest || !sources) {
        free(keys);
        free(nearest);
        free(sources);
        return OP_RESOURCE_EXHAUSTED;
    }
    for (uint32_t first = 0; first < size; first += band) {
        uint32_t last = first + band < size ? first + band : size;
        uint32_t low = first > limit ? first - limit : 0;
        uint32_t high = last + limit < size ? last + limit : size;
        /* Each column's nearest covered row above and below, swept row by row (the same
         * per-column scan as down each column, but reading the arrays in memory order). */
        for (uint32_t col = 0; col < size; col++)
            sources[col] = -1;
        for (uint32_t row = low; row < last; row++) {
            const uint8_t *line = covered + (uint64_t)row * size;
            uint64_t *key = keys + (uint64_t)(row - first) * size;
            for (uint32_t col = 0; col < size; col++) {
                if (line[col])
                    sources[col] = (int32_t)row;
                if (row >= first)
                    key[col] = donor_key(islands, size, row, col, sources[col], limit);
            }
        }
        for (uint32_t col = 0; col < size; col++)
            sources[col] = (int32_t)size;
        for (uint32_t end = high; end > first;) {
            uint32_t row = --end;
            const uint8_t *line = covered + (uint64_t)row * size;
            uint64_t *key = keys + (uint64_t)(row - first) * size;
            for (uint32_t col = 0; col < size; col++) {
                if (line[col])
                    sources[col] = (int32_t)row;
                if (row < last) {
                    uint64_t candidate = donor_key(islands, size, row, col, sources[col], limit);
                    if (candidate < key[col])
                        key[col] = candidate;
                }
            }
        }
        for (uint32_t row = first; row < last; row++) {
            const uint64_t *source = keys + (uint64_t)(row - first) * size;
            /* Columns to the nearest one with a donor in reach vertically: the columns closer
             * than it offer none (their keys are INF), and past the limit there is none. */
            uint32_t gap = UINT32_MAX;
            for (uint32_t col = 0; col < size; col++) {
                gap = source[col] < INF ? 0 : gap == UINT32_MAX ? gap : gap + 1;
                nearest[col] = gap;
            }
            gap = UINT32_MAX;
            for (uint32_t col = size; col-- > 0;) {
                gap = source[col] < INF ? 0 : gap == UINT32_MAX ? gap : gap + 1;
                if (gap < nearest[col])
                    nearest[col] = gap;
            }
            for (uint32_t col = 0; col < size; col++) {
                uint64_t index = (uint64_t)row * size + col;
                if (covered[index]) {
                    donors[index] = -1;
                    distance2[index] = 0;
                    continue;
                }
                uint64_t best = source[col];
                /* A donor dx columns away is at least dx^2 away: once that exceeds the best
                 * distance found, no farther column can win or tie (ties break on the island
                 * and index bits, so a candidate at an equal distance is still compared). */
                for (uint32_t dx = nearest[col] > 1 ? nearest[col] : 1;
                     dx <= limit && (uint64_t)dx * dx <= best >> 46; dx++) {
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
    free(nearest);
    free(sources);
    return OP_OK;
}

op_status_t op_padding_extend(const float *src, const int32_t *donors, uint64_t count,
                              uint32_t components, float *dst) {
    if (!src || !donors || !dst || !count || !components || components > 4 || count > INT32_MAX)
        return OP_INVALID_ARGUMENT;
    uintptr_t a = (uintptr_t)src, b = (uintptr_t)dst, bytes = (uintptr_t)(count * components * sizeof(float));
    if (src != dst && a < b + bytes && b < a + bytes)
        return OP_INVALID_ARGUMENT;
    for (uint64_t i = 0; i < count; i++) {
        int32_t donor = donors[i];
        if (donor >= 0 && ((uint64_t)donor >= count || donors[donor] >= 0))
            return OP_VALIDATION_FAILED;
    }
    size_t texel = (size_t)components * sizeof(float);
    if (src == dst) {
        for (uint64_t i = 0; i < count; i++)
            if (donors[i] >= 0)
                memcpy(dst + i * components, src + (uint64_t)donors[i] * components, texel);
        return OP_OK;
    }
    for (uint64_t i = 0; i < count; i++) {
        uint64_t from = donors[i] >= 0 ? (uint64_t)donors[i] : i;
        memcpy(dst + i * components, src + from * components, texel);
    }
    return OP_OK;
}
