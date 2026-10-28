"""Analytic and cell-migration checks of the actual DEM kernels."""

import argparse
import json
from pathlib import Path

import numpy as np
import taichi as ti
from granular import GranularBed

p = argparse.ArgumentParser()
p.add_argument("--backend", choices=["cpu", "metal"], required=True)
p.add_argument("--output", type=Path)
a = p.parse_args()
ti.init(
    arch=ti.metal if a.backend == "metal" else ti.cpu, enable_fallback=False, cpu_max_num_threads=1
)
checks = []


def check(name, ok):
    checks.append({"name": name, "passed": bool(ok)})
    if not ok:
        raise AssertionError(name)


def bed(points):
    return GranularBed(
        np.array(points, dtype=np.float32), half_x=0.05, half_y=0.05, foot_radius=0.02
    )


t = bed([[0, 0, 0.04], [0.00285, 0, 0.04]])
t.grid()
t.forces(0)
f = t.force.to_numpy()
gravity = np.array([0, 0, -9.81 * t.mass])
check(
    "pair normal force matches spring law",
    np.allclose(f[0] - gravity, [-t.stiffness * 0.00015, 0, 0], atol=1e-6),
)
check("pair conserves linear momentum", np.allclose(f.sum(0) - 2 * gravity, 0, atol=1e-6))
old_counts = t.count.to_numpy().copy()
t.x.from_numpy(np.array([[0.03, 0, 0.04], [0.03285, 0, 0.04]], np.float32))
t.grid()
counts = t.count.to_numpy()
check("old occupied cells clear after migration", np.count_nonzero(counts[old_counts > 0]) == 0)
check("migrated particles counted once", counts.sum() == 2)
u = bed([[0, 0, 0.04]])
feet = np.full((4, 3), 1, dtype=np.float32)
feet[0] = [0, 0, 0.04 + u.fr + u.r - 0.0004]
u.foot.from_numpy(feet)
u.grid()
u.forces(1)
check(
    "foot reaction equals opposite particle contact",
    np.allclose(u.force.to_numpy()[0] - gravity + u.step_reactions.to_numpy()[0], 0, atol=1e-6),
)
check("robot receives upward reaction", u.step_reactions.to_numpy()[0, 2] > 0.1)
u.foot_v.from_numpy(np.array([[0.1, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]], np.float32))
u.grid()
u.forces(1)
force_on_foot = u.step_reactions.to_numpy()[0]
check(
    "foot tangential torque uses center-to-contact arm",
    np.allclose(u.step_reactions.to_numpy()[4], np.cross([0, 0, -u.fr], force_on_foot), atol=1e-7),
)
u.foot_v.fill(0)
u.foot_w.from_numpy(np.array([[0, 1, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]], np.float32))
u.grid()
u.forces(1)
check("foot spin contributes contact velocity", u.step_reactions.to_numpy()[0, 0] > 0)
u.foot_w.fill(0)
u.clear_reactions()
expected = np.zeros((8, 3), np.float32)
for _ in range(20):
    u.grid()
    u.forces(1)
    expected += u.step_reactions.to_numpy()
    u.integrate()
check(
    "20-step reactions integrate without dropped forces",
    np.allclose(u.reactions.to_numpy(), expected, atol=1e-6),
)
u.clear_reactions()
check("reaction reset", not u.reactions.to_numpy().any())
c = bed([[0, 0, 0.04]] * 17)
c.grid()
check("overflow rejected", c.overflow[None] > 0)
b = bed([[0, 0, 0.001]])
b.grid()
b.forces(0)
check("floor restoring force", b.force.to_numpy()[0, 2] > 0)
r = {"backend": a.backend, "checks": checks}
if a.output:
    a.output.write_text(json.dumps(r, indent=2) + "\n")
print(json.dumps(r, indent=2))
