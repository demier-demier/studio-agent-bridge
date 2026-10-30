"""Synchronized Metal/CPU kernel timings on a recorded full-density grain bed.

Stage timings synchronize after each stage, so they are diagnostic rather than
end-to-end simulation timings. End-to-end batches retain normal synchronization.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np
import taichi as ti
from backends import initialize_backend
from granular import GranularBed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("--grid-layout", choices=("dense", "hashed"), required=True)
    p.add_argument("--backend", choices=("cpu", "metal"), default="metal")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        p.error("Use a new output path")
    initialize_backend(a.backend)
    points = np.load(a.input, allow_pickle=False)
    bed = GranularBed(points, grid_layout=a.grid_layout)
    for _ in range(10):
        bed.grid()
        bed.forces(0)
        bed.integrate()
    ti.sync()
    batch = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(200):
            bed.grid()
            bed.forces(0)
            bed.integrate()
        ti.sync()
        batch.append((time.perf_counter() - start) / 200 * 1000)
    stages = {"grid": [], "forces": [], "integrate": []}
    for _ in range(20):
        for key, operation in (
            ("grid", bed.grid),
            ("forces", lambda: bed.forces(0)),
            ("integrate", bed.integrate),
        ):
            start = time.perf_counter()
            operation()
            ti.sync()
            stages[key].append((time.perf_counter() - start) * 1000)
    if bed.overflow[None]:
        raise RuntimeError("Grid overflow or out-of-bounds")
    report = {
        "scope": "Fixed full-density granular bed without robot coupling; not whole-video performance",
        "layout": a.grid_layout,
        "particles": len(points),
        "substep_ms_median": statistics.median(batch),
        "batch_substep_ms": batch,
        "synchronized_stage_ms": {k: statistics.median(v) for k, v in stages.items()},
    }
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
