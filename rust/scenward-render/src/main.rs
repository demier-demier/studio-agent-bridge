mod cache;
mod environment;
mod live;
mod physics;
mod reference;
mod renderer;
#[cfg(feature = "robotics")]
mod robot;

use anyhow::{ensure, Context, Result};
use cache::Scene;
use clap::Parser;
use renderer::{Camera, Renderer};
use std::{fs::OpenOptions, path::PathBuf, sync::Arc, time::Instant};
use winit::{
    application::ApplicationHandler,
    event::{ElementState, MouseButton, MouseScrollDelta, WindowEvent},
    event_loop::{ActiveEventLoop, ControlFlow, EventLoop},
    keyboard::{KeyCode, PhysicalKey},
    window::{Window, WindowId},
};

#[derive(Parser, Clone)]
#[command(about = "Scenward: native Rust/Metal rendering of recorded physical simulations")]
struct Args {
    #[arg(long)]
    run: PathBuf,
    /// Advance fixed-step GPU physics instead of reading successive cache frames.
    #[arg(long)]
    live: bool,
    #[arg(long)]
    model: Option<PathBuf>,
    #[arg(long)]
    environment: Option<PathBuf>,
    #[arg(long, default_value = "robot")]
    view: String,
    #[arg(long, default_value_t = 100)]
    live_ticks: u32,
    #[arg(long)]
    physics_check: bool,
    #[arg(long)]
    headless: bool,
    #[arg(long)]
    robot_check: bool,
    #[arg(long)]
    geometry: PathBuf,
    #[arg(long, default_value_t = 1280)]
    width: u32,
    #[arg(long, default_value_t = 720)]
    height: u32,
    #[arg(long, default_value_t = 4)]
    samples: u32,
    #[arg(long, default_value_t = 0)]
    frame: usize,
    /// Save a headless PNG; does not launch a window.
    #[arg(long)]
    output: Option<PathBuf>,
    /// Measure completed GPU frames including cache read and upload (no vsync).
    #[arg(long, default_value_t = 0)]
    benchmark: usize,
    #[arg(long)]
    report: Option<PathBuf>,
    /// Exit an interactive window after this many frames (for integration checks).
    #[arg(long)]
    exit_after: Option<usize>,
}

fn gpu_instance() -> wgpu::Instance {
    wgpu::Instance::new(&wgpu::InstanceDescriptor {
        backends: if cfg!(target_os = "macos") {
            wgpu::Backends::METAL
        } else {
            wgpu::Backends::VULKAN | wgpu::Backends::DX12
        },
        ..Default::default()
    })
}

fn headless(args: &Args, mut scene: Scene) -> Result<()> {
    let instance = gpu_instance();
    let mut renderer = pollster::block_on(Renderer::new(
        &instance,
        None,
        &scene,
        args.width,
        args.height,
        args.samples,
        args.live || args.physics_check,
    ))?;
    if args.physics_check {
        return physics_checks(&renderer);
    }
    if args.live {
        return live_benchmark(args, scene, renderer);
    }
    let texture = renderer.target();
    let view = texture.create_view(&Default::default());
    scene.read(args.frame)?;
    renderer.upload(&scene);
    renderer.render(&view);
    renderer.wait()?;
    let mut times = vec![];
    if args.benchmark > 0 {
        for i in 0..5 {
            scene.read(i % scene.meta.frames)?;
            renderer.upload(&scene);
            renderer.render(&view);
            renderer.wait()?;
        }
        for i in 0..args.benchmark {
            let start = Instant::now();
            scene.read(i % scene.meta.frames)?;
            renderer.upload(&scene);
            renderer.render(&view);
            renderer.wait()?;
            times.push(start.elapsed().as_secs_f64() * 1000.);
        }
        let mut sorted = times.clone();
        sorted.sort_by(f64::total_cmp);
        let mean = times.iter().sum::<f64>() / times.len() as f64;
        let report = serde_json::json!({
            "schema":"scenward-render-benchmark/v1","adapter":renderer.adapter_info.name,
            "backend":format!("{:?}",renderer.adapter_info.backend),"width":args.width,"height":args.height,
            "msaa_samples":args.samples,"rendered_particles_per_frame":scene.meta.grain_count,
            "robot_mesh_instances":scene.geometry.geoms.len(),"frames_measured":times.len(),"warmup_frames":5,
            "mean_completed_frame_ms":mean,"median_completed_frame_ms":sorted[sorted.len()/2],
            "p95_completed_frame_ms":sorted[((sorted.len() as f64*0.95).ceil() as usize-1).min(sorted.len()-1)],
            "completed_frames_per_second":1000./mean,"samples_ms":times,
            "scope":"Recorded-cache playback: frame reads, validation, upload, rendering and GPU completion. Physics computation and PNG encoding excluded.",
            "geometry":"Every recorded grain is drawn as an analytic depth-correct sphere; original robot mesh triangles are retained.",
            "limits":["Raster shading is not Cycles path tracing.","Only robot meshes cast shadow maps; grains receive shadows but do not cast granular self-shadow maps.","This measures rendering, not real-time million-particle physics.","GPU execution on Windows NVIDIA/AMD is not established by this Mac run."]
        });
        if let Some(path) = &args.report {
            serde_json::to_writer_pretty(
                OpenOptions::new().write(true).create_new(true).open(path)?,
                &report,
            )?;
        }
        println!("{}", serde_json::to_string_pretty(&report)?);
    }
    if let Some(path) = &args.output {
        scene.read(args.frame)?;
        renderer.upload(&scene);
        renderer.render(&view);
        renderer.save_png(&texture, path)?;
        println!("Capture: {}", path.display());
    }
    Ok(())
}

