"""Original bounded translational DEM on Metal, coupled to MuJoCo foot forces.

This is not Cosmos, a Newton port, calibrated soil, or a learned locomotion policy.
"""

import argparse
import json
import math
import time
from pathlib import Path

import mujoco
import numpy as np
import taichi as ti

R = 0.003
MASS = 2500 * 4 / 3 * math.pi * R**3
DT = 0.0001
CELL = 2.2 * R
SHAPE = (72, 52, 32)
LOW = np.array([-0.2376, -0.1716, -0.02], dtype=np.float32)
CAPACITY = 16
STIFFNESS = 1000.0
DAMPING = 0.25
FRICTION = 0.55
FOOT_R = 0.022


@ti.data_oriented
class Terrain:
    def __init__(self, points):
        self.n = len(points)
        self.x = ti.Vector.field(3, ti.f32, shape=self.n)
        self.v = ti.Vector.field(3, ti.f32, shape=self.n)
        self.force = ti.Vector.field(3, ti.f32, shape=self.n)
        self.count = ti.field(ti.i32, shape=SHAPE)
        self.indices = ti.field(ti.i32, shape=(*SHAPE, CAPACITY))
        self.overflow = ti.field(ti.i32, shape=())
        self.foot = ti.Vector.field(3, ti.f32, shape=4)
        self.foot_v = ti.Vector.field(3, ti.f32, shape=4)
        self.foot_force = ti.Vector.field(3, ti.f32, shape=4)
        self.foot_torque = ti.Vector.field(3, ti.f32, shape=4)
        self.x.from_numpy(points)

    @ti.func
    def contact(self, normal, overlap, relative):
        vn = relative.dot(normal)
        magnitude = ti.max(0.0, STIFFNESS * overlap - DAMPING * vn)
        tangent = relative - vn * normal
        tangential = -ti.min(DAMPING, FRICTION * magnitude / (tangent.norm() + 1e-8)) * tangent
        return magnitude * normal + tangential

    @ti.kernel
    def grid(self):
        for cell_index in ti.grouped(self.count):
            self.count[cell_index] = 0
        for k in range(4):
            self.foot_force[k] = ti.Vector.zero(ti.f32, 3)
            self.foot_torque[k] = ti.Vector.zero(ti.f32, 3)
        for i in self.x:
            c = ti.cast(ti.floor((self.x[i] - ti.Vector([-0.2376, -0.1716, -0.02])) / CELL), ti.i32)
            if 0 <= c[0] < SHAPE[0] and 0 <= c[1] < SHAPE[1] and 0 <= c[2] < SHAPE[2]:
                slot = ti.atomic_add(self.count[c], 1)
                if slot < CAPACITY:
                    self.indices[c[0], c[1], c[2], slot] = i
                else:
                    ti.atomic_add(self.overflow[None], 1)
            else:
                ti.atomic_add(self.overflow[None], 1)

    @ti.kernel
    def forces(self, feet_active: ti.i32):
        for i in self.x:
            f = ti.Vector([0.0, 0.0, -9.81 * MASS])
            c = ti.cast(ti.floor((self.x[i] - ti.Vector([-0.2376, -0.1716, -0.02])) / CELL), ti.i32)
            for off in ti.grouped(ti.ndrange((-1, 2), (-1, 2), (-1, 2))):
                other = c + off
                if (
                    0 <= other[0] < SHAPE[0]
                    and 0 <= other[1] < SHAPE[1]
                    and 0 <= other[2] < SHAPE[2]
                ):
                    for j in range(ti.min(self.count[other], CAPACITY)):
                        idx = self.indices[other[0], other[1], other[2], j]
                        if idx != i:
                            delta = self.x[i] - self.x[idx]
                            distance = delta.norm()
                            if 1e-8 < distance < 2 * R:
                                f += self.contact(
                                    delta / distance, 2 * R - distance, self.v[i] - self.v[idx]
                                )
            # Analytic rigid tray boundaries; no positional clamping or particle deletion.
            for axis in ti.static(range(3)):
                lower = -0.218
                upper = 0.218
                if ti.static(axis == 1):
                    lower = -0.150
                    upper = 0.150
                if ti.static(axis == 2):
                    lower = 0.0
                    upper = 0.160
                for side in ti.static(range(2)):
                    n = ti.Vector.zero(ti.f32, 3)
                    n[axis] = 1.0 if side == 0 else -1.0
                    overlap = (
                        lower + R - self.x[i][axis] if side == 0 else self.x[i][axis] + R - upper
                    )
                    if overlap > 0:
                        f += self.contact(n, overlap, self.v[i])
            if feet_active:
                for k in ti.static(range(4)):
                    delta = self.x[i] - self.foot[k]
                    distance = delta.norm()
                    if 1e-8 < distance < R + FOOT_R:
                        n = delta / distance
                        force = self.contact(n, R + FOOT_R - distance, self.v[i] - self.foot_v[k])
                        f += force
                        ti.atomic_add(self.foot_force[k], -force)
                        ti.atomic_add(self.foot_torque[k], (-FOOT_R * n).cross(-force))
            self.force[i] = f

    @ti.kernel
    def integrate(self):
        for i in self.x:
            self.v[i] += DT * self.force[i] / MASS
            self.x[i] += DT * self.v[i]


