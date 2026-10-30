use crate::cache::{Mesh, Scene};
use anyhow::{ensure, Context, Result};
use bytemuck::{Pod, Zeroable};
use glam::{Mat4, Vec3};
use std::{path::Path, sync::mpsc};
use wgpu::util::DeviceExt;

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Uniform {
    vp: [[f32; 4]; 4],
    inverse_vp: [[f32; 4]; 4],
    shadow_vp: [[f32; 4]; 4],
    eye_radius: [f32; 4],
    right: [f32; 4],
    up: [f32; 4],
    color: [f32; 4],
    viewport: [f32; 4],
}
#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Vertex {
    position: [f32; 3],
    normal: [f32; 3],
}
#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Instance {
    matrix: [[f32; 4]; 4],
    color: [f32; 4],
    material: [f32; 4],
}
struct GpuMesh {
    vertex: wgpu::Buffer,
    index: wgpu::Buffer,
    count: u32,
}

pub struct Camera {
    pub yaw: f32,
    pub pitch: f32,
    pub distance: f32,
    pub target: Vec3,
}
impl Default for Camera {
    fn default() -> Self {
        Self {
            yaw: -1.05,
            pitch: 0.54,
            distance: 1.55,
            target: Vec3::new(0., 0., 0.09),
        }
    }
}
impl Camera {
    fn matrices(&self, width: u32, height: u32) -> (Mat4, Vec3, Vec3, Vec3) {
        let eye = self.target
            + Vec3::new(
                self.yaw.cos() * self.pitch.cos(),
                self.yaw.sin() * self.pitch.cos(),
                self.pitch.sin(),
            ) * self.distance;
        let direction = (self.target - eye).normalize();
        let right = direction.cross(Vec3::Z).normalize();
        let up = right.cross(direction).normalize();
        let vp = Mat4::perspective_rh(48f32.to_radians(), width as f32 / height as f32, 0.01, 50.)
            * Mat4::look_at_rh(eye, self.target, Vec3::Z);
        (vp, eye, right, up)
    }
}

pub struct Renderer {
    pub device: wgpu::Device,
    pub queue: wgpu::Queue,
    pub adapter_info: wgpu::AdapterInfo,
    pub width: u32,
    pub height: u32,
    pub samples: u32,
    pub camera: Camera,
    format: wgpu::TextureFormat,
    meshes: Vec<GpuMesh>,
    mesh_refs: Vec<usize>,
    model_data: Vec<Instance>,
    models: wgpu::Buffer,
    particles: wgpu::Buffer,
    feet: wgpu::Buffer,
    particle_count: u32,
    uniform: [wgpu::Buffer; 3],
    groups: [wgpu::BindGroup; 3],
    shadow_group: wgpu::BindGroup,
    mesh_pipeline: wgpu::RenderPipeline,
    sphere_pipeline: wgpu::RenderPipeline,
    shadow_pipeline: wgpu::RenderPipeline,
    shadow: wgpu::TextureView,
    depth: wgpu::TextureView,
    msaa: Option<wgpu::TextureView>,
    grain_radius: f32,
    foot_radius: f32,
}

