"""Explicit NumPy/Rust analysis engines; no silent fallback or altered physics."""

import importlib

import numpy as np


def _points(points):
    if (
        points.dtype != np.dtype("float32")
        or points.ndim != 2
        or points.shape[1] != 3
        or not len(points)
        or not points.flags.c_contiguous
    ):
        raise ValueError("Expected nonempty C-contiguous float32 (N, 3) points")


class CacheAnalysis:
    def __init__(self, engine="numpy"):
        if engine not in {"numpy", "rust"}:
            raise ValueError("Cache engine must be numpy or rust")
        self.engine = engine
        self.native = None
        if engine == "rust":
            try:
                self.native = importlib.import_module("scenward_cache")
            except ImportError as error:
                raise RuntimeError(
                    "Rust cache engine is not installed. Build ./native or choose numpy."
                ) from error

    def surface(self, points, nx, ny, xmin, ymin, resolution, radius):
        _points(points)
        if (
            not isinstance(nx, int)
            or not isinstance(ny, int)
            or nx <= 0
            or ny <= 0
            or nx * ny > 16_000_000
            or not all(np.isfinite(v) for v in (xmin, ymin, resolution, radius))
            or resolution <= 0
            or radius < 0
        ):
            raise ValueError("Invalid bounded finite raster parameters")
        if self.native:
            return self.native.surface(points, nx, ny, xmin, ymin, resolution, radius).reshape(
                nx, ny
            )
        if not np.isfinite(points).all():
            raise ValueError("Non-finite particle coordinate")
        ix = np.floor((points[:, 0] - xmin) / resolution).astype(np.int64)
        iy = np.floor((points[:, 1] - ymin) / resolution).astype(np.int64)
        valid = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        heights = points[valid, 2] + radius
        if not np.isfinite(heights).all():
            raise ValueError("Non-finite particle surface height")
        flat = np.full(nx * ny, -np.inf, np.float32)
        np.maximum.at(flat, ix[valid] * ny + iy[valid], heights)
        flat[~np.isfinite(flat)] = np.nan
        return flat.reshape(nx, ny)
