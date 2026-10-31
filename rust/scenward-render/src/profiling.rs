//! Bounded, opt-in diagnostics. Timers observe the solver; they never change its steps.
use anyhow::{ensure, Result};
use serde::Serialize;

pub const MAX_STEPS: u32 = 256;
pub const QUERIES_PER_STEP: u32 = 4;
pub const MAX_QUERIES: u32 = MAX_STEPS * QUERIES_PER_STEP;

#[derive(Default, Clone, Serialize)]
pub struct Timing {
    pub samples: u64,
    pub total_seconds: f64,
    pub max_seconds: f64,
}
impl Timing {
    pub fn add(&mut self, seconds: f64) {
        self.samples += 1;
        self.total_seconds += seconds;
        self.max_seconds = self.max_seconds.max(seconds);
    }
}

#[derive(Default, Clone, Serialize)]
pub struct PhysicsTimings {
    pub completed_batches: u64,
    pub completed_steps: u64,
    pub cpu_encode: Timing,
    /// Includes submission, GPU completion, mapping and host scheduling, not just transfer.
    pub submit_to_readback: Timing,
    pub gpu_prepare_pass: Timing,
    pub gpu_solver_pass: Timing,
    pub rejected_timestamp_pairs: u64,
}
#[derive(Default, Serialize)]
pub struct LiveTimings {
    pub collect_bodies: Timing,
    pub native_robot_step: Timing,
    pub upload_poses: Timing,
    pub completed_render: Timing,
}
impl PhysicsTimings {
    pub fn record_gpu(&mut self, bytes: &[u8], steps: u32, period_ns: f32) -> Result<()> {
        ensure!(
            (1..=MAX_STEPS).contains(&steps),
            "Invalid timestamp batch size"
        );
        ensure!(
            period_ns.is_finite() && period_ns > 0.,
            "Invalid GPU timestamp period"
        );
        ensure!(
            bytes.len() == steps as usize * QUERIES_PER_STEP as usize * 8,
            "Timestamp payload length mismatch"
        );
        for (i, pair) in bytes.as_chunks::<16>().0.iter().enumerate() {
            // Decode bytes without depending on the host allocation's alignment.
            let begin = u64::from_ne_bytes(pair[..8].try_into().unwrap());
            let end = u64::from_ne_bytes(pair[8..].try_into().unwrap());
            if let Some(ticks) = end.checked_sub(begin) {
                let seconds = ticks as f64 * f64::from(period_ns) * 1e-9;
                if i % 2 == 0 {
                    self.gpu_prepare_pass.add(seconds);
                } else {
                    self.gpu_solver_pass.add(seconds);
                }
            } else {
                // Counter wrap/reordering must not turn into a huge speed claim.
                self.rejected_timestamp_pairs += 1;
            }
        }
        self.completed_batches += 1;
        self.completed_steps += u64::from(steps);
        Ok(())
    }
}

pub struct GpuProfiler {
    queries: wgpu::QuerySet,
    resolve: wgpu::Buffer,
    pub timings: PhysicsTimings,
}
impl GpuProfiler {
    pub fn new(device: &wgpu::Device) -> Self {
        let queries = device.create_query_set(&wgpu::QuerySetDescriptor {
            label: Some("DEM prepare and solver pass timestamps"),
            ty: wgpu::QueryType::Timestamp,
            count: MAX_QUERIES,
        });
        let resolve = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("bounded DEM timestamp resolve"),
            size: u64::from(MAX_QUERIES) * 8,
            usage: wgpu::BufferUsages::QUERY_RESOLVE | wgpu::BufferUsages::COPY_SRC,
            mapped_at_creation: false,
        });
        Self {
            queries,
            resolve,
            timings: PhysicsTimings::default(),
        }
    }
    pub fn pass(&self, step: u32, solver: bool) -> wgpu::ComputePassTimestampWrites<'_> {
        let first = step * QUERIES_PER_STEP + if solver { 2 } else { 0 };
        wgpu::ComputePassTimestampWrites {
            query_set: &self.queries,
            beginning_of_pass_write_index: Some(first),
            end_of_pass_write_index: Some(first + 1),
        }
    }
    pub fn resolve(
        &self,
        encoder: &mut wgpu::CommandEncoder,
        readback: &wgpu::Buffer,
        offset: u64,
        steps: u32,
    ) {
        let count = steps * QUERIES_PER_STEP;
        encoder.resolve_query_set(&self.queries, 0..count, &self.resolve, 0);
        encoder.copy_buffer_to_buffer(&self.resolve, 0, readback, offset, u64::from(count) * 8);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn bytes(values: &[u64]) -> Vec<u8> {
        values.iter().flat_map(|v| v.to_ne_bytes()).collect()
    }
    #[test]
    fn periods_stages_and_multiple_batches_are_not_conflated() {
        let mut t = PhysicsTimings::default();
        t.record_gpu(&bytes(&[100, 110, 120, 150, 200, 220, 230, 270]), 2, 2.)
            .unwrap();
        t.record_gpu(&bytes(&[10, 20, 30, 80]), 1, 2.).unwrap();
        assert_eq!((t.completed_batches, t.completed_steps), (2, 3));
        assert_eq!(t.gpu_prepare_pass.samples, 3);
        assert!((t.gpu_prepare_pass.total_seconds - 80e-9).abs() < 1e-20);
        assert!((t.gpu_solver_pass.total_seconds - 240e-9).abs() < 1e-20);
        assert!((t.gpu_solver_pass.max_seconds - 100e-9).abs() < 1e-20);
    }
    #[test]
    fn wrapped_pairs_are_rejected_not_subtracted_as_unsigned() {
        let mut t = PhysicsTimings::default();
        t.record_gpu(&bytes(&[u64::MAX, 0, 1, 1]), 1, 1.).unwrap();
        assert_eq!(t.rejected_timestamp_pairs, 1);
        assert_eq!(t.gpu_prepare_pass.samples, 0);
        assert_eq!(t.gpu_solver_pass.samples, 1);
        assert_eq!(t.gpu_solver_pass.total_seconds, 0.);
    }
    #[test]
    fn invalid_payloads_and_periods_do_not_mutate_stats() {
        for (data, steps, period) in [
            (vec![], 0, 1.),
            (vec![], 257, 1.),
            (vec![0; 31], 1, 1.),
            (vec![0; 40], 1, 1.),
            (vec![0; 32], 1, f32::NAN),
            (vec![0; 32], 1, f32::INFINITY),
            (vec![0; 32], 1, 0.),
        ] {
            let mut t = PhysicsTimings::default();
            assert!(t.record_gpu(&data, steps, period).is_err());
            assert_eq!(t.completed_batches, 0);
            assert_eq!(t.gpu_prepare_pass.samples, 0);
        }
    }
    #[test]
    fn maximum_batch_is_bounded_and_accepted() {
        let mut t = PhysicsTimings::default();
        t.record_gpu(&vec![0; MAX_QUERIES as usize * 8], MAX_STEPS, 1.)
            .unwrap();
        assert_eq!(t.gpu_prepare_pass.samples, 256);
        assert_eq!(t.gpu_solver_pass.samples, 256);
    }
}
