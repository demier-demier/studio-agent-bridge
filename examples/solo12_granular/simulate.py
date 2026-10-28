"""Coupled free-body Solo12 / Metal grains with streamed frame caches."""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import mujoco
import numpy as np
import taichi as ti
from granular import GranularBed, make_bed
from locomotion.controller import LEGS, RADIUS, SoloController, initialize

ROOT = Path(__file__).resolve().parent


def run(a):
    out = Path(a.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            ROOT / "simulate.py",
            ROOT / "granular.py",
            ROOT / "locomotion/controller.py",
            ROOT / "locomotion/solo12-walk.xml",
        ]
    }
    radius = a.radius
    dt = radius / 0.003 * 0.0001
    # Exactly 1 ms rigid steps, integer DEM substeps.
    substeps = round(0.001 / dt)
    dt = 0.001 / substeps
    ti.init(
        arch=ti.metal if a.arch == "metal" else ti.cpu,
        enable_fallback=False,
        default_fp=ti.f32,
        offline_cache=True,
        random_seed=17,
        cpu_max_num_threads=a.cpu_threads,
    )
    points = make_bed(radius, a.half_x, a.half_y, a.layers)
    bed = GranularBed(
        points, radius=radius, half_x=a.half_x, half_y=a.half_y, dt=dt, foot_radius=RADIUS
    )
    model = mujoco.MjModel.from_xml_path(str(ROOT / "locomotion/solo12-walk.xml"))
    model.opt.timestep = 0.001
    data = mujoco.MjData(model)
    ground_z = radius * 2.035 * a.layers * 0.94
    initialize(model, data, ground_z=ground_z, x=a.start_x)
    ctrl = SoloController(
        model,
        data,
        ground_z=ground_z,
        speed=a.speed,
        step_time=a.step_time,
        shift=a.shift,
        height=a.height,
        lift=0.04,
        start_time=1.0,
        balance=True,
    )
    bids = np.array([model.body(f"{leg}_FOOT").id for leg in LEGS])
    gids = np.array([model.geom(f"{leg}_pad").id for leg in LEGS])
    visual = [
        i
        for i in range(model.ngeom)
        if model.geom_type[i] == mujoco.mjtGeom.mjGEOM_MESH and model.geom_group[i] == 1
    ]
    fps = 30
    frame_count = math.floor(a.seconds * fps) + 1
    frames = np.lib.format.open_memmap(
        out / "particles.npy", mode="w+", dtype="float32", shape=(frame_count, len(points), 3)
    )
    poses = np.lib.format.open_memmap(
        out / "robot_poses.npy", mode="w+", dtype="float32", shape=(frame_count, len(visual), 12)
    )
    feet = np.lib.format.open_memmap(
        out / "foot_centers.npy", mode="w+", dtype="float32", shape=(frame_count, 4, 3)
    )
    trace = []
    start = time.perf_counter()
    for _ in range(round(0.28 / dt)):
        bed.grid()
        bed.forces(0)
        bed.integrate()
    ti.sync()
    settled_speed = np.linalg.norm(bed.v.to_numpy(), axis=1)
    settling_rms = float(np.sqrt(np.mean(settled_speed**2)))
    if settling_rms > 0.05:
        raise RuntimeError(f"Bed not settled: RMS speed {settling_rms}")
    np.save(out / "settled.npy", bed.x.to_numpy())
    warm = time.perf_counter() - start
    velocity = np.zeros(6)
    contact = np.zeros(6)
    frame_index = 0
    simstart = time.perf_counter()
    qpos = []
    loads = []
    for step in range(round(a.seconds / 0.001) + 1):
        t = step * 0.001
        ctrl.step(t)
        foot_position = data.geom_xpos[gids].astype(np.float32)
        foot_velocity = []
        foot_angular = []
        for gid in gids:
            mujoco.mj_objectVelocity(model, data, mujoco.mjtObj.mjOBJ_GEOM, int(gid), velocity, 0)
            foot_velocity.append(velocity[3:].copy())
            foot_angular.append(velocity[:3].copy())
        bed.foot.from_numpy(foot_position)
        bed.foot_v.from_numpy(np.array(foot_velocity, np.float32))
        bed.foot_w.from_numpy(np.array(foot_angular, np.float32))
        bed.clear_reactions()
        for _ in range(substeps):
            bed.grid()
            bed.forces(1)
            bed.integrate()
        reaction = bed.reactions.to_numpy() / substeps
        data.xfrc_applied[:] = 0
        data.xfrc_applied[bids, :3] = reaction[:4]
        data.xfrc_applied[bids, 3:] = reaction[4:]
        mujoco.mj_step(model, data)
        # Recompute current transforms for the actual just-integrated qpos.
        mujoco.mj_forward(model, data)
        if frame_index < frame_count and t + 1e-9 >= frame_index / fps:
            particle = bed.x.to_numpy()
            if not np.isfinite(particle).all() or not np.isfinite(data.qpos).all():
                raise RuntimeError("Non-finite state")
            if bed.overflow[None]:
                raise RuntimeError("Grid overflow/out-of-bounds")
            upright = float(data.xmat[model.body("base_link").id, 8])
            if upright < 0.90 or data.qpos[2] < 0.15:
                raise RuntimeError(f"Unsafe pose at {t}: upright={upright}, height={data.qpos[2]}")
            if particle[:, 2].min() < -0.003:
                raise RuntimeError("Excessive ground penetration")
            if abs(data.qpos[0]) > 0.65:
                raise RuntimeError("Robot left validated bed region")
            frames[frame_index] = particle
            poses[frame_index, :, :3] = data.geom_xpos[visual]
            poses[frame_index, :, 3:] = data.geom_xmat[visual]
            feet[frame_index] = data.geom_xpos[gids]
            rigid_load = np.zeros(4)
            for ci in range(data.ncon):
                c = data.contact[ci]
                for k, gid in enumerate(gids):
                    if gid in (c.geom1, c.geom2):
                        mujoco.mj_contactForce(model, data, ci, contact)
                        rigid_load[k] += contact[0]
            trace.append(
                {
                    "t": t,
                    "base": data.qpos[:3].tolist(),
                    "upright": upright,
                    "grain_load_z": reaction[:4, 2].tolist(),
                    "substrate_load": rigid_load.tolist(),
                    "torque_max": float(np.abs(data.actuator_force).max()),
                }
            )
            qpos.append(data.qpos.copy())
            loads.append(reaction[:4].copy())
            frame_index += 1
        if step % 500 == 0:
            frames.flush()
            poses.flush()
            feet.flush()
            print(
                json.dumps(
                    {
                        "t": t,
                        "wall": time.perf_counter() - simstart,
                        "base": data.qpos[:3].tolist(),
                        "grains": len(points),
                    }
                ),
                flush=True,
            )
    ti.sync()
    wall = time.perf_counter() - simstart
    frames.flush()
    poses.flush()
    feet.flush()
    np.save(out / "qpos.npy", qpos)
    np.save(out / "loads.npy", loads)
    metadata = {
        "fps": fps,
        "frames": frame_index,
        "grain_count": len(points),
        "grain_radius_m": radius,
        "foot_radius_m": RADIUS,
        "bed_bounds": [-a.half_x, a.half_x, -a.half_y, a.half_y],
        "robot_geometry_path": "robot/preview-geometry.json",
        "robot_geometry": "robot/preview-geometry.json",
        "seconds": a.seconds,
        "ground_z": ground_z,
        "surface_z": ground_z,
        "grain_dt": dt,
        "rigid_dt": 0.001,
        "warmup_seconds": warm,
        "simulation_wall_seconds": wall,
        "travel_x": float(data.qpos[0] - a.start_x),
        "minimum_upright": min(x["upright"] for x in trace),
        "controller": {**vars(a), "output": out.name},
        "backend": a.arch,
        "physics_source_sha256": source_hashes,
        "time_note": "Frame labels use pre-step time; saved state is post-step, exactly label + 1 ms. This preserves the released simulation loop.",
        "grid": "occupied-cell clearing",
        "trace": trace,
        "limits": [
            "Translational spherical DEM, no grain spin/rolling resistance or calibrated sand parameters.",
            "Classical scheduled crawl, joint PD and base feedback through joint torques, not learned policy or Cosmos inference.",
            "Only foot pads exchange DEM forces; rigid mesh/floor contacts use MuJoCo.",
            "No real-hardware or NVIDIA-parity validation.",
        ],
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (out / "settling.json").write_text(
        json.dumps(
            {
                "warmup_simulated_seconds": 0.28,
                "rms_grain_speed_m_s": settling_rms,
                "maximum_grain_speed_m_s": float(settled_speed.max()),
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in metadata.items() if k not in ["trace", "controller", "limits"]}
        ),
        flush=True,
    )


PRESETS = {
    "video": {
        "seconds": 8.0,
        "radius": 0.0015,
        "half_x": 0.80,
        "half_y": 0.34,
        "layers": 9,
        "start_x": -0.33,
        "speed": 0.08,
        "step_time": 0.6,
        "shift": 0.008,
        "height": 0.235,
    },
    "smoke": {
        "seconds": 1.2,
        "radius": 0.008,
        "half_x": 0.65,
        "half_y": 0.25,
        "layers": 2,
        "start_x": -0.20,
        "speed": 0.08,
        "step_time": 0.6,
        "shift": 0.008,
        "height": 0.235,
    },
}


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESETS, default="smoke")
    parser.add_argument("--arch", choices=["cpu", "metal"], default="cpu")
    parser.add_argument(
        "--output", required=True, help="New run directory; existing paths are refused."
    )
    parser.add_argument("--cpu-threads", type=int, default=4)
    for key in PRESETS["video"]:
        parser.add_argument("--" + key.replace("_", "-"), type=int if key == "layers" else float)
    args = parser.parse_args(argv)
    for key, value in PRESETS[args.preset].items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    numeric = [getattr(args, key) for key in PRESETS["video"]]
    if not all(math.isfinite(value) for value in numeric):
        parser.error("All numeric parameters must be finite.")
    if not 0.001 <= args.radius <= 0.015:
        parser.error("--radius must be between .001 and .015 m.")
    if not 0.55 <= args.half_x <= 1.0 or not 0.22 <= args.half_y <= 0.5:
        parser.error(
            "Bed half-extents must fit the supported robot region: x .55..1.0, y .22...5 m."
        )
    if not 1 <= args.layers <= 12 or not 0.067 <= args.seconds <= 10:
        parser.error("--layers must be 1..12 and --seconds .067..10.")
    if not 0 <= args.speed <= 0.1 or not 0.4 <= args.step_time <= 1.0:
        parser.error("--speed must be 0...1 m/s and --step-time .4..1.0 s.")
    if not 0 <= args.shift <= 0.03 or not 0.22 <= args.height <= 0.25:
        parser.error("--shift must be 0...03 m and --height .22...25 m.")
    if not 1 <= args.cpu_threads <= 32:
        parser.error("--cpu-threads must be 1..32.")
    if abs(args.start_x) + 0.26 >= args.half_x:
        parser.error("Initial robot feet must fit inside the bed.")
    end_x = args.start_x + args.speed * max(0, args.seconds - 1)
    if abs(end_x) + 0.26 >= args.half_x or abs(end_x) >= 0.6:
        parser.error("Commanded walk leaves the supported bed region; shorten the run or speed.")
    spacing = args.radius * 2.035
    tallest_initial_surface = (args.layers - 0.5) * spacing + 1.16 * args.radius
    if tallest_initial_surface >= 0.21:
        parser.error("Initial grain bed exceeds the fixed vertical grid; reduce radius or layers.")
    count = (
        math.floor((2 * args.half_x - 0.04) / spacing)
        * math.floor((2 * args.half_y - 0.04) / spacing)
        * args.layers
    )
    cell = 2.2 * args.radius
    grid_cells = (
        math.ceil((2 * args.half_x + 0.02) / cell)
        * math.ceil((2 * args.half_y + 0.02) / cell)
        * math.ceil(0.22 / cell)
    )
    cache_bytes = (math.floor(args.seconds * 30) + 1) * count * 3 * 4
    if count > 2_000_000 or grid_cells * 68 > 2_000_000_000 or cache_bytes > 8_000_000_000:
        parser.error("Requested particles/grid/cache exceed the bounded example resource budget.")
    if Path(args.output).exists():
        parser.error("Output path already exists; choose a new run directory.")
    return args


if __name__ == "__main__":
    run(options())
