"""Independent trajectory acceptance checks; does not import the simulator."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

p = argparse.ArgumentParser()
p.add_argument("run")
args = p.parse_args()
root = Path(args.run)
report = json.loads((root / "report.json").read_text())
with np.load(root / "trajectory.npz", allow_pickle=False) as data:
    q = data["particles"]
    poses = data["geom"]
    loads = data["loads"]
    fps = float(data["fps"])
    matrices = poses[:, :, 3:].reshape(*poses.shape[:2], 3, 3)
    checks = {
        "constant_particle_count": q.shape[1] == report["particles"],
        "frame_count_matches": q.shape[0] == report["frames"] == len(report["trace"]),
        "all_states_finite": bool(
            np.isfinite(q).all() and np.isfinite(poses).all() and np.isfinite(loads).all()
        ),
        "no_grid_overflow": report["grid_overflow"] == 0,
        "floor_tolerance_1mm": float(q[:, :, 2].min()) >= -0.001,
        "x_boundary_tolerance_1mm": float(np.abs(q[:, :, 0]).max()) <= 0.219,
        "y_boundary_tolerance_1mm": float(np.abs(q[:, :, 1]).max()) <= 0.151,
        "robot_stays_upright": float(matrices[:, 0, 2, 2].min()) >= 0.85,
        "robot_base_above_tray": min(x["base_z"] for x in report["trace"]) >= 0.12,
        "rigid_rotation_determinants": bool(np.allclose(np.linalg.det(matrices), 1.0, atol=1e-4)),
        "terrain_reaction_present": float(np.linalg.norm(loads, axis=2).max()) > 0.1,
        "particles_actually_move": float(np.linalg.norm(q[-1] - q[0], axis=1).max()) > 0.005,
        "frame_clock_matches": bool(
            np.allclose([x["time"] for x in report["trace"]], np.arange(len(q)) / fps, atol=1e-7)
        ),
    }
    result = {
        "schema": 1,
        "checks": checks,
        "all_passed": all(checks.values()),
        "particles": report["particles"],
        "peak_recorded_foot_reaction_N": float(np.linalg.norm(loads, axis=2).max()),
        "maximum_recorded_particle_speed_m_s": max(x["speed_max"] for x in report["trace"]),
        "minimum_body_upright_cosine": float(matrices[:, 0, 2, 2].min()),
        "trajectory_sha256": hashlib.sha256((root / "trajectory.npz").read_bytes()).hexdigest(),
        "scope": "Numerical sanity of this trace; not hardware validation or model fidelity certification.",
    }
(root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
if not result["all_passed"]:
    raise SystemExit(1)
