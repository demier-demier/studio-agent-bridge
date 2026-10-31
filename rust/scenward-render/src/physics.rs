//! GPU-resident fixed-step DEM; storage buffers are shared with the renderer.
use crate::profiling::{GpuProfiler, MAX_QUERIES, MAX_STEPS, QUERIES_PER_STEP};
use anyhow::{ensure, Context, Result};
use bytemuck::{Pod, Zeroable};
use std::time::Instant;
use wgpu::util::DeviceExt;
#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Params {
    count: u32,
    table: u32,
    capacity: u32,
    bodies: u32,
    shape: [u32; 4],
    low: [f32; 4],
    bounds: [f32; 4],
    material: [f32; 4],
    contact: [f32; 4],
}
#[repr(C)]
#[derive(Clone, Copy, Default, Pod, Zeroable)]
pub struct SphereBody {
    pub center: [f32; 4],
    pub velocity: [f32; 4],
    pub omega: [f32; 4],
}
pub struct GpuPhysics {
    pub positions: [wgpu::Buffer; 2],
    velocities: [wgpu::Buffer; 2],
    indirect: wgpu::Buffer,
    prepare_group: wgpu::BindGroup,
    prepare: wgpu::ComputePipeline,
    pub rebuilds: u32,
    bodies: wgpu::Buffer,
    status: wgpu::Buffer,
    readback: wgpu::Buffer,
    groups: [wgpu::BindGroup; 2],
    grid: wgpu::ComputePipeline,
    integrate: wgpu::ComputePipeline,
    clear: wgpu::ComputePipeline,
    neighbours: wgpu::ComputePipeline,
    finish: wgpu::ComputePipeline,
    pub current: usize,
    pub count: usize,
    pub steps: u64,
    pub dt: f64,
    pub body_count: usize,
    pub profiler: Option<GpuProfiler>,
}
impl GpuPhysics {
    pub fn new(
        device: &wgpu::Device,
        points: &[f32],
        velocity: &[f32],
        body_count: usize,
    ) -> Result<Self> {
        ensure!(
            !points.is_empty() && points.len().is_multiple_of(3) && points.len() == velocity.len(),
            "Invalid particle arrays"
        );
        ensure!(
            points.len() <= 6_000_000 && points.iter().chain(velocity).all(|x| x.is_finite()),
            "Invalid particle values/count"
        );
        ensure!(body_count <= 64, "At most 64 coupled spherical bodies");
        let count = points.len() / 3;
        let radius = 0.0015f32;
        let cell = 2.2 * radius;
        let shape = [
            (1.62 / cell).ceil() as u32,
            (0.70 / cell).ceil() as u32,
            (0.22 / cell).ceil() as u32,
            0,
        ];
        let span = 2 * shape[1] * shape[2] + 2 * shape[2] + 3;
        let table = (2 * count as u32).max(span).next_power_of_two();
        ensure!(
            table as u64 * 16 * 4 <= device.limits().max_storage_buffer_binding_size as u64,
            "GPU storage limit too small for unchanged particle count"
        );
        let cfg = Params {
            count: count as u32,
            table,
            capacity: 16,
            bodies: body_count as u32,
            shape,
            low: [-0.81, -0.35, -0.01, 0.],
            bounds: [0.80, 0.34, 0.20, 0.],
            material: [
                radius,
                2500. * 4. / 3. * std::f32::consts::PI * radius.powi(3),
                0.00005,
                cell,
            ],
            contact: [500., 0.0625, 0.65, 0.],
        };
        let storage = |bytes, label| {
            device.create_buffer(&wgpu::BufferDescriptor {
                label: Some(label),
                size: bytes,
                usage: wgpu::BufferUsages::STORAGE
                    | wgpu::BufferUsages::COPY_DST
                    | wgpu::BufferUsages::COPY_SRC,
                mapped_at_creation: false,
            })
        };
        let positions = std::array::from_fn(|_| {
            device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("live particle centers"),
                contents: bytemuck::cast_slice(points),
                usage: wgpu::BufferUsages::STORAGE
                    | wgpu::BufferUsages::VERTEX
                    | wgpu::BufferUsages::COPY_SRC
                    | wgpu::BufferUsages::COPY_DST,
            })
        });
        let velocities = std::array::from_fn(|_| {
            device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("live particle velocities"),
                contents: bytemuck::cast_slice(velocity),
                usage: wgpu::BufferUsages::STORAGE
                    | wgpu::BufferUsages::COPY_SRC
                    | wgpu::BufferUsages::COPY_DST,
            })
        });
        let counts = storage(table as u64 * 4, "hash counts");
        let indices = storage(table as u64 * 16 * 4, "hash slots");
        let bodies = storage(64 * 48, "coupled bodies");
        let status = storage((4 + 64 * 6 + 2) * 4, "faults and reactions");
        let profiler = device
            .features()
            .contains(wgpu::Features::TIMESTAMP_QUERY)
            .then(|| GpuProfiler::new(device));
        let readback = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("small reaction readback"),
            size: status.size()
                + if profiler.is_some() {
                    u64::from(MAX_QUERIES) * 8
                } else {
                    0
                },
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        let params = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("unchanged DEM constants"),
            contents: bytemuck::bytes_of(&cfg),
            usage: wgpu::BufferUsages::UNIFORM,
        });
        let neighbours = storage(count as u64 * 33 * 4, "bounded Verlet neighbours");
        let reference = storage(count as u64 * 12, "neighbour reference positions");
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("resident physics"),
            entries: &(0..11)
                .map(|i| wgpu::BindGroupLayoutEntry {
                    binding: i,
                    visibility: wgpu::ShaderStages::COMPUTE,
                    ty: wgpu::BindingType::Buffer {
                        ty: if i == 0 {
                            wgpu::BufferBindingType::Uniform
                        } else {
                            wgpu::BufferBindingType::Storage {
                                read_only: matches!(i, 1 | 2 | 7),
                            }
                        },
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                })
                .collect::<Vec<_>>(),
        });
        let groups = std::array::from_fn(|i| {
            device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: Some("physics ping-pong"),
                layout: &layout,
                entries: &[
                    &params,
                    &positions[i],
                    &velocities[i],
                    &positions[1 - i],
                    &velocities[1 - i],
                    &counts,
                    &indices,
                    &bodies,
                    &status,
                    &neighbours,
                    &reference,
                ]
                .iter()
                .enumerate()
                .map(|(i, b)| wgpu::BindGroupEntry {
                    binding: i as u32,
                    resource: b.as_entire_binding(),
                })
                .collect::<Vec<_>>(),
            })
        });
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("native live DEM"),
            source: wgpu::ShaderSource::Wgsl(include_str!("physics.wgsl").into()),
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: None,
            bind_group_layouts: &[&layout],
            push_constant_ranges: &[],
        });
        let pipeline = |entry| {
            device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                label: Some(entry),
                layout: Some(&pipeline_layout),
                module: &shader,
                entry_point: Some(entry),
                compilation_options: Default::default(),
                cache: None,
            })
        };
        let indirect = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("GPU-controlled rebuild dispatch"),
            size: 24,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::INDIRECT,
            mapped_at_creation: false,
        });
        let prep_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: None,
            entries: &[0, 8, 11].map(|binding| wgpu::BindGroupLayoutEntry {
                binding,
                visibility: wgpu::ShaderStages::COMPUTE,
                ty: wgpu::BindingType::Buffer {
                    ty: if binding == 0 {
                        wgpu::BufferBindingType::Uniform
                    } else {
                        wgpu::BufferBindingType::Storage { read_only: false }
                    },
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            }),
        });
        let prepare_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: None,
            layout: &prep_layout,
            entries: &[(0, &params), (8, &status), (11, &indirect)].map(|(binding, b)| {
                wgpu::BindGroupEntry {
                    binding,
                    resource: b.as_entire_binding(),
                }
            }),
        });
        let prep_pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: None,
            bind_group_layouts: &[&prep_layout],
            push_constant_ranges: &[],
        });
        let prepare = device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
            label: Some("conditional grid rebuild"),
            layout: Some(&prep_pipeline_layout),
            module: &shader,
            entry_point: Some("prepare_dispatch"),
            compilation_options: Default::default(),
            cache: None,
        });
        Ok(Self {
            positions,
            velocities,
            indirect,
            prepare_group,
            prepare,
            rebuilds: 0,
            bodies,
            status,
            readback,
            groups,
            grid: pipeline("grid"),
            integrate: pipeline("integrate"),
            clear: pipeline("clear_grid"),
            neighbours: pipeline("build_neighbours"),
            finish: pipeline("finish_grid"),
            current: 0,
            count,
            steps: 0,
            dt: 0.00005,
            body_count,
            profiler,
        })
    }
    pub fn advance(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        bodies: &[SphereBody],
        steps: u32,
    ) -> Result<Vec<[f32; 6]>> {
        let encode_start = self.profiler.as_ref().map(|_| Instant::now());
        ensure!(
            bodies.len() == self.body_count && steps > 0 && steps <= MAX_STEPS,
            "Invalid coupling batch"
        );
        ensure!(
            bytemuck::cast_slice::<_, f32>(bodies)
                .iter()
                .all(|x| x.is_finite()),
            "Nonfinite body state"
        );
        if !bodies.is_empty() {
            queue.write_buffer(&self.bodies, 0, bytemuck::cast_slice(bodies));
        }
        queue.write_buffer(&self.status, 0, &[0u8; 388 * 4]);
        if self.steps == 0 {
            queue.write_buffer(&self.status, 388 * 4, bytemuck::bytes_of(&1u32));
        }
        let mut enc = device.create_command_encoder(&Default::default());
        for step in 0..steps {
            {
                let mut p = enc.begin_compute_pass(&wgpu::ComputePassDescriptor {
                    label: Some("DEM prepare"),
                    timestamp_writes: self.profiler.as_ref().map(|p| p.pass(step, false)),
                });
                p.set_bind_group(0, &self.prepare_group, &[]);
                p.set_pipeline(&self.prepare);
                p.dispatch_workgroups(1, 1, 1);
            }
            {
                let mut p = enc.begin_compute_pass(&wgpu::ComputePassDescriptor {
                    label: Some("DEM rebuild and integrate"),
                    timestamp_writes: self.profiler.as_ref().map(|p| p.pass(step, true)),
                });
                p.set_bind_group(0, &self.groups[self.current], &[]);
                p.set_pipeline(&self.clear);
                p.dispatch_workgroups_indirect(&self.indirect, 0);
                p.set_pipeline(&self.grid);
                p.dispatch_workgroups_indirect(&self.indirect, 12);
                p.set_pipeline(&self.neighbours);
                p.dispatch_workgroups_indirect(&self.indirect, 12);
                p.set_pipeline(&self.finish);
                p.dispatch_workgroups(1, 1, 1);
                p.set_pipeline(&self.integrate);
                p.dispatch_workgroups((self.count as u32).div_ceil(128), 1, 1);
            }
            self.current = 1 - self.current;
        }
        enc.copy_buffer_to_buffer(&self.status, 0, &self.readback, 0, self.status.size());
        if let Some(profile) = &self.profiler {
            profile.resolve(&mut enc, &self.readback, self.status.size(), steps);
        }
        let commands = enc.finish();
        let encode_seconds = encode_start.map(|s| s.elapsed().as_secs_f64());
        let readback_start = self.profiler.as_ref().map(|_| Instant::now());
        queue.submit(Some(commands));
        let bytes = read_buffer(device, &self.readback)?;
        let readback_seconds = readback_start.map(|s| s.elapsed().as_secs_f64());
        let status: &[u32] = bytemuck::cast_slice(&bytes[..self.status.size() as usize]);
        ensure!(
            status[0] == 0 && status[1] == 0 && status[2] == 0,
            "Physics aborted: overflow={}, out-of-grid={}, nonfinite={}",
            status[0],
            status[1],
            status[2]
        );
        self.steps += steps as u64;
        self.rebuilds = status[389];
        if let Some(profile) = &mut self.profiler {
            let offset = self.status.size() as usize;
            let count = steps as usize * QUERIES_PER_STEP as usize * 8;
            profile.timings.record_gpu(
                &bytes[offset..offset + count],
                steps,
                queue.get_timestamp_period(),
            )?;
            profile.timings.cpu_encode.add(encode_seconds.unwrap());
            profile
                .timings
                .submit_to_readback
                .add(readback_seconds.unwrap());
        }
        Ok((0..self.body_count)
            .map(|k| std::array::from_fn(|a| f32::from_bits(status[4 + k * 6 + a]) / steps as f32))
            .collect())
    }
    pub fn snapshot(
        &self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
    ) -> Result<(Vec<f32>, Vec<f32>)> {
        let size = self.positions[0].size();
        let buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("explicit physics checkpoint"),
            size: size * 2,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        let mut enc = device.create_command_encoder(&Default::default());
        enc.copy_buffer_to_buffer(&self.positions[self.current], 0, &buffer, 0, size);
        enc.copy_buffer_to_buffer(&self.velocities[self.current], 0, &buffer, size, size);
        queue.submit(Some(enc.finish()));
        let bytes = read_buffer(device, &buffer)?;
        let floats: &[f32] = bytemuck::cast_slice(&bytes);
        let (x, v) = floats.split_at(self.count * 3);
        Ok((x.to_vec(), v.to_vec()))
    }
}
fn read_buffer(device: &wgpu::Device, buffer: &wgpu::Buffer) -> Result<Vec<u8>> {
    let (tx, rx) = std::sync::mpsc::channel();
    buffer.slice(..).map_async(wgpu::MapMode::Read, move |r| {
        let _ = tx.send(r);
    });
    device.poll(wgpu::PollType::wait_indefinitely())?;
    rx.recv().context("GPU readback channel")??;
    let mapped = buffer.slice(..).get_mapped_range();
    let bytes = mapped.to_vec();
    drop(mapped);
    buffer.unmap();
    Ok(bytes)
}
