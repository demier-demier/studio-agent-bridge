mod cache;
mod renderer;

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
    ))?;
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
        if self.playing {
            self.accumulator += elapsed.min(0.25) * self.scene.meta.fps as f64;
            let advance = self.accumulator.floor() as usize;
            self.accumulator -= advance as f64;
            self.frame = (self.frame + advance) % self.scene.meta.frames;
        }
        let r = self.renderer.as_mut().context("Renderer not initialized")?;
        if self.loaded != Some(self.frame) {
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
        {
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
            let renderer = pollster::block_on(Renderer::new(
                &self.instance,
                Some(&surface),
                &self.scene,
                size.width.max(1),
                size.height.max(1),
                self.args.samples,
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
                    PhysicalKey::Code(KeyCode::ArrowRight) => {
                        self.playing = false;
                        self.frame = (self.frame + 1) % self.scene.meta.frames;
                    }
                    PhysicalKey::Code(KeyCode::ArrowLeft) => {
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
        if self.playing || self.dirty {
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
    let scene = Scene::load(&args.run, &args.geometry)?;
    ensure!(args.frame < scene.meta.frames, "Frame out of bounds");
    if args.output.is_some() || args.benchmark > 0 {
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
    };
    event_loop.run_app(&mut app)?;
    if let Some(e) = app.error {
        anyhow::bail!(e);
    }
    Ok(())
}