fn depth_texture(
    device: &wgpu::Device,
    width: u32,
    height: u32,
    samples: u32,
    sampled: bool,
) -> wgpu::TextureView {
    let usage = wgpu::TextureUsages::RENDER_ATTACHMENT
        | if sampled {
            wgpu::TextureUsages::TEXTURE_BINDING
        } else {
            wgpu::TextureUsages::empty()
        };
    device
        .create_texture(&wgpu::TextureDescriptor {
            label: Some("scene depth"),
            size: wgpu::Extent3d {
                width,
                height,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: samples,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Depth32Float,
            usage,
            view_formats: &[],
        })
        .create_view(&Default::default())
}

fn vertex_layouts() -> [wgpu::VertexBufferLayout<'static>; 2] {
    const VERTEX: [wgpu::VertexAttribute; 2] = wgpu::vertex_attr_array![0=>Float32x3,1=>Float32x3];
    const INSTANCE: [wgpu::VertexAttribute; 6] = wgpu::vertex_attr_array![2=>Float32x4,3=>Float32x4,4=>Float32x4,5=>Float32x4,6=>Float32x4,7=>Float32x4];
    [
        wgpu::VertexBufferLayout {
            array_stride: 24,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &VERTEX,
        },
        wgpu::VertexBufferLayout {
            array_stride: 96,
            step_mode: wgpu::VertexStepMode::Instance,
            attributes: &INSTANCE,
        },
    ]
}

fn upload_mesh(device: &wgpu::Device, mesh: &Mesh) -> GpuMesh {
    let mut normals = vec![Vec3::ZERO; mesh.vertices.len()];
    for f in &mesh.faces {
        let a = Vec3::from_array(mesh.vertices[f[0] as usize]);
        let b = Vec3::from_array(mesh.vertices[f[1] as usize]);
        let c = Vec3::from_array(mesh.vertices[f[2] as usize]);
        let n = (b - a).cross(c - a);
        for i in f {
            normals[*i as usize] += n;
        }
    }
    let vertices: Vec<Vertex> = mesh
        .vertices
        .iter()
        .zip(normals)
        .map(|(p, n)| Vertex {
            position: *p,
            normal: n.normalize_or_zero().to_array(),
        })
        .collect();
    GpuMesh {
        vertex: device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("original robot vertices"),
            contents: bytemuck::cast_slice(&vertices),
            usage: wgpu::BufferUsages::VERTEX,
        }),
        index: device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("original robot indices"),
            contents: bytemuck::cast_slice(&mesh.faces),
            usage: wgpu::BufferUsages::INDEX,
        }),
        count: (mesh.faces.len() * 3) as u32,
    }
}

