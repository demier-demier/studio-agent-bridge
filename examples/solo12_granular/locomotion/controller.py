"""Experimental Solo12 quasi-static crawl; no kinematic base or external support."""

import math

import mujoco
import numpy as np

LEGS = ("FL", "FR", "HL", "HR")
RADIUS = 0.020


def ik(relative, leg):
    """Exact three revolute joint IK of official Solo12 limb offsets."""
    front = leg < 2
    side = 1 if leg % 2 == 0 else -1
    hipx = 0.2141 if front else -0.2141
    x, y, z = relative - np.array([hipx, side * 0.0875, 0])
    offset = side * 0.06045
    planar_z = -math.sqrt(max(1e-8, y * y + z * z - offset * offset))
    haa = math.atan2(z, y) - math.atan2(planar_z, offset)
    haa = (haa + math.pi) % (2 * math.pi) - math.pi
    x -= -0.0195 if front else 0.0195
    cosk = np.clip((x * x + planar_z * planar_z - 2 * 0.16**2) / (2 * 0.16**2), -0.9999, 0.9999)
    knee = math.acos(cosk) * (-1 if front else 1)
    hip = math.atan2(-x, -planar_z) - math.atan2(
        0.16 * math.sin(knee), 0.16 + 0.16 * math.cos(knee)
    )
    return [haa, hip, knee]


def initialize(model, data, ground_z=0.0, x=0.0):
    data.qpos[:] = [x, 0, ground_z + 0.24, 1, 0, 0, 0] + 2 * [0, 0.8, -1.6] + 2 * [0, -0.8, 1.6]
    data.qvel[:] = 0
    data.ctrl[:] = data.qpos[7:]
    mujoco.mj_forward(model, data)


def smooth(u):
    u = np.clip(u, 0, 1)
    return u * u * u * (10 + u * (-15 + u * 6))


class SoloController:
    def __init__(
        self,
        model,
        data,
        ground_z=0.0,
        speed=0.075,
        step_time=0.60,
        shift=0.02,
        lift=0.04,
        height=0.235,
        start_time=1.0,
        balance=False,
    ):
        self.model = model
        self.data = data
        self.ground_z = ground_z
        self.speed = speed
        self.step_time = step_time
        self.shift = shift
        self.lift = lift
        self.height = height
        self.start_time = start_time
        self.origin = data.qpos[:3].copy()
        self.origin[2] = 0
        self.nominal = (
            np.array(
                [
                    [0.1946, 0.14795, ground_z + RADIUS],
                    [0.1946, -0.14795, ground_z + RADIUS],
                    [-0.1946, 0.14795, ground_z + RADIUS],
                    [-0.1946, -0.14795, ground_z + RADIUS],
                ]
            )
            + self.origin
        )
        self.feet = self.nominal.copy()
        self.order = [0, 3, 1, 2]
        self.last_phase = -1
        self.step_origin = None
        self.step_target = None
        self.targets = self.feet.copy()
        self.desired_base = np.array([0, 0, ground_z + height]) + self.origin
        self.balance = balance
        self.foot_ids = [model.body(f"{leg}_FOOT").id for leg in LEGS]
        self.jacp = np.zeros((3, model.nv))
        self.jacr = self.jacp.copy()

    def step(self, t):
        elapsed = max(0, t - self.start_time)
        phase = int(elapsed / self.step_time)
        u = (elapsed / self.step_time) % 1
        leg = self.order[phase % 4]
        prev = self.order[(phase - 1) % 4]
        base_x = self.origin[0] + self.speed * elapsed
        targetbase = np.array([base_x, 0, self.ground_z + self.height]) + np.array(
            [0, self.origin[1], 0]
        )
        if t >= self.start_time:
            if phase != self.last_phase:
                if self.last_phase >= 0:
                    old = self.order[self.last_phase % 4]
                    self.feet[old] = self.step_target
                self.step_origin = self.feet[leg].copy()
                self.step_target = self.nominal[leg].copy()
                self.step_target[0] += self.speed * (elapsed + self.step_time * 1.65)
                self.last_phase = phase

            # Shift the support target before lifting one leg; actual contacts are measured.
            def offset(k):
                return np.array([0.0, -self.shift if k % 2 == 0 else self.shift, 0])

            targetbase += offset(prev) * (1 - smooth(u / 0.25)) + offset(leg) * smooth(u / 0.25)
            self.targets = self.feet.copy()
            swing_u = np.clip((u - 0.28) / 0.60, 0, 1)
            self.targets[leg] = self.step_origin + (self.step_target - self.step_origin) * smooth(
                swing_u
            )
            self.targets[leg, 2] += self.lift * math.sin(math.pi * swing_u) ** 2
        else:
            self.targets = self.nominal.copy()
        self.desired_base = targetbase
        ctrl = np.array([ik(self.targets[k] - targetbase, k) for k in range(4)]).flatten()
        if self.balance:
            supports = [
                k for k in range(4) if not (t >= self.start_time and 0.28 < u < 0.88 and k == leg)
            ]
            rotation = self.data.xmat[1].reshape(3, 3)
            # Regulate physical base through contact/joint torques only.
            error = targetbase - self.data.qpos[:3]
            vtarget = np.array([self.speed if t >= self.start_time else 0, 0, 0])
            force = self.model.body_mass.sum() * (
                np.array([0, 0, 9.81])
                + np.array([12, 12, 30]) * error
                - np.array([3, 3, 5]) * (self.data.qvel[:3] - vtarget)
            )
            torque = (
                np.array(
                    [
                        -1.5 * rotation[2, 1],
                        1.5 * rotation[2, 0],
                        -1.2 * math.atan2(rotation[1, 0], rotation[0, 0]),
                    ]
                )
                - 0.1 * self.data.qvel[3:6]
            )
            A = np.zeros((6, 3 * len(supports)))
            for i, k in enumerate(supports):
                r = self.data.xpos[self.foot_ids[k]] - self.data.qpos[:3]
                A[:3, 3 * i : 3 * i + 3] = np.eye(3)
                A[3:, 3 * i : 3 * i + 3] = [[0, -r[2], r[1]], [r[2], 0, -r[0]], [-r[1], r[0], 0]]
            forces = np.linalg.lstsq(A, np.r_[force, torque], rcond=None)[0].reshape(-1, 3)
            motor = self.data.qfrc_bias[6:].copy()
            for i, k in enumerate(supports):
                forces[i, 2] = max(0, forces[i, 2])
                forces[i, :2] = np.clip(forces[i, :2], -0.7 * forces[i, 2], 0.7 * forces[i, 2])
                mujoco.mj_jacBody(self.model, self.data, self.jacp, self.jacr, self.foot_ids[k])
                motor -= self.jacp[:, 6:].T @ forces[i]
            ctrl += np.clip(motor, -1.5, 1.5) / 16
        self.data.ctrl[:] = ctrl
        return ctrl
