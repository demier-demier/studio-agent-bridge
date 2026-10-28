"""Explicit Taichi compute selection with no backend fallback.

A backend name identifies a compute API, not a GPU vendor or physical device.
Use check_granular.py for the complete contact-kernel checks on the target machine.
"""

import argparse
import importlib
import json
import os
import platform

BACKENDS = ("cpu", "metal", "cuda", "vulkan")


class BackendError(RuntimeError):
    """The requested backend could not be initialized and verified."""


def backend_config(name, *, cpu_threads=4, environ=None, system=None):
    """Validate user choices without initializing a runtime or writing files."""
    if name not in BACKENDS:
        raise BackendError(f"Unknown backend {name!r}; choose one of {', '.join(BACKENDS)}.")
    if (
        isinstance(cpu_threads, bool)
        or not isinstance(cpu_threads, int)
        or not 1 <= cpu_threads <= 32
    ):
        raise BackendError("CPU thread count must be an integer from 1 to 32.")
    env = os.environ if environ is None else environ
    host = platform.system() if system is None else system
    # In Taichi 1.7.4 TI_ARCH overrides even an explicit ti.init(arch=...).
    # Reject it rather than allowing the command and evidence to disagree.
    if env.get("TI_ARCH"):
        raise BackendError(
            "Remove TI_ARCH from the environment; select the backend on the command line."
        )
    if name == "metal" and host != "Darwin":
        raise BackendError("Metal requires macOS; choose cuda, vulkan or cpu on Windows/Linux.")
    if name == "cuda" and host not in ("Windows", "Linux"):
        raise BackendError("This CUDA route requires Windows/Linux and a compatible NVIDIA driver.")
    return {"requested_backend": name, "cpu_threads": cpu_threads, "system": host}


def _probe_backend(ti):
    """Compile and execute the field/atomic operations used by the DEM solver."""
    integer_sum = ti.field(ti.i32, shape=())
    floating_sum = ti.field(ti.f32, shape=())

    @ti.kernel
    def probe():
        for i in range(32):
            ti.atomic_add(integer_sum[None], i + 1)
            ti.atomic_add(floating_sum[None], (i + 1) * 0.5)

    probe()
    ti.sync()
    if integer_sum[None] != 528 or floating_sum[None] != 264.0:
        raise BackendError("The selected backend failed its integer/float atomic execution check.")


def initialize_backend(name, *, cpu_threads=4):
    """Initialize exactly one requested API, verify it, and run a small kernel."""
    selection = backend_config(name, cpu_threads=cpu_threads)
    ti = importlib.import_module("taichi")
    requested_arch = getattr(ti, name)
    try:
        ti.init(
            arch=requested_arch,
            enable_fallback=False,
            default_fp=ti.f32,
            offline_cache=True,
            random_seed=17,
            cpu_max_num_threads=cpu_threads,
        )
        actual_arch = ti.cfg.arch
        if actual_arch != requested_arch:
            raise BackendError("Runtime backend differs from the explicitly requested backend.")
        _probe_backend(ti)
    except Exception as error:
        ti.reset()
        if isinstance(error, BackendError):
            raise
        raise BackendError(
            f"Cannot execute the requested {name} backend. Check the GPU driver and Taichi support. "
            "No CPU fallback was used; choose cpu explicitly for a CPU run."
        ) from error
    return {
        **selection,
        "actual_backend": name,
        "actual_arch": actual_arch.name,
        "taichi_version": ".".join(map(str, ti.__version__)),
        "fallback_enabled": False,
        "atomic_execution_probe": "passed",
        "device_identity": "Not queried; a compute API does not prove a GPU vendor or model.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    try:
        result = initialize_backend(args.backend, cpu_threads=args.cpu_threads)
    except BackendError as error:
        parser.exit(1, f"Backend check failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
