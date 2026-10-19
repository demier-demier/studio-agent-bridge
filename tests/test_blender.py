"""Boundary tests run without Blender. Native proof is an explicit integration test."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_agent_bridge.adapters import blender


class BlenderBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name).resolve()
        self.run_dir = self.workspace / "runs" / "test"

    def tearDown(self):
        self.directory.cleanup()

    def run_action(self, action, parameters=None, config=None):
        return blender.execute(
            self.workspace,
            {"action": action, "parameters": parameters or {}},
            self.run_dir,
            config or {},
        )

    def test_missing_executable_is_blocked(self):
        result = self.run_action(
            "create_demo", config={"blender_executable": "missing-blender-executable-09"}
        )
        self.assertEqual(result["status"], "blocked")

    def test_script_requires_server_opt_in(self):
        result = self.run_action("run_script", {"script_path": "never-opened.py"})
        self.assertEqual(result["status"], "blocked")
        self.assertIn("allow_scripts", result["summary"])

    def test_client_cannot_enable_script_privilege(self):
        result = self.run_action("run_script", {"script_path": "x.py", "allow_scripts": True})
        self.assertEqual(result["status"], "blocked")

    def test_unknown_action_fails_before_host_launch(self):
        with patch.object(blender, "_launch") as launch:
            result = self.run_action("remove_everything")
        self.assertEqual(result["status"], "failed")
        launch.assert_not_called()

    def test_absolute_and_parent_escape_inputs_rejected(self):
        with self.assertRaises(ValueError):
            blender._input(self.workspace, "/tmp/source.blend", {".blend"})
        with self.assertRaises(ValueError):
            blender._input(self.workspace, "../source.blend", {".blend"})

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            other = Path(outside) / "other.blend"
            other.write_bytes(b"source")
            (self.workspace / "link.blend").symlink_to(other)
            with self.assertRaises(ValueError):
                blender._input(self.workspace, "link.blend", {".blend"})

    def test_nonempty_run_directory_preserved(self):
        self.run_dir.mkdir(parents=True)
        existing = self.run_dir / "existing.blend"
        existing.write_bytes(b"previous result")
        result = self.run_action("create_demo")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(existing.read_bytes(), b"previous result")

    def test_export_format_validated_before_launch(self):
        source = self.workspace / "scene.blend"
        source.write_bytes(b"fixture")
        with (
            patch.object(blender, "_executable", return_value="fake"),
            patch.object(blender, "_launch") as launch,
        ):
            result = self.run_action(
                "export_asset", {"input_blend": "scene.blend", "export_formats": ["shell"]}
            )
        self.assertEqual(result["status"], "failed")
        launch.assert_not_called()

    def test_timeout_bounded_before_launch(self):
        with (
            patch.object(blender, "_executable", return_value="fake"),
            patch.object(blender, "_launch") as launch,
        ):
            result = self.run_action("create_demo", config={"blender_timeout_seconds": 99999})
        self.assertEqual(result["status"], "failed")
        launch.assert_not_called()

    def test_nan_does_not_verify(self):
        self.assertFalse(blender._near([float("nan")], [1.0]))

    def test_inspect_returns_native_artifact_and_hash(self):
        source = self.workspace / "source.blend"
        source.write_bytes(b"fixture, no real geometry")
        scene = {"object_count": 1, "mesh_count": 1}
        with (
            patch.object(blender, "_executable", return_value="fake"),
            patch.object(
                blender,
                "_launch",
                return_value={"blender_version": "test", "scene": scene, "outputs": []},
            ),
        ):
            result = self.run_action("inspect", {"input_blend": "source.blend"})
        self.assertEqual(result["status"], "verified")
        artifact = next(item for item in result["artifacts"] if item["kind"] == "blend")
        self.assertEqual(artifact["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        verification = json.loads((self.run_dir / "verification.json").read_text())
        self.assertTrue(verification["all_checks_passed"])

    def test_failed_roundtrip_is_not_verified(self):
        def host(executable, job, run_dir, label, timeout):
            native = run_dir / "scene.blend"
            native.write_bytes(b"fixture")
            count = 2 if label == "execute" else 1
            scene = {
                "object_names": ["Mesh"],
                "mesh_count": count,
                "modifier_count": 1,
                "material_count": 1,
                "animated_objects": [],
                "render_settings": {},
                "asset_bounds_m": {"dimensions": [1, 1, 1]},
            }
            return {
                "blender_version": "test",
                "scene": scene,
                "outputs": [{"kind": "blend", "path": str(native)}],
            }

        with (
            patch.object(blender, "_executable", return_value="fake"),
            patch.object(blender, "_launch", side_effect=host),
        ):
            result = self.run_action("create_demo", {"render": False})
        self.assertEqual(result["status"], "executed_unverified")


if __name__ == "__main__":
    unittest.main()
