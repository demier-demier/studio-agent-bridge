"""Translational sphere DEM with dense or bounded hashed storage.

Experimental visual simulation: no angular grain dynamics or calibrated soil.
All represented grains are integrated; no painted tracks or particle deletion.
"""

import math

import numpy as np
import taichi as ti


@ti.data_oriented
class GranularBed:
    def __init__(
        self,
        points,
        radius=0.0015,
        half_x=0.80,
        half_y=0.34,
        dt=0.00005,
        foot_radius=0.015,
        grid_layout="dense",
    ):
        if grid_layout not in {"dense", "hashed"}:
            raise ValueError("grid_layout must be dense or hashed")
        self.grid_layout = grid_layout
        self.hashed = grid_layout == "hashed"
        self.n = len(points)
        self.r = radius
        self.mass = 2500 * 4 / 3 * math.pi * radius**3
        self.dt = dt
        self.cell = 2.2 * radius
        self.hx, self.hy = half_x, half_y
        self.low = (-half_x - 0.01, -half_y - 0.01, -0.01)
        self.shape = (
            math.ceil((2 * half_x + 0.02) / self.cell),
            math.ceil((2 * half_y + 0.02) / self.cell),
            math.ceil(0.22 / self.cell),
        )
        self.capacity = 16
        self.stiffness = 1000 * radius / 0.003
        self.damping = 0.25 * (radius / 0.003) ** 2
        self.friction = 0.65
        self.fr = foot_radius
        self.x = ti.Vector.field(3, ti.f32, shape=self.n)
        self.v = ti.Vector.field(3, ti.f32, shape=self.n)
        self.force = ti.Vector.field(3, ti.f32, shape=self.n)
        # Neighbour offsets in the 27-cell stencil span less than this table.
        # Thus two queried neighbour cells cannot alias one another. Other,
        # distant cells may share buckets; exact sphere tests filter them.
        stencil_span = 2 * self.shape[1] * self.shape[2] + 2 * self.shape[2] + 3
        self.hash_size = 1 << (max(2 * self.n, stencil_span) - 1).bit_length()
        self.count = ti.field(ti.i32, shape=self.hash_size if self.hashed else self.shape)
        if self.hashed:
            self.indices = ti.field(ti.i32, shape=(self.capacity, self.hash_size))
        else:
            self.indices = ti.field(ti.i32, shape=(*self.shape, self.capacity))
        self.active = ti.Vector.field(3, ti.i32, shape=self.n)
        self.active_count = ti.field(ti.i32, shape=())
        self.overflow = ti.field(ti.i32, shape=())
        self.foot = ti.Vector.field(3, ti.f32, shape=4)
        self.foot_v = ti.Vector.field(3, ti.f32, shape=4)
        self.foot_w = ti.Vector.field(3, ti.f32, shape=4)
        self.reactions = ti.Vector.field(3, ti.f32, shape=8)
        self.step_reactions = ti.Vector.field(3, ti.f32, shape=8)
        self.x.from_numpy(np.asarray(points, dtype=np.float32))

    @ti.func
    def contact(self, normal, overlap, velocity):
        vn = velocity.dot(normal)
        magnitude = ti.max(0.0, self.stiffness * overlap - self.damping * vn)
        tangent = velocity - vn * normal
        return (
            magnitude * normal
            - ti.min(self.damping, self.friction * magnitude / (tangent.norm() + 1e-8)) * tangent
        )

    @ti.func
    def bucket(self, cell):
        return ((cell[0] * self.shape[1] + cell[1]) * self.shape[2] + cell[2]) & (
            self.hash_size - 1
        )

    @ti.func
    def cell_count(self, cell):
        value = 0
        if ti.static(self.hashed):
            value = self.count[self.bucket(cell)]
        else:
            value = self.count[cell]
        return value

    @ti.kernel
    def grid(self):
        for k in range(self.active_count[None]):
            if ti.static(self.hashed):
                self.count[self.bucket(self.active[k])] = 0
            else:
                self.count[self.active[k]] = 0
        self.active_count[None] = 0
        for k in range(8):
            self.step_reactions[k] = ti.Vector.zero(ti.f32, 3)
        for i in self.x:
            cell = ti.cast(ti.floor((self.x[i] - ti.Vector(self.low)) / self.cell), ti.i32)
            slot = -1
            if (
                0 <= cell[0] < self.shape[0]
                and 0 <= cell[1] < self.shape[1]
                and 0 <= cell[2] < self.shape[2]
            ):
                if ti.static(self.hashed):
                    slot = ti.atomic_add(self.count[self.bucket(cell)], 1)
                else:
                    slot = ti.atomic_add(self.count[cell], 1)
                if slot == 0:
                    active_slot = ti.atomic_add(self.active_count[None], 1)
                    self.active[active_slot] = cell
                if slot < self.capacity:
                    if ti.static(self.hashed):
                        self.indices[slot, self.bucket(cell)] = i
                    else:
                        self.indices[cell[0], cell[1], cell[2], slot] = i
                else:
                    ti.atomic_add(self.overflow[None], 1)
            else:
                ti.atomic_add(self.overflow[None], 1)

    @ti.kernel
    def forces(self, feet_active: ti.i32):
        ti.loop_config(block_dim=128)
        for i in self.x:
            force = ti.Vector([0.0, 0.0, -9.81 * self.mass])
            cell = ti.cast(ti.floor((self.x[i] - ti.Vector(self.low)) / self.cell), ti.i32)
            for offset in ti.grouped(ti.ndrange((-1, 2), (-1, 2), (-1, 2))):
                other = cell + offset
                if (
                    0 <= other[0] < self.shape[0]
                    and 0 <= other[1] < self.shape[1]
                    and 0 <= other[2] < self.shape[2]
                ):
                    for k in range(ti.min(self.cell_count(other), self.capacity)):
                        j = 0
                        if ti.static(self.hashed):
                            j = self.indices[k, self.bucket(other)]
                        else:
                            j = self.indices[other[0], other[1], other[2], k]
                        delta = self.x[i] - self.x[j]
                        nearby = True
                        if ti.static(self.hashed):
                            nearby = (
                                ti.abs(delta[0]) < 2 * self.r
                                and ti.abs(delta[1]) < 2 * self.r
                                and ti.abs(delta[2]) < 2 * self.r
                            )
                        if nearby:
                            distance = delta.norm()
                            if i != j and 1e-8 < distance < 2 * self.r:
                                force += self.contact(
                                    delta / distance, 2 * self.r - distance, self.v[i] - self.v[j]
                                )
            # Ground and lateral bounds, physically represented by restoring forces.
            for axis in ti.static(range(3)):
                lower = -self.hx
                upper = self.hx
                if ti.static(axis == 1):
                    lower = -self.hy
                    upper = self.hy
                if ti.static(axis == 2):
                    lower = 0.0
                    upper = 0.20
                for side in ti.static(range(2)):
                    normal = ti.Vector.zero(ti.f32, 3)
                    normal[axis] = 1.0 if side == 0 else -1.0
                    overlap = (
                        lower + self.r - self.x[i][axis]
                        if side == 0
                        else self.x[i][axis] + self.r - upper
                    )
                    if overlap > 0:
                        force += self.contact(normal, overlap, self.v[i])
            if feet_active:
                for k in ti.static(range(4)):
                    delta = self.x[i] - self.foot[k]
                    distance = delta.norm()
                    if 1e-8 < distance < self.r + self.fr:
                        normal = delta / distance
                        contact_velocity = self.foot_v[k] + self.foot_w[k].cross(self.fr * normal)
                        reaction = self.contact(
                            normal, self.r + self.fr - distance, self.v[i] - contact_velocity
                        )
                        force += reaction
                        ti.atomic_add(self.step_reactions[k], -reaction)
                        ti.atomic_add(
                            self.step_reactions[k + 4], (self.fr * normal).cross(-reaction)
                        )
            self.force[i] = force

    @ti.kernel
    def integrate(self):
        for i in self.x:
            self.v[i] += self.dt * self.force[i] / self.mass
            self.x[i] += self.dt * self.v[i]
        for k in range(8):
            self.reactions[k] += self.step_reactions[k]

    @ti.kernel
    def clear_reactions(self):
        for k in range(8):
            self.reactions[k] = ti.Vector.zero(ti.f32, 3)


def make_bed(radius=0.0015, half_x=0.80, half_y=0.34, layers=9):
    spacing = radius * 2.035
    nx = math.floor((half_x * 2 - 0.04) / spacing)
    ny = math.floor((half_y * 2 - 0.04) / spacing)
    p = (np.indices((nx, ny, layers), dtype=np.float32).reshape(3, -1).T + 0.5) * spacing
    p[:, 0] -= nx * spacing / 2
    p[:, 1] -= ny * spacing / 2
    # Break artificial grid alignment; overlap is relaxed by the warm-up dynamics.
    p += (
        np.random.default_rng(17).uniform(-radius * 0.16, radius * 0.16, p.shape).astype(np.float32)
    )
    return p