struct App {
    args: Args,
    scene: Scene,
    instance: wgpu::Instance,
    window: Option<Arc<Window>>,
    surface: Option<wgpu::Surface<'static>>,
    renderer: Option<Renderer>,
    config: Option<wgpu::SurfaceConfiguration>,
    frame: usize,
    loaded: Option<usize>,
    playing: bool,
    last_tick: Instant,
    accumulator: f64,
    dragging: bool,
    last_cursor: Option<(f64, f64)>,
    draws: usize,
    dirty: bool,
    error: Option<String>,
    live: Option<live::Live>,
    single_step: bool,
}
impl App {
    fn configure(&mut self, width: u32, height: u32) {
        if width == 0 || height == 0 {
            return;
        }
        if let (Some(r), Some(s), Some(c)) = (&mut self.renderer, &self.surface, &mut self.config) {
            r.resize(width, height);
            c.width = width;
            c.height = height;
            s.configure(&r.device, c);
        }
    }
    fn draw(&mut self) -> Result<()> {
        let now = Instant::now();
        let elapsed = now.duration_since(self.last_tick).as_secs_f64();
        self.last_tick = now;
        if self.playing && self.live.is_none() {
            self.accumulator += elapsed.min(0.25) * self.scene.meta.fps as f64;
            let advance = self.accumulator.floor() as usize;
            self.accumulator -= advance as f64;
            self.frame = (self.frame + advance) % self.scene.meta.frames;
        }
        let r = self.renderer.as_mut().context("Renderer not initialized")?;
        if let Some(live) = &mut self.live {
            if self.playing || self.single_step {
                live.tick(r)?;
                self.single_step = false;
            }
        } else if self.loaded != Some(self.frame) {
            self.scene.read(self.frame)?;
            r.upload(&self.scene);
            self.loaded = Some(self.frame);
        }
        let surface = self.surface.as_ref().context("Surface not initialized")?;
        let output = match surface.get_current_texture() {
            Ok(o) => o,
            Err(wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated) => {
                surface.configure(&r.device, self.config.as_ref().unwrap());
                return Ok(());
            }
            Err(wgpu::SurfaceError::Timeout) => return Ok(()),
            Err(e) => return Err(e.into()),
        };
        r.render(&output.texture.create_view(&Default::default()));
        output.present();
        self.draws += 1;
        self.dirty = false;
        if let Some(live) = &self.live {
            self.window.as_ref().unwrap().set_title(&format!("Scenward LIVE PHYSICS | {} grains | sim {:.3}s | physics {:.4}x realtime | {} | Space run/pause · Right step · P push robot",live.particles.count,live.seconds(),live.seconds()/live.physics_wall.max(1e-9),if self.playing {"RUN"}else{"PAUSED"}));
        } else {
            self.window.as_ref().unwrap().set_title(&format!("Scenward / Rust GPU Engine  |  {} grains  |  frame {}/{}  |  {}  |  Space play/pause · drag orbit · scroll zoom · arrows scrub",self.scene.meta.grain_count,self.frame,self.scene.meta.frames-1,if self.playing {"PLAY"}else{"PAUSED"}));
        }
        Ok(())
    }
}
impl ApplicationHandler for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let result = (|| -> Result<()> {
            let window = Arc::new(
                event_loop.create_window(
                    Window::default_attributes()
                        .with_title("Scenward / Rust GPU Engine")
                        .with_inner_size(winit::dpi::LogicalSize::new(
                            self.args.width,
                            self.args.height,
                        )),
                )?,
            );
            let surface = self.instance.create_surface(window.clone())?;
            let size = window.inner_size();
            let mut renderer = pollster::block_on(Renderer::new(
                &self.instance,
                Some(&surface),
                &self.scene,
                size.width.max(1),
                size.height.max(1),
                self.args.samples,
                self.args.live || self.args.physics_check,
            ))?;
            let config = wgpu::SurfaceConfiguration {
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
                format: renderer.format(),
                width: size.width.max(1),
                height: size.height.max(1),
                present_mode: wgpu::PresentMode::Fifo,
                desired_maximum_frame_latency: 2,
                alpha_mode: wgpu::CompositeAlphaMode::Auto,
                view_formats: vec![],
            };
            surface.configure(&renderer.device, &config);
            if self.args.live {
                self.live = Some(live::Live::new(&self.args, &mut self.scene, &mut renderer)?);
            } else if let Some(path) = &self.args.environment {
                renderer.import_environment(&environment::Environment::load(path)?);
            }
            self.window = Some(window);
            self.surface = Some(surface);
            self.renderer = Some(renderer);
            self.config = Some(config);
            self.last_tick = Instant::now();
            Ok(())
        })();
        if let Err(e) = result {
            self.error = Some(format!("{e:#}"));
            event_loop.exit();
        }
    }
    fn window_event(&mut self, event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        if !matches!(event, WindowEvent::RedrawRequested) {
            self.dirty = true;
        }
        match event {
            WindowEvent::CloseRequested => event_loop.exit(),
            WindowEvent::Resized(size) => self.configure(size.width, size.height),
            WindowEvent::MouseInput {
                state,
                button: MouseButton::Left,
                ..
            } => self.dragging = state == ElementState::Pressed,
            WindowEvent::CursorMoved { position, .. } => {
                if self.dragging {
                    if let (Some((x, y)), Some(r)) = (self.last_cursor, &mut self.renderer) {
                        r.camera.yaw -= ((position.x - x) * 0.006) as f32;
                        r.camera.pitch =
                            (r.camera.pitch + ((position.y - y) * 0.004) as f32).clamp(0.05, 1.50);
                    }
                }
                self.last_cursor = Some((position.x, position.y));
            }
            WindowEvent::MouseWheel { delta, .. } => {
                let delta = match delta {
                    MouseScrollDelta::LineDelta(_, y) => y,
                    MouseScrollDelta::PixelDelta(p) => p.y as f32 / 50.,
                };
                if let Some(r) = &mut self.renderer {
                    r.camera.distance =
                        (r.camera.distance * (-delta * 0.12).exp()).clamp(0.25, 12.);
                }
            }
            WindowEvent::KeyboardInput { event, .. } if event.state == ElementState::Pressed => {
                match event.physical_key {
                    PhysicalKey::Code(KeyCode::Escape) => event_loop.exit(),
                    PhysicalKey::Code(KeyCode::Space) => self.playing = !self.playing,
                    PhysicalKey::Code(KeyCode::KeyP) => {
                        if let Some(live) = &mut self.live {
                            live.push();
                        }
                    }
                    PhysicalKey::Code(KeyCode::ArrowRight) => {
                        if self.live.is_some() {
                            self.single_step = true;
                        }
                        self.playing = false;
                        self.frame = (self.frame + 1) % self.scene.meta.frames;
                    }
                    PhysicalKey::Code(KeyCode::ArrowLeft) if self.live.is_none() => {
                        self.playing = false;
                        self.frame =
                            (self.frame + self.scene.meta.frames - 1) % self.scene.meta.frames;
                    }
                    PhysicalKey::Code(KeyCode::Home) => {
                        if let Some(r) = &mut self.renderer {
                            r.camera = Camera::default();
                        }
                    }
                    PhysicalKey::Code(KeyCode::Digit1) => {
                        if let Some(r) = &mut self.renderer {
                            r.camera = Camera::default();
                        }
                    }
                    PhysicalKey::Code(KeyCode::Digit2) => {
                        if let Some(r) = &mut self.renderer {
                            r.camera.yaw = -1.57;
                            r.camera.pitch = 0.22;
                            r.camera.distance = 1.35;
                        }
                    }
                    PhysicalKey::Code(KeyCode::Digit4) => {
                        if let Some(r) = &mut self.renderer {
                            r.camera.distance = 4.1;
                            r.camera.target = glam::Vec3::new(0., 2., 1.1);
                            r.camera.yaw = -1.1;
                            r.camera.pitch = 0.18;
                        }
                    }
                    PhysicalKey::Code(KeyCode::Digit3) => {
                        if let Some(r) = &mut self.renderer {
                            r.camera.pitch = 1.48;
                            r.camera.distance = 1.9;
                        }
                    }
                    _ => {}
                }
            }
            WindowEvent::RedrawRequested => {
                if let Err(e) = self.draw() {
                    self.error = Some(format!("{e:#}"));
                    event_loop.exit();
                }
                if self.args.exit_after.is_some_and(|n| self.draws >= n) {
                    event_loop.exit();
                }
            }
            _ => {}
        }
    }
    fn about_to_wait(&mut self, _: &ActiveEventLoop) {
        if self.playing || self.dirty || self.single_step {
            if let Some(w) = &self.window {
                w.request_redraw();
            }
        }
    }
}

