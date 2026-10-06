#include "openpainter/op_graph.h"
#include <stdlib.h>
#include <string.h>
#define MAX_NODES 65536u
#define MAX_EDGES 1048576u
struct op_graph {
    uint32_t count, edge_count;
    int executing;
    uint32_t *offsets, *edges, *order, *work;
    uint64_t *own;
    unsigned char *needed;
    /* Optional composition (op_graph_set_composition): reset with the topology. */
    uint32_t *backdrop, *grouped, *pending;
    unsigned char *affine, *state;
    uint64_t *local, *rendered;
    /* The composition a topology replaced, until the next one is set: what op_graph_set_composition_from
     * carries over to the nodes it maps back to their previous indices. */
    uint32_t previous_count;
    uint32_t *previous_backdrop, *previous_grouped;
    unsigned char *previous_affine;
    uint64_t *previous_local, *previous_rendered;
};
static void clear_previous(op_graph_t *g) {
    free(g->previous_backdrop);
    free(g->previous_grouped);
    free(g->previous_affine);
    free(g->previous_local);
    free(g->previous_rendered);
    g->previous_backdrop = g->previous_grouped = NULL;
    g->previous_affine = NULL;
    g->previous_local = g->previous_rendered = NULL;
    g->previous_count = 0;
}
static void clear_composition(op_graph_t *g);
/* A topology replaces the composition; what it knew is kept for op_graph_set_composition_from. */
static void stash_composition(op_graph_t *g) {
    clear_previous(g);
    if (g->rendered && g->local && g->backdrop && g->grouped && g->affine) {
        g->previous_count = g->count;
        g->previous_backdrop = g->backdrop;
        g->previous_grouped = g->grouped;
        g->previous_affine = g->affine;
        g->previous_local = g->local;
        g->previous_rendered = g->rendered;
        g->backdrop = g->grouped = NULL;
        g->affine = NULL;
        g->local = g->rendered = NULL;
    }
    clear_composition(g);
}
static void clear_composition(op_graph_t *g) {
    free(g->backdrop);
    free(g->grouped);
    free(g->pending);
    free(g->affine);
    free(g->state);
    free(g->local);
    free(g->rendered);
    g->backdrop = g->grouped = g->pending = NULL;
    g->affine = g->state = NULL;
    g->local = g->rendered = NULL;
}
op_status_t op_graph_create(op_graph_t **out) {
    if (!out)
        return OP_INVALID_ARGUMENT;
    *out = calloc(1, sizeof(op_graph_t));
    return *out ? OP_OK : OP_RESOURCE_EXHAUSTED;
}
void op_graph_destroy(op_graph_t *g) {
    if (!g || g->executing)
        return;
    free(g->offsets);
    free(g->edges);
    free(g->order);
    free(g->work);
    free(g->needed);
    free(g->own);
    clear_composition(g);
    clear_previous(g);
    free(g);
}
op_status_t op_graph_replace(op_graph_t *g, uint32_t count, const uint32_t *offsets,
                             const uint32_t *edges, uint32_t edge_count) {
    uint32_t *copy_offsets = NULL, *copy_edges = NULL, *order = NULL, *stack = NULL, *cursor = NULL;
    unsigned char *mark = NULL;
    uint64_t *own = NULL;
    uint32_t used = 0;
    op_status_t status = OP_RESOURCE_EXHAUSTED;
    if (!g || !offsets || (edge_count && !edges) || count > MAX_NODES || edge_count > MAX_EDGES)
        return OP_INVALID_ARGUMENT;
    if (g->executing)
        return OP_PENDING;
    if (offsets[0] || offsets[count] != edge_count)
        return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < count; i++)
        if (offsets[i] > offsets[i + 1])
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < edge_count; i++)
        if (edges[i] >= count)
            return OP_VALIDATION_FAILED;
    copy_offsets = malloc(((size_t)count + 1) * sizeof(uint32_t));
    copy_edges = malloc(((size_t)edge_count + 1) * sizeof(uint32_t));
    order = malloc(((size_t)count + 1) * sizeof(uint32_t));
    stack = malloc(((size_t)count + 1) * sizeof(uint32_t));
    cursor = calloc((size_t)count + 1, sizeof(uint32_t));
    mark = calloc((size_t)count + 1, 1);
    own = calloc((size_t)count + 1, sizeof(uint64_t));
    if (!copy_offsets || !copy_edges || !order || !stack || !cursor || !mark || !own)
        goto done;
    memcpy(copy_offsets, offsets, ((size_t)count + 1) * sizeof(uint32_t));
    if (edge_count)
        memcpy(copy_edges, edges, (size_t)edge_count * sizeof(uint32_t));
    /* Iterative DFS: adversarial deep stacks cannot exhaust the C call stack. */
    for (uint32_t root = 0; root < count; root++) {
        uint32_t depth = 0;
        if (mark[root])
            continue;
        stack[depth++] = root;
        mark[root] = 1;
        cursor[root] = offsets[root];
        while (depth) {
            uint32_t node = stack[depth - 1];
            if (cursor[node] < offsets[node + 1]) {
                uint32_t next = edges[cursor[node]++];
                if (mark[next] == 1) {
                    status = OP_VALIDATION_FAILED;
                    goto done;
                }
                if (!mark[next]) {
                    mark[next] = 1;
                    cursor[next] = offsets[next];
                    stack[depth++] = next;
                }
            } else {
                order[used++] = node;
                mark[node] = 2;
                depth--;
            }
        }
    }
    free(g->offsets);
    free(g->edges);
    free(g->order);
    free(g->work);
    free(g->needed);
    free(g->own);
    stash_composition(g);
    g->own = own;
    own = NULL;
    g->work = stack;
    g->needed = mark;
    stack = NULL;
    mark = NULL;
    g->count = count;
    g->edge_count = edge_count;
    g->offsets = copy_offsets;
    g->edges = copy_edges;
    g->order = order;
    copy_offsets = copy_edges = order = NULL;
    status = OP_OK;
