"""Compare completed identical-workload runs and macOS /usr/bin/time -l records.

This compares measured runs, not calibrated soil fidelity or cross-GPU parity.
Run locomotion/audit_run.py on both directories first, without --skip-particles.
"""

import argparse
import json
import re
from pathlib import Path

import numpy as np


def process_metrics(path):
    text = path.read_text()
    values = {}
    for label in ("maximum resident set size", "peak memory footprint"):
        match = re.search(r"^\s*(\d+)\s+" + label + r"\s*$", text, re.MULTILINE)
        if not match:
            raise ValueError(f"Missing macOS measurement: {label}")
        values[label.replace(" ", "_") + "_bytes"] = int(match.group(1))
    match = re.search(r"^\s*([0-9.]+)\s+real\s", text, re.MULTILINE)
    if not match:
        raise ValueError("Missing process wall time")
    values["process_wall_seconds"] = float(match.group(1))
    return values


def compare(before, after, before_time, after_time):
    metas = [json.loads((p / "metadata.json").read_text()) for p in (before, after)]
    audits = [json.loads((p / "audit.json").read_text()) for p in (before, after)]
    keys = (
        "grain_count",
        "grain_radius_m",
        "foot_radius_m",
        "frames",
        "fps",
        "seconds",
        "grain_dt",
        "rigid_dt",
        "bed_bounds",
        "backend",
    )
    for key in keys:
        if metas[0][key] != metas[1][key]:
            raise ValueError(f"Unequal workload: {key}")
    for key in (
        "preset",
        "layers",
        "start_x",
        "speed",
        "step_time",
        "shift",
        "height",
        "cpu_threads",
    ):
        if metas[0]["controller"][key] != metas[1]["controller"][key]:
            raise ValueError(f"Unequal controller: {key}")
    for key in ("backends.py", "locomotion/controller.py", "locomotion/solo12-walk.xml"):
        if metas[0]["physics_source_sha256"][key] != metas[1]["physics_source_sha256"][key]:
            raise ValueError(f"Different unchanged physics input: {key}")
    for path, meta, audit in zip((before, after), metas, audits):
        if not audit["particles"]["checked"] or not all(audit["checks"].values()):
            raise ValueError("Full trajectory/particle audit is required and must pass")
        array = np.load(path / "particles.npy", mmap_mode="r", allow_pickle=False)
        if array.shape != (meta["frames"], meta["grain_count"], 3) or array.dtype != np.dtype(
            "float32"
        ):
            raise ValueError("Particle cache shape or precision changed")
    runs = []
    for meta, audit, timer in zip(metas, audits, (before_time, after_time)):
        runs.append(
            {
                **process_metrics(timer),
                "simulation_wall_seconds": meta["simulation_wall_seconds"],
                "settling_wall_seconds": meta["warmup_seconds"],
                "forward_travel_m": audit["forward_travel_m"],
                "maximum_tilt_degrees": audit["maximum_tilt_degrees"],
                "maximum_motor_torque_nm": audit["maximum_motor_torque_nm"],
                "granular_support_percent": audit["support"][
                    "granular_share_of_recorded_vertical_support_percent"
                ],
                "audit_checks_passed": len(audit["checks"]),
                "physics_source_sha256": meta["physics_source_sha256"],
            }
        )
    return {
        "schema": "solo12-performance-comparison/v1",
        "workload": {key: metas[0][key] for key in keys},
        "before": runs[0],
        "after": runs[1],
        "speedup": runs[0]["simulation_wall_seconds"] / runs[1]["simulation_wall_seconds"],
        "reductions_percent": {
            key: 100 * (1 - runs[1][key] / runs[0][key])
            for key in (
                "simulation_wall_seconds",
                "maximum_resident_set_size_bytes",
                "peak_memory_footprint_bytes",
            )
        },
        "limits": [
            "One full run per variant; normal desktop workload, not an isolated statistical benchmark.",
            "macOS process RSS includes file mappings; footprint is a distinct OS process metric, not dedicated GPU VRAM.",
            "Floating-point atomic reduction ordering can change trajectories; bit-identical GPU results are not claimed.",
            "Particle count, float32 storage, 241 output frames and physical parameters are preserved.",
            "Rust raster timing is separate and is not included in the physics speedup.",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--before-time", required=True, type=Path)
    p.add_argument("--after-time", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    if a.output.exists():
        p.error("Use a new report path")
    report = compare(a.before, a.after, a.before_time, a.after_time)
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
