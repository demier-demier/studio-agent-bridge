"""Controller geometry checks independent of the DEM integration."""

import json

import numpy as np
from walking_terrain import gait

period = 0.85
q = np.array([[gait(t, k) for k in range(4)] for t in np.linspace(0, 3 * period, 501)])
checks = {
    "finite_targets": bool(np.isfinite(q).all()),
    "hip_targets_inside_joint_limits": bool((np.abs(q[:, :, 0]) < 1).all()),
    "knee_targets_inside_joint_limits": bool(((q[:, :, 1] >= 0) & (q[:, :, 1] <= 2.5)).all()),
    "periodic_targets": all(np.allclose(gait(0.123, k), gait(0.123 + period, k)) for k in range(4)),
    "diagonal_pairs_agree": bool(np.allclose(q[:, 0], q[:, 3]) and np.allclose(q[:, 1], q[:, 2])),
}
h = q[:, :, 0]
k = q[:, :, 1]
x = -0.075 * (np.sin(h) + np.sin(h + k))
z = -0.075 * (np.cos(h) + np.cos(h + k))
checks["forward_and_return_foot_path"] = float(x.max() - x.min()) > 0.039
checks["four_cm_commanded_clearance"] = float(z.max() - z.min()) > 0.039
checks["leg_endpoint_within_workspace"] = bool((np.sqrt(x * x + z * z) < 0.15).all())
result = {
    "checks": checks,
    "all_passed": all(checks.values()),
    "scope": "Target geometry only; dynamic acceptance is checked separately.",
}
print(json.dumps(result, indent=2))
assert result["all_passed"]