done:
    free(copy_offsets);
    free(copy_edges);
    free(order);
    free(stack);
    free(cursor);
    free(mark);
    free(own);
    return status;
}
static uint64_t mix(uint64_t h, uint64_t v) {
    v ^= v >> 30;
    v *= UINT64_C(0xbf58476d1ce4e5b9);
    v ^= v >> 27;
    v *= UINT64_C(0x94d049bb133111eb);
    v ^= v >> 31;
    return (h ^ v) * UINT64_C(0x100000001b3);
}
op_status_t op_graph_evaluate(op_graph_t *g, const uint64_t *own, const uint64_t *resident,
                              uint64_t *stamps, uint32_t *plan, uint32_t capacity,
                              uint32_t *out_count) {
    if (!g || !out_count || (g->count && (!own || !resident || !stamps || !plan)))
        return OP_INVALID_ARGUMENT;
    *out_count = 0;
    if (capacity < g->count)
        return OP_RESOURCE_EXHAUSTED;
    for (uint32_t j = 0; j < g->count; j++) {
        uint32_t i = g->order[j];
        uint64_t stamp = mix(UINT64_C(0xcbf29ce484222325), own[i]);
        for (uint32_t d = g->offsets[i]; d < g->offsets[i + 1]; d++)
            stamp = mix(stamp, stamps[g->edges[d]]);
        stamps[i] = stamp ? stamp : 1;
        if (resident[i] != stamps[i])
            plan[(*out_count)++] = i;
        if (g->local) {
            /* Everything the node reads except the one backdrop edge it composes
             * over: unchanged while only the layers below it change. */
            uint64_t local = mix(UINT64_C(0x84222325cbf29ce4), own[i]);
            int skipped = g->backdrop[i] == UINT32_MAX;
            for (uint32_t d = g->offsets[i]; d < g->offsets[i + 1]; d++) {
                if (!skipped && g->edges[d] == g->backdrop[i]) {
                    skipped = 1;
                    continue;
                }
                local = mix(local, stamps[g->edges[d]]);
            }
            g->local[i] = local ? local : 1;
        }
    }
    return OP_OK;
}

