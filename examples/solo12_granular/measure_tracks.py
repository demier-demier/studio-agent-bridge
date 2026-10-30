"""Measure preserved top-surface changes in the actually traversed region."""

import argparse
import json
from pathlib import Path

import numpy as np
from cache_analysis import CacheAnalysis

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("run", type=Path)
parser.add_argument("--cache-engine", choices=("numpy", "rust"), default="numpy")
args = parser.parse_args()
analysis = CacheAnalysis(args.cache_engine)
run = args.run
meta = json.loads((run / "metadata.json").read_text())
p = np.load(run / "particles.npy", mmap_mode="r")
initial = np.load(run / "settled.npy", mmap_mode="r")
feet = np.load(run / "foot_centers.npy", mmap_mode="r")
r = meta["grain_radius_m"]
resolution = 0.004
xmin, xmax, ymin, ymax = meta["bed_bounds"]
nx = int(np.ceil((xmax - xmin) / resolution))
ny = int(np.ceil((ymax - ymin) / resolution))


def surface(points):
    return analysis.surface(points, nx, ny, xmin, ymin, resolution, r)


h0 = surface(initial)
h1 = surface(p[-1])
mask = np.zeros((nx, ny), bool)
for frame in feet:
    for foot in frame:
        ix = int((foot[0] - xmin) / resolution)
        iy = int((foot[1] - ymin) / resolution)
        pad = round(0.025 / resolution)
        lo, hi = max(0, ix - pad), min(nx, ix + pad + 1)
        ly, hy = max(0, iy - pad), min(ny, iy + pad + 1)
        xx = xmin + (np.arange(lo, hi) + 0.5) * resolution
        yy = ymin + (np.arange(ly, hy) + 0.5) * resolution
        mask[lo:hi, ly:hy] |= (xx[:, None] - foot[0]) ** 2 + (yy[None, :] - foot[1]) ** 2 < 0.025**2
xx = xmin + (np.arange(nx) + 0.5) * resolution
mask &= xx[:, None] < min(feet[-1, 2:, 0]) - 0.04
mask &= np.isfinite(h0) & np.isfinite(h1)
delta = h1 - h0
depressed = mask & (delta < -0.002)
report = {
    "cache_engine": args.cache_engine,
    "method": "4mm raster maximum particle-top surface; visited foot neighborhoods behind final hind feet; same grains before/after",
    "visible_trail_cells": int(depressed.sum()),
    "depressed_area_cm2": float(depressed.sum() * resolution**2 * 1e4),
    "median_surface_change_mm": float(np.median(delta[depressed]) * 1000)
    if depressed.any()
    else None,
    "particle_count": p.shape[1],
    "limitation": "Raster surface metric at finite grain scale; not a calibrated soil deformation measurement.",
}
(run / "track-measurement.json").write_text(json.dumps(report, indent=2) + "\n")
np.savez_compressed(run / "track-surface.npz", initial=h0, final=h1, visited=mask, change=delta)
print(json.dumps(report, indent=2))
