"""Acceptance-boundary tests; no native application or network required."""

import copy
import hashlib
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "publication_fingerprint",
    Path(__file__).resolve().parents[1] / "examples/publication_safety/fingerprint_scene.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
compare_records, digest, quantized = module.compare_records, module.digest, module.quantized
binding_spec = importlib.util.spec_from_file_location(
    "publication_binding",
    Path(__file__).resolve().parents[1] / "examples/publication_safety/payload_binding.py",
)
binding_module = importlib.util.module_from_spec(binding_spec)
binding_spec.loader.exec_module(binding_module)


class FingerprintAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.before = {
            "objects": {"enabled": True, "count": 1, "x": 1.0},
            "evaluated_geometry": {"mesh": {"vertices": [[0.0, 1.0, 2.0]], "faces": [[0, 1, 2]]}},
        }
        self.after = copy.deepcopy(self.before)

    def test_inside_and_outside_numeric_bound(self):
        self.after["evaluated_geometry"]["mesh"]["vertices"][0][0] = 9e-7
        result = compare_records(self.before, self.after)
        self.assertTrue(result["passed"])
        self.assertEqual(result["max_evaluated_float_residual"], 9e-7)
        self.after["evaluated_geometry"]["mesh"]["vertices"][0][0] = 1.1e-6
        self.assertFalse(compare_records(self.before, self.after)["passed"])

    def test_quantization_boundary_is_not_acceptance(self):
        self.before["evaluated_geometry"]["mesh"]["vertices"][0][0] = 0.00000049
        self.after["evaluated_geometry"]["mesh"]["vertices"][0][0] = 0.00000051
        self.assertNotEqual(digest(quantized(self.before)), digest(quantized(self.after)))
        self.assertTrue(compare_records(self.before, self.after)["passed"])

    def test_array_shape_change_rejected(self):
        self.after["evaluated_geometry"]["mesh"]["vertices"][0].append(0.0)
        self.assertFalse(compare_records(self.before, self.after)["passed"])

    def test_evaluated_type_change_rejected(self):
        for value in (0, False, "0.0"):
            with self.subTest(value=value):
                self.after["evaluated_geometry"]["mesh"]["vertices"][0][0] = value
                self.assertFalse(compare_records(self.before, self.after)["passed"])

    def test_topology_change_is_exact(self):
        self.after["evaluated_geometry"]["mesh"]["faces"][0][0] = 1
        self.assertFalse(compare_records(self.before, self.after)["passed"])

    def test_non_evaluated_values_are_exact_and_typed(self):
        for key, value in (
            ("x", 1),
            ("x", 1.0 + 1e-12),
            ("enabled", 1),
            ("count", True),
            ("count", 1.0),
        ):
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(self.before)
                changed["objects"][key] = value
                self.assertFalse(compare_records(self.before, changed)["passed"])

    def test_nonfinite_values_rejected_in_every_category(self):
        for value in (math.nan, math.inf, -math.inf):
            for category in ("objects", "evaluated_geometry"):
                with self.subTest(value=value, category=category):
                    changed = copy.deepcopy(self.before)
                    if category == "objects":
                        changed[category]["x"] = value
                    else:
                        changed[category]["mesh"]["vertices"][0][0] = value
                    self.assertFalse(compare_records(changed, changed)["passed"])

    def test_missing_category_rejected(self):
        del self.after["evaluated_geometry"]
        self.assertFalse(compare_records(self.before, self.after)["passed"])


class PayloadBindingTests(unittest.TestCase):
    @staticmethod
    def record(data):
        return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def setUp(self):
        self.scenes = {"scene-day.blend": b"clean-day", "scene-night.blend": b"clean-night"}
        self.scene_records = {name: self.record(data) for name, data in self.scenes.items()}
        texture = {"path": "textures/color.png", **self.record(b"texture")}
        manifest = {
            "assets": [
                {
                    "exportFile": "meshes/model.fbx",
                    "fileSha256": self.record(b"clean-fbx")["sha256"],
                    "exportBytes": 9,
                }
            ],
            "materials": [{"textures": {"color": texture}}],
        }
        self.transfer = {
            "meshes/model.fbx": b"clean-fbx",
            "textures/color.png": b"texture",
            "environment-manifest.json": json.dumps(manifest).encode(),
        }
        self.manifest_sha = hashlib.sha256(self.transfer["environment-manifest.json"]).hexdigest()
        self.fbx_records = [
            {
                "member": "transfer/meshes/model.fbx",
                "output_sha256": self.record(b"clean-fbx")["sha256"],
                "output_bytes": 9,
            }
        ]
        self.original = {
            "industrial-lab/scene-day.blend": b"old-day",
            "industrial-lab/scene-night.blend": b"old-night",
            "industrial-lab/transfer/meshes/model.fbx": b"old-fbx",
            "industrial-lab/transfer/textures/color.png": b"texture",
            "industrial-lab/transfer/environment-manifest.json": b"old-manifest",
            "industrial-lab/model.glb": b"glb",
            "industrial-lab/scripts/example.py": b"original-script",
        }
        outer = {
            name.removeprefix("industrial-lab/"): self.record(data)
            for name, data in self.original.items()
        }
        self.original["industrial-lab/manifest.json"] = json.dumps(outer).encode()

    def bind(self):
        return binding_module.bind_payloads(
            self.original,
            self.scenes,
            self.transfer,
            self.scene_records,
            self.fbx_records,
            self.manifest_sha,
        )

    def test_returned_snapshot_does_not_follow_input_mapping_changes(self):
        snapshot = self.bind()
        self.scenes["scene-day.blend"] = b"changed-later"
        self.transfer["meshes/model.fbx"] = b"changed-later"
        self.assertEqual(snapshot["industrial-lab/scene-day.blend"], b"clean-day")
        self.assertEqual(snapshot["industrial-lab/transfer/meshes/model.fbx"], b"clean-fbx")

    def test_local_scene_mutation_rejected(self):
        self.scenes["scene-day.blend"] = b"changed-scene"
        with self.assertRaises(ValueError):
            self.bind()

    def test_local_transfer_mutations_rejected(self):
        for path in self.transfer:
            with self.subTest(path=path):
                data = self.transfer[path]
                self.transfer[path] = b"changed"
                with self.assertRaises(ValueError):
                    self.bind()
                self.transfer[path] = data

    def test_manifest_file_hash_is_not_a_substitute_for_fbx_observation(self):
        self.fbx_records[0]["output_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.bind()

    def test_added_or_missing_transfer_member_rejected(self):
        self.transfer["extra.txt"] = b"extra"
        with self.assertRaises(ValueError):
            self.bind()
        del self.transfer["extra.txt"]
        del self.transfer["textures/color.png"]
        with self.assertRaises(ValueError):
            self.bind()

    def test_historical_glb_mutation_rejected(self):
        self.original["industrial-lab/model.glb"] = b"changed-glb"
        with self.assertRaises(ValueError):
            self.bind()

    def test_existing_proof_only_directory_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "candidate"
            output.mkdir()
            proof = output / "observed-native-proof.json"
            proof.write_bytes(b"existing-evidence")
            with self.assertRaises(FileExistsError):
                binding_module.create_output_directory(root, output)
            self.assertEqual(proof.read_bytes(), b"existing-evidence")
            self.assertEqual(list(output.iterdir()), [proof])

    def test_output_cannot_escape_owned_work_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "owned"
            root.mkdir()
            outside = Path(temporary) / "outside"
            with self.assertRaises(ValueError):
                binding_module.create_output_directory(root, outside)
            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