op_status_t op_graph_plan_root(op_graph_t *g, uint32_t root, const uint64_t *resident,
                               const uint64_t *stamps, uint32_t *plan, uint32_t capacity,
                               uint32_t *out_count) {
    uint32_t depth = 0;
    if (!g || root >= g->count || !resident || !stamps || !plan || !out_count)
        return OP_INVALID_ARGUMENT;
    *out_count = 0;
    if (capacity < g->count)
        return OP_RESOURCE_EXHAUSTED;
    memset(g->needed, 0, g->count);
    if (resident[root] == stamps[root])
        return OP_OK;
    g->needed[root] = 1;
    g->work[depth++] = root;
    while (depth) {
        uint32_t node = g->work[--depth];
        for (uint32_t d = g->offsets[node]; d < g->offsets[node + 1]; d++) {
            uint32_t next = g->edges[d];
            if (!g->needed[next] && resident[next] != stamps[next]) {
                g->needed[next] = 1;
                g->work[depth++] = next;
            }
        }
    }
    for (uint32_t j = 0; j < g->count; j++)
        if (g->needed[g->order[j]])
            plan[(*out_count)++] = g->order[j];
    return OP_OK;
}

op_status_t op_graph_apply_patch(op_graph_t *g, const op_graph_patch_t *patch, uint32_t count) {
    if (!g || count > MAX_NODES || (count && !patch))
        return OP_INVALID_ARGUMENT;
    if (g->executing)
        return OP_PENDING;
    for (uint32_t i = 0; i < count; i++)
        if (patch[i].node >= g->count || patch[i].reserved)
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < count; i++)
        g->own[patch[i].node] = patch[i].value;
    return OP_OK;
}
op_status_t op_graph_evaluate_current(op_graph_t *g, const uint64_t *resident, uint64_t *stamps,
                                      uint32_t *plan, uint32_t capacity, uint32_t *out_count) {
    if (!g)
        return OP_INVALID_ARGUMENT;
    return op_graph_evaluate(g, g->own, resident, stamps, plan, capacity, out_count);
}

op_status_t op_graph_checkpoint(const op_graph_t *g, const uint32_t *order, const uint32_t *below,
                                uint32_t count, const uint64_t *current, const uint64_t *desired,
                                uint32_t *keep) {
    if (!g || !keep || count > g->count || (count && (!order || !below || !current || !desired)))
        return OP_INVALID_ARGUMENT;
    *keep = UINT32_MAX;
    for (uint32_t i = 0; i < count; i++)
        if (order[i] >= g->count || (below[i] != UINT32_MAX && below[i] >= g->count))
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < count; i++)
        if (current[order[i]] != desired[order[i]]) {
            *keep = below[i];
            break;
        }
    return OP_OK;
}

