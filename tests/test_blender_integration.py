"""Run only with STUDIO_BLENDER_TEST_WORKSPACE pointing to a disposable workspace."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from studio_agent_bridge.adapters.blender import execute, probe


@unittest.skipUnless(
    os.environ.get("STUDIO_BLENDER_TEST_WORKSPACE"), "Native Blender integration is opt-in"
)
class NativeBlenderTests(unittest.TestCase):
    def test_native_scene_exports_and_readonly_inspection(self):
        workspace = Path(os.environ["STUDIO_BLENDER_TEST_WORKSPACE"]).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self.assertTrue(probe({})["available"])
        with tempfile.TemporaryDirectory(prefix="blender-native-test-", dir=workspace) as directory:
            root = Path(directory)
            result = execute(
                workspace,
                {"action": "create_demo", "parameters": {"render": False}},
                root / "create",
                {},
            )
            self.assertEqual(result["status"], "verified", result["summary"])
            self.assertEqual(result["observations"]["initial_scene"]["asset_mesh_count"], 84)
            self.assertGreaterEqual(result["observations"]["initial_scene"]["modifier_count"], 80)
            for format_name in ("glb", "fbx"):
                self.assertTrue(
                    result["observations"][format_name + "_roundtrip"]["checks"][
                        "dimensions_preserved"
                    ]
                )
            native = root / "create" / "inspection_rover.blend"
            before = hashlib.sha256(native.read_bytes()).hexdigest()
            inspected = execute(
                workspace,
                {
                    "action": "inspect",
                    "parameters": {"input_blend": native.relative_to(workspace).as_posix()},
                },
                root / "inspect",
                {},
            )
            self.assertEqual(inspected["status"], "verified", inspected["summary"])
            self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
