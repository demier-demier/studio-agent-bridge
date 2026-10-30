"""Create a fresh single-state particle bed, never a recorded animation.

Requires NumPy. The live engine settles and evolves it on the GPU.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
if a.output.exists():
    p.error("Use a new output directory")
a.output.mkdir(parents=True)
r = 0.0015
spacing = r * 2.035
nx = math.floor((1.6 - 0.04) / spacing)
ny = math.floor((0.68 - 0.04) / spacing)
points = (np.indices((nx, ny, 9), dtype=np.float32).reshape(3, -1).T + 0.5) * spacing
points[:, 0] -= nx * spacing / 2
points[:, 1] -= ny * spacing / 2
points += np.random.default_rng(17).uniform(-r * 0.16, r * 0.16, points.shape).astype(np.float32)
assert len(points) == 961191
np.save(a.output / "particles.npy", np.ascontiguousarray(points[None]))
poses = np.zeros((1, 13, 12), np.float32)
poses[:, :, 3:] = np.eye(3, dtype=np.float32).reshape(9)
np.save(a.output / "robot_poses.npy", poses)
np.save(a.output / "foot_centers.npy", np.zeros((1, 4, 3), np.float32))
(a.output / "metadata.json").write_text(
    json.dumps(
        {
            "fps": 30,
            "frames": 1,
            "grain_count": len(points),
            "grain_radius_m": r,
            "foot_radius_m": 0.02,
            "initialization": "fresh_procedural_bed",
            "settle_steps": 5600,
            "future_frames": 0,
        },
        indent=2,
    )
    + "\n"
)
print(f"Fresh initial state: {len(points)} particles, one frame, no future motion")