op_status_t op_graph_execute(op_graph_t *g, uint32_t root, const uint64_t *resident,
                             const uint64_t *stamps, op_graph_submit_fn submit, void *user,
                             uint32_t *rendered) {
    if (!g || !resident || !stamps || !submit || !rendered || root >= g->count)
        return OP_INVALID_ARGUMENT;
    *rendered = 0;
    if (g->executing)
        return OP_PENDING;
    for (uint32_t i = 0; i < g->count; i++)
        if (!stamps[i])
            return OP_VALIDATION_FAILED;
    uint32_t *plan = malloc((size_t)g->count * sizeof(*plan));
    uint32_t *uses = calloc(g->count, sizeof(*uses));
    unsigned char *pinned = calloc(g->count, 1);
    uint64_t *desired = malloc((size_t)g->count * sizeof(*desired));
    op_status_t status = OP_RESOURCE_EXHAUSTED;
    uint32_t count = 0;
    if (!plan || !uses || !pinned || !desired)
        goto done;
    status = op_graph_plan_root(g, root, resident, stamps, plan, g->count, &count);
    if (status)
        goto done;
    memcpy(desired, stamps, (size_t)g->count * sizeof(*desired));
    /* Account for every edge consumed by the selected batch, including inputs
     * already resident at its boundary. Duplicate edges count independently. */
    uses[root] = 1;
    for (uint32_t i = 0; i < count; i++)
        for (uint32_t e = g->offsets[plan[i]]; e < g->offsets[plan[i] + 1]; e++)
            uses[g->edges[e]]++;
    g->executing = 1;
    for (uint32_t i = 0; i < g->count; i++) {
        if (!uses[i])
            continue;
        status = submit(user, OP_GRAPH_PIN, i, desired[i]);
        if (status)
            goto release;
        pinned[i] = 1;
    }
    for (uint32_t i = 0; i < count; i++) {
        uint32_t node = plan[i];
        status = submit(user, OP_GRAPH_RENDER, node, desired[node]);
        if (status)
            goto release;
        if (g->rendered)
            g->rendered[node] = g->local[node];
        (*rendered)++;
        for (uint32_t e = g->offsets[node]; e < g->offsets[node + 1]; e++) {
            uint32_t dependency = g->edges[e];
            if (--uses[dependency] == 0) {
                pinned[dependency] = 0;
                status = submit(user, OP_GRAPH_UNPIN, dependency, desired[dependency]);
                if (status)
                    goto release;
            }
        }
    }
release:
    /* Never stop cleanup after an error. Keep the original submission status. */
    for (uint32_t i = g->count; i-- > 0;) {
        if (pinned[i]) {
            op_status_t cleanup = submit(user, OP_GRAPH_UNPIN, i, desired[i]);
            if (!status)
                status = cleanup;
        }
    }
    g->executing = 0;
done:
    free(desired);
    free(pinned);
    free(uses);
    free(plan);
    return status;
}

static int has_edge(const op_graph_t *g, uint32_t node, uint32_t target) {
    for (uint32_t d = g->offsets[node]; d < g->offsets[node + 1]; d++)
        if (g->edges[d] == target)
            return 1;
    return 0;
}

