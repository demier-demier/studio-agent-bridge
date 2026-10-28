"""Backend contract tests; mocks do not constitute Windows GPU validation."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backends = load_module("solo12_backend_contract", ROOT / "backends.py")
        with patch.object(sys, "path", [str(ROOT), *sys.path]):
            cls.simulate = load_module("solo12_simulate_backends", ROOT / "simulate.py")

    def test_windows_compute_choices_are_explicit_and_vendor_neutral(self):
        for name in ("cpu", "cuda", "vulkan"):
            with self.subTest(name=name):
                plan = self.backends.backend_config(name, system="Windows", environ={})
                self.assertEqual(plan["requested_backend"], name)
                self.assertNotIn("vendor", plan)
        with self.assertRaises(self.backends.BackendError):
            self.backends.backend_config("metal", system="Windows", environ={})
        with self.assertRaises(self.backends.BackendError):
            self.backends.backend_config("cuda", system="Darwin", environ={})

    def test_implicit_or_unknown_backends_are_rejected(self):
        for name in ("gpu", "auto", "amdgpu", "hip", "nvidia", ""):
            with self.subTest(name=name), self.assertRaises(self.backends.BackendError):
                self.backends.backend_config(name, system="Windows", environ={})
        for value in (0, 33, True, 1.5):
            with self.subTest(threads=value), self.assertRaises(self.backends.BackendError):
                self.backends.backend_config("cpu", cpu_threads=value, environ={})

    def test_environment_cannot_override_requested_architecture(self):
        # Even a matching TI_ARCH is rejected: command-line selection is authoritative.
        for value in ("cpu", "cuda", "vulkan", "metal"):
            with self.subTest(value=value), self.assertRaises(self.backends.BackendError):
                self.backends.backend_config("vulkan", environ={"TI_ARCH": value})

    def fake_taichi(self, actual="cuda"):
        arches = {name: SimpleNamespace(name=name) for name in self.backends.BACKENDS}
        return SimpleNamespace(
            **arches,
            f32="f32",
            __version__=(1, 7, 4),
            cfg=SimpleNamespace(arch=arches[actual]),
            init=Mock(),
            reset=Mock(),
        )

    def test_runtime_uses_requested_arch_and_disables_fallback(self):
        for name in ("cuda", "vulkan"):
            fake = self.fake_taichi(actual=name)
            with (
                self.subTest(name=name),
                patch.dict(os.environ, {}, clear=True),
                patch.object(self.backends.platform, "system", return_value="Windows"),
                patch.object(self.backends.importlib, "import_module", return_value=fake),
                patch.object(self.backends, "_probe_backend") as probe,
            ):
                result = self.backends.initialize_backend(name)
            self.assertIs(fake.init.call_args.kwargs["arch"], getattr(fake, name))
            self.assertIs(fake.init.call_args.kwargs["enable_fallback"], False)
            self.assertEqual(result["requested_backend"], result["actual_backend"])
            self.assertEqual(result["actual_arch"], name)
            probe.assert_called_once_with(fake)

    def test_unexpected_runtime_fallback_is_detected(self):
        fake = self.fake_taichi(actual="cpu")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(self.backends.platform, "system", return_value="Windows"),
            patch.object(self.backends.importlib, "import_module", return_value=fake),
            patch.object(self.backends, "_probe_backend") as probe,
            self.assertRaises(self.backends.BackendError),
        ):
            self.backends.initialize_backend("cuda")
        fake.reset.assert_called_once()
        probe.assert_not_called()

    def test_failed_driver_or_kernel_does_not_create_run_directory(self):
        for stage in ("driver initialization", "atomic kernel execution"):
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "runs" / "new-smoke"
                args = self.simulate.options(["--arch", "vulkan", "--output", str(output)])
                with (
                    self.subTest(stage=stage),
                    patch.object(
                        self.simulate,
                        "initialize_backend",
                        side_effect=self.backends.BackendError(stage),
                    ),
                    self.assertRaises(self.backends.BackendError),
                ):
                    self.simulate.run(args)
                self.assertFalse(output.exists())
                self.assertFalse(output.parent.exists())

    def test_driver_and_probe_failures_are_not_retried_on_cpu(self):
        for stage in ("init", "probe"):
            fake = self.fake_taichi()
            probe = Mock()
            if stage == "init":
                fake.init.side_effect = RuntimeError("No driver")
            else:
                probe.side_effect = RuntimeError("Kernel unsupported")
            with (
                self.subTest(stage=stage),
                patch.dict(os.environ, {}, clear=True),
                patch.object(self.backends.platform, "system", return_value="Windows"),
                patch.object(self.backends.importlib, "import_module", return_value=fake),
                patch.object(self.backends, "_probe_backend", probe),
                self.assertRaises(self.backends.BackendError),
            ):
                self.backends.initialize_backend("cuda")
            fake.init.assert_called_once()
            fake.reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
