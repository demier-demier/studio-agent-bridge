"""Check a measured full-detail profile; stdlib only, no simulation or inferred results."""

import argparse
import json
import math
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def timing(record, samples):
    require(record["samples"] == samples, "Timing sample count mismatch")
    total, maximum = record["total_seconds"], record["max_seconds"]
    require(number(total) and number(maximum), "Invalid timing values")
    require(maximum <= total + 1e-9, "Maximum exceeds total")


def physics(record, steps, batches):
    require(record["completed_steps"] == steps, "Completed step count mismatch")
    require(record["completed_batches"] == batches, "Completed batch count mismatch")
    require(record["rejected_timestamp_pairs"] == 0, "Incomplete GPU timestamp evidence")
    for key in ("gpu_prepare_pass", "gpu_solver_pass"):
        timing(record[key], steps)
    for key in ("cpu_encode", "submit_to_readback"):
        timing(record[key], batches)
    require(record["gpu_solver_pass"]["total_seconds"] > 0, "Missing measured GPU work")


def validate(data):
    require(data["schema"] == "scenward-live-physics/v1", "Unexpected live schema")
    require(data["particles"] == 961191, "Full particle count required")
    require(data["grain_dt"] == 0.00005, "Unchanged DEM timestep required")
    require(data["grain_radius_m"] == 0.0015, "Unchanged grain radius required")
    require(
        data["steps"] == 48000 and data["simulation_seconds"] == 2.4,
        "Full 2.4-second workload required",
    )
    require(
        data["coupled_spheres"] == 36 and data["dynamic_render_objects"] == 48,
        "Full live laboratory coupling required",
    )
    diag = data["final_particle_diagnostics"]
    require(
        diag["all_finite"] is True and diag["count"] == 961191,
        "All particles must be retained and finite",
    )
    require(
        number(data["wall_seconds_including_render"]) and data["wall_seconds_including_render"] > 0,
        "Invalid completed wall time",
    )
    require(
        math.isclose(
            data["real_time_factor"], 2.4 / data["wall_seconds_including_render"], rel_tol=1e-10
        ),
        "Real-time factor mismatch",
    )
    p = data["profiling"]
    require(p["schema"] == "scenward-physics-profile/v1", "Unexpected profile schema")
    physics(p["loop_gpu_and_submission"], 48000, 2400)
    physics(p["settling_gpu_and_submission"], 5600, 280)
    for key in ("collect_bodies", "native_robot_step", "upload_poses", "completed_render"):
        timing(p["loop_host"][key], 2400)
    return {
        "validated": True,
        "particles": 961191,
        "loop_gpu_steps": 48000,
        "settling_gpu_steps": 5600,
        "real_time_factor": data["real_time_factor"],
        "scope": "Report consistency, not independent proof of physical accuracy or runtime speed.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    require(args.report.stat().st_size <= 1024 * 1024, "Report too large")
    print(json.dumps(validate(json.loads(args.report.read_text())), indent=2))


if __name__ == "__main__":
    main()