static op_status_t set_composition(op_graph_t *g, const uint32_t *backdrop, const uint32_t *grouped,
                                   const unsigned char *affine, uint32_t count, const uint32_t *previous) {
    if (!g || (count && (!backdrop || !grouped || !affine)))
        return OP_INVALID_ARGUMENT;
    if (g->executing)
        return OP_PENDING;
    if (count != g->count)
        return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < count; i++) {
        if (backdrop[i] != UINT32_MAX && (backdrop[i] >= count || !has_edge(g, i, backdrop[i])))
            return OP_VALIDATION_FAILED;
        if (grouped[i] != UINT32_MAX && (grouped[i] >= count || !has_edge(g, i, grouped[i])))
            return OP_VALIDATION_FAILED;
    }
    size_t n = (size_t)count + 1;
    uint32_t *b = malloc(n * sizeof(*b)), *p = malloc(n * sizeof(*p));
    /* The run walk pushes a node once per composition edge into it, plus the root. */
    uint32_t *pending = malloc((2 * n + 1) * sizeof(*pending));
    unsigned char *a = malloc(n), *state = calloc(n, 1);
    uint64_t *local = calloc(n, sizeof(*local)), *rendered = calloc(n, sizeof(*rendered));
    /* Before: the composition this one follows (the one a topology replaced, else the current). */
    int stashed = g->previous_rendered != NULL;
    uint32_t before = stashed ? g->previous_count : (g->rendered ? g->count : 0);
    const uint32_t *before_backdrop = stashed ? g->previous_backdrop : g->backdrop;
    const uint32_t *before_grouped = stashed ? g->previous_grouped : g->grouped;
    const unsigned char *before_affine = stashed ? g->previous_affine : g->affine;
    const uint64_t *before_local = stashed ? g->previous_local : g->local;
    const uint64_t *before_rendered = stashed ? g->previous_rendered : g->rendered;
    uint32_t *moved = previous && before ? malloc((size_t)before * sizeof(*moved)) : NULL;
    if (!b || !p || !pending || !a || !state || !local || !rendered || (previous && before && !moved)) {
        free(b);
        free(p);
        free(pending);
        free(a);
        free(state);
        free(local);
        free(rendered);
        free(moved);
        return OP_RESOURCE_EXHAUSTED;
    }
    if (count) {
        memcpy(b, backdrop, count * sizeof(*b));
        memcpy(p, grouped, count * sizeof(*p));
        for (uint32_t i = 0; i < count; i++)
            a[i] = affine[i] ? 1 : 0;
    }
    if (previous && before) {
        /* Nodes mapped back to their previous indices keep what was known about them while
         * their composition (backdrop, child composite, affinity) maps onto the same nodes. */
        for (uint32_t k = 0; k < before; k++)
            moved[k] = UINT32_MAX;
        for (uint32_t i = 0; i < count; i++)
            if (previous[i] < before)
                moved[previous[i]] = i;
        for (uint32_t i = 0; i < count; i++) {
            uint32_t k = previous[i];
            if (k >= before)
                continue;
            uint32_t was_b = before_backdrop[k], was_p = before_grouped[k];
            uint32_t now_b = was_b == UINT32_MAX ? UINT32_MAX : (was_b < before ? moved[was_b] : UINT32_MAX - 1);
            uint32_t now_p = was_p == UINT32_MAX ? UINT32_MAX : (was_p < before ? moved[was_p] : UINT32_MAX - 1);
            if (now_b == b[i] && now_p == p[i] && before_affine[k] == a[i]) {
                local[i] = before_local[k];
                rendered[i] = before_rendered[k];
            }
        }
    } else if (!previous && !stashed && g->rendered) {
        /* Preserve what is already known about a node whose composition did not change. */
        for (uint32_t i = 0; i < count; i++)
            if (g->backdrop[i] == b[i] && g->grouped[i] == p[i] && g->affine[i] == a[i]) {
                local[i] = g->local[i];
                rendered[i] = g->rendered[i];
            }
    }
    free(moved);
    clear_composition(g);
    clear_previous(g);
    g->backdrop = b;
    g->grouped = p;
    g->pending = pending;
    g->affine = a;
    g->state = state;
    g->local = local;
    g->rendered = rendered;
    return OP_OK;
}

op_status_t op_graph_set_composition(op_graph_t *g, const uint32_t *backdrop,
                                     const uint32_t *grouped, const unsigned char *affine,
                                     uint32_t count) {
    return set_composition(g, backdrop, grouped, affine, count, NULL);
}

op_status_t op_graph_set_composition_from(op_graph_t *g, const uint32_t *backdrop,
                                          const uint32_t *grouped, const unsigned char *affine,
                                          uint32_t count, const uint32_t *previous) {
    if (count && !previous)
        return OP_INVALID_ARGUMENT;
    return set_composition(g, backdrop, grouped, affine, count, previous);
}

/* Run walk shared by the planner and edit prediction. stale[v] bit 0: the node
 * must be rendered again; bit 1: it reads a change through something other than
 * its backdrop. A member is stale only through its backdrop and was rendered
 * with its current local stamp. */
enum { RUN_STALE = 1, RUN_DIRTY = 2 };
static int run_member(const op_graph_t *g, uint32_t v, const unsigned char *state) {
    return state[v] == RUN_STALE && g->affine[v] && g->rendered[v] &&
           g->rendered[v] == g->local[v] && !g->needed[v];
}

