# Walking and persistent granular tracks on Apple Silicon

An original experimental quadruped walks through **604,800 two-millimeter coarse grains**, with Metal particle contact coupled to MuJoCo articulated dynamics. The accepted local trace advanced **0.297 m** over seven simulated seconds. Sixteen artifact checks passed, including repeated foot lift/landing crossings, bounded particles, upright posture, terrain reactions and a persistent surface depression behind the final feet. [Observed record](observed-run.json).

This extends the [original contact experiment](../metal_terrain/). The walking experiment is kept standalone while its control and contact assumptions are evaluated. It is not a learned policy, a Cosmos/Newton port, or a claim of equivalence to an NVIDIA platform.

## Mechanics and acceptance

The original robot has a floating chassis, four two-link legs and eight position-controlled joints. An analytic diagonal gait uses a 0.85-second period, 60% stance duty, a 0.055 m foot stroke and 0.04 m lift. Two-link inverse kinematics computes joint targets. The chassis mass is 1.3 kg plus the original limb masses. No script translates the chassis along a path or applies artificial supporting forces. Ground reaction and motor commands produce its motion. Lateral drift was approximately 9.5 cm; this is a bounded open-loop demonstration, not a navigation controller.

The same normal spring/damping and slip-friction sphere model runs on CPU or Metal. Foot force and torque sums stay on the device for four granular steps before being applied to MuJoCo. Ten analytic checks cover spring forces, momentum balance, slip friction, floor response, action/reaction, four-step accumulation/reset and grid overflow. Eight controller checks cover limits, periodicity, diagonal phasing and reachable foot paths. A controlled single-threaded CPU before/after comparison of the accumulation change produced identical particle, transform and load arrays over 0.4 seconds. Parallel runs need not be bitwise identical.

The surface checker compares rasterized top heights at 0.8 seconds and the final frame. It restricts measurement to visited foot regions behind the final rear foot and excludes the initial rear-foot location. In this trace, 557 six-millimeter cells had a surface reduction greater than 1 mm, covering approximately 201 cm². This bounded metric records disturbance; it is not soil calibration or an accuracy claim against real footprints.

## Reproduce

Use Python 3.12 and the existing optional dependency pins:

```sh
python -m pip install -r examples/metal_terrain/requirements.txt
python examples/metal_walking/verify_controller.py
python examples/metal_walking/verify_walking_kernels.py --backend metal
python examples/metal_walking/walking_terrain.py \
  --backend metal --grain-scale 3 --seconds 7 --output runs/walking-metal
python examples/metal_walking/verify_walking.py runs/walking-metal
```

Scale 1 uses 25,200 grains, scale 2 uses 168,000 and scale 3 uses 604,800. Use a new output directory; existing directories are rejected. CPU mode uses the same equations, but runtime and floating-point trajectories differ. The full dense trajectory is large and deliberately not committed. CI runs the analytic CPU and controller checks, not the full M4 experiment. The seven-second Metal run took roughly nine minutes before compression/rendering; other small checks overlapped it, so this is not an isolated benchmark or real-time result.

Render real trajectory frames in Blender:

```sh
blender --background --python examples/metal_walking/render_preview.py -- runs/walking-metal --last
blender --background --python examples/metal_walking/render_preview.py -- runs/walking-metal --last --macro
blender --background --python examples/metal_walking/render_preview.py -- runs/walking-metal --all
```

Wide frames render at the recorded 30 Hz. `--all --macro` samples every second trajectory frame and must be played at 15 Hz. The macro lighting grazes the terrain to reveal the computed surface. Rendering never paints or carves footprints. Decorative chassis details are visual geometry, not separately simulated components. The saved `.blend` is the final frame; replay uses the NPZ and script. Editorial sound is separate from physics.

## Limits, failures and provenance

The thin granular bed also rests on a rigid substrate that supports the feet. Only feet collide with grains. Spherical translational DEM omits grain rotation, rolling resistance, contact history, cohesion, moisture and calibrated soil parameters. No robot hardware or real sand was used for validation. Finite spring contact permits small penetration; the checker declares a 1 mm floor tolerance. This case is not ready for control deployment or safety decisions.

Earlier cases are not presented as successes: insufficient foot clearance caused one pilot to fail; a denser run stayed upright but advanced only 14 cm; a proposed longer stride exceeded the hip target limit and was stopped. Increasing clearance, revising the bounded stroke and selecting a heavier original robot configuration produced the accepted trace. The surface checker was also corrected to use rear-foot geometry rather than chassis extents, which had excluded valid rear-foot track regions. These are experimental design changes, not a convergence or fidelity study.

All scene geometry, robot definitions, tests and source are original synthetic work under the repository MIT license. Dependencies and their licenses are documented in the original contact example. No employer/customer data, third-party robot assets, Cosmos weights or generated world-model video are included. Commercial media remains subject to author review before social publication.
