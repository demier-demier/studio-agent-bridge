"""Independent numeric checks and a bounded surface-footprint measurement."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

p = argparse.ArgumentParser()
p.add_argument("run")
args = p.parse_args()
root = Path(args.run)
r = json.loads((root / "report.json").read_text())
with np.load(root / "trajectory.npz", allow_pickle=False) as a:
    particles = a["particles"]
    poses = a["geom"]
    loads = a["loads"]
    radius = float(a["radius"])
    fps = float(a["fps"])
    feet = poses[:, [3, 6, 9, 12], :3]
    base = poses[:, 0, :3]
    rot = poses[:, :, 3:].reshape(*poses.shape[:2], 3, 3)
    # Rasterize the visible top surface, using the same grid for reference and end state.
    spacing = 0.006
    nx = 154
    ny = 70
    low = np.array([-0.462, -0.21])

    def surface(points):
        ij = np.floor((points[:, :2] - low) / spacing).astype(int)
        ok = (ij[:, 0] >= 0) & (ij[:, 0] < nx) & (ij[:, 1] >= 0) & (ij[:, 1] < ny)
        flat = np.full(nx * ny, -np.inf, dtype=np.float32)
        np.maximum.at(flat, ij[ok, 0] * ny + ij[ok, 1], points[ok, 2] + radius)
        flat[~np.isfinite(flat)] = np.nan
        return flat.reshape(nx, ny)

    baseline = surface(particles[round(0.8 * fps)])
    final = surface(particles[-1])
    delta = final - baseline
    x = low[0] + (np.arange(nx) + 0.5) * spacing
    y = low[1] + (np.arange(ny) + 0.5) * spacing
    xx, yy = np.meshgrid(x, y, indexing="ij")
    visited = np.zeros((nx, ny), dtype=bool)
    for t in range(round(1 * fps), len(feet)):
        for pt in feet[t]:
            if pt[2] - 0.022 < 0.018:
                visited |= (xx - pt[0]) ** 2 + (yy - pt[1]) ** 2 < 0.024**2
    # Exclude the robot's final vicinity, initial settling footprint, walls and uncovered cells.
    rear_start = float(feet[round(0.8 * fps), :, 0].min())
    rear_end = float(feet[-1, :, 0].min())
    trail = visited & (xx < rear_end - 0.03) & (xx > rear_start + 0.025) & np.isfinite(delta)
    depression = trail & (delta < -0.001)
    touchdowns = []
    for k in range(4):
        lift = feet[round(1 * fps) :, k, 2] - 0.022 > 0.018
        touchdowns.append(int(np.count_nonzero(lift[:-1] & ~lift[1:])))
    checks = {
        "constant_particle_count": particles.shape[1] == r["particles"],
        "frames_match": len(particles) == len(poses) == r["frames"],
        "finite_states": bool(
            np.isfinite(particles).all() and np.isfinite(poses).all() and np.isfinite(loads).all()
        ),
        "no_grid_overflow": r["grid_overflow"] == 0,
        "no_floor_escape": float(particles[:, :, 2].min()) >= -0.001,
        "x_bounds": float(np.abs(particles[:, :, 0]).max()) <= 0.461,
        "y_bounds": float(np.abs(particles[:, :, 1]).max()) <= 0.211,
        "robot_upright": float(rot[:, 0, 2, 2].min()) > 0.85,
        "body_height_valid": float(base[:, 2].min()) > 0.12,
        "rotation_matrices": bool(np.allclose(np.linalg.det(rot), 1, atol=1e-4)),
        "forward_motion_over_15cm": float(base[-1, 0] - base[0, 0]) > 0.15,
        "feet_stay_within_tray": bool(
            (np.abs(feet[:, :, 0]) + 0.022 < 0.46).all()
            and (np.abs(feet[:, :, 1]) + 0.022 < 0.21).all()
        ),
        "terrain_reaction_measured": float(np.linalg.norm(loads, axis=-1).max()) > 0.1,
        "repeated_steps": min(touchdowns) >= 2,
        "persistent_trail_depression": int(depression.sum()) >= 20,
        "clock_consistent": bool(
            np.allclose([v["time"] for v in r["trace"]], np.arange(len(particles)) / fps, atol=1e-7)
        ),
    }
    result = {
        "all_passed": all(checks.values()),
        "checks": checks,
        "particles": r["particles"],
        "forward_travel_m": float(base[-1, 0] - base[0, 0]),
        "lateral_travel_m": float(base[-1, 1] - base[0, 1]),
        "minimum_upright_cosine": float(rot[:, 0, 2, 2].min()),
        "touchdowns_per_foot": touchdowns,
        "trail_cells": int(trail.sum()),
        "depressed_trail_cells": int(depression.sum()),
        "depressed_trail_area_cm2": float(depression.sum() * spacing**2 * 10000),
        "median_trail_height_change_mm": float(np.nanmedian(delta[trail]) * 1000)
        if trail.any()
        else None,
        "max_trail_depression_mm": float(-np.nanmin(delta[trail]) * 1000) if trail.any() else None,
        "trail_window_x_m": [rear_start + 0.025, rear_end - 0.03],
        "scope": "Surface difference behind the rearmost final foot versus t=0.8s, restricted to visited foot regions and excluding the initial rear-foot position. No calibrated soil or physical robot validation.",
    }
    np.savez_compressed(
        root / "trail-analysis.npz",
        baseline=baseline,
        final=final,
        delta=delta,
        trail=trail,
        x=x,
        y=y,
        feet=feet,
        base=base,
    )
result["trajectory_sha256"] = hashlib.file_digest(
    (root / "trajectory.npz").open("rb"), "sha256"
).hexdigest()
(root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
if not result["all_passed"]:
    raise SystemExit(1)