impl Renderer {
    pub async fn new(
        instance: &wgpu::Instance,
        surface: Option<&wgpu::Surface<'_>>,
        scene: &Scene,
        width: u32,
        height: u32,
        samples: u32,
        live_compute: bool,
    ) -> Result<Self> {
        let adapter = instance
            .request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::HighPerformance,
                force_fallback_adapter: false,
                compatible_surface: surface,
            })
            .await?;
        let info = adapter.get_info();
        ensure!(
            info.device_type != wgpu::DeviceType::Cpu,
            "A hardware GPU is required; software fallback was rejected"
        );
        let format = surface
            .map(|s| {
                s.get_capabilities(&adapter)
                    .formats
                    .into_iter()
                    .find(|f| f.is_srgb())
                    .unwrap_or(wgpu::TextureFormat::Bgra8Unorm)
            })
            .unwrap_or(wgpu::TextureFormat::Rgba8UnormSrgb);
        ensure!(
            adapter
                .get_texture_format_features(format)
                .flags
                .sample_count_supported(samples),
            "Requested MSAA is not supported"
        );
        let (device, queue) = adapter
            .request_device(&wgpu::DeviceDescriptor {
                label: Some("Scenward native GPU engine"),
                required_limits: wgpu::Limits {
                    max_storage_buffers_per_shader_stage: if live_compute { 10 } else { 8 },
                    ..Default::default()
                },
                ..Default::default()
            })
            .await?;
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("analytic spheres and shaded robot"),
            source: wgpu::ShaderSource::Wgsl(include_str!("scene.wgsl").into()),
        });
        let uniform_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("camera and shadows"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Depth,
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Comparison),
                    count: None,
                },
            ],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: None,
            bind_group_layouts: &[&uniform_layout],
            push_constant_ranges: &[],
        });
        let depth_state = wgpu::DepthStencilState {
            format: wgpu::TextureFormat::Depth32Float,
            depth_write_enabled: true,
            depth_compare: wgpu::CompareFunction::Less,
            stencil: Default::default(),
            bias: Default::default(),
        };
        let layouts = vertex_layouts();
        let target = Some(wgpu::ColorTargetState {
            format,
            blend: None,
            write_mask: wgpu::ColorWrites::ALL,
        });
        let mesh_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("original mesh renderer"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("mesh_vs"),
                compilation_options: Default::default(),
                buffers: &layouts,
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("mesh_fs"),
                compilation_options: Default::default(),
                targets: std::slice::from_ref(&target),
            }),
            primitive: wgpu::PrimitiveState {
                cull_mode: None,
                ..Default::default()
            },
            depth_stencil: Some(depth_state.clone()),
            multisample: wgpu::MultisampleState {
                count: samples,
                ..Default::default()
            },
            multiview: None,
            cache: None,
        });
        let sphere_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("exact per-fragment spherical grains"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("sphere_vs"),
                compilation_options: Default::default(),
                buffers: &[wgpu::VertexBufferLayout {
                    array_stride: 12,
                    step_mode: wgpu::VertexStepMode::Instance,
                    attributes: &wgpu::vertex_attr_array![0=>Float32x3],
                }],
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("sphere_fs"),
                compilation_options: Default::default(),
                targets: &[target],
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: Some(depth_state.clone()),
            multisample: wgpu::MultisampleState {
                count: samples,
                ..Default::default()
            },
            multiview: None,
            cache: None,
        });
        let shadow_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("robot shadow map"),
            layout: None,
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("shadow_vs"),
                compilation_options: Default::default(),
                buffers: &layouts,
            },
            fragment: None,
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: Some(depth_state),
            multisample: Default::default(),
            multiview: None,
            cache: None,
        });
        let uniform = std::array::from_fn(|_| {
            device.create_buffer(&wgpu::BufferDescriptor {
                label: Some("camera"),
                size: std::mem::size_of::<Uniform>() as u64,
                usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            })
        });
        let shadow = depth_texture(&device, 2048, 2048, 1, true);
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("PCF shadows"),
            compare: Some(wgpu::CompareFunction::LessEqual),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let groups = std::array::from_fn(|i| {
            device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: None,
                layout: &uniform_layout,
                entries: &[
                    wgpu::BindGroupEntry {
                        binding: 0,
                        resource: uniform[i].as_entire_binding(),
                    },
                    wgpu::BindGroupEntry {
                        binding: 1,
                        resource: wgpu::BindingResource::TextureView(&shadow),
                    },
                    wgpu::BindGroupEntry {
                        binding: 2,
                        resource: wgpu::BindingResource::Sampler(&sampler),
                    },
                ],
            })
        });
        let shadow_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: None,
            layout: &shadow_pipeline.get_bind_group_layout(0),
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: uniform[0].as_entire_binding(),
            }],
        });
        let mut meshes: Vec<GpuMesh> = scene
            .geometry
            .meshes
            .iter()
            .map(|m| upload_mesh(&device, m))
            .collect();
        meshes.push(upload_mesh(
            &device,
            &Mesh {
                vertices: vec![
                    [-4., -4., -0.002],
                    [4., -4., -0.002],
                    [4., 4., -0.002],
                    [-4., 4., -0.002],
                ],
                faces: vec![[0, 1, 2], [0, 2, 3]],
            },
        ));
        let mut mesh_refs: Vec<usize> = scene.geometry.geoms.iter().map(|g| g.mesh).collect();
        mesh_refs.push(meshes.len() - 1);
        let mut model_data: Vec<Instance> = scene
            .geometry
            .geoms
            .iter()
            .map(|g| Instance {
                matrix: Mat4::IDENTITY.to_cols_array_2d(),
                material: [0.28, 0.72, 0., 0.],
                color: if g.body.contains("LOWER") {
                    [0.52, 0.59, 0.65, 1.]
                } else {
                    [0.045, 0.065, 0.075, 1.]
                },
            })
            .collect();
        model_data.push(Instance {
            matrix: Mat4::IDENTITY.to_cols_array_2d(),
            color: [0.018, 0.025, 0.032, 0.],
            material: [0.85, 0., 0., 0.],
        });
        let models = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("recorded rigid transforms"),
            contents: bytemuck::cast_slice(&model_data),
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
        });
        let buffer = |size, label| {
            device.create_buffer(&wgpu::BufferDescriptor {
                label: Some(label),
                size,
                usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            })
        };
        let particles = buffer(
            scene.meta.grain_count as u64 * 12,
            "all recorded grain centers",
        );
        let feet = buffer(48, "four recorded pads");
        let depth = depth_texture(&device, width, height, samples, false);
        let mut this = Self {
            device,
            queue,
            adapter_info: info,
            width,
            height,
            samples,
            camera: Camera::default(),
            format,
            meshes,
            mesh_refs,
            model_data,
            models,
            particles,
            feet,
            particle_count: scene.meta.grain_count as u32,
            uniform,
            groups,
            shadow_group,
            mesh_pipeline,
            sphere_pipeline,
            shadow_pipeline,
            shadow,
            depth,
            msaa: None,
            grain_radius: scene.meta.grain_radius_m,
            foot_radius: scene.meta.foot_radius_m,
        };
        this.resize(width, height);
        Ok(this)
    }

    pub fn import_environment(
        &mut self,
        env: &crate::environment::Environment,
    ) -> Vec<(usize, String)> {
        let mut dynamic = vec![];
        for o in &env.objects {
            let mesh = self.meshes.len();
            self.meshes.push(upload_mesh(&self.device, &o.mesh));
            let index = self.model_data.len() - 1;
            self.mesh_refs.insert(index, mesh);
            self.model_data.insert(
                index,
                Instance {
                    matrix: Mat4::IDENTITY.to_cols_array_2d(),
                    color: o.color,
                    material: [o.roughness, o.metallic, 0., 0.],
                },
            );
            if let Some(body) = &o.dynamic_body {
                dynamic.push((index, body.clone()));
            }
        }
        self.models = self
            .device
            .create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("robot and complete Blender environment"),
                contents: bytemuck::cast_slice(&self.model_data),
                usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            });
        dynamic
    }
    pub fn update_pose(&mut self, index: usize, p: &[f32]) {
        self.model_data[index].matrix = [
            [p[3], p[6], p[9], 0.],
            [p[4], p[7], p[10], 0.],
            [p[5], p[8], p[11], 0.],
            [p[0], p[1], p[2], 1.],
        ];
    }
    pub fn upload_robot(&mut self, poses: &[f32], feet: &[f32]) {
        for (i, p) in poses.as_chunks::<12>().0.iter().enumerate() {
            self.update_pose(i, p);
        }
        self.queue
            .write_buffer(&self.feet, 0, bytemuck::cast_slice(feet));
        self.queue
            .write_buffer(&self.models, 0, bytemuck::cast_slice(&self.model_data));
    }
    pub fn use_particle_buffer(&mut self, buffer: &wgpu::Buffer) {
        self.particles = buffer.clone();
    }
    pub fn format(&self) -> wgpu::TextureFormat {
        self.format
    }
    pub fn resize(&mut self, width: u32, height: u32) {
        self.width = width.max(1);
        self.height = height.max(1);
        self.depth = depth_texture(&self.device, self.width, self.height, self.samples, false);
        self.msaa = if self.samples > 1 {
            Some(
                self.device
                    .create_texture(&wgpu::TextureDescriptor {
                        label: Some("MSAA color"),
                        size: wgpu::Extent3d {
                            width: self.width,
                            height: self.height,
                            depth_or_array_layers: 1,
                        },
                        mip_level_count: 1,
                        sample_count: self.samples,
                        dimension: wgpu::TextureDimension::D2,
                        format: self.format,
                        usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
                        view_formats: &[],
                    })
                    .create_view(&Default::default()),
            )
        } else {
            None
        };
    }
    pub fn target(&self) -> wgpu::Texture {
        self.device.create_texture(&wgpu::TextureDescriptor {
            label: Some("headless capture"),
            size: wgpu::Extent3d {
                width: self.width,
                height: self.height,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: self.format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        })
    }
    pub fn upload(&mut self, scene: &Scene) {
        self.queue.write_buffer(
            &self.particles,
            0,
            bytemuck::cast_slice(&scene.particles.values),
        );
        self.upload_robot(&scene.poses.values, &scene.feet.values);
    }

    fn update_camera(&self) {
        let (vp, eye, right, up) = self.camera.matrices(self.width, self.height);
        let shadow_vp = Mat4::orthographic_rh(-1.2, 1.2, -1.2, 1.2, 0.1, 6.)
            * Mat4::look_at_rh(Vec3::new(-1.5, -1.2, 3.), Vec3::ZERO, Vec3::Z);
        for (i, (radius, color)) in [
            (self.grain_radius, [0.42, 0.255, 0.105, 1.]),
            (self.foot_radius, [0.014, 0.019, 0.023, 0.]),
            (0., [1., 1., 1., 0.]),
        ]
        .into_iter()
        .enumerate()
        {
            let uniform = Uniform {
                vp: vp.to_cols_array_2d(),
                inverse_vp: vp.inverse().to_cols_array_2d(),
                shadow_vp: shadow_vp.to_cols_array_2d(),
                eye_radius: [eye.x, eye.y, eye.z, radius],
                right: [right.x, right.y, right.z, 0.],
                up: [up.x, up.y, up.z, 0.],
                color,
                viewport: [self.width as f32, self.height as f32, 0., 0.],
            };
            self.queue
                .write_buffer(&self.uniform[i], 0, bytemuck::bytes_of(&uniform));
        }
    }
    pub fn render(&self, output: &wgpu::TextureView) {
        self.update_camera();
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("Scenward frame"),
            });
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("robot shadows"),
                color_attachments: &[],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &self.shadow,
                    depth_ops: Some(wgpu::Operations {
                        load: wgpu::LoadOp::Clear(1.),
                        store: wgpu::StoreOp::Store,
                    }),
                    stencil_ops: None,
                }),
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            pass.set_pipeline(&self.shadow_pipeline);
            pass.set_bind_group(0, &self.shadow_group, &[]);
            pass.set_vertex_buffer(1, self.models.slice(..));
            for (i, index) in self.mesh_refs[..self.mesh_refs.len() - 1]
                .iter()
                .enumerate()
            {
                let m = &self.meshes[*index];
                pass.set_vertex_buffer(0, m.vertex.slice(..));
                pass.set_index_buffer(m.index.slice(..), wgpu::IndexFormat::Uint32);
                pass.draw_indexed(0..m.count, 0, i as u32..i as u32 + 1);
            }
        }
        {
            let colors = [Some(wgpu::RenderPassColorAttachment {
                view: self.msaa.as_ref().unwrap_or(output),
                resolve_target: if self.msaa.is_some() {
                    Some(output)
                } else {
                    None
                },
                depth_slice: None,
                ops: wgpu::Operations {
                    load: wgpu::LoadOp::Clear(wgpu::Color {
                        r: 0.008,
                        g: 0.013,
                        b: 0.022,
                        a: 1.,
                    }),
                    store: wgpu::StoreOp::Store,
                },
            })];
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("all grains and original robot"),
                color_attachments: &colors,
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &self.depth,
                    depth_ops: Some(wgpu::Operations {
                        load: wgpu::LoadOp::Clear(1.),
                        store: wgpu::StoreOp::Store,
                    }),
                    stencil_ops: None,
                }),
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            pass.set_pipeline(&self.mesh_pipeline);
            pass.set_bind_group(0, &self.groups[2], &[]);
            pass.set_vertex_buffer(1, self.models.slice(..));
            for (i, index) in self.mesh_refs.iter().enumerate() {
                let m = &self.meshes[*index];
                pass.set_vertex_buffer(0, m.vertex.slice(..));
                pass.set_index_buffer(m.index.slice(..), wgpu::IndexFormat::Uint32);
                pass.draw_indexed(0..m.count, 0, i as u32..i as u32 + 1);
            }
            pass.set_pipeline(&self.sphere_pipeline);
            pass.set_bind_group(0, &self.groups[0], &[]);
            pass.set_vertex_buffer(0, self.particles.slice(..));
            pass.draw(0..6, 0..self.particle_count);
            pass.set_bind_group(0, &self.groups[1], &[]);
            pass.set_vertex_buffer(0, self.feet.slice(..));
            pass.draw(0..6, 0..4);
        }
        self.queue.submit(Some(encoder.finish()));
    }
    pub fn wait(&self) -> Result<()> {
        self.device.poll(wgpu::PollType::wait_indefinitely())?;
        Ok(())
    }
    pub fn save_png(&self, texture: &wgpu::Texture, path: &Path) -> Result<()> {
        ensure!(!path.exists(), "Capture already exists");
        let row = (self.width * 4).div_ceil(256) * 256;
        let buffer = self.device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("capture readback"),
            size: row as u64 * self.height as u64,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        let mut encoder = self.device.create_command_encoder(&Default::default());
        encoder.copy_texture_to_buffer(
            texture.as_image_copy(),
            wgpu::TexelCopyBufferInfo {
                buffer: &buffer,
                layout: wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(row),
                    rows_per_image: Some(self.height),
                },
            },
            wgpu::Extent3d {
                width: self.width,
                height: self.height,
                depth_or_array_layers: 1,
            },
        );
        self.queue.submit(Some(encoder.finish()));
        let (send, receive) = mpsc::channel();
        buffer.slice(..).map_async(wgpu::MapMode::Read, move |r| {
            let _ = send.send(r);
        });
        self.wait()?;
        receive.recv().context("Readback channel")??;
        let mapped = buffer.slice(..).get_mapped_range();
        let mut rgba = Vec::with_capacity((self.width * self.height * 4) as usize);
        for bytes in mapped.chunks(row as usize) {
            rgba.extend_from_slice(&bytes[..self.width as usize * 4]);
        }
        image::save_buffer(
            path,
            &rgba,
            self.width,
            self.height,
            image::ColorType::Rgba8,
        )?;
        drop(mapped);
        buffer.unmap();
        Ok(())
    }
}
