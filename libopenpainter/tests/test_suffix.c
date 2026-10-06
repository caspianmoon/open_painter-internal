#include "openpainter/op_graph.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

#define NONE UINT32_MAX
#define N 16

typedef struct host {
    uint64_t resident[N];
    const uint64_t *stamps;
} host;

static op_status_t submit(void *user, uint32_t action, uint32_t node, uint64_t stamp) {
    host *h = user;
    if (action == OP_GRAPH_RENDER)
        h->resident[node] = stamp;
    return OP_OK;
}

typedef struct plan {
    op_graph_run_t runs[N];
    uint32_t members[N], run_count, member_count;
} plan;

static void runs_of(op_graph_t *g, uint32_t root, const host *h, plan *p) {
    assert(op_graph_suffix_runs(g, root, h->resident, h->stamps, p->runs, N, &p->run_count,
                                p->members, N, &p->member_count) == OP_OK);
}

static void evaluate(op_graph_t *g, uint32_t count, host *h, uint64_t *stamps) {
    uint32_t plan[N], used = 0;
    assert(op_graph_evaluate_current(g, h->resident, stamps, plan, N, &used) == OP_OK);
    (void)count;
    h->stamps = stamps;
}

static void render(op_graph_t *g, uint32_t root, host *h) {
    uint32_t rendered = 0;
    assert(op_graph_execute(g, root, h->resident, h->stamps, submit, h, &rendered) == OP_OK);
}

static void set_own(op_graph_t *g, uint32_t node, uint64_t value) {
    op_graph_patch_t patch = {node, 0, value};
    assert(op_graph_apply_patch(g, &patch, 1) == OP_OK);
}