fn main() -> Result<()> {
    // A locally packaged macOS app carries its numeric scene in Resources.
    // Keep the actual Rust binary as CFBundleExecutable so macOS identifies
    // permissions and the native window with the correct application.
    let mut cli: Vec<std::ffi::OsString> = std::env::args_os().collect();
    if cfg!(target_os = "macos") && cli.len() == 1 {
        if let Some(contents) = std::env::current_exe()?.parent().and_then(|p| p.parent()) {
            let resources = contents.join("Resources");
            if resources.join("geometry.json").is_file() {
                cli.extend([
                    "--run".into(),
                    resources.join("run").into_os_string(),
                    "--geometry".into(),
                    resources.join("geometry.json").into_os_string(),
                ]);
                if resources.join("live-model.xml").is_file() {
                    cli.extend([
                        "--live".into(),
                        "--model".into(),
                        resources.join("live-model.xml").into_os_string(),
                        "--environment".into(),
                        resources.join("environment.json").into_os_string(),
                    ]);
                }
            }
        }
    }
    let args = Args::parse_from(cli);
    ensure!(
        (320..=3840).contains(&args.width) && (180..=2160).contains(&args.height),
        "Resolution outside supported bounds"
    );
    ensure!(
        args.samples == 1 || args.samples == 4,
        "MSAA must be 1 or 4"
    );
    ensure!(args.benchmark <= 10_000, "Benchmark frame limit exceeded");
    if args.robot_check {
        #[cfg(feature = "robotics")]
        {
            let mut robot =
                robot::Robot::open(args.model.as_ref().context("--model is required")?)?;
            let initial = robot.state();
            for _ in 0..3000 {
                robot.step(&vec![[0.; 6]; robot.count])?;
            }
            let state = robot.state();
            ensure!(state[2] > 0.15 && state[2] < 0.4, "Robot standing height");
            ensure!(
                state[0] > initial[0] + 0.05,
                "Controller produces forward motion"
            );
            println!(
                "Native robotics check passed: time={:.3}s, travel={:.4}m, height={:.4}m",
                state[38],
                state[0] - initial[0],
                state[2]
            );
            return Ok(());
        }
        #[cfg(not(feature = "robotics"))]
        anyhow::bail!("Build with --features robotics");
    }
    let scene = Scene::load(&args.run, &args.geometry)?;
    ensure!(args.frame < scene.meta.frames, "Frame out of bounds");
    if args.output.is_some() || args.benchmark > 0 || args.physics_check || args.headless {
        return headless(&args, scene);
    }
    let event_loop = EventLoop::new()?;
    event_loop.set_control_flow(ControlFlow::Wait);
    let frame = args.frame;
    let mut app = App {
        args,
        scene,
        instance: gpu_instance(),
        window: None,
        surface: None,
        renderer: None,
        config: None,
        frame,
        loaded: None,
        playing: false,
        last_tick: Instant::now(),
        accumulator: 0.,
        dragging: false,
        last_cursor: None,
        draws: 0,
        dirty: true,
        error: None,
        live: None,
        single_step: false,
    };
    event_loop.run_app(&mut app)?;
    if let Some(e) = app.error {
        anyhow::bail!(e);
    }
    Ok(())
}