static op_status_t plan_runs(op_graph_t *g, uint32_t root, const unsigned char *state,
                             op_graph_run_t *runs, uint32_t run_capacity, uint32_t *run_count,
                             uint32_t *members, uint32_t member_capacity, uint32_t *member_count) {
    uint32_t depth = 0;
    memset(g->needed, 0, g->count);
    g->pending[depth++] = root;
    while (depth) {
        uint32_t u = g->pending[--depth];
        if (g->needed[u] || !state[u])
            continue;
        uint32_t start = *member_count, length = 0, v = u;
        while (v != UINT32_MAX && run_member(g, v, state)) {
            if (*member_count >= member_capacity)
                return OP_RESOURCE_EXHAUSTED;
            members[(*member_count)++] = v;
            length++;
            v = g->backdrop[v];
        }
        if (length >= 2) {
            if (*run_count >= run_capacity)
                return OP_RESOURCE_EXHAUSTED;
            /* The members' local stamps alone: they carry each member's identity and
             * everything it reads, and nothing about node indices, so an armed
             * transform stays valid across topology changes that keep its members. */
            uint64_t key = mix(UINT64_C(0x9e3779b97f4a7c15), length);
            for (uint32_t i = 0; i < length / 2; i++) {
                uint32_t t = members[start + i];
                members[start + i] = members[start + length - 1 - i];
                members[start + length - 1 - i] = t;
            }
            for (uint32_t i = start; i < start + length; i++) {
                g->needed[members[i]] = 1;
                key = mix(key, g->local[members[i]]);
            }
            op_graph_run_t *run = &runs[(*run_count)++];
            run->top = u;
            run->bottom = members[start];
            run->input = v;
            run->length = length;
            run->first = start;
            run->reserved = 0;
            run->key = key ? key : 1;
            if (v != UINT32_MAX)
                g->pending[depth++] = v;
        } else {
            *member_count = start;
            g->needed[u] = 1;
            /* Rendered normally: look for runs below it. */
            if (g->backdrop[u] != UINT32_MAX && !g->needed[g->backdrop[u]])
                g->pending[depth++] = g->backdrop[u];
            if (g->grouped[u] != UINT32_MAX && !g->needed[g->grouped[u]])
                g->pending[depth++] = g->grouped[u];
        }
    }
    return OP_OK;
}

op_status_t op_graph_suffix_runs(op_graph_t *g, uint32_t root, const uint64_t *current,
                                 const uint64_t *stamps, op_graph_run_t *runs,
                                 uint32_t run_capacity, uint32_t *run_count, uint32_t *members,
                                 uint32_t member_capacity, uint32_t *member_count) {
    if (!g || !current || !stamps || !run_count || !member_count || root >= g->count ||
        (run_capacity && !runs) || (member_capacity && !members))
        return OP_INVALID_ARGUMENT;
    *run_count = *member_count = 0;
    if (g->executing)
        return OP_PENDING;
    if (!g->backdrop)
        return OP_OK;
    for (uint32_t i = 0; i < g->count; i++)
        g->state[i] = current[i] != stamps[i] ? RUN_STALE : 0;
    return plan_runs(g, root, g->state, runs, run_capacity, run_count, members, member_capacity,
                     member_count);
}

op_status_t op_graph_mark_rendered(op_graph_t *g, const uint32_t *nodes, uint32_t count) {
    if (!g || (count && !nodes))
        return OP_INVALID_ARGUMENT;
    if (g->executing)
        return OP_PENDING;
    for (uint32_t i = 0; i < count; i++)
        if (nodes[i] >= g->count)
            return OP_INVALID_ARGUMENT;
    if (g->rendered)
        for (uint32_t i = 0; i < count; i++)
            g->rendered[nodes[i]] = g->local[nodes[i]];
    return OP_OK;
}

