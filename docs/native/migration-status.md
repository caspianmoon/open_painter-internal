# Native migration ownership — ABI 8

Metal is the active platform scope. Windows and Vulkan implementation and device
qualification are deferred to the other machine. The independent standalone
application under `native/` is unchanged.

## Implemented C ownership

| Area | Portable native implementation | Blender boundary |
| --- | --- | --- |
| Program model | Owned typed IR, shader source, validated parameter banks and unique program/input revisions (`op_program.h`) | Copies socket values, image metadata and object transforms |
| Compiler | Scalar/vector operations, implicit conversions, image alpha handling, packed channels, normal maps, imports, all 28 blend modes including coverage and normal composition, all 30 pointwise effects, mask levels/inversion/folding, 71 procedural families (`op_ir.h`) | Resolves persisted recipe bindings into numeric IR |
| Composition | Ordered layers, nested isolated/passthrough folders, per-channel backdrops and grouped inputs (`op_stack.h`) | Snapshots included recipe units and stable keys |
| Evaluation | Copied dependency DAG, atomic narrow revision patches, cycle rejection, dependency stamps, root plans, checkpoint selection, batch submission and last-consumer dependency holds (`op_graph.h`) | Binds host resources and submits each native-selected pass |
| Channel evaluation | Accepted channel descriptors, exact constant comparison, native revision/target/configuration change selection, ordered dispatch and atomic batch acceptance (`op_channels.h`) | Snapshots root bindings and opaque target IDs; submits staged host work |
| GPU submission | Compiled CPython host-GPU adapter validates feedback hazards, binds framebuffer/uniforms/samplers, submits draw and restores framebuffer context | Blender owns its Metal device and command queue |
| Resources | Texture reuse, leases, pool limits, LRU eviction and retained-result, filter and brush-history budgets (`op_pool.h`, `op_cache.h`) | Holds GPU API objects by opaque integer tokens |
| Revisions | Bounded immutable eight-channel resource sets, checked leases, stage/commit/abort, preflight ordering, multichannel mip submission and exhaustive rollback (`op_revision.h`) | Prepares public GPU attachments and submits copy commands selected by C |
| Filters | Filter shaders, procedural displacement fields, Gaussian/box coefficient generation, morphology pass plans and repeat bounds (`op_filter.h`) | Copies evaluated mesh loops and submits passes |
| Brush | Batched stamp frames, pressure/flow/alpha coverage, bounds and resampling (`op_paint.h`) | Captures Blender input/rays and persists the existing stroke recipe |
| Geometry | Owned bounds index, UV rasterization/interpolation, tangent frames, edge adjacency, closest-triangle distance, UV clipping windows, exact padding donors, owned surface/anchor resolution, cross-target selection, UV island connectivity and stable IDs, border extraction, seam pairing/centroids, closest-seam bands/selection, island bounds, projection Jacobians and surface reach walk (`op_geometry.h`, `op_paint.h`, `op_reach.h`, `op_seam.h`, `op_topology.h`) | Extracts evaluated Blender mesh/UV/selection snapshots |
| Binding | Limited CPython ABI 3.11+, copied input buffers, owned capsules, exception mapping, GIL release for compilation/raster batches | Uses public `gpu` methods on Blender's context thread |

There is no independent Metal device or external texture import. Host API calls
are implemented in compiled C using public Python and GPU APIs. The portable
library does not depend on Python, Blender headers, RNA addresses, private GPU
layouts or unexported Blender symbols.

## Batch and blend execution

Layer channels enter the IR as one typed blend operation. C owns separable,
nonseparable and normal blend formulas, transparent premultiplied composition,
coverage clamping and opaque-backdrop specialization. The legacy translator
expands the original Python math only when explicitly selected for diagnosis.
All 28 modes are compared against independent NumPy and legacy GPU results.

C batches protect both rendered dependencies and already-resident boundary
inputs before submission. Edge use counts release each input after its last
consumer, including repeated/shared edges. Every successful acquire receives
one release on failure; cleanup continues after a release error. Graph mutation,
close and nested execution are rejected during submission. Versioned graph
capsule tags reject older module layouts before accessing their payload. Host rendering rejects
an unexpected missing dependency rather than falling into recursive evaluation.
The retained-cache budget is re-applied after transient batch holds are released.

## Effect and mask semantics

