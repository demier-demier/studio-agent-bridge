"""Measure optional Rust analysis against the original NumPy raster algorithm.

Run each engine in a fresh process with /usr/bin/time -l on macOS for peak RSS.
Use the same recorded float32 particle frame for both engines.
"""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np
from cache_analysis import CacheAnalysis


def legacy_surface(points, nx, ny, xmin, ymin, resolution, radius):
    """Exact original measure_tracks.py algorithm, including its allocations."""
    ix = np.floor((points[:, 0] - xmin) / resolution).astype(int)
    iy = np.floor((points[:, 1] - ymin) / resolution).astype(int)
    valid = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    h = np.full((nx, ny), np.nan, np.float32)
    flat = np.full(nx * ny, -np.inf, np.float32)
    np.maximum.at(flat, ix[valid] * ny + iy[valid], points[valid, 2] + radius)
    h = flat.reshape(nx, ny)
    h[~np.isfinite(h)] = np.nan
    return h


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="Recorded settled.npy, same input for both engines")
    p.add_argument("--engine", choices=("numpy", "rust"), required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--repeats", type=int, default=31)
    a = p.parse_args()
    if not 3 <= a.repeats <= 101 or a.output.exists():
        p.error("Use 3..101 repeats and a new output file")
    points = np.load(a.input, mmap_mode="r", allow_pickle=False)
    analysis = CacheAnalysis(a.engine)
    surface = legacy_surface if a.engine == "numpy" else analysis.surface
    args = (points, 400, 170, -0.8, -0.34, 0.004, 0.0015)
    for _ in range(3):
        result = surface(*args)
    timings = {"surface": []}
    for _ in range(a.repeats):
        start = time.perf_counter_ns()
        result = surface(*args)
        timings["surface"].append((time.perf_counter_ns() - start) / 1e6)
    # Canonicalize NaN payloads for an independently comparable raster hash.
    result[np.isnan(result)] = np.float32(np.nan)
    report = {
        "scope": "Analysis of one recorded frame; not a simulation speed measurement",
        "engine": a.engine,
        "particles": len(points),
        "repeats": a.repeats,
        "input_sha256": hashlib.sha256(memoryview(points)).hexdigest(),
        "surface_sha256": hashlib.sha256(memoryview(result)).hexdigest(),
        "timing_ms": {
            k: {"median": statistics.median(v), "min": min(v), "max": max(v), "samples": v}
            for k, v in timings.items()
        },
    }
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "timing_ms"}))


if __name__ == "__main__":
    main()
