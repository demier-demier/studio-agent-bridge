"""Device-selection contracts, not GPU execution benchmarks."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cinema.device_selection import configure_device, resolve_device  # noqa: E402


class FakePreferences:
    def __init__(self, devices):
        self.devices = devices
        self.queries = []
        self.compute_device_type = None

    def get_devices_for_type(self, requested):
        self.queries.append(requested)
        return [d for d in self.devices if d.type in (requested, "CPU")]


def device(kind, name=None):
    return SimpleNamespace(type=kind, name=name or kind, use=True)


class RenderDeviceTests(unittest.TestCase):
    def test_cpu_does_not_query_gpu_driver(self):
        scene = SimpleNamespace(cycles=SimpleNamespace(device=None))
        report = configure_device(scene, None, "CPU")
        self.assertEqual(scene.cycles.device, "CPU")
        self.assertEqual(report["selected_devices"], [{"name": "Host CPU", "type": "CPU"}])

    def test_each_gpu_backend_selects_only_its_matching_devices(self):
        for backend in ("METAL", "CUDA", "OPTIX", "HIP"):
            with self.subTest(backend=backend):
                entries = [device("CPU"), device(backend, "GPU A"), device(backend, "GPU B")]
                other = "HIP" if backend != "HIP" else "CUDA"
                entries.append(device(other))
                preferences = FakePreferences(entries)
                scene = SimpleNamespace(cycles=SimpleNamespace(device=None))
                report = configure_device(scene, preferences, backend)
                self.assertEqual(preferences.queries, [backend])
                self.assertEqual(scene.cycles.device, "GPU")
                self.assertEqual([d.use for d in entries], [False, True, True, False])
                self.assertEqual(len(report["selected_devices"]), 2)
                self.assertFalse(report["cpu_render_device_enabled"])

    def test_no_compatible_gpu_fails_without_enabling_cpu(self):
        prefs = FakePreferences([device("CPU"), device("HIP")])
        scene = SimpleNamespace(cycles=SimpleNamespace(device=None))
        with self.assertRaisesRegex(RuntimeError, "no CUDA GPU"):
            configure_device(scene, prefs, "CUDA")
        self.assertTrue(all(not d.use for d in prefs.devices))
        self.assertIsNone(scene.cycles.device)

    def test_unsupported_blender_backend_is_actionable_error(self):
        class Unsupported:
            @property
            def compute_device_type(self):
                return "NONE"

            @compute_device_type.setter
            def compute_device_type(self, value):
                raise TypeError("enum not found")

        scene = SimpleNamespace(cycles=SimpleNamespace(device=None))
        with self.assertRaisesRegex(RuntimeError, "No CPU fallback"):
            configure_device(scene, Unsupported(), "OPTIX")
        self.assertIsNone(scene.cycles.device)

    def test_defaults_and_compatibility_alias_are_unambiguous(self):
        self.assertEqual(resolve_device(system="Darwin"), "METAL")
        self.assertEqual(resolve_device(system="Windows"), "CPU")
        self.assertEqual(resolve_device("HIP", system="Windows"), "HIP")
        self.assertEqual(resolve_device(cpu=True, system="Darwin"), "CPU")
        self.assertEqual(resolve_device("CPU", cpu=True), "CPU")
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            resolve_device("CUDA", cpu=True)

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            configure_device(None, None, "AUTO")
        with self.assertRaises(ValueError):
            resolve_device("AUTO")


if __name__ == "__main__":
    unittest.main()
