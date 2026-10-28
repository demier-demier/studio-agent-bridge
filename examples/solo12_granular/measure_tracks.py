"""Measure preserved top-surface changes in the actually traversed region."""

import json
import sys
from pathlib import Path

import numpy as np

run = Path(sys.argv[1])
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
    ix = np.floor((points[:, 0] - xmin) / resolution).astype(int)
    iy = np.floor((points[:, 1] - ymin) / resolution).astype(int)
    valid = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    h = np.full((nx, ny), np.nan, np.float32)
    flat = np.full(nx * ny, -np.inf, np.float32)
    np.maximum.at(flat, ix[valid] * ny + iy[valid], points[valid, 2] + r)
    h = flat.reshape(nx, ny)
    h[~np.isfinite(h)] = np.nan
    return h


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
