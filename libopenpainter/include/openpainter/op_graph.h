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
#ifdef __cplusplus
}
#endif
#endif