def robot_xml():
    limbs = []
    actuators = []
    for i, (x, y) in enumerate([(0.08, 0.065), (0.08, -0.065), (-0.08, 0.065), (-0.08, -0.065)]):
        limbs.append(f'''<body name="hip{i}" pos="{x} {y} 0">
          <joint name="hip{i}" axis="0 1 0" range="-1 1"/>
          <geom type="capsule" fromto="0 0 0 0 0 -.075" size=".009" mass=".06"/>
          <body name="knee{i}" pos="0 0 -.075">
            <joint name="knee{i}" axis="0 1 0" range="-1.6 1.6"/>
            <geom type="capsule" fromto="0 0 0 0 0 -.075" size=".007" mass=".04"/>
            <body name="foot{i}" pos="0 0 -.075">
              <geom name="footgeom{i}" type="sphere" size="{FOOT_R}" mass=".025" contype="1" conaffinity="1"/>
            </body>
          </body></body>''')
        for name in [f"hip{i}", f"knee{i}"]:
            actuators.append(
                f'<position name="{name}" joint="{name}" kp="6" kv=".18" forcerange="-.8 .8"/>'
            )
    return f'''<mujoco model="original_terrain_quadruped"><compiler angle="radian"/>
      <option timestep="{DT}" gravity="0 0 -9.81" integrator="implicitfast"/>
      <default><joint damping=".025" armature=".002"/><geom contype="0" conaffinity="0"/></default>
      <worldbody><geom name="tray_substrate" type="plane" size="1 1 .01" contype="1" conaffinity="1" friction=".6 .01 .001"/>
      <body name="chassis" pos="0 0 .23"><freejoint/>
      <geom type="box" size=".10 .05 .027" mass=".65"/>{"".join(limbs)}</body></worldbody>
      <actuator>{"".join(actuators)}</actuator></mujoco>'''