int main(void) {
    /* Sources 0..5 feed layer units 6..11, a chain composing bottom (6) to top (11). */
    enum { S = 0, C = 6, COUNT = 12 };
    uint32_t offsets[COUNT + 1], edges[32], e = 0;
    uint32_t backdrop[COUNT], grouped[COUNT];
    unsigned char affine[COUNT];
    for (uint32_t i = 0; i < COUNT; i++) {
        offsets[i] = e;
        backdrop[i] = grouped[i] = NONE;
        affine[i] = 0;
        if (i >= C) {
            if (i > C)
                edges[e++] = backdrop[i] = i - 1;
            edges[e++] = S + (i - C);
            affine[i] = 1;
        }
    }
    offsets[COUNT] = e;
    op_graph_t *g = NULL;
    assert(op_graph_create(&g) == OP_OK);
    assert(op_graph_replace(g, COUNT, offsets, edges, e) == OP_OK);
    for (uint32_t i = 0; i < COUNT; i++)
        set_own(g, i, 100 + i);

    /* A composition edge must be a real dependency. */
    backdrop[8] = 6;
    assert(op_graph_set_composition(g, backdrop, grouped, affine, COUNT) == OP_VALIDATION_FAILED);
    backdrop[8] = 7;
    assert(op_graph_set_composition(g, backdrop, grouped, affine, COUNT - 1) ==
           OP_VALIDATION_FAILED);
    assert(op_graph_set_composition(g, backdrop, grouped, affine, COUNT) == OP_OK);

    host h = {{0}, NULL};
    uint64_t stamps[N];
    plan p;
    evaluate(g, COUNT, &h, stamps);
    /* Nothing has been rendered with a known local stamp yet: no runs. */
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 0);
    render(g, 11, &h);
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 0); /* current */

    /* Before the edit, prediction reports the runs it will produce. */
    plan predicted;
    assert(op_graph_edit_runs(g, 11, 7, NULL, NULL, predicted.runs, N, &predicted.run_count, predicted.members, N,
                              &predicted.member_count) == OP_OK);
    assert(predicted.run_count == 1 && predicted.runs[0].input == 7 && predicted.runs[0].length == 4);
    assert(op_graph_edit_runs(g, 11, COUNT, NULL, NULL, predicted.runs, N, &predicted.run_count, predicted.members,
                              N, &predicted.member_count) == OP_INVALID_ARGUMENT);
    /* Edit the second layer: everything above it is stale only by its backdrop. */
    set_own(g, 7, 500);
    evaluate(g, COUNT, &h, stamps);
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.member_count == 4);
    op_graph_run_t run = p.runs[0];
    assert(run.top == 11 && run.bottom == 8 && run.input == 7 && run.length == 4 && run.first == 0);
    assert(run.key == predicted.runs[0].key);   /* armed while idle, found on the first edit */
    for (uint32_t i = 0; i < 4; i++)
        assert(p.members[i] == 8 + i);
    /* Materialize the top as the host would; the members stay stale. */
    h.resident[7] = stamps[7];
    h.resident[11] = stamps[11];
    uint64_t key = run.key;
    /* A later edit of the same layer keeps the run and its key. */
    set_own(g, 7, 501);
    evaluate(g, COUNT, &h, stamps);
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.runs[0].key == key && p.runs[0].input == 7);
    /* The source of a member changes: that member splits the run. */
    set_own(g, 3, 900); /* feeds layer 9 */
    evaluate(g, COUNT, &h, stamps);
    runs_of(g, 11, &h, &p);
    /* 11,10 form a run over 9; 9 is dirty; 8 alone over 7 is too short. */
    assert(p.run_count == 1 && p.runs[0].top == 11 && p.runs[0].input == 9 &&
           p.runs[0].length == 2 && p.runs[0].key != key);
    /* Rendering records the new local stamps: the long run is back. */
    render(g, 11, &h);
    set_own(g, 7, 502);
    evaluate(g, COUNT, &h, stamps);
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.runs[0].length == 4 && p.runs[0].key != key);
    key = p.runs[0].key;

    /* A non-affine layer is a barrier with runs on both sides. */
    affine[9] = 0;
    assert(op_graph_set_composition(g, backdrop, grouped, affine, COUNT) == OP_OK);
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.runs[0].top == 11 && p.runs[0].input == 9);
    /* Layers 8 and 7: 8 is a run of one over the dirty 7, not reported. */
    affine[9] = 1;
    assert(op_graph_set_composition(g, backdrop, grouped, affine, COUNT) == OP_OK);
    evaluate(g, COUNT, &h, stamps);
    /* Unchanged composition kept the rendered local stamps of other nodes, but
     * the re-classified node lost them and splits the run until rendered. */
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.runs[0].input == 9 && p.runs[0].length == 2);
    render(g, 11, &h);
    set_own(g, 7, 503);
    evaluate(g, COUNT, &h, stamps);
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.runs[0].key == key);

    /* An edit of the bottom layer: the run reaches the chain's first layer. */
    render(g, 11, &h);
    set_own(g, 6, 700);
    evaluate(g, COUNT, &h, stamps);
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.runs[0].input == 6 && p.runs[0].length == 5);
    render(g, 11, &h);
    /* A failed submission resets host stamps without changing any content: the
     * whole chain is one run over the chain's constant start. */
    for (uint32_t i = C; i < COUNT; i++)
        h.resident[i] = 0;
    runs_of(g, 11, &h, &p);
    assert(p.run_count == 1 && p.runs[0].input == NONE && p.runs[0].length == 6 &&
           p.runs[0].bottom == C);
    op_graph_destroy(g);

    /* A folder (unit 5) blends the child chain 2 <- 3 <- 4 over the outer chain
     * 0 <- 1; the top-level chain continues 5 <- 6 <- 7. Children read sources 8..10. */
    uint32_t f_offsets[12], f_edges[32];
    uint32_t f_backdrop[11], f_grouped[11];
    unsigned char f_affine[11];
    e = 0;
    for (uint32_t i = 0; i < 11; i++) {
        f_offsets[i] = e;
        f_backdrop[i] = f_grouped[i] = NONE;
        f_affine[i] = i < 8;
        if (i == 1 || i == 6 || i == 7)
            f_edges[e++] = f_backdrop[i] = i - 1;
        if (i == 3 || i == 4) /* passthrough-like opaque child chain */
            f_edges[e++] = f_backdrop[i] = i - 1;
        if (i >= 2 && i <= 4)
            f_edges[e++] = 8 + (i - 2);
        if (i == 5) {
            f_edges[e++] = f_backdrop[i] = 1;
            f_edges[e++] = f_grouped[i] = 4;
        }
    }
    f_offsets[11] = e;
    assert(op_graph_create(&g) == OP_OK);
    assert(op_graph_replace(g, 11, f_offsets, f_edges, e) == OP_OK);
    for (uint32_t i = 0; i < 11; i++)
        set_own(g, i, 40 + i);
    assert(op_graph_set_composition(g, f_backdrop, f_grouped, f_affine, 11) == OP_OK);
    memset(&h, 0, sizeof(h));
    evaluate(g, 11, &h, stamps);
    render(g, 7, &h);
    /* Edit the bottom child: its siblings form a run, the folder is dirty (its
     * grouped input changed) and the top-level layers above it form another.
     * Prediction for an edit of the child's own content matches. */
    assert(op_graph_edit_runs(g, 7, 2, NULL, NULL, predicted.runs, N, &predicted.run_count, predicted.members, N,
                              &predicted.member_count) == OP_OK);
    assert(predicted.run_count == 2 && predicted.runs[0].input == 5 && predicted.runs[1].input == 2);
    /* An edit of a source reaches its layer through a non-backdrop edge. */
    assert(op_graph_edit_runs(g, 7, 8, NULL, NULL, p.runs, N, &p.run_count, p.members, N, &p.member_count) == OP_OK);
    assert(p.run_count == 2 && p.runs[1].input == 2);
    set_own(g, 8, 77);
    set_own(g, 2, 78);
    evaluate(g, 11, &h, stamps);
    runs_of(g, 7, &h, &p);
    assert(p.run_count == 2);
    assert(p.runs[0].top == 7 && p.runs[0].input == 5 && p.runs[0].length == 2);
    assert(p.runs[1].top == 4 && p.runs[1].input == 2 && p.runs[1].length == 2);
    assert(p.members[p.runs[1].first] == 3 && p.members[p.runs[1].first + 1] == 4);
    assert(p.runs[0].key == predicted.runs[0].key && p.runs[1].key == predicted.runs[1].key);
    /* The spine of an edit: what it composes over without changing it. */
    uint32_t keep[N], kept = 0;
    assert(op_graph_spine(g, 3, keep, N, &kept) == OP_OK && kept == 2 && keep[0] == 2 && keep[1] == 1);
    assert(op_graph_spine(g, 2, keep, N, &kept) == OP_OK && kept == 1 && keep[0] == 1);
    assert(op_graph_spine(g, 6, keep, N, &kept) == OP_OK && kept == 1 && keep[0] == 5);
    /* The folder's own edit keeps its child composite and its backdrop. */
    assert(op_graph_spine(g, 5, keep, N, &kept) == OP_OK && kept == 2 && keep[0] == 4 && keep[1] == 1);
    assert(op_graph_spine(g, 5, keep, 1, &kept) == OP_RESOURCE_EXHAUSTED);
    assert(op_graph_spine(g, 0, keep, N, &kept) == OP_OK && kept == 0);
    assert(op_graph_spine(g, 3, keep, 1, &kept) == OP_RESOURCE_EXHAUSTED);
    assert(op_graph_spine(g, 11, keep, N, &kept) == OP_INVALID_ARGUMENT);
    uint32_t order[] = {0, 1, 2, 3, 4, 5, 6, 7}, lowest = 0;
    render(g, 7, &h);
    assert(op_graph_lowest_changed(g, order, 8, h.resident, stamps, &lowest) == OP_OK && lowest == UINT32_MAX);
    set_own(g, 3, 123);
    evaluate(g, 11, &h, stamps);
    assert(op_graph_lowest_changed(g, order, 8, h.resident, stamps, &lowest) == OP_OK && lowest == 3);
    order[0] = 40;
    assert(op_graph_lowest_changed(g, order, 8, h.resident, stamps, &lowest) == OP_VALIDATION_FAILED);
    /* Released results below an edit are rendered again: prediction given the
     * resident stamps finds the same run from the chain's start as the planner. */
    render(g, 7, &h);
    h.resident[0] = h.resident[1] = 0;   /* the outer chain below the folder, released */
    assert(op_graph_edit_runs(g, 7, 2, h.resident, stamps, predicted.runs, N, &predicted.run_count,
                              predicted.members, N, &predicted.member_count) == OP_OK);
    assert(op_graph_edit_runs(g, 7, 2, h.resident, NULL, predicted.runs, N, &predicted.run_count,
                              predicted.members, N, &predicted.member_count) == OP_INVALID_ARGUMENT);
    assert(op_graph_edit_runs(g, 7, 2, h.resident, stamps, predicted.runs, N, &predicted.run_count,
                              predicted.members, N, &predicted.member_count) == OP_OK);
    set_own(g, 2, 79);
    evaluate(g, 11, &h, stamps);
    runs_of(g, 7, &h, &p);
    assert(p.run_count == predicted.run_count && p.run_count == 3);
    for (uint32_t i = 0; i < p.run_count; i++)
        assert(p.runs[i].key == predicted.runs[i].key);
    op_graph_destroy(g);

    /* A layer inserted below layer 8 of the first chain: a new topology in which every old node
     * sits two indices later (the new source and layer come first). Layers 9..11 compose as
     * before; with their previous indices they keep what was known about them, so the run an
     * edit of layer 8 was armed with is still planned, under the same key. */
    {
        enum { OLD = 12, NEW = 14, SHIFT = 2 };
        uint32_t o_offsets[OLD + 1], o_edges[32], n_offsets[NEW + 1], n_edges[40];
        uint32_t o_back[OLD], o_group[OLD], n_back[NEW], n_group[NEW], previous[NEW];
        unsigned char o_aff[OLD], n_aff[NEW];
        e = 0;
        for (uint32_t i = 0; i < OLD; i++) {
            o_offsets[i] = e;
            o_back[i] = o_group[i] = NONE;
            o_aff[i] = 0;
            if (i >= C) {
                if (i > C)
                    o_edges[e++] = o_back[i] = i - 1;
                o_edges[e++] = S + (i - C);
                o_aff[i] = 1;
            }
        }
        o_offsets[OLD] = e;
        assert(op_graph_create(&g) == OP_OK);
        assert(op_graph_replace(g, OLD, o_offsets, o_edges, e) == OP_OK);
        for (uint32_t i = 0; i < OLD; i++)
            set_own(g, i, 100 + i);
        assert(op_graph_set_composition(g, o_back, o_group, o_aff, OLD) == OP_OK);
        host before = {{0}, NULL};
        uint64_t old_stamps[N];
        evaluate(g, OLD, &before, old_stamps);
        render(g, 11, &before);
        plan armed;
        assert(op_graph_edit_runs(g, 11, 8, NULL, NULL, armed.runs, N, &armed.run_count, armed.members, N,
                                  &armed.member_count) == OP_OK);
        assert(armed.run_count == 1 && armed.runs[0].input == 8 && armed.runs[0].length == 3);
        /* New nodes: 0 the inserted layer's source, 1 the inserted layer over old 7. */
        uint32_t ne = 0;
        for (uint32_t i = 0; i < NEW; i++) {
            n_offsets[i] = ne;
            n_back[i] = n_group[i] = NONE;
            n_aff[i] = 0;
            previous[i] = i >= SHIFT ? i - SHIFT : NONE;
            if (i == 1) {
                n_edges[ne++] = n_back[i] = 7 + SHIFT;
                n_edges[ne++] = 0;
                n_aff[i] = 1;
            } else if (i >= SHIFT) {
                uint32_t k = i - SHIFT;
                for (uint32_t d = o_offsets[k]; d < o_offsets[k + 1]; d++) {
                    uint32_t target = o_edges[d] + SHIFT;
                    if (k == 8 && o_edges[d] == 7)
                        target = 1;   /* layer 8 now composes over the inserted layer */
                    n_edges[ne++] = target;
                    if (o_back[k] == o_edges[d])
                        n_back[i] = target;
                }
                n_aff[i] = o_aff[k];
            }
        }
        n_offsets[NEW] = ne;
        host after = {{0}, NULL};
        uint64_t new_stamps[N];
        for (uint32_t i = SHIFT; i < NEW; i++)
            after.resident[i] = before.resident[i - SHIFT];
        /* Without the previous indices nothing is known after the replace: no run. */
        assert(op_graph_replace(g, NEW, n_offsets, n_edges, ne) == OP_OK);
        for (uint32_t i = 0; i < NEW; i++)
            set_own(g, i, i >= SHIFT ? 100 + i - SHIFT : 300 + i);
        assert(op_graph_set_composition(g, n_back, n_group, n_aff, NEW) == OP_OK);
        evaluate(g, NEW, &after, new_stamps);
        runs_of(g, 11 + SHIFT, &after, &p);
        assert(p.run_count == 0);
        /* With them, layers 9..11 are one run over layer 8, under the key armed before. */
        assert(op_graph_replace(g, NEW, n_offsets, n_edges, ne) == OP_OK);
        for (uint32_t i = 0; i < NEW; i++)
            set_own(g, i, i >= SHIFT ? 100 + i - SHIFT : 300 + i);
        assert(op_graph_set_composition(g, n_back, n_group, n_aff, NEW) == OP_OK);   /* rendered: lost again */
        assert(op_graph_replace(g, NEW, n_offsets, n_edges, ne) == OP_OK);
        for (uint32_t i = 0; i < NEW; i++)
            set_own(g, i, i >= SHIFT ? 100 + i - SHIFT : 300 + i);
        assert(op_graph_set_composition_from(g, n_back, n_group, n_aff, NEW, NULL) == OP_INVALID_ARGUMENT);
        op_graph_destroy(g);

        /* Once more from the rendered chain, with previous indices this time. */
        assert(op_graph_create(&g) == OP_OK);
        assert(op_graph_replace(g, OLD, o_offsets, o_edges, e) == OP_OK);
        for (uint32_t i = 0; i < OLD; i++)
            set_own(g, i, 100 + i);
        assert(op_graph_set_composition(g, o_back, o_group, o_aff, OLD) == OP_OK);
        memset(&before, 0, sizeof(before));
        evaluate(g, OLD, &before, old_stamps);
        render(g, 11, &before);
        assert(op_graph_replace(g, NEW, n_offsets, n_edges, ne) == OP_OK);
        for (uint32_t i = 0; i < NEW; i++)
            set_own(g, i, i >= SHIFT ? 100 + i - SHIFT : 300 + i);
        assert(op_graph_set_composition_from(g, n_back, n_group, n_aff, NEW, previous) == OP_OK);
        memset(&after, 0, sizeof(after));
        for (uint32_t i = SHIFT; i < NEW; i++)
            after.resident[i] = before.resident[i - SHIFT];
        evaluate(g, NEW, &after, new_stamps);
        runs_of(g, 11 + SHIFT, &after, &p);
        assert(p.run_count == 1 && p.runs[0].input == 8 + SHIFT && p.runs[0].length == 3);
        assert(p.runs[0].key == armed.runs[0].key);
        op_graph_destroy(g);
    }
    {
        /* A host that renders members itself (a fused pass) records it: without the record
         * the members' rendered local stamps are stale and they split the run; with it the
         * run is back, as after op_graph_execute rendered them. */
        enum { S2 = 0, C2 = 6, COUNT2 = 12 };
        uint32_t offsets2[COUNT2 + 1], edges2[32], e2 = 0;
        uint32_t back2[COUNT2], group2[COUNT2];
        unsigned char aff2[COUNT2];
        for (uint32_t i = 0; i < COUNT2; i++) {
            offsets2[i] = e2;
            back2[i] = group2[i] = NONE;
            aff2[i] = 0;
            if (i >= C2) {
                if (i > C2)
                    edges2[e2++] = back2[i] = i - 1;
                edges2[e2++] = S2 + (i - C2);
                aff2[i] = 1;
            }
        }
        offsets2[COUNT2] = e2;
        op_graph_t *g2 = NULL;
        assert(op_graph_create(&g2) == OP_OK);
        assert(op_graph_replace(g2, COUNT2, offsets2, edges2, e2) == OP_OK);
        uint32_t members4[4] = {8, 9, 10, 11}, bad[2] = {8, COUNT2};
        /* No composition yet: nothing to record, any valid index accepted. */
        assert(op_graph_mark_rendered(g2, members4, 4) == OP_OK);
        for (uint32_t i = 0; i < COUNT2; i++)
            set_own(g2, i, 100 + i);
        assert(op_graph_set_composition(g2, back2, group2, aff2, COUNT2) == OP_OK);
        host h2 = {{0}, NULL};
        uint64_t stamps2[N];
        plan p2;
        evaluate(g2, COUNT2, &h2, stamps2);
        render(g2, 11, &h2);
        /* Sources of members 9 and 10 change; the host renders 7..11 in one pass. */
        set_own(g2, 3, 900);
        set_own(g2, 4, 901);
        set_own(g2, 7, 500);
        evaluate(g2, COUNT2, &h2, stamps2);
        for (uint32_t i = 7; i <= 11; i++)
            h2.resident[i] = stamps2[i];
        assert(op_graph_mark_rendered(g2, bad, 2) == OP_INVALID_ARGUMENT);
        assert(op_graph_mark_rendered(NULL, members4, 4) == OP_INVALID_ARGUMENT);
        assert(op_graph_mark_rendered(g2, NULL, 1) == OP_INVALID_ARGUMENT);
        /* The next edit of layer 7: 9 and 10 still carry the local stamps from before. */
        set_own(g2, 7, 501);
        evaluate(g2, COUNT2, &h2, stamps2);
        runs_of(g2, 11, &h2, &p2);
        assert(p2.run_count == 0);   /* 9 and 10 split it; 8 and 11 alone are too short */
        /* Recorded (as the host does after its pass), the run of four is back. */
        set_own(g2, 7, 500);
        evaluate(g2, COUNT2, &h2, stamps2);
        for (uint32_t i = 7; i <= 11; i++)
            h2.resident[i] = stamps2[i];
        uint32_t fused[5] = {7, 8, 9, 10, 11};
        assert(op_graph_mark_rendered(g2, fused, 5) == OP_OK);
        set_own(g2, 7, 502);
        evaluate(g2, COUNT2, &h2, stamps2);
        runs_of(g2, 11, &h2, &p2);
        assert(p2.run_count == 1 && p2.runs[0].length == 4 && p2.runs[0].input == 7);
        op_graph_destroy(g2);
    }
    puts("PASS test_suffix");
    return 0;
}