op_status_t op_graph_edit_runs(op_graph_t *g, uint32_t root, uint32_t node, const uint64_t *current,
                               const uint64_t *stamps, op_graph_run_t *runs, uint32_t run_capacity,
                               uint32_t *run_count, uint32_t *members, uint32_t member_capacity,
                               uint32_t *member_count) {
    if (!g || !run_count || !member_count || root >= g->count || node >= g->count ||
        (!current != !stamps) || (run_capacity && !runs) || (member_capacity && !members))
        return OP_INVALID_ARGUMENT;
    *run_count = *member_count = 0;
    if (g->executing)
        return OP_PENDING;
    if (!g->backdrop)
        return OP_OK;
    /* Producers precede consumers in order: one pass finds everything an edit
     * of node reaches, and what reaches it other than through a backdrop. */
    memset(g->state, 0, g->count);
    for (uint32_t j = 0; j < g->count; j++) {
        uint32_t v = g->order[j];
        if (v == node) {
            g->state[v] = RUN_STALE | RUN_DIRTY;
            continue;
        }
        int skipped = g->backdrop[v] == UINT32_MAX;
        for (uint32_t d = g->offsets[v]; d < g->offsets[v + 1]; d++) {
            uint32_t input = g->edges[d];
            if (!skipped && input == g->backdrop[v]) {
                skipped = 1;
                if (g->state[input])
                    g->state[v] |= RUN_STALE;
                continue;
            }
            if (g->state[input])
                g->state[v] = RUN_STALE | RUN_DIRTY;
        }
        /* A result that is not resident is rendered again whatever the edit: the
         * planner will treat it as stale, so prediction does too. */
        if (current && current[v] != stamps[v])
            g->state[v] |= RUN_STALE;
    }
    return plan_runs(g, root, g->state, runs, run_capacity, run_count, members, member_capacity,
                     member_count);
}

op_status_t op_graph_lowest_changed(const op_graph_t *g, const uint32_t *order, uint32_t count,
                                    const uint64_t *current, const uint64_t *desired,
                                    uint32_t *node) {
    if (!g || !node || count > g->count || (count && (!order || !current || !desired)))
        return OP_INVALID_ARGUMENT;
    *node = UINT32_MAX;
    for (uint32_t i = 0; i < count; i++)
        if (order[i] >= g->count)
            return OP_VALIDATION_FAILED;
    for (uint32_t i = 0; i < count; i++)
        if (current[order[i]] != desired[order[i]]) {
            *node = order[i];
            break;
        }
    return OP_OK;
}

op_status_t op_graph_spine(const op_graph_t *g, uint32_t node, uint32_t *keep, uint32_t capacity,
                           uint32_t *count) {
    if (!g || !count || node >= g->count || (capacity && !keep))
        return OP_INVALID_ARGUMENT;
    *count = 0;
    if (!g->backdrop)
        return OP_OK;
    uint32_t *above = malloc(((size_t)g->count + 1) * sizeof(*above));
    uint32_t *folder = malloc(((size_t)g->count + 1) * sizeof(*folder));
    if (!above || !folder) {
        free(above);
        free(folder);
        return OP_RESOURCE_EXHAUSTED;
    }
    memset(above, 0xff, (size_t)g->count * sizeof(*above));
    memset(folder, 0xff, (size_t)g->count * sizeof(*folder));
    for (uint32_t i = 0; i < g->count; i++) {
        /* A passthrough folder and its first child share a backdrop; the walk
         * below only ever steps up from a chain member, never from that backdrop. */
        if (g->backdrop[i] != UINT32_MAX)
            above[g->backdrop[i]] = i;
        if (g->grouped[i] != UINT32_MAX)
            folder[g->grouped[i]] = i;
    }
    op_status_t status = OP_OK;
    /* A folder's own edit (its opacity, its blend) composes over its child composite as over
     * its backdrop, and changes neither. */
    if (g->grouped[node] != UINT32_MAX) {
        if (*count >= capacity)
            status = OP_RESOURCE_EXHAUSTED;
        else
            keep[(*count)++] = g->grouped[node];
    }
    uint32_t v = status == OP_OK ? node : UINT32_MAX, steps = 0;
    while (v != UINT32_MAX) {
        if (g->backdrop[v] != UINT32_MAX) {
            if (*count >= capacity) {
                status = OP_RESOURCE_EXHAUSTED;
                break;
            }
            keep[(*count)++] = g->backdrop[v];
        }
        /* Up the sibling chain to its top, then to the folder composing it. */
        uint32_t top = v;
        while (above[top] != UINT32_MAX && folder[top] == UINT32_MAX && steps++ <= g->count)
            top = above[top];
        if (steps > g->count) {
            status = OP_VALIDATION_FAILED;   /* composition edges form a loop */
            break;
        }
        v = folder[top];
    }
    free(above);
    free(folder);
    return status;
}
