# Profiling the full-detail live loop

`--profile-physics` adds opt-in GPU pass timestamps and host stage timings to the
live JSON report. It is a diagnostic capability, **not a solver speedup**. It keeps
all 961,191 grains, the 50 microsecond DEM timestep, the native robot coupling and
every render tick. No recorded motion, coarser physics or reduced geometry is used.

Follow [the live setup](LIVE_PHYSICS.md) first, then run with new output filenames:

```sh
./target/release/scenward-render \
  --run runs/initial --geometry ../../examples/solo12_granular/robot/preview-geometry.json \
  --live --model runs/laboratory/physics.xml --environment runs/laboratory/environment.json \
  --headless --live-ticks 2400 --profile-physics --report profile.json

# Run without instrumentation to measure normal throughput.
./target/release/scenward-render \
  --run runs/initial --geometry ../../examples/solo12_granular/robot/preview-geometry.json \
  --live --model runs/laboratory/physics.xml --environment runs/laboratory/environment.json \
  --headless --live-ticks 2400 --report uninstrumented.json
```

Existing reports are refused. Profiling requires hardware GPU support for wgpu
`TIMESTAMP_QUERY`; an unsupported adapter fails explicitly. Omit the flag to run
normally. The uninstrumented path requests no timestamp feature and allocates no
profiling buffers. The flag requires `--live` or `--physics-check`.

## Measured full-scene result — 2026-10-31

Apple M4 Pro (12 CPU cores, 16 GPU cores, 24 GB unified memory), Metal. Sequential
baseline, instrumented and default-after runs; one run each, normal desktop
conditions. Every run completed 2.4 simulated seconds, 48,000 DEM steps and 2,400
rendered frames at 1280 × 720 with 4x MSAA. All grains remained finite. Inputs and
the unchanged physics shader are pinned by hashes in the [comparison record](evidence/profile-comparison-2026-10-31.json).

| Run | Completed loop wall time | Real-time factor | Peak physical footprint |
| --- | ---: | ---: | ---: |
| Previous binary | 75.887 s | 0.03163x | 1.0831 GB |
| New binary, profiling enabled | 76.524 s | 0.03136x | 1.0885 GB |
| New binary, profiling disabled | 76.430 s | 0.03140x | 1.0830 GB |

The default path was 0.72% slower in this single comparison; there is no measured
speedup claim. Profiling added 0.12% loop time relative to the default-after run,
but these individual observations do not isolate small differences from normal
run variance. Physical footprint is the macOS whole-process measurement, not
dedicated VRAM or just the explicit profiler buffers. **Physical real time is
still not achieved.**

The [instrumented run](evidence/profile-full-2026-10-31.json) recorded:

| Stage | Total time | Clock/scope |
| --- | ---: | --- |
| Solver pass | 43.996 s | GPU; includes rebuild and integration |
| Dispatch preparation | 0.244 s | GPU |
| Submission to reaction readback | 47.134 s | Host wall; includes the GPU work above |
| Command encoding | 1.360 s | Host wall |
| Native robot/rigid dynamics | 0.379 s | Host wall |
| Pose upload | 0.112 s | Host wall |
| Completed rendering | 27.535 s | Host wall; encoding + GPU + wait |

Inference from this workload: the solver pass and completed rendering deserve
the next investigation. Native robot stepping accounts for less than 0.5% of the
total loop wall time, so speeding up that stage alone cannot close the real-time
gap. More granular kernel measurements are still needed before attributing the
solver time to neighbour construction versus integration. Nothing here establishes
competitor parity, calibrated soil fidelity or performance on other GPUs.

Verify the recorded counts and timing consistency with:

```sh
python scripts/check_profile.py evidence/profile-full-2026-10-31.json
```

The checker also rejects changed grain counts/timesteps, inconsistent real-time
factors, missing samples, rejected timestamp pairs and nonfinite timing totals.
It checks a report, not independently re-executes physics. CI runs this consistency
check and CPU tests; the Metal execution above was performed locally.

