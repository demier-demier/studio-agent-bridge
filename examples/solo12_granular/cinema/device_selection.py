"""Explicit Cycles device selection, testable without importing Blender.

GPU selection never enables a CPU render device. Driver failures remain errors;
there is no automatic retry on another backend. Blender still uses the CPU for
scene preparation and other host work while rendering on a GPU.
"""

DEVICES = ("CPU", "METAL", "CUDA", "OPTIX", "HIP")


def resolve_device(requested=None, *, cpu=False, system=""):
    """Keep the original Mac default; use CPU elsewhere until a GPU is requested."""
    if requested is not None and requested not in DEVICES:
        raise ValueError(f"Unknown render device: {requested}")
    if cpu and requested not in (None, "CPU"):
        raise ValueError("--cpu cannot be combined with a GPU --device")
    if cpu:
        return "CPU"
    return requested or ("METAL" if system == "Darwin" else "CPU")


def configure_device(scene, preferences, requested):
    """Select only actual devices returned for the requested Cycles backend."""
    if requested not in DEVICES:
        raise ValueError(f"Unknown render device: {requested}")
    if requested == "CPU":
        # Do not initialize graphics drivers for the portable CPU path.
        scene.cycles.device = "CPU"
        return {
            "requested_device": requested,
            "device": "CPU",
            "selected_devices": [{"name": "Host CPU", "type": "CPU"}],
            "cpu_render_device_enabled": True,
        }
    try:
        preferences.compute_device_type = requested
        available = list(preferences.get_devices_for_type(requested))
    except (TypeError, ValueError, RuntimeError) as exc:
        raise RuntimeError(
            f"Cycles {requested} is unavailable in this Blender build or driver. "
            "Install a compatible official Blender build and GPU driver, or explicitly "
            "request --device CPU. No CPU fallback was selected."
        ) from exc
    # This collection can retain devices from previous backend queries. Disable
    # every stale entry as well as CPU entries before selecting the fresh result.
    for device in preferences.devices:
        device.use = False
    selected = [device for device in available if device.type == requested]
    if not selected:
        raise RuntimeError(
            f"Cycles found no {requested} GPU. Check hardware/driver compatibility "
            "or explicitly request --device CPU. No CPU fallback was selected."
        )
    for device in selected:
        device.use = True
    scene.cycles.device = "GPU"
    return {
        "requested_device": requested,
        "device": requested,
        "selected_devices": [{"name": device.name, "type": device.type} for device in selected],
        "cpu_render_device_enabled": False,
    }
