"""Independent analytic checks of the actual CPU/Metal kernels, no render required."""

import argparse
import json

import numpy as np
import taichi as ti
from walking_terrain import FOOT_R, MASS, STIFFNESS, R, Terrain

p = argparse.ArgumentParser()
p.add_argument("--backend", choices=["metal", "cpu"], required=True)
a = p.parse_args()
ti.init(arch=ti.metal if a.backend == "metal" else ti.cpu, enable_fallback=False)
checks = []
gravity = np.array([0, 0, -9.81 * MASS])


def check(name, value):
    checks.append({"name": name, "passed": bool(value)})
    if not value:
        raise AssertionError(name)


pair = Terrain(np.array([[0, 0, 0.08], [1.9 * R, 0, 0.08]], dtype=np.float32))
pair.grid()
pair.forces(0)
f = pair.force.to_numpy()
expected = STIFFNESS * 0.1 * R
check(
    "sphere-pair normal force matches analytic spring law",
    np.allclose(f[0] - gravity, [-expected, 0, 0], atol=1e-5),
)
check(
    "sphere-pair forces conserve linear momentum",
    np.allclose(f.sum(axis=0) - 2 * gravity, 0, atol=1e-6),
)
pair.v.from_numpy(np.array([[0, 1, 0], [0, 0, 0]], dtype=np.float32))
pair.forces(0)
f = pair.force.to_numpy()
check("friction opposes slip", f[0, 1] < 0 and f[1, 1] > 0)
check("friction obeys Coulomb bound", abs(f[0, 1]) <= 0.55 * abs(f[0, 0]) + 1e-6)
floor = Terrain(np.array([[0, 0, 0.8 * R]], dtype=np.float32))
floor.grid()
floor.forces(0)
check("floor restores a penetrating grain", floor.force.to_numpy()[0, 2] > 0)
contact = Terrain(np.array([[0, 0, 0.08]], dtype=np.float32))
feet = np.full((4, 3), 3, dtype=np.float32)
feet[0] = [0, 0, 0.08 + FOOT_R + R - 0.0004]
contact.foot.from_numpy(feet)
contact.grid()
contact.forces(1)
check(
    "grain and robot reaction forces balance",
    np.allclose(
        contact.force.to_numpy()[0] - gravity + contact.foot_force.to_numpy()[0], 0, atol=1e-6
    ),
)
check("robot receives upward terrain reaction", contact.foot_force.to_numpy()[0, 2] > 0.1)
contact.clear_reactions()
expected_force = np.zeros((4, 3), dtype=np.float32)
expected_torque = expected_force.copy()
for _ in range(4):
    contact.grid()
    contact.forces(1)
    expected_force += contact.foot_force.to_numpy()
    expected_torque += contact.foot_torque.to_numpy()
    contact.integrate()
check(
    "four-step GPU accumulation preserves measured reactions",
    np.allclose(contact.accumulated_force.to_numpy(), expected_force, atol=1e-6)
    and np.allclose(contact.accumulated_torque.to_numpy(), expected_torque, atol=1e-6),
)
contact.clear_reactions()
check(
    "reaction accumulator clears without residue",
    np.count_nonzero(contact.accumulated_force.to_numpy()) == 0
    and np.count_nonzero(contact.accumulated_torque.to_numpy()) == 0,
)
crowded = Terrain(np.tile([[0, 0, 0.08]], (17, 1)).astype(np.float32))
crowded.grid()
check("capacity overflow is detected", crowded.overflow[None] > 0)
print(json.dumps({"backend": str(ti.lang.impl.current_cfg().arch), "checks": checks}, indent=2))
