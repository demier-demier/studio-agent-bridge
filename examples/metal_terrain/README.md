# Experimental granular contact on Apple Silicon

An original, local robotics contact experiment using **Taichi's Metal GPU backend for granular particles** and **MuJoCo on CPU for an articulated robot**. It explores a lower hardware dependency path for physical simulation. It does not require CUDA.

The current case is a small four-legged robot settling into a bounded bed of coarse spherical grains, followed by a bounded posture motion. Eight actuated joints, a floating chassis and terrain reaction forces are computed. The controller is a classical position controller; it is not a learned walking policy.

## Observed on an M4 Pro

The larger local run used **89,856 grains**, eight actuated joints, 80,000 granular steps and 20,000 rigid-body steps over **four simulated seconds**. The recorded simulation wall time was approximately **85 seconds**, excluding rendering and initialization. This is an observed run, not a real-time or comparative performance claim.

The machine had an Apple M4 Pro with 16 GPU cores and 24 GiB unified memory. The Metal backend was explicitly selected with fallback disabled. A separate artifact validator passed 13 checks on finite states, particle count, boundaries, grid capacity, rigid transforms, robot posture, timing and terrain reaction. Eight analytic kernel checks passed independently on both CPU and Metal. [Measured record](observed-run.json).

This is **not a port of NVIDIA Cosmos or Newton**. No Cosmos model was executed, no NVIDIA hardware baseline was measured, and no system-wide parity or superiority claim is made. The objective is to test a useful contact-simulation capability on accessible hardware.

## Reproduce

Use a separate Python 3.12 environment; these optional dependencies are not required by the Studio Agent Bridge core.

```sh
python3.12 -m venv .venv-terrain
.venv-terrain/bin/python -m pip install -r examples/metal_terrain/requirements.txt
.venv-terrain/bin/python examples/metal_terrain/verify_kernels.py --backend metal
.venv-terrain/bin/python examples/metal_terrain/metal_terrain.py \
  --backend metal --grain-scale 2 --seconds 4 --output runs/terrain-metal
.venv-terrain/bin/python examples/metal_terrain/verify_run.py runs/terrain-metal
```

`--backend cpu` runs the same granular kernels on CPU. The default `--grain-scale 1` uses 11,232 grains. Scale 2 halves grain diameter and time step and doubles each initial lattice dimension. Only scales 1 and 2 are accepted. Use an empty output destination: existing directories are rejected. A final report is written only after the simulation completes its gates. Failed destinations may contain partial evidence and must not be presented as successful runs.

To render the measured trajectory in an installed Blender:

```sh
blender --background --python examples/metal_terrain/render_preview.py -- runs/terrain-metal
blender --background --python examples/metal_terrain/render_preview.py -- runs/terrain-metal --all
blender --background --python examples/metal_terrain/render_preview.py -- runs/terrain-metal --all --macro
```

The three first-command images are a visual review gate. Rendered grain centers come directly from the measured trajectory. The detailed robot housing is original decorative geometry around the physics proxies; bolts, vents and camera lenses do not have separate simulated dynamics. The `.blend` saves the final inspected frame, not a self-contained animated replay. Use the trajectory and script to reproduce animation. Rendering is separate from the solver; slow-motion editorial footage must be labeled accordingly.

## What the equations do

- Spatial bins bound particle-neighbor searches; overflow is accumulated and rejects the run instead of silently dropping interactions.
- Each coarse spherical grain has mass, translation and velocity. Overlapping spheres interact through a normal penalty spring, normal damping and a capped tangential force opposing slip.
- Analytic tray walls apply the same contact rule. Particles are not position-clamped, deleted or repositioned to make a render appear stable.
- Four spherical feet exchange equal-and-opposite particle contact forces. Foot forces and torques are accumulated across four granular steps and applied to the corresponding rigid bodies in MuJoCo.
- MuJoCo integrates the free body and eight motorized joints, including physical contact with the tray substrate. That substrate matters: the thin granular layer does not support the robot alone.

The analytic tests check an isolated sphere-pair spring force, pair momentum balance, friction direction and its Coulomb bound, floor response, robot/grain action-reaction, nonzero terrain load and overflow detection. CPU and Metal passing those tests is a narrow cross-backend validation, not proof that long trajectories match exactly. Parallel floating-point accumulation can change trajectories.

## Limits and failure evidence

This is a **coarse translational discrete-element approximation**, not a calibrated sand model. Grain diameter is 6 mm at scale 1 and 3 mm at scale 2. No grain rotation, rolling resistance, history-dependent tangential contact, cohesion, moisture, calibrated soil parameters or experimental validation is implemented. The rendering must not be presented as resolved fine beach sand.

Only the feet interact with the grains. Chassis/limb contact with the granular bed is not supported; posture and height gates reject collapse outside the supported regime. The robot does not navigate or learn a policy. Contact coupling is explicitly partitioned with held foot state over four granular steps; energy accuracy and time-step convergence remain open validation tasks.

Early local experiments were rejected: a Newton MPM contact configuration allowed particles to escape beneath the floor, and an initial Metal prototype omitted the rigid tray substrate for the robot. Neither failed result is used as evidence of successful physical fidelity. Adding substrate contact and checking posture, boundaries and reaction forces produced the accepted trace. This is a recorded engineering progression, not a claim that the upstream Newton engine is faulty.

This standalone experiment is not yet an MCP action or an autonomous environment builder. No robot hardware, actuators or remote services are controlled. It is not suitable for safety certification or control deployment.

## NVIDIA context and provenance

NVIDIA's [Cosmos Sim-Transfer model card](https://huggingface.co/nvidia/Cosmos3-Nano-Sim-Transfer) assigns physics and ground-truth state to a simulator and generates visual observations from spatial controls. [Warp's compatibility documentation](https://nvidia.github.io/warp/v1.13/user_guide/compatibility.html) documents its CPU-only macOS path. These are the motivation for exploring a separate Metal implementation; they do not establish compatibility with Cosmos.

All scene geometry, kernels, robot definition and test fixtures in this directory are original synthetic work, published under this repository's MIT license. No employer or customer material is used. Dependencies retain their own licenses: [Taichi](https://github.com/taichi-dev/taichi) and [MuJoCo](https://github.com/google-deepmind/mujoco) use Apache-2.0; [NumPy](https://github.com/numpy/numpy) uses BSD-3-Clause. They are installed from PyPI, not vendored here. Blender is a separately installed GPL application.

Commercial video and LinkedIn copy are held for author review; they are not published by this example.
