"""Independent trajectory diagnostics; writes a report inside the selected run.

Usage: python locomotion/audit_run.py /path/to/completed-run
Contact support ratios are scenario diagnostics, not calibrated soil properties.
"""

import argparse
import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np

ROOT = Path(__file__).resolve().parent
LEGS = ("FL", "FR", "HL", "HR")


def audit(path, skip_particles=False, minimum_travel=0.3, report_path=None):
    path = Path(path).resolve()
    meta = json.loads((path / "metadata.json").read_text())
    q = np.load(path / "qpos.npy", mmap_mode="r")
    feet = np.load(path / "foot_centers.npy", mmap_mode="r")
    grain_forces = np.load(path / "loads.npy", mmap_mode="r")
    trace = meta["trace"]
    t = np.array([r["t"] for r in trace], dtype=float)
    rigid_normal = np.array([r["substrate_load"] for r in trace], dtype=float)
    grain_z = np.array([r["grain_load_z"] for r in trace], dtype=float)
    post = t >= 1.0
    if len(t) < 3 or np.count_nonzero(post) < 2:
        raise ValueError("Audit requires at least two frames after the 1 s controller startup.")
    if not np.isfinite(minimum_travel) or minimum_travel < 0:
        raise ValueError("Minimum travel must be finite and non-negative.")
    assert len(q) == len(feet) == len(grain_forces) == len(trace) == meta["frames"]
    assert q.shape[1] == 19 and feet.shape[1:] == (4, 3)
    assert np.all(np.diff(t) > 0)
    quat = q[:, 3:7]
    upright = 1 - 2 * (quat[:, 1] ** 2 + quat[:, 2] ** 2)
    base_v = np.gradient(q[:, :3], t, axis=0)
    foot_v = np.gradient(feet, t, axis=0)
    center_speed = np.linalg.norm(foot_v[:, :, :2], axis=2)
    active = (rigid_normal + np.maximum(grain_z, 0)) > 0.5
    stance_center_speed = center_speed[post][active[post]]
    qnorm = np.linalg.norm(quat, axis=1)
    torque = np.array([r["torque_max"] for r in trace])
    trace_base = np.array([r["base"] for r in trace])
    trace_up = np.array([r["upright"] for r in trace])
    weights = np.diff(t, append=t[-1] + np.median(np.diff(t)))
    rigid_impulse = float(np.sum(weights[post] * rigid_normal[post].sum(axis=1)))
    # Keep signed vertical granular force: upward support and downward drag are distinct.
    grain_impulse = float(np.sum(weights[post] * grain_z[post].sum(axis=1)))
    total_impulse = rigid_impulse + grain_impulse
    positive_grain_impulse = float(np.sum(weights[post] * np.maximum(grain_z[post], 0).sum(axis=1)))
    negative_grain_impulse = float(np.sum(weights[post] * np.minimum(grain_z[post], 0).sum(axis=1)))

    model = mujoco.MjModel.from_xml_path(str(ROOT / "solo12-walk.xml"))
    data = mujoco.MjData(model)
    pad_ids = [model.geom(f"{leg}_pad").id for leg in LEGS]
    floor = model.geom("floor").id
    foot_contacts_to_other_bodies = []
    nonfoot_floor_contacts = []
    center_error = 0.0
    for i, position in enumerate(q):
        data.qpos[:] = position
        mujoco.mj_forward(model, data)
        center_error = max(center_error, float(np.abs(data.geom_xpos[pad_ids] - feet[i]).max()))
        if not post[i]:
            continue
        for c in data.contact[: data.ncon]:
            if c.dist > 0:
                continue
            if c.geom1 in pad_ids or c.geom2 in pad_ids:
                other = c.geom2 if c.geom1 in pad_ids else c.geom1
                if other != floor:
                    foot_contacts_to_other_bodies.append(
                        {"frame": i, "geom1": int(c.geom1), "geom2": int(c.geom2)}
                    )
            if floor in (c.geom1, c.geom2):
                other = c.geom2 if c.geom1 == floor else c.geom1
                if other not in pad_ids:
                    nonfoot_floor_contacts.append({"frame": i, "geom": int(other)})

    particles_report = {"checked": False}
    if not skip_particles:
        particles = np.load(path / "particles.npy", mmap_mode="r")
        assert particles.shape == (meta["frames"], meta["grain_count"], 3)
        finite = True
        lower = np.full(3, np.inf)
        upper = -lower
        for frame in particles:
            finite = finite and bool(np.isfinite(frame).all())
            lower = np.minimum(lower, frame.min(axis=0))
            upper = np.maximum(upper, frame.max(axis=0))
        particles_report = {
            "checked": True,
            "all_finite": finite,
            "minimum_xyz_m": lower.tolist(),
            "maximum_xyz_m": upper.tolist(),
            "grain_count": particles.shape[1],
        }

    checks = {
        "all_qpos_finite": bool(np.isfinite(q).all()),
        "all_foot_positions_finite": bool(np.isfinite(feet).all()),
        "all_recorded_loads_finite": bool(
            np.isfinite(grain_forces).all() and np.isfinite(rigid_normal).all()
        ),
        "quaternion_normalization": bool(np.max(np.abs(qnorm - 1)) < 1e-5),
        "trace_base_matches_qpos": bool(np.allclose(trace_base, q[:, :3], rtol=0, atol=1e-6)),
        "trace_upright_matches_quaternion": bool(np.allclose(trace_up, upright, rtol=0, atol=1e-6)),
        "trace_grain_force_matches_loads": bool(
            np.allclose(grain_z, grain_forces[:, :, 2], rtol=1e-6, atol=1e-6)
        ),
        "exported_feet_match_forward_kinematics": bool(center_error < 1e-6),
        "remained_upright_post_startup": bool(upright[post].min() > 0.97),
        "bounded_height_post_startup": bool(q[post, 2].min() > 0.20 and q[post, 2].max() < 0.35),
        "motor_torque_within_2_5_nm": bool(torque.max() <= 2.5 + 1e-6),
        "forward_progress_over_requested_minimum": bool(q[-1, 0] - q[0, 0] > minimum_travel),
        "no_nonfoot_floor_contacts_post_startup": not nonfoot_floor_contacts,
        "foot_contact_normal_samples_only_floor": not foot_contacts_to_other_bodies,
        "bounded_base_speed_post_startup": bool(np.abs(base_v[post]).max() < 1.0),
        "no_gross_foot_floor_penetration": bool(
            feet[post, :, 2].min() >= meta["foot_radius_m"] - 0.003
        ),
    }
    if not skip_particles:
        checks["all_particles_finite"] = particles_report["all_finite"]
        checks["no_gross_particle_floor_penetration"] = (
            particles_report["minimum_xyz_m"][2] > -0.003
        )

    report = {
        "run": path.name,
        "minimum_travel_m": minimum_travel,
        "status": "passed bounded diagnostics"
        if all(checks.values())
        else "requires investigation",
        "checks": checks,
        "sample_window_seconds": [float(t[post][0]), float(t[-1])],
        "time_note": "Simulation loop labels frames by pre-step t but saves post-step state: actual state time is label + rigid_dt (1 ms). Frame derivatives use recorded nonuniform 33/34 ms spacing. This is disclosed, not corrected by changing the simulation.",
        "geometry_max_error_m": center_error,
        "forward_travel_m": float(q[-1, 0] - q[0, 0]),
        "lateral_displacement_m": float(q[-1, 1] - q[0, 1]),
        "minimum_upright_cosine": float(upright[post].min()),
        "maximum_tilt_degrees": float(np.degrees(np.arccos(np.clip(upright[post].min(), -1, 1)))),
        "base_height_range_m": [float(q[post, 2].min()), float(q[post, 2].max())],
        "forward_velocity_range_m_s": [float(base_v[post, 0].min()), float(base_v[post, 0].max())],
        "reverse_velocity_sample_fraction": float(np.mean(base_v[post, 0] < -0.01)),
        "forward_velocity_sign_changes": int(np.count_nonzero(np.diff(np.sign(base_v[post, 0])))),
        "foot_center_height_range_by_leg_m": {
            leg: [float(feet[post, k, 2].min()), float(feet[post, k, 2].max())]
            for k, leg in enumerate(LEGS)
        },
        "supported_foot_center_tangential_speed_median_m_s": float(np.median(stance_center_speed)),
        "supported_foot_center_tangential_speed_p95_m_s": float(
            np.percentile(stance_center_speed, 95)
        ),
        "foot_speed_note": "Finite-difference center-speed proxy; this is not exact contact-point slip, which also depends on angular velocity and grain contact normals.",
        "maximum_motor_torque_nm": float(torque.max()),
        "support": {
            "substrate_normal_impulse_Ns": rigid_impulse,
            "granular_signed_vertical_impulse_Ns": grain_impulse,
            "granular_upward_impulse_Ns": positive_grain_impulse,
            "granular_downward_impulse_Ns": negative_grain_impulse,
            "substrate_share_of_recorded_vertical_support_percent": 100
            * rigid_impulse
            / total_impulse,
            "granular_share_of_recorded_vertical_support_percent": 100
            * grain_impulse
            / total_impulse,
            "method": "Time-weighted sums of 30 Hz recorded averaged DEM vertical forces and instantaneous MuJoCo contact-normal forces after startup. Floor is horizontal and kinematic checks find no other pad contact partners. Diagnostic sampled impulse estimate, not high-rate integrated ground truth.",
        },
        "particles": particles_report,
        "contact_anomalies": {
            "foot_to_other": foot_contacts_to_other_bodies[:12],
            "nonfoot_floor": nonfoot_floor_contacts[:12],
        },
        "source_sha256_at_simulation_start": meta.get("physics_source_sha256"),
        "current_source_sha256": {
            str(f.relative_to(ROOT.parent)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in [
                ROOT.parent / "granular.py",
                ROOT.parent / "simulate.py",
                ROOT / "controller.py",
                ROOT / "solo12-walk.xml",
            ]
        },
        "limitations": [
            "Spherical translational grains; no angular grain dynamics, rolling resistance, cohesion or calibrated sand law.",
            "Classical control; no learned policy or model inference.",
            "Passing these checks does not validate real robot performance, soil fidelity, visual quality or equivalence to NVIDIA.",
            "Thin granular bed over a load-bearing rigid substrate. Only footpads exchange DEM forces.",
            "Current source hashes identify audited files, not proof that an older run executed those exact bytes.",
        ],
    }
    output = Path(report_path) if report_path else path / "audit.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ["source_sha256", "limitations"]}, indent=2
        )
    )
    print("REPORT", output)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--skip-particles", action="store_true")
    parser.add_argument(
        "--minimum-travel",
        type=float,
        default=0.3,
        help="Explicit progress threshold in metres; use 0 only for a short smoke run.",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    raise SystemExit(audit(args.run, args.skip_particles, args.minimum_travel, args.report))