Pointwise effects enter the native compiler as one typed operation over RGB;
alpha is preserved by the caller. C owns all 30 formulas, effect opacity, enabled
state, tie-to-even quantization, hue conversions and normal handling. Python
copies named scalar parameters and packs vector/matrix inputs. Mask entries
also use one native operation for levels, inversion, strength and first-entry
or subsequent blending. Input types, ordering and operation names are validated
before shader source is returned. See [semantic IR contracts](semantic-ir.md).

The legacy translator expands independent Python formulas for diagnosis. The
Metal comparison covers 90 effect cases and 30 mask-fold cases, including exact
threshold/rounding boundaries, disabled effects and degenerate normals. Eight
production edits to matrix, bias, gamma, enabled, strength and mask controls
reuse topology and compiled shaders and match the legacy executor.

## Channel evaluation

C retains the accepted description of each of the eight outputs and chooses
changed channels from native graph stamps or exact constant values, target
registry tokens, resolution, precision and padding. Requests are copied and
validated as one bounded batch before callbacks run. Unrequested channels retain
their accepted state. Requested changed channels render in order; one successful
material commit accepts the whole batch. A failed render or commit accepts none.
Nested evaluation, graph adoption, reset and close are rejected during dispatch.

Host mirrors and publication target maps are allocated before visible writes.
Scratch retirement follows channel acceptance; a cleanup failure preserves the
accepted revision and retries cleanup without republishing it. Metal uses this
native channel driver. The explicitly selected pixel publication path retains
its existing Python time budget and uniform/fused compatibility behavior.

## UV topology and seam ownership

C owns UV connectivity with stable union representatives, assigned-material borders,
manifold seam pairing, face centroids, compact island IDs, global closest-seam
selection and covered-island bounds. Edge keys compare 64-bit vertex pairs without
integer multiplication. Native inputs and outputs are owned copies; no evaluated
mesh or RNA pointer escapes the host snapshot boundary. Allocation and input limits
are explicit, and errors return no partial topology.

The independent NumPy paths remain available for differential checks. Random
meshes, a 5,184-face grid, material subsets, non-manifold/degenerate edges, UV
continuity thresholds and large vertex IDs exercise connectivity and ordering.
An evaluated Blender mesh checks labels, borders, seam sides, nearest flags and
bounds alongside rasterization, Jacobians and projection behavior.

## Authority and recovery

The existing `.blend` recipe and durable vector stroke records remain project
truth. Native graphs and program banks are evaluated runtime counterparts.
Load, undo and redo close the runtime generation before current recipe data is
rebound. Export/render/save continue through explicit pixel synchronization;
interactive Metal publication never uses that boundary.

The C publication driver stages all changed outputs first. A compiled capsule owns
the revision stream and rejects close/reentrant publication during callbacks. Resource allocation, shader
compilation and mip preparation precede visible writes. Publication runs on one
host context thread without yielding. A recoverable copy failure restores every
touched channel from GPU snapshots and does not commit the native revision.
Context/device loss that also prevents restoration is an explicit evaluation
error; it cannot be represented as a successfully displayed revision.

Image texture eviction is detected through public GPU wrapper identity. The accepted
native revision restores the recreated textures and every mip before viewport drawing
or during the normal session tick, without a CPU pixel copy or revision increment.

Native frame leases describe resources ready for ordered host-queue consumption.
They are not CPU readback completion fences. All asynchronous resource retirement
is subject to Blender's public GPU ownership/queue contract.

## Remaining boundaries and qualification

The Blender recipe frontend, image/source snapshot extraction,
CPU compatibility boundaries, per-unit host binding and
save/export adapters still contain Python logic. Some reference CPU inputs (for
example legacy sine noise and unsupported custom shader groups) remain existing
CPU source boundaries. They are not secretly counted as native GPU kernels.
The old constant-fill C document API is a separate bounded reference fixture;
it is not the production representation of arbitrary layered materials.

Consequently diagnostics retain `fully_migrated: false`; the table above records
actual ownership instead of relabeling the entire existing runtime as C.
Completing the original all-native ownership requirement still entails removing
the remaining evaluation orchestration and feature semantics from those host
boundaries. The original plan's sustained latency protocol, broad device-loss
qualification and other-machine platforms are separate, unfinished gates.

The main remaining code boundaries are `evaluation/shader_graph.py` (recipe,
mask source/group traversal and effect-chain binding), `evaluation/engine/runtime.py` (source
binding, per-pass host dispatch and persistence adapters). `geometry/seams.py` copies
evaluated mesh data and binds native topology/correspondence results. Native
batch execution owns the dependency pass loop and its input lifetimes; the
native channel driver owns output selection, dispatch and acceptance.
