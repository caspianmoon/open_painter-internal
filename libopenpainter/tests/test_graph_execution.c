#include "openpainter/op_graph.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct fixture {
    op_graph_t *graph;
    uint32_t count, root, event, fail_at, rendered;
    const uint32_t *offsets, *edges;
    const uint64_t *desired;
    uint64_t *resident;
    unsigned char *pinned;
} fixture;
static op_status_t submit(void *user, uint32_t action, uint32_t node, uint64_t stamp) {
    fixture *f = user;
    int fail = ++f->event == f->fail_at;
    assert(node < f->count && stamp == f->desired[node]);
    if (f->event == 1) {
        uint32_t rendered;
        op_graph_patch_t patch = {0, 0, 123};
        assert(op_graph_apply_patch(f->graph, &patch, 1) == OP_PENDING);
        assert(op_graph_replace(f->graph, f->count, f->offsets, f->edges, f->offsets[f->count]) ==
               OP_PENDING);
        assert(op_graph_execute(f->graph, f->root, f->resident, f->desired, submit, f, &rendered) ==
               OP_PENDING);
    }
    if (action == OP_GRAPH_PIN) {
        assert(!f->pinned[node]);
        if (!fail)
            f->pinned[node] = 1;
    } else if (action == OP_GRAPH_RENDER) {
        assert(f->pinned[node]);
        for (uint32_t e = f->offsets[node]; e < f->offsets[node + 1]; e++) {
            uint32_t input = f->edges[e];
            assert(f->pinned[input] && f->resident[input] == f->desired[input]);
        }
        if (!fail) {
            f->resident[node] = stamp;
            f->rendered++;
        }
    } else {
        assert(action == OP_GRAPH_UNPIN && f->pinned[node]);
        f->pinned[node] = 0;
        /* Aggressive reclamation: every unheld input is immediately evicted. */
        if (node != f->root)
            f->resident[node] = 0;
    }
    return fail ? OP_DEVICE_LOSS : OP_OK;
}
static void run(uint32_t n, uint32_t *offsets, uint32_t *edges, int failures) {
    op_graph_t *g = NULL;
    assert(op_graph_create(&g) == OP_OK);
    assert(op_graph_replace(g, n, offsets, edges, offsets[n]) == OP_OK);
    uint64_t *own = calloc(n, sizeof(*own)), *resident = calloc(n, sizeof(*resident));
    uint64_t *desired = calloc(n, sizeof(*desired));
    uint32_t *plan = calloc(n, sizeof(*plan));
    unsigned char *pinned = calloc(n, 1);
    assert(own && resident && desired && plan && pinned);
    uint32_t count, rendered;
    assert(op_graph_evaluate(g, own, resident, desired, plan, n, &count) == OP_OK);
    fixture f = {g, n, n - 1, 0, 0, 0, offsets, edges, desired, resident, pinned};
    assert(op_graph_execute(g, f.root, resident, desired, submit, &f, &rendered) == OP_OK);
    assert(rendered == n && f.rendered == n && resident[f.root] == desired[f.root]);
    for (uint32_t i = 0; i < n; i++)
        assert(!pinned[i]);
    uint32_t events = f.event;
    f.event = 0;
    assert(op_graph_execute(g, f.root, resident, desired, submit, &f, &rendered) == OP_OK);
    assert(rendered == 0 && f.event == 2); /* already resident root */
    for (uint32_t failure = 1; failures && failure <= events; failure++) {
        memset(resident, 0, n * sizeof(*resident));
        f.event = f.rendered = 0;
        f.fail_at = failure;
        assert(op_graph_execute(g, f.root, resident, desired, submit, &f, &rendered) ==
               OP_DEVICE_LOSS);
        assert(rendered == f.rendered);
        for (uint32_t i = 0; i < n; i++)
            assert(!pinned[i]);
        f.fail_at = 0;
        f.event = f.rendered = 0;
        assert(op_graph_execute(g, f.root, resident, desired, submit, &f, &rendered) == OP_OK);
        assert(resident[f.root] == desired[f.root]);
    }
    desired[0] = 0;
    assert(op_graph_execute(g, f.root, resident, desired, submit, &f, &rendered) ==
           OP_VALIDATION_FAILED);
    op_graph_destroy(g);
    free(own);
    free(resident);
    free(desired);
    free(plan);
    free(pinned);
}
int main(void) {
    uint32_t offsets[] = {0, 0, 0, 2, 3, 7}, edges[] = {0, 1, 0, 2, 3, 1, 1};
    run(5, offsets, edges, 1); /* shared and repeated inputs */
    uint32_t n = 65536;
    uint32_t *deep_offsets = calloc(n + 1, sizeof(uint32_t));
    uint32_t *deep_edges = calloc(n, sizeof(uint32_t));
    assert(deep_offsets && deep_edges);
    for (uint32_t i = 1; i < n; i++) {
        deep_offsets[i + 1] = i;
        deep_edges[i - 1] = i - 1;
    }
    run(n, deep_offsets, deep_edges, 0); /* bounded, no recursive host/C stack */
    free(deep_offsets);
    free(deep_edges);
    puts("PASS test_graph_execution");
    return 0;
}
