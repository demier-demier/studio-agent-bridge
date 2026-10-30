# Optional Rust surface analysis

This PyO3 module accelerates rasterization of recorded float32 particle positions. It never changes simulation dynamics or caches. Install Rust and then run `python -m pip install ./native` from the parent example directory; select it explicitly with `measure_tracks.py RUN --cache-engine rust`. NumPy remains the default. Missing Rust support fails clearly rather than silently changing engines.

Inputs must be nonempty C-contiguous float32 (N, 3), finite, with bounded positive raster dimensions. Output is a float32 maximum particle-top surface. The implementation borrows input memory and avoids per-particle index/mask/gather allocations. Coordinates outside the raster are omitted consistently with the reference. Unit tests cover cell boundaries, nextafter values, nonfinite inputs, shapes, layout and exact parity.

See the [measured comparison and reproducible commands](../docs/performance.md). The published 3.09x improvement applies to this analysis step only. MIT license under the repository license.
