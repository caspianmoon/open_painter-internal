# Paired Metal performance — ABI 10 / 0.14.0

The C backend did **not** deliver a speedup on this 2048² opacity-edit workload.
C had lower material-feedback cadence and higher p95 receipt-to-draw latency in
four of five pairs. Its evaluation call was slower in all five pairs. One pair
favored C in viewport responsiveness; it is retained in the results.

The median paired changes were **29.9% fewer new material revisions/second**,
**41.6% higher p95 receipt-to-draw latency**, and **198.5% higher p95 evaluation
call time**. This is evidence of a regression for this tested workload, not a
claim about every feature, machine or C implementation.

## Measured results

Each aggregate column is the median of five run-level measurements. For latency,
that means the median of five p95/p99 values, not a percentile obtained by pooling
all events. Paired changes are calculated within each pair before taking their
median; they need not equal the ratio of the two aggregate columns.

| Metric | Legacy | C | Median paired C change | C better pairs |
| --- | --- | --- | --- | --- |
| New material revisions/s (higher is better) | 16.23 | 11.86 | -29.9% | 1/5 |
| RNA callback p95, ms | 1.23 | 1.20 | -3.1% | 3/5 |
| Evaluation call p95, ms | 9.82 | 23.07 | +198.5% | 0/5 |
| Receipt-to-draw p95, ms | 36.96 | 48.03 | +41.6% | 1/5 |
| Receipt-to-draw p99, ms | 47.30 | 64.41 | +32.4% | 1/5 |
| Drawn-state age p95, ms | 90.65 | 153.40 | +71.5% | 1/5 |

| Pair | Execution order | Legacy revisions/s | C revisions/s | Legacy p95 draw ms | C p95 draw ms |
| --- | --- | --- | --- | --- | --- |
| 1 | legacy → native | 16.30 | 9.63 | 33.92 | 48.03 |
| 2 | native → legacy | 7.43 | 13.57 | 43.96 | 41.98 |
| 3 | legacy → native | 16.92 | 11.86 | 39.81 | 63.26 |
| 4 | native → legacy | 16.23 | 13.26 | 29.38 | 40.90 |
| 5 | legacy → native | 15.58 | 8.14 | 36.96 | 82.10 |

The second pair's legacy run had long stalls (p95 drawn-state age approximately
2.6 seconds), and the final C run also had elevated latency. Neither was removed.
The full trace includes all delivered commands, superseded inputs, accepted but
undrawn states, timer-slot gaps, drawn-state age and tracking error relative to
the common intended opacity trajectory. This prevents a high count of repeated
old frames from being presented as fast material feedback. These are descriptive
results from five pairs, without a statistical-significance or universal-speedup
claim.

## What adds work

Both executors produced **bit-identical pixels across all eight 2048² output
maps** at the fixed validation endpoint in every pair (maximum absolute error
0.0). Both had zero warm shader compilations, topology builds and interactive
CPU image writes. The production implementation and native binaries were held
unchanged for the entire comparison.

The actual pass count was **112 per evaluation for legacy versus 296 for C**.
Legacy already performs texture math on the GPU. The C publication path prepares
both old and new mip chains and then copies the new levels into Blender images;
its old levels support rollback. See [revision publication](../../src/open_painter/native_bridge/revisions.py)
and [legacy publication](../../src/open_painter/evaluation/engine/runtime.py).
These extra passes are a concrete source of work; this experiment does not
attribute a precise fraction of the latency difference to each subsystem.

Observation counters show **125 managed texture allocations per eight-channel
publication for C versus zero for legacy**, after warmup. C prepares the complete
transaction's scratch before visible writes, while its pool retains only a few
spares per size/format. Managed memory ends at 512 MiB leased + approximately
480 MiB pooled for C, versus 224 MiB + approximately 256 MiB for legacy.
These are engine-managed allocations, excluding Blender and driver memory.
Counters span the instrumentation window including brief untimed settling;
latency/cadence figures use the timed command/draw traces only.

The measured regression is in the current backend implementation. Moving
orchestration to C does not remove GPU work that was already performed by the
legacy Metal executor.

## Protocol and scope

- Stock Blender 5.2.1 LTS (`9e2066aef7ef`), macOS 26.7.1, Apple M3 Pro, Metal.
  AC power and Low Power Mode enabled at the start and end; preferences unchanged.
- Release implementation at `560236b`, ABI 10. Source/library hashes match the
  [final 0.14.0 artifact](evidence/metal-v10-artifacts.json). The test harness is
  new; the add-on and binary are unchanged.
- `OP_NATIVE_ENGINE=0` versus `1` in the same release. This is the retained legacy
  executor, not a checkout of a historical published add-on version.
- Five 60-second runs per executor, serialized in AB/BA/AB/BA/AB order. Each uses
  a fresh process and isolated profile, 15 seconds of repeated warmup edits,
  identical nonuniform materials, all eight channels, resolution and viewport.
- An ideal 60 Hz opacity trajectory feeds a Blender timer. Receipt cadence can
  fall below 60 Hz under load; gaps and superseded commands remain observable.
  This is scripted RNA input, not OS mouse delivery or hardware display scanout.
- Identical acceptance observation for both modes: successful `Engine.evaluate`
  return. A new revision is counted at its first viewport POST_PIXEL draw.
  Native revision objects are diagnostic metadata, never required to count
  legacy results. The final edit is drained to a draw; duration includes that drain.
- Screenshots, full-map readbacks, compression and numerical comparisons occur
  outside the measured intervals. Every pair checks all eight final maps after
  setting the exact same opacity, 0.37. Actual source pixels are nonuniform.
- No other GPU tests or builds run concurrently. General desktop load was not
  locked down; per-run system load is recorded. No outlier exclusion or tuning
  of implementation settings between modes is performed.
- This measures warm opacity editing on one 2K fixture. Cold startup, brush
  throughput, large structural rebuilds, other GPUs and Windows/Vulkan are not
  measured by this experiment.

## Evidence and reproduction

[Machine-readable results](evidence/metal-v10-paired/results.json) contain every
pair, aggregate calculations, pixel errors/hashes, production artifact identities
and harness hashes. [Trace index](evidence/metal-v10-paired/traces.json) maps each
original report to its gzip archive and both hashes; decompress to inspect every
command and draw. Logs and representative screenshots are stored alongside them.
[Final power settings](evidence/metal-v10-paired/environment-after.json) accompany
the initial environment in the result report. Full pixel arrays remain in the
local benchmark run directory; the archive retains their SHA-256 identities and
complete comparison results.

```sh
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 \
  scripts/benchmark_native_metal.py \
  --lib /tmp/openpainter-native-release/libopenpainter.dylib \
  --pairs 5 --seconds 60
```

The runner requires NumPy and a logged-in macOS desktop. It asserts the selected
executor, zero warm recompilation, matching viewport/build, full-output parity,
and unchanged implementation hashes. Its PASS status means the experiment
completed correctly; it does not mean C was faster. Two unit tests verify paired
percentage calculations and preserve the direction of a measured regression.