def run(args):
    global R, MASS, DT, CELL, SHAPE, STIFFNESS, DAMPING
    scale = args.grain_scale
    if scale not in (1, 2) or not 0 < args.seconds <= 12:
        raise ValueError("Use grain-scale 1 or 2 and a duration of at most 12 seconds")
    R /= scale
    MASS /= scale**3
    DT /= scale
    CELL /= scale
    SHAPE = tuple(d * scale for d in SHAPE)
    STIFFNESS /= scale
    DAMPING /= scale**2
    ti.init(
        arch=ti.metal if args.backend == "metal" else ti.cpu,
        enable_fallback=False,
        default_fp=ti.f32,
        random_seed=7,
        offline_cache=True,
    )
    rng = np.random.default_rng(7)
    nx, ny, nz = (52, 36, 6) if not args.small else (24, 20, 3)
    nx *= scale
    ny *= scale
    nz *= scale
    grid = (
        np.array(
            np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij"),
            dtype=np.float32,
        )
        .reshape(3, -1)
        .T
    )
    points = (grid + 0.5) * (0.0061 / scale)
    points[:, 0] -= nx * 0.0061 / (2 * scale)
    points[:, 1] -= ny * 0.0061 / (2 * scale)
    points += rng.uniform(-0.00008 / scale, 0.00008 / scale, points.shape).astype(np.float32)
    terrain = Terrain(points)
    model = mujoco.MjModel.from_xml_string(robot_xml())
    data = mujoco.MjData(model)
    foot_ids = np.array([model.body(f"foot{i}").id for i in range(4)])
    geom_ids = np.array([model.geom(f"footgeom{i}").id for i in range(4)])
    data.qpos[7:] = np.tile([-0.24, 0.48], 4)
    data.ctrl[:] = data.qpos[7:]
    mujoco.mj_forward(model, data)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    frames = []
    robots = []
    loads = []
    trace = []
    start = time.perf_counter()
    # Settle grains before releasing the free body.
    for _ in range(800):
        terrain.grid()
        terrain.forces(0)
        terrain.integrate()
    ti.sync()
    warm_seconds = time.perf_counter() - start
    settled = terrain.x.to_numpy().copy()
    steps = round(args.seconds / DT)
    stride = round(1 / (30 * DT))
    sample_count = 0
    accumulated = np.zeros((4, 3), dtype=np.float32)
    torques = accumulated.copy()
    v6 = np.zeros(6)
    simstart = time.perf_counter()
    # Readback/rigid integration every 4 DEM steps, a declared partitioned coupling.
    model.opt.timestep = 4 * DT
    for step in range(steps):
        t = step * DT
        if step % 4 == 0:
            positions = data.geom_xpos[geom_ids].astype(np.float32)
            velocities = []
            for bid in foot_ids:
                mujoco.mj_objectVelocity(model, data, mujoco.mjtObj.mjOBJ_BODY, int(bid), v6, 0)
                velocities.append(v6[3:].copy())
            terrain.foot.from_numpy(positions)
            terrain.foot_v.from_numpy(np.array(velocities, dtype=np.float32))
        terrain.grid()
        terrain.forces(1)
        terrain.integrate()
        accumulated += terrain.foot_force.to_numpy()
        torques += terrain.foot_torque.to_numpy()
        if step % 4 == 3:
            data.xfrc_applied[:] = 0
            data.xfrc_applied[foot_ids, :3] = accumulated / 4
            data.xfrc_applied[foot_ids, 3:] = torques / 4
            # Bounded classical posture modulation after settling; no learned policy.
            t = step * DT
            sway = 0.045 * math.sin(2 * math.pi * 0.7 * (t - 0.8)) if t > 0.8 else 0.0
            data.ctrl[:] = np.tile([-0.24 + sway, 0.48], 4)
            mujoco.mj_step(model, data)
            accumulated[:] = 0
            torques[:] = 0
        if step % stride == 0:
            p = terrain.x.to_numpy()
            vv = terrain.v.to_numpy()
            if not np.isfinite(p).all() or not np.isfinite(data.qpos).all():
                raise RuntimeError("Non-finite state")
            if terrain.overflow[None]:
                raise RuntimeError("Spatial-grid overflow: simulation rejected")
            if p[:, 2].min() < -0.004 or np.max(np.abs(p[:, :2])) > 0.222:
                raise RuntimeError("Boundary penetration: simulation rejected")
            if data.qpos[2] < 0.12:
                raise RuntimeError("Robot base fell below valid operating height; rejected")
            if data.xmat[1, 8] < 0.85:
                raise RuntimeError("Robot tilted outside accepted posture; rejected")
            frames.append(p)
            robots.append(
                np.concatenate([data.geom_xpos[1:], data.geom_xmat[1:]], axis=1).astype(np.float32)
            )
            loads.append(data.xfrc_applied[foot_ids, :3].copy())
            trace.append(
                {
                    "time": t,
                    "base_z": float(data.qpos[2]),
                    "speed_max": float(np.linalg.norm(vv, axis=1).max()),
                }
            )
            sample_count += 1
        if step % 2000 == 0:
            print(
                json.dumps(
                    {"step": step, "seconds": step * DT, "wall": time.perf_counter() - simstart}
                ),
                flush=True,
            )
    ti.sync()
    wall = time.perf_counter() - simstart
    np.savez_compressed(
        out / "trajectory.npz",
        particles=np.array(frames),
        geom=np.array(robots),
        loads=np.array(loads),
        fps=1 / (stride * DT),
        radius=R,
        settled=settled,
    )
    report = {
        "backend": str(ti.lang.impl.current_cfg().arch),
        "hardware": "Measured at runtime; see host proof",
        "particles": terrain.n,
        "seconds": steps * DT,
        "frames": sample_count,
        "warmup_and_settle_seconds": warm_seconds,
        "wall_seconds": wall,
        "physics_dt": DT,
        "rigid_dt": model.opt.timestep,
        "grid_overflow": int(terrain.overflow[None]),
        "min_z": float(np.min(np.array(frames)[:, :, 2])),
        "max_particle_displacement": float(np.max(np.linalg.norm(frames[-1] - settled, axis=1))),
        "robot_final_base_z": float(data.qpos[2]),
        "trace": trace,
        "limitations": [
            "Original coarse spherical-grain translational DEM; no grain angular dynamics or calibrated sand parameters.",
            "Newton/Cosmos models not executed by this Metal solver; MuJoCo integrates original free-body robot dynamics on CPU.",
            "Eight actuated joints with prescribed posture control, not learned locomotion.",
            "Only feet contact grains; no chassis/limb terrain contacts. No hardware validation.",
        ],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "robot.xml").write_text(robot_xml())
    print(
        json.dumps({k: v for k, v in report.items() if k not in ["trace", "limitations"]}),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["cpu", "metal"], default="metal")
    p.add_argument("--grain-scale", type=int, default=1)
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--small", action="store_true")
    p.add_argument("--output", required=True)
    run(p.parse_args())