## Report fields

`profiling.loop_gpu_and_submission` contains:

- `gpu_prepare_pass`: GPU duration of conditional-dispatch preparation.
- `gpu_solver_pass`: GPU duration of the existing combined clear/grid/neighbour,
  finish and integrate pass. This does **not** separate individual kernels or
  rebuild steps from reused-neighbour steps.
- `cpu_encode`: host time collecting uploads and encoding the physics commands.
- `submit_to_readback`: wall time from queue submission until the reaction and
  timestamp bytes are available on the host. This includes GPU execution, queue
  submission, mapping, copying and host scheduling. It is **not** transfer-only
  latency.
- Completed batches/steps, per-stage sample counts, sums, maxima and rejected
  timestamp pairs. No unbounded per-step trace is retained.

`profiling.loop_host` separates collecting native sphere states, the native
MuJoCo step, uploading poses and (in the headless benchmark) completed rendering.
The render measurement includes encoding, GPU completion and the host wait; it is
not GPU-only render time. The interactive path does not populate that render timer.

Settling has its own `settling_gpu_and_submission` record. Its counters reset
before the timed live loop. Initialization, final diagnostics and report/PNG
writing are outside the loop as before. Physical real-time factor still divides
simulated seconds by completed loop wall time; rendering FPS is not substituted.

**GPU times overlap host submission/completion wall time. Do not add the GPU and
CPU columns together.** Instrumentation itself adds overhead. Use the disabled
run to discuss throughput, and repeat runs under controlled conditions before
claiming a small optimization.

## Design, bounds and failure behavior

The profiler writes begin/end timestamps on the two **existing** compute passes
per DEM step. It does not split passes, insert waits between kernels or change
dispatch order. Up to 256 DEM steps require 1,024 timestamp slots. A fixed 8 KiB
resolve buffer and 8 KiB extension to the existing readback buffer bound explicit
buffer storage; the driver also owns the query set and staging allocations.

Results resolve into a GPU buffer and copy alongside the existing reaction
readback. There is no second map/poll synchronization. Counts, periods and payload
lengths are validated before accumulating statistics. Reversed/wrapped timestamp
pairs are counted and excluded rather than producing an unsigned underflow.
Zero-duration pairs remain valid at the device's timestamp resolution. Reports
containing rejected pairs have incomplete stage totals and must not be treated as
complete timing evidence.

Overflow, grid bounds and nonfinite physics failures still abort the action. The
profiler never repairs, discards or hides particles. It opens no network service,
loads no third-party scripts and accepts no executable content. Existing trusted
native-model and numeric-import boundaries in [the threat model](LIVE_PHYSICS.md#security-provenance-and-remaining-limits)
continue to apply. Timestamp profiling is for this local engineering tool, not an
untrusted multi-tenant timing API.

The implementation follows the official [wgpu timestamp query example](https://wgpu.rs/doc/wgpu_examples/timestamp_queries/index.html):
pass timestamp writes, explicit query resolve/copy and conversion using the
queue's timestamp period. Code uses the repository's locked wgpu 27 dependency.
Source code and fixtures are original under this repository's MIT license;
existing Solo12 and MuJoCo notices are unchanged.

## Verification

```sh
cargo test --locked --features robotics
cargo clippy --locked --all-targets --features robotics -- -D warnings
./target/release/scenward-render \
  --run runs/initial --geometry ../../examples/solo12_granular/robot/preview-geometry.json \
  --physics-check --profile-physics
```

CPU tests cover conversion/stage attribution across batches, invalid periods and
payload sizes, counter wrap and the maximum batch. The hardware check repeats the
independent O(N²) CPU oracle and contact/fault tests with profiling enabled, then
exercises a 256-step GPU query batch followed by query reuse. These checks do not
validate GPU execution on machines that were not tested.
