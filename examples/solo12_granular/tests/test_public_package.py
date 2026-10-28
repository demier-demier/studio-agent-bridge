"""Independent bounded acceptance tests for the public Solo12 package.

These checks cover the packaged model, initialization and controller contracts.
They do not validate real-world sand or learned robot control.
"""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mujoco
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.granular = load_module("solo12_public_granular", ROOT / "granular.py")
        cls.controller = load_module(
            "solo12_public_controller", ROOT / "locomotion" / "controller.py"
        )
        cls.model_path = ROOT / "locomotion" / "solo12-walk.xml"
        with patch.object(sys, "path", [str(ROOT), *sys.path]):
            cls.simulate = load_module("solo12_public_simulate", ROOT / "simulate.py")

    def test_cli_rejects_nonfinite_unsafe_and_oversized_runs_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new-run"
            invalid_arguments = (
                ["--seconds", "nan"],
                ["--radius", "inf"],
                ["--radius", "0"],
                ["--layers", "0"],
                ["--cpu-threads", "0"],
                ["--start-x", "0.6"],
                ["--seconds", "10", "--speed", "0.1"],
                ["--radius", ".015", "--layers", "12"],
                ["--radius", ".001", "--half-x", "1", "--half-y", ".5", "--layers", "12"],
            )
            for arguments in invalid_arguments:
                with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        self.simulate.options(["--output", str(output), *arguments])
                    self.assertEqual(error.exception.code, 2)
                    self.assertFalse(output.exists())

    def test_cli_refuses_an_existing_run_without_changing_its_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "preserve.txt"
            marker.write_text("existing simulation evidence\n")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.simulate.options(["--output", temporary])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(marker.read_text(), "existing simulation evidence\n")

    def test_video_bed_count_and_seed_are_reproducible(self):
        points = self.granular.make_bed(radius=0.0015, half_x=0.8, half_y=0.34, layers=9)
        self.assertEqual(points.shape, (961191, 3))
        self.assertTrue(np.isfinite(points).all())
        duplicate = self.granular.make_bed(radius=0.0015, half_x=0.8, half_y=0.34, layers=9)
        np.testing.assert_array_equal(points, duplicate)
        self.assertTrue((np.abs(points[:, 0]) < 0.8).all())
        self.assertTrue((np.abs(points[:, 1]) < 0.34).all())
        self.assertGreater(float(points[:, 2].min()), 0)

    def test_model_loads_outside_checkout(self):
        old = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                os.chdir(temporary)
                model = mujoco.MjModel.from_xml_path(str(self.model_path))
        finally:
            os.chdir(old)
        self.assertEqual((model.nq, model.nv, model.nu), (19, 18, 12))
        self.assertEqual(model.nmesh, 9)
        visual_meshes = [
            i
            for i in range(model.ngeom)
            if model.geom_type[i] == mujoco.mjtGeom.mjGEOM_MESH and model.geom_group[i] == 1
        ]
        self.assertEqual(len(visual_meshes), 13)
        self.assertEqual(model.jnt_type[0], mujoco.mjtJoint.mjJNT_FREE)
        for leg in self.controller.LEGS:
            pad = model.geom(f"{leg}_pad").id
            self.assertEqual(model.geom_type[pad], mujoco.mjtGeom.mjGEOM_SPHERE)
            self.assertAlmostEqual(float(model.geom_size[pad, 0]), 0.02)
        np.testing.assert_allclose(model.actuator_forcerange, [[-2.5, 2.5]] * 12)

    def test_controller_cannot_impose_base_motion_or_external_support(self):
        model = mujoco.MjModel.from_xml_path(str(self.model_path))
        data = mujoco.MjData(model)
        self.controller.initialize(model, data, ground_z=0, x=-0.33)
        controller = self.controller.SoloController(
            model,
            data,
            ground_z=0,
            speed=0.08,
            step_time=0.6,
            shift=0.008,
            lift=0.04,
            height=0.235,
            start_time=1.0,
            balance=True,
        )
        before = {
            "qpos": data.qpos.copy(),
            "qvel": data.qvel.copy(),
            "xfrc_applied": data.xfrc_applied.copy(),
            "qfrc_applied": data.qfrc_applied.copy(),
        }
        for time in (0.0, 1.0, 1.35, 1.59, 2.2, 4.1):
            controller.step(time)
            self.assertTrue(np.isfinite(data.ctrl).all())
            for field, original in before.items():
                np.testing.assert_array_equal(getattr(data, field), original, err_msg=field)

    def test_rigid_floor_crawl_advances_with_bounded_joint_torques(self):
        model = mujoco.MjModel.from_xml_path(str(self.model_path))
        data = mujoco.MjData(model)
        self.controller.initialize(model, data, ground_z=0, x=-0.33)
        start_x = float(data.qpos[0])
        controller = self.controller.SoloController(
            model,
            data,
            ground_z=0,
            speed=0.08,
            step_time=0.6,
            shift=0.008,
            lift=0.04,
            height=0.235,
            start_time=1.0,
            balance=True,
        )
        for step in range(8000):
            controller.step(step * 0.001)
            mujoco.mj_step(model, data)
            self.assertTrue(np.isfinite(data.qpos).all())
            self.assertLessEqual(float(np.abs(data.actuator_force).max()), 2.5 + 1e-6)
            self.assertFalse(np.any(data.xfrc_applied))
            if step >= 1000:
                self.assertGreater(float(data.xmat[model.body("base_link").id, 8]), 0.97)
                self.assertGreater(float(data.qpos[2]), 0.20)
                self.assertLess(float(data.qpos[2]), 0.35)
        self.assertGreater(float(data.qpos[0]) - start_x, 0.3)
        self.assertLess(abs(float(data.qpos[1])), 0.03)


if __name__ == "__main__":
    unittest.main()