fn physics_checks(r: &Renderer) -> Result<()> {
    use physics::{GpuPhysics, SphereBody};
    let dt = 0.00005f32;
    let radius = 0.0015f32;
    let mass = 2500. * 4. / 3. * std::f32::consts::PI * radius.powi(3);
    let tests = [
        ("gravity", vec![0., 0., 0.1], vec![0., 0., 0.]),
        ("pair", vec![0., 0., 0.1, 0.0029, 0., 0.1], vec![0.; 6]),
        ("floor", vec![0., 0., 0.0014], vec![0.; 3]),
    ];
    for (name, x, v) in tests {
        let mut p = GpuPhysics::new(&r.device, &x, &v, 0)?;
        p.advance(&r.device, &r.queue, &[], 1)?;
        let (after, vel) = p.snapshot(&r.device, &r.queue)?;
        ensure!(
            (vel[2] + 9.81 * dt
                - (if name == "floor" {
                    500. * (radius - x[2]) * dt / mass
                } else {
                    0.
                }))
            .abs()
                < 1e-5,
            "{name} vertical acceleration"
        );
        if name == "pair" {
            let expected = 500. * (2. * radius - 0.0029) * dt / mass;
            ensure!(
                (vel[0] + expected).abs() < 1e-5 && (vel[3] - expected).abs() < 1e-5,
                "Pair force symmetry"
            );
        }
        ensure!(after.iter().all(|x| x.is_finite()), "Finite output");
        println!("PASS {name}");
    }
    let mut p = GpuPhysics::new(&r.device, &[0., 0., 0.1], &[0.; 3], 1)?;
    let body = SphereBody {
        center: [0.02, 0., 0.1, 0.02],
        ..Default::default()
    };
    let reaction = p.advance(&r.device, &r.queue, &[body], 1)?;
    let (_, vel) = p.snapshot(&r.device, &r.queue)?;
    ensure!(
        (vel[0] * mass / dt + reaction[0][0]).abs() < 1e-4,
        "Equal and opposite body reaction"
    );
    println!("PASS two-way reaction");
    let x = [0., 0., 0.1].repeat(17);
    let mut p = GpuPhysics::new(&r.device, &x, &vec![0.; x.len()], 0)?;
    ensure!(
        p.advance(&r.device, &r.queue, &[], 1).is_err(),
        "Overflow must abort"
    );
    println!("PASS overflow rejection");
    let mut p = GpuPhysics::new(&r.device, &[4., 0., 0.1], &[0.; 3], 0)?;
    ensure!(
        p.advance(&r.device, &r.queue, &[], 1).is_err(),
        "Out of grid must abort"
    );
    println!("PASS bounds rejection");
    let mut initial = vec![];
    for z in 0..3 {
        for y in 0..3 {
            for x in 0..3 {
                initial.extend_from_slice(&[
                    x as f32 * 0.00295 - 0.003,
                    y as f32 * 0.003,
                    0.04 + z as f32 * 0.00295,
                ]);
            }
        }
    }
    let mut cpu_x = initial.clone();
    let mut cpu_v = vec![0.; initial.len()];
    let mut gpu = GpuPhysics::new(&r.device, &initial, &cpu_v, 0)?;
    for _ in 0..80 {
        crate::reference::step(&mut cpu_x, &mut cpu_v);
    }
    // Exercise persistent neighbour state across separate host-side batches.
    gpu.advance(&r.device, &r.queue, &[], 40)?;
    gpu.advance(&r.device, &r.queue, &[], 40)?;
    let (gpu_x, gpu_v) = gpu.snapshot(&r.device, &r.queue)?;
    let error_x = cpu_x
        .iter()
        .zip(gpu_x)
        .map(|(a, b)| (a - b).abs())
        .fold(0f32, f32::max);
    let error_v = cpu_v
        .iter()
        .zip(gpu_v)
        .map(|(a, b)| (a - b).abs())
        .fold(0f32, f32::max);
    ensure!(
        error_x < 1e-6 && error_v < 1e-4,
        "CPU oracle mismatch: x={error_x}, v={error_v}"
    );
    println!("PASS 80-step CPU oracle: position error {error_x}, velocity error {error_v}");
    let mut cpu_x = vec![-0.003, 0., 0.05, 0.003, 0., 0.05];
    let mut cpu_v = vec![2., 0., 0., -2., 0., 0.];
    let mut gpu = GpuPhysics::new(&r.device, &cpu_x, &cpu_v, 0)?;
    for _ in 0..40 {
        crate::reference::step(&mut cpu_x, &mut cpu_v);
    }
    gpu.advance(&r.device, &r.queue, &[], 40)?;
    let (gpu_x, gpu_v) = gpu.snapshot(&r.device, &r.queue)?;
    ensure!(
        cpu_x.iter().zip(gpu_x).all(|(a, b)| (a - b).abs() < 1e-6)
            && cpu_v.iter().zip(gpu_v).all(|(a, b)| (a - b).abs() < 1e-4),
        "Skin crossing missed a new contact"
    );
    println!("PASS skin crossing rebuild");

    Ok(())
}
fn live_benchmark(args: &Args, mut scene: Scene, mut r: Renderer) -> Result<()> {
    ensure!(
        args.live_ticks > 0 && args.live_ticks <= 20000,
        "Live ticks must be 1..20000"
    );
    let mut live = live::Live::new(args, &mut scene, &mut r)?;
    if args.view == "environment" {
        r.camera.distance = 4.1;
        r.camera.target = glam::Vec3::new(0., 2., 1.1);
        r.camera.yaw = -1.1;
        r.camera.pitch = 0.18;
    }
    let target = r.target();
    let view = target.create_view(&Default::default());
    let start = Instant::now();
    for _ in 0..args.live_ticks {
        live.tick(&mut r)?;
        r.render(&view);
        r.wait()?;
    }
    let wall = start.elapsed().as_secs_f64();
    let mut report = live.report(wall);
    let (positions, velocities) = live.particles.snapshot(&r.device, &r.queue)?;
    ensure!(
        positions.iter().chain(&velocities).all(|v| v.is_finite()),
        "Final particle state must be finite"
    );
    let min: [f32; 3] = std::array::from_fn(|j| {
        positions
            .as_chunks::<3>()
            .0
            .iter()
            .map(|p| p[j])
            .fold(f32::INFINITY, f32::min)
    });
    let max: [f32; 3] = std::array::from_fn(|j| {
        positions
            .as_chunks::<3>()
            .0
            .iter()
            .map(|p| p[j])
            .fold(f32::NEG_INFINITY, f32::max)
    });
    let rms = (velocities.iter().map(|v| (*v as f64).powi(2)).sum::<f64>()
        / live.particles.count as f64)
        .sqrt();
    report["final_particle_diagnostics"] = serde_json::json!({"count":positions.len()/3,"all_finite":true,"min_m":min,"max_m":max,"rms_speed_m_s":rms,"scope":"Read after timed loop; no reduction or discarded particles."});
    println!("{}", serde_json::to_string_pretty(&report)?);
    if let Some(path) = &args.output {
        r.save_png(&target, path)?;
    }
    if let Some(path) = &args.report {
        serde_json::to_writer_pretty(
            OpenOptions::new().write(true).create_new(true).open(path)?,
            &report,
        )?;
    }
    Ok(())
}
