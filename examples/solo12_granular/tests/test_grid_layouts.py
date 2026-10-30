"""Differential physics tests: same laws, bounds, particles and overflow policy."""

import sys
import unittest
from pathlib import Path

import numpy as np
import taichi as ti

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from granular import GranularBed


class GridLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ti.init(arch=ti.cpu, cpu_max_num_threads=1, default_fp=ti.f32)

    @classmethod
    def tearDownClass(cls):
        ti.reset()

    def pair(self, points):
        return [
            GranularBed(points, half_x=0.05, half_y=0.05, grid_layout=layout)
            for layout in ("dense", "hashed")
        ]

    def test_force_and_trajectory_match_single_thread_reference(self):
        rng = np.random.default_rng(101)
        points = rng.uniform([-0.01, -0.01, 0.0015], [0.01, 0.01, 0.02], (200, 3)).astype("f4")
        beds = self.pair(points)
        feet = np.full((4, 3), 1, np.float32)
        feet[0] = [0, 0, 0.025]
        for bed in beds:
            bed.foot.from_numpy(feet)
        for _ in range(40):
            for bed in beds:
                bed.grid()
                bed.forces(1)
                bed.integrate()
        for bed in beds[1:]:
            np.testing.assert_array_equal(beds[0].x.to_numpy(), bed.x.to_numpy())
            np.testing.assert_array_equal(beds[0].v.to_numpy(), bed.v.to_numpy())
            np.testing.assert_array_equal(beds[0].reactions.to_numpy(), bed.reactions.to_numpy())
            self.assertEqual(bed.overflow[None], 0)

    def test_overflow_and_out_of_bounds_still_count(self):
        points = np.array([[0, 0, 0.04]] * 17 + [[2, 0, 0]], dtype="f4")
        for bed in self.pair(points):
            bed.grid()
            self.assertEqual(bed.overflow[None], 2)
            self.assertEqual(bed.count.to_numpy().sum(), 17)

    def test_cell_reuse_after_migration(self):
        points = np.array([[-0.03, 0, 0.03], [0.03, 0, 0.03]], dtype="f4")
        for bed in self.pair(points):
            for frame in (
                points,
                points[::-1].copy(),
                np.array([[0, 0, 0.03], [0.001, 0, 0.03]], "f4"),
            ):
                bed.x.from_numpy(frame)
                bed.grid()
                bed.forces(0)
                self.assertEqual(bed.count.to_numpy().sum(), 2)
                self.assertEqual(bed.overflow[None], 0)
                np.testing.assert_allclose(
                    bed.force.to_numpy().sum(0), [0, 0, -2 * 9.81 * bed.mass], atol=1e-6
                )

    def test_hash_collisions_do_not_create_contacts_or_duplicate_neighbours(self):
        probe = self.pair(np.array([[0, 0, 0.03]], "f4"))[-1]
        shape = probe.shape
        first = np.ravel_multi_index((3, 3, 10), shape)
        cells = [np.array(np.unravel_index(first + shift, shape)) for shift in (0, probe.hash_size)]
        points = np.array([np.array(probe.low) + (c + 0.5) * probe.cell for c in cells], "f4")
        points = np.vstack([points, points + np.array([0.00285, 0, 0], "f4")])
        beds = self.pair(points)
        for bed in beds:
            bed.grid()
            bed.forces(0)
            self.assertEqual(bed.overflow[None], 0)
        np.testing.assert_array_equal(beds[0].force.to_numpy(), beds[-1].force.to_numpy())
        offsets = [
            x * shape[1] * shape[2] + y * shape[2] + z
            for x in (-1, 0, 1)
            for y in (-1, 0, 1)
            for z in (-1, 0, 1)
        ]
        self.assertEqual(len({x & (probe.hash_size - 1) for x in offsets}), 27)


if __name__ == "__main__":
    unittest.main()
