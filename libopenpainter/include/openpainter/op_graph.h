/* Portable evaluation DAG. No Blender/Python/GPU pointers cross this ABI.
 * All arrays are copied synchronously. Node indices are stable for a topology.
 * Offsets has count+1 entries; edges are producer indices. Cycles are rejected.
 * A plan is postorder producer-before-consumer; zero resident stamp means evicted.
 */
#ifndef OP_GRAPH_H
#define OP_GRAPH_H
#include "op_api.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct op_graph op_graph_t;
op_status_t op_graph_create(op_graph_t **out);
void op_graph_destroy(op_graph_t *graph);
op_status_t op_graph_replace(op_graph_t *graph, uint32_t count, const uint32_t *offsets,
                             const uint32_t *edges, uint32_t edge_count);
op_status_t op_graph_evaluate(op_graph_t *graph, const uint64_t *own, const uint64_t *resident,
                              uint64_t *stamps, uint32_t *plan, uint32_t capacity,
                              uint32_t *out_count);
typedef struct op_graph_patch {
    uint32_t node, reserved;
    uint64_t value;
} op_graph_patch_t;
/* Atomic narrow updates to the native-owned local revisions. */
op_status_t op_graph_apply_patch(op_graph_t *graph, const op_graph_patch_t *patch, uint32_t count);
op_status_t op_graph_evaluate_current(op_graph_t *graph, const uint64_t *resident, uint64_t *stamps,
                                      uint32_t *plan, uint32_t capacity, uint32_t *out_count);
op_status_t op_graph_plan_root(op_graph_t *graph, uint32_t root, const uint64_t *resident,
                               const uint64_t *stamps, uint32_t *plan, uint32_t capacity,
                               uint32_t *out_count);
/* Execute a root batch with native dependency lifetime accounting. PIN must
 * protect a node against host eviction; a failed PIN must acquire nothing.
 * Each successful PIN gets exactly one UNPIN, even after submission failure.
 * UNPIN must drop the hold before returning an error. RENDER receives the
 * desired stamp; the host publishes it only after successful submission.
 * The requested root must remain available to the caller after UNPIN.
 * Callbacks run synchronously on the caller's context thread. No graph
 * mutation, destruction or nested execution is allowed during callbacks. */
enum { OP_GRAPH_PIN = 0, OP_GRAPH_RENDER = 1, OP_GRAPH_UNPIN = 2 };
typedef op_status_t (*op_graph_submit_fn)(void *user, uint32_t action, uint32_t node,
                                          uint64_t stamp);
op_status_t op_graph_execute(op_graph_t *graph, uint32_t root, const uint64_t *resident,
                             const uint64_t *stamps, op_graph_submit_fn submit, void *user,
                             uint32_t *rendered);
/* Choose the checkpoint below the lowest changed layer. UINT32_MAX means
 * no unit checkpoint. Current stamps describe previously rendered content,
 * independently of whether its resource remains resident. */
op_status_t op_graph_checkpoint(const op_graph_t *graph, const uint32_t *order,
                                const uint32_t *below, uint32_t count, const uint64_t *current,
                                const uint64_t *desired, uint32_t *keep);

/* Layer composition for affine suffix collapse. backdrop[i] is the node that i
 * composes over and grouped[i] the child composite a folder blends (UINT32_MAX:
 * none); each must be one of i's dependencies. affine[i] marks a node whose only
 * output is a per-texel affine function of its backdrop (op_ir_backdrop_affine).
 * Arrays are copied and cleared by op_graph_replace. Knowledge about nodes whose
 * composition is unchanged survives a repeated call, and a topology change through
 * op_graph_set_composition_from.
 *
 * With a composition, evaluation also derives each node's local stamp: its own
 * revision and everything it reads except its backdrop. Execution records the
 * local stamp each node was rendered with. */
op_status_t op_graph_set_composition(op_graph_t *graph, const uint32_t *backdrop,
                                     const uint32_t *grouped, const unsigned char *affine,
                                     uint32_t count);
/* op_graph_set_composition after a topology change: previous[i] is node i's index before
 * op_graph_replace (UINT32_MAX: a new node). A node whose composition maps onto the same
 * nodes keeps its local and rendered stamps, so an affine run armed before a layer was
 * added, removed or moved elsewhere is still planned (ABI 13). */
op_status_t op_graph_set_composition_from(op_graph_t *graph, const uint32_t *backdrop,
                                          const uint32_t *grouped, const unsigned char *affine,
                                          uint32_t count, const uint32_t *previous);
/* The first node of a channel's render order whose current stamp differs from
 * its desired one (UINT32_MAX: none). */
op_status_t op_graph_lowest_changed(const op_graph_t *graph, const uint32_t *order, uint32_t count,
                                    const uint64_t *current, const uint64_t *desired,
                                    uint32_t *node);
/* The results an edit of node composes over without changing them: a folder
 * node's own child composite, then the backdrop of node and of every folder
 * enclosing it, innermost first. Keeping them resident means repeated edits of
 * node never re-render anything below it (or, for a folder, inside it).
 * Requires a composition (op_graph_set_composition); otherwise none. */
op_status_t op_graph_spine(const op_graph_t *graph, uint32_t node, uint32_t *keep,
                           uint32_t capacity, uint32_t *count);
/* A run is a chain top -> ... -> bottom of affine nodes, each composing over the
 * next through its backdrop edge, that are stale only because that backdrop
 * changed: their local stamps equal the ones they were last rendered with. The
 * run's result is then an affine map of its input (the bottom's backdrop, or
 * UINT32_MAX for the chain's constant start) whose coefficients depend only on
 * the members' local stamps, which key identifies. Keys do not depend on node
 * indices, so they survive topology changes that keep a run's members. members[first .. first+length)
 * lists the run bottom-up. Runs are reported for the stale composition tree
 * under root, top-down: apply them in reverse order. Only runs of two or more
 * nodes are reported. Capacities of the node count always suffice. */
typedef struct op_graph_run {
    uint32_t top, bottom, input, length, first, reserved;
    uint64_t key;
} op_graph_run_t;
op_status_t op_graph_suffix_runs(op_graph_t *graph, uint32_t root, const uint64_t *current,
                                 const uint64_t *stamps, op_graph_run_t *runs,
                                 uint32_t run_capacity, uint32_t *run_count, uint32_t *members,
                                 uint32_t member_capacity, uint32_t *member_count);
/* The host rendered nodes itself, outside op_graph_execute (several units in one
 * pass): record that each was rendered with its current local stamp, as
 * op_graph_execute records for the nodes it renders, so they stay run members.
 * All indices are checked before any is recorded. OP_PENDING during execution;
 * nothing to record without a composition (ABI 14). */
op_status_t op_graph_mark_rendered(op_graph_t *g, const uint32_t *nodes, uint32_t count);
/* The runs under root that an edit of node's own content would produce, before
 * it happens: the same chains, members and keys op_graph_suffix_runs reports
 * once the edit is evaluated, provided nothing else changed. current/stamps
 * (both or neither) add results that are not resident, which the planner will
 * render again too. Lets a host arm a likely edit's transforms while idle. */
op_status_t op_graph_edit_runs(op_graph_t *graph, uint32_t root, uint32_t node,
                               const uint64_t *current, const uint64_t *stamps,
                               op_graph_run_t *runs, uint32_t run_capacity, uint32_t *run_count,
                               uint32_t *members, uint32_t member_capacity,
                               uint32_t *member_count);
#ifdef __cplusplus
}
#endif
#endif
