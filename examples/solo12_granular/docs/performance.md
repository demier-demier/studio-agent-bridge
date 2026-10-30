# Measured optimization: preserve the physical workload

The 2026-10-29 comparison retained **961,191 grains, 3 mm diameter, nine layers, 8 seconds, 241 float32 frames, 50 µs grain steps and 1 ms rigid steps**. Apple M4 Pro: 12 CPU cores, 16 GPU cores, 24 GB RAM; Python 3.12.13, NumPy 2.5.4, Taichi 1.7.4, MuJoCo 3.3.7, Metal. Rust uses 1.99.1. The baseline is commit `2020d2719cdc7213b526248f12b0237acc5eb10b`.

## Full coupled simulation

| Metric | Original dense grid | Hashed SoA grid |
| --- | ---: | ---: |
| Dynamics wall time | 555.42 s | 466.39 s |
| Settling | 16.17 s | 13.47 s |
| Whole process | 572.56 s | 480.19 s |
| Peak RSS | 3,039.61 MB | 3,033.17 MB |
| macOS peak physical footprint | 844.89 MB | 512.82 MB |
| Forward travel | 0.578385 m | 0.578554 m |
| Maximum tilt | 4.3667° | 4.3573° |
| Independent trajectory checks | 18 passed | 18 passed |

Observed dynamics time fell **16.03%** (1.19x speedup). OS physical footprint fell **39.30%**; RSS fell only **0.21%**. Do not equate these memory metrics: RSS includes mapped trajectory data and neither measurement is a dedicated VRAM counter. MB are decimal.

This is one complete run per variant under normal desktop conditions. Floating-point GPU atomics can change summation order and slightly alter the trajectory. Both complete particle/trajectory audits passed; deterministic CPU differential tests compare positions, velocities and reactions exactly. These checks establish preservation of the implemented model, not calibrated sand fidelity.

The accepted change stays on the GPU in Taichi/Metal. A bounded hash table replaces the mostly empty dense spatial grid. Sixteen particle slots per bucket are stored slot-first to improve access. The original 2.2-radius cells and 27-cell neighborhood are retained. The table-size bound prevents adjacent stencil cells from aliasing. Distant-cell aliases are rejected by exact distance checks; overflow and out-of-bounds conditions abort instead of dropping contacts. The original dense implementation remains selectable with `--grid-layout dense`.

The Rust module is **not responsible for this physics speedup**. No particle reduction, larger time steps, lower precision or controller change was used.

## Where Rust earned its place

The surface-raster postprocessor used temporary index, mask and gathered-coordinate arrays in NumPy. The optional Rust implementation borrows the input frame and allocates only the output raster. Using the same 961,191-particle settled frame, three warmups and 31 measurements per implementation:

| Metric | Original NumPy raster | Rust raster |
| --- | ---: | ---: |
| Median raster time | 8.915 ms | 2.884 ms |
| Peak process RSS | 90.55 MB | 44.19 MB |

That is **3.09x** raster throughput and approximately **51%** lower process RSS in this small analysis command. It does not imply a 3x improvement in the whole simulation. Raster hashes are identical. A complete initial/final trail analysis also produced exactly equal arrays and metrics: 2,760 depressed raster cells and 441.6 cm² under the existing measurement definition. These are finite-scale raster measurements, not measured real soil deformation.

The separate [Rust graphics engine](../../../rust/scenward-render/) renders the full cache at measured 57.17 completed frames/s in 1080p/4x MSAA. It is recorded playback, not real-time physics.

## Reproduce

From `examples/solo12_granular`, use a new output directory for each run. To reproduce the exact historical before version, use a separate checkout of the baseline commit. The current `--grid-layout dense` is also a useful reference without switching commits.

```sh
/usr/bin/time -l .venv/bin/python simulate.py --preset video --arch metal --grid-layout dense --output runs/dense 2>dense.time
/usr/bin/time -l .venv/bin/python simulate.py --preset video --arch metal --grid-layout hashed --output runs/hashed 2>hashed.time
.venv/bin/python locomotion/audit_run.py runs/dense
.venv/bin/python locomotion/audit_run.py runs/hashed
.venv/bin/python compare_runs.py runs/dense runs/hashed --before-time dense.time --after-time hashed.time --output comparison.json
.venv/bin/python check_granular.py --backend metal --grid-layout hashed
.venv/bin/python -m pip install ./native
/usr/bin/time -l .venv/bin/python benchmark_cache.py runs/hashed/settled.npy --engine numpy --output numpy.json
/usr/bin/time -l .venv/bin/python benchmark_cache.py runs/hashed/settled.npy --engine rust --output rust.json
.venv/bin/python measure_tracks.py runs/hashed --cache-engine rust
.venv/bin/python -m unittest discover -s tests -v
```

`/usr/bin/time -l` here is the macOS tool. `compare_runs.py` requires full passing audits and matching workload/controller fields. Source hashes identify the measured physics versions. The compact benchmark (`benchmark_grid.py`) is an uncoupled settled-bed diagnostic; do not present it as a full coupled simulation result.

## Rejected changes

An occupied-row compact grid added indirection and another GPU pass, and was approximately 10% slower in preliminary comparison. Its full run was deliberately interrupted and is not counted as a completed benchmark. Diameter-sized cells and a different hash-axis ordering did not improve the retained layout. A Rust frame-statistics prototype was slower than NumPy (approximately 0.970 vs 0.388 ms), so it was removed. Rust is used for the measured raster win and the native rendering layer.

Raw evidence: [full comparison](../evidence/physics-comparison.json), [NumPy samples](../evidence/cache-numpy-final.json), [Rust samples](../evidence/cache-rust-final.json), [complete trail parity](../evidence/track-analysis-parity.json), [Metal analytic checks](../evidence/hashed-analytic-metal.json). Cache files remain generated local artifacts rather than multi-GB source-control blobs.
