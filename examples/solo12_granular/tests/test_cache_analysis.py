import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_analysis import CacheAnalysis


@unittest.skipUnless(
    importlib.util.find_spec("scenward_cache"), "Optional Rust module not installed"
)
class RustCacheTests(unittest.TestCase):
    def setUp(self):
        self.engines = [CacheAnalysis(name) for name in ("numpy", "rust")]
        self.points = np.random.default_rng(17).uniform(-1, 1, (100003, 3)).astype("f4")

    def test_exact_raster(self):
        args = (self.points, 400, 170, -0.8, -0.34, 0.004, 0.0015)
        np.testing.assert_array_equal(*(e.surface(*args) for e in self.engines))

    def test_cell_boundaries_and_outside_points(self):
        coords = np.arange(-20, 21, dtype="f4") * np.float32(0.004)
        coords = np.r_[
            coords,
            np.nextafter(coords, np.float32(-np.inf)),
            np.nextafter(coords, np.float32(np.inf)),
        ]
        points = np.column_stack([coords, coords, coords]).astype("f4")
        args = (points, 40, 40, -0.08, -0.08, 0.004, 0.0015)
        np.testing.assert_array_equal(*(e.surface(*args) for e in self.engines))

    def test_nonfinite_coordinates_rejected(self):
        for axis in range(3):
            for value in (np.nan, np.inf, -np.inf):
                points = self.points[:5].copy()
                points[2, axis] = value
                for e in self.engines:
                    with self.assertRaises(ValueError):
                        e.surface(points, 40, 40, -0.08, -0.08, 0.004, 0.0015)

    def test_no_implicit_copy_or_unbounded_allocation(self):
        for e in self.engines:
            for data in (
                self.points[::2],
                self.points.astype("f8"),
                np.empty((0, 3), "f4"),
                self.points[:, :2],
            ):
                with self.assertRaises(ValueError):
                    e.surface(data, 40, 40, -0.08, -0.08, 0.004, 0.0015)
            for nx, ny in ((0, 10), (10, -1), (10**9, 10**9)):
                with self.assertRaises((ValueError, OverflowError)):
                    e.surface(self.points, nx, ny, 0, 0, 0.01, 0.0015)


if __name__ == "__main__":
    unittest.main()
