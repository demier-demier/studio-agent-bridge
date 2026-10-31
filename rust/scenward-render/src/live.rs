use crate::profiling::{LiveTimings, PhysicsTimings};
use crate::{cache::Scene, physics::GpuPhysics, renderer::Renderer, Args};
use anyhow::{ensure, Result};
use std::time::Instant;
pub struct Live {
    pub particles: GpuPhysics,
    #[cfg(feature = "robotics")]
    pub robot: Option<crate::robot::Robot>,
    pub dynamic: Vec<(usize, String)>,
    pub physics_wall: f64,
    pub ticks: u64,
    pub settling_wall: f64,
    pub initial_frames: usize,
    pub initial_robot: Option<[f64; 48]>,
    pub profile: Option<LiveTimings>,
    pub settling_profile: Option<PhysicsTimings>,
}
impl Live {
    pub fn new(args: &Args, scene: &mut Scene, r: &mut Renderer) -> Result<Self> {
        ensure!(
            scene.meta.grain_count == 961191 && (scene.meta.grain_radius_m - 0.0015).abs() < 1e-8,
            "Live full-detail case requires exactly 961191 grains of 1.5mm radius"
        );
        scene.read(0)?;
        r.upload(scene);
        let dynamic = if let Some(path) = &args.environment {
            r.import_environment(&crate::environment::Environment::load(path)?)
        } else {
            vec![]
        };
        #[cfg(not(feature = "robotics"))]
        ensure!(
            args.model.is_none() && dynamic.is_empty(),
            "Build --features robotics to use native robot/dynamic objects"
        );
        #[cfg(feature = "robotics")]
        let robot = args
            .model
            .as_ref()
            .map(|p| crate::robot::Robot::open(p))
            .transpose()?;
        #[cfg(feature = "robotics")]
        let count = robot.as_ref().map_or(0, |r| r.count);
        #[cfg(not(feature = "robotics"))]
        let count = 0;
        #[cfg(feature = "robotics")]
        let initial_robot = robot.as_ref().map(|r| r.state());
        #[cfg(not(feature = "robotics"))]
        let initial_robot = None;
        let mut particles = GpuPhysics::new(
            &r.device,
            &scene.particles.values,
            &vec![0.; scene.particles.values.len()],
            count,
        )?;
        ensure!(
            scene.meta.settle_steps <= 20000 && scene.meta.settle_steps.is_multiple_of(20),
            "Invalid settling budget"
        );
        let settle_start = Instant::now();
        let distant = vec![
            crate::physics::SphereBody {
                center: [0., 0., 10., 0.02],
                ..Default::default()
            };
            count
        ];
        for _ in 0..scene.meta.settle_steps / 20 {
            particles.advance(&r.device, &r.queue, &distant, 20)?;
        }
        let settling_wall = settle_start.elapsed().as_secs_f64();
        particles.steps = 0;
        let settling_profile = particles
            .profiler
            .as_mut()
            .map(|p| std::mem::take(&mut p.timings));
        let profile = settling_profile.as_ref().map(|_| LiveTimings::default());
        let mut live = Self {
            particles,
            #[cfg(feature = "robotics")]
            robot,
            dynamic,
            physics_wall: 0.,
            ticks: 0,
            settling_wall,
            initial_frames: scene.meta.frames,
            initial_robot,
            profile,
            settling_profile,
        };
        live.upload(r)?;
        Ok(live)
    }
    pub fn tick(&mut self, r: &mut Renderer) -> Result<()> {
        let start = Instant::now();
        #[cfg(feature = "robotics")]
        let bodies = self.robot.as_ref().map_or_else(Vec::new, |r| r.spheres());
        #[cfg(not(feature = "robotics"))]
        let bodies = vec![];
        if let Some(p) = &mut self.profile {
            p.collect_bodies.add(start.elapsed().as_secs_f64());
        }
        let reactions = self.particles.advance(&r.device, &r.queue, &bodies, 20)?;
        let native_start = self.profile.as_ref().map(|_| Instant::now());
        #[cfg(feature = "robotics")]
        if let Some(robot) = &mut self.robot {
            robot.step(&reactions)?;
        }
        #[cfg(not(feature = "robotics"))]
        let _ = reactions;
        if let Some(p) = &mut self.profile {
            p.native_robot_step
                .add(native_start.unwrap().elapsed().as_secs_f64());
        }
        let upload_start = self.profile.as_ref().map(|_| Instant::now());
        self.upload(r)?;
        if let Some(p) = &mut self.profile {
            p.upload_poses
                .add(upload_start.unwrap().elapsed().as_secs_f64());
        }
        self.physics_wall += start.elapsed().as_secs_f64();
        self.ticks += 1;
        Ok(())
    }
    pub fn upload(&mut self, r: &mut Renderer) -> Result<()> {
        r.use_particle_buffer(&self.particles.positions[self.particles.current]);
        #[cfg(feature = "robotics")]
        if let Some(robot) = &self.robot {
            let poses = robot.visual();
            ensure!(
                poses.len() == 13 * 12,
                "Expected 13 original Solo12 visual instances"
            );
            for (index, name) in &self.dynamic {
                r.update_pose(*index, &robot.body_pose(name)?);
            }
            let bodies = robot.spheres();
            let feet: Vec<f32> = bodies[..4]
                .iter()
                .flat_map(|b| b.center[..3].iter().copied())
                .collect();
            r.upload_robot(&poses, &feet);
        }
        Ok(())
    }
    pub fn seconds(&self) -> f64 {
        self.particles.steps as f64 * self.particles.dt
    }
    pub fn push(&mut self) {
        #[cfg(feature = "robotics")]
        if let Some(robot) = &mut self.robot {
            robot.push();
        }
    }
    pub fn report(&self, wall: f64) -> serde_json::Value {
        #[cfg(feature = "robotics")]
        let state = self.robot.as_ref().map(|r| r.state().to_vec());
        #[cfg(not(feature = "robotics"))]
        let state: Option<Vec<f64>> = None;
        let mut report = serde_json::json!({"schema":"scenward-live-physics/v1","mode":"live GPU DEM and optional native articulated dynamics","particles":self.particles.count,"grain_radius_m":0.0015,"grain_dt":self.particles.dt,"rigid_dt":0.001,"steps":self.particles.steps,"neighbour_cache_rebuilds_including_settling":self.particles.rebuilds,"settling_wall_seconds":self.settling_wall,"source_frames":self.initial_frames,"simulation_seconds":self.seconds(),"wall_seconds_including_render":wall,"physics_wall_seconds":self.physics_wall,"real_time_factor":self.seconds()/wall,"realtime_achieved":self.seconds()>=wall,"coupled_spheres":self.particles.body_count,"dynamic_render_objects":self.dynamic.len(),"initial_robot":self.initial_robot.map(|v|v.to_vec()),"final_robot":state,"limits":["Only the initial position frame is read. Fresh procedural seeds are settled by live GPU integration; no future motion is supplied.","GPU grains couple to declared spherical robot pads and dynamic spheres; imported rigid-body collisions are handled by MuJoCo.","Visual mesh detail and collision representation are separately declared.","Same translational DEM constants; GPU floating-point accumulation may differ.","Render FPS is not physical real-time factor."]});
        if let Some(p) = &self.profile {
            report["profiling"] = serde_json::json!({
                "schema": "scenward-physics-profile/v1",
                "loop_gpu_and_submission": self.particles.profiler.as_ref().map(|p| &p.timings),
                "settling_gpu_and_submission": self.settling_profile,
                "loop_host": p,
                "limits": [
                    "Opt-in GPU pass timestamps add overhead; compare throughput with profiling disabled.",
                    "GPU solver pass combines conditional clear/grid/neighbour rebuild, finish and integration; not individual kernel timings.",
                    "GPU pass sums overlap CPU submission/completion wall time; do not add GPU and CPU totals.",
                    "Submit-to-readback includes GPU execution and host scheduling; it is not isolated transfer latency.",
                    "Completed render wall time includes encoding, GPU completion and host wait; no GPU render-only timing."
                ]
            });
        }
        report
    }
}
