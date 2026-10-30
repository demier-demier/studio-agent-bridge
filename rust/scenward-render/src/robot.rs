//! Safe owner around a narrow MuJoCo 3.3.7 C bridge and a Rust torque controller.
use crate::physics::SphereBody;
use anyhow::{bail, ensure, Result};
use std::{
    ffi::{c_char, c_void, CStr, CString},
    path::Path,
};
unsafe extern "C" {
    fn sw_robot_open(path: *const c_char, error: *mut c_char, len: i32) -> *mut c_void;
    fn sw_robot_close(robot: *mut c_void);
    fn sw_robot_count(robot: *mut c_void) -> i32;
    fn sw_robot_visual_count(robot: *mut c_void) -> i32;
    fn sw_robot_visual(robot: *mut c_void, out: *mut f32);
    fn sw_robot_spheres(robot: *mut c_void, out: *mut f32);
    fn sw_robot_body_pose(robot: *mut c_void, name: *const c_char, out: *mut f32) -> i32;
    fn sw_robot_state(robot: *mut c_void, out: *mut f64);
    fn sw_robot_jacobian(robot: *mut c_void, foot: i32, out: *mut f64);
    fn sw_robot_step(robot: *mut c_void, ctrl: *const f64, reactions: *const f32) -> i32;
    fn sw_robot_push(robot: *mut c_void);
}
pub struct Robot {
    raw: *mut c_void,
    pub count: usize,
    visual_count: usize,
    controller: Controller,
}
impl Drop for Robot {
    fn drop(&mut self) {
        unsafe { sw_robot_close(self.raw) }
    }
}
impl Robot {
    pub fn open(path: &Path) -> Result<Self> {
        let path = CString::new(
            path.to_str()
                .ok_or_else(|| anyhow::anyhow!("UTF-8 model path required"))?,
        )?;
        let mut error = [0i8; 1024];
        let raw = unsafe { sw_robot_open(path.as_ptr(), error.as_mut_ptr(), 1024) };
        if raw.is_null() {
            bail!(
                "Native model: {}",
                unsafe { CStr::from_ptr(error.as_ptr()) }.to_string_lossy()
            );
        }
        let count = unsafe { sw_robot_count(raw) } as usize;
        let visual_count = unsafe { sw_robot_visual_count(raw) } as usize;
        Ok(Self {
            raw,
            count,
            visual_count,
            controller: Controller::new(),
        })
    }
    pub fn visual(&self) -> Vec<f32> {
        let mut values = vec![0.; self.visual_count * 12];
        unsafe { sw_robot_visual(self.raw, values.as_mut_ptr()) };
        values
    }
    pub fn spheres(&self) -> Vec<SphereBody> {
        let mut values = vec![SphereBody::default(); self.count];
        unsafe { sw_robot_spheres(self.raw, values.as_mut_ptr().cast()) };
        values
    }
    pub fn body_pose(&self, name: &str) -> Result<[f32; 12]> {
        let name = CString::new(name)?;
        let mut p = [0.; 12];
        ensure!(
            unsafe { sw_robot_body_pose(self.raw, name.as_ptr(), p.as_mut_ptr()) } == 1,
            "Imported dynamic body not found"
        );
        Ok(p)
    }
    pub fn state(&self) -> [f64; 48] {
        let mut state = [0.; 48];
        unsafe { sw_robot_state(self.raw, state.as_mut_ptr()) };
        state
    }
    pub fn push(&mut self) {
        unsafe { sw_robot_push(self.raw) }
    }
    pub fn step(&mut self, reactions: &[[f32; 6]]) -> Result<()> {
        ensure!(
            reactions.len() == self.count && reactions.iter().flatten().all(|v| v.is_finite()),
            "Invalid body reactions"
        );
        let state = self.state();
        let spheres = self.spheres();
        let targets = self.controller.targets(state[38]);
        let mut ctrl = [0.; 12];
        for k in 0..4 {
            let relative = std::array::from_fn(|j| targets[k][j] - self.controller.base[j]);
            ctrl[k * 3..k * 3 + 3].copy_from_slice(&ik(relative, k));
        }
        // Same support wrench feedback as the public crawl, with a full-rank 6x6 solve.
        let active = self.controller.supports(state[38]);
        let mut a = [[0.; 12]; 6];
        for (slot, &k) in active.iter().enumerate() {
            let r: [f64; 3] = std::array::from_fn(|j| spheres[k].center[j] as f64 - state[j]);
            for j in 0..3 {
                a[j][slot * 3 + j] = 1.;
            }
            let cross = [[0., -r[2], r[1]], [r[2], 0., -r[0]], [-r[1], r[0], 0.]];
            for i in 0..3 {
                for j in 0..3 {
                    a[3 + i][slot * 3 + j] = cross[i][j];
                }
            }
        }
        let mut wrench = [0.; 6];
        for j in 0..3 {
            let vt = if j == 0 && state[38] >= 1. { 0.08 } else { 0. };
            wrench[j] = state[37]
                * ((if j == 2 { 9.81 } else { 0. })
                    + [12., 12., 30.][j] * (self.controller.base[j] - state[j])
                    - [3., 3., 5.][j] * (state[19 + j] - vt));
        }
        wrench[3] = -1.5 * state[46] - 0.1 * state[22];
        wrench[4] = 1.5 * state[45] - 0.1 * state[23];
        wrench[5] = -1.2 * state[42].atan2(state[39]) - 0.1 * state[24];
        let mut aa = [[0.; 6]; 6];
        for i in 0..6 {
            for j in 0..6 {
                aa[i][j] = (0..3 * active.len()).map(|k| a[i][k] * a[j][k]).sum();
            }
        }
        let dual = solve6(aa, wrench)
            .ok_or_else(|| anyhow::anyhow!("Degenerate robot support geometry"))?;
        let mut motor: [f64; 12] = std::array::from_fn(|j| state[25 + j]);
        for (slot, &k) in active.iter().enumerate() {
            let mut f: [f64; 3] =
                std::array::from_fn(|j| (0..6).map(|i| a[i][slot * 3 + j] * dual[i]).sum());
            f[2] = f[2].max(0.);
            for j in 0..2 {
                f[j] = f[j].clamp(-0.7 * f[2], 0.7 * f[2]);
            }
            let mut jac = [0.; 36];
            unsafe { sw_robot_jacobian(self.raw, k as i32, jac.as_mut_ptr()) };
            for j in 0..12 {
                for i in 0..3 {
                    motor[j] -= jac[i * 12 + j] * f[i];
                }
            }
        }
        for j in 0..12 {
            ctrl[j] += motor[j].clamp(-1.5, 1.5) / 16.;
        }
        ensure!(ctrl.iter().all(|x| x.is_finite()), "Nonfinite controller");
        ensure!(
            unsafe { sw_robot_step(self.raw, ctrl.as_ptr(), reactions.as_ptr().cast()) } == 1,
            "Nonfinite robot dynamics"
        );
        Ok(())
    }
}
fn smooth(u: f64) -> f64 {
    let u = u.clamp(0., 1.);
    u * u * u * (10. + u * (-15. + u * 6.))
}
fn ik(relative: [f64; 3], leg: usize) -> [f64; 3] {
    let front = leg < 2;
    let side = if leg.is_multiple_of(2) { 1. } else { -1. };
    let mut x = relative[0] - (if front { 0.2141 } else { -0.2141 });
    let y = relative[1] - side * 0.0875;
    let z = relative[2];
    let offset = side * 0.06045;
    let planar_z = -(y * y + z * z - offset * offset).max(1e-8).sqrt();
    let haa = (z.atan2(y) - planar_z.atan2(offset) + std::f64::consts::PI)
        .rem_euclid(2. * std::f64::consts::PI)
        - std::f64::consts::PI;
    x -= if front { -0.0195 } else { 0.0195 };
    let cosk = ((x * x + planar_z * planar_z - 2. * 0.16f64.powi(2)) / (2. * 0.16f64.powi(2)))
        .clamp(-0.9999, 0.9999);
    let knee = cosk.acos() * (if front { -1. } else { 1. });
    let hip = (-x).atan2(-planar_z) - (0.16 * knee.sin()).atan2(0.16 + 0.16 * knee.cos());
    [haa, hip, knee]
}
struct Controller {
    feet: [[f64; 3]; 4],
    nominal: [[f64; 3]; 4],
    origin: [f64; 3],
    target: [f64; 3],
    phase: i64,
    base: [f64; 3],
}
impl Controller {
    fn new() -> Self {
        let g = 0.0015 * 2.035 * 9. * 0.94;
        let nominal = [
            [0.1946 - 0.36, 0.14795, g + 0.02],
            [0.1946 - 0.36, -0.14795, g + 0.02],
            [-0.1946 - 0.36, 0.14795, g + 0.02],
            [-0.1946 - 0.36, -0.14795, g + 0.02],
        ];
        Self {
            feet: nominal,
            nominal,
            origin: [0.; 3],
            target: [0.; 3],
            phase: -1,
            base: [-0.36, 0., g + 0.235],
        }
    }
    fn supports(&self, t: f64) -> Vec<usize> {
        let elapsed = (t - 1.).max(0.);
        let phase = (elapsed / 0.6) as usize;
        let u = (elapsed / 0.6) % 1.;
        let leg = [0, 3, 1, 2][phase % 4];
        (0..4)
            .filter(|k| !(t >= 1. && u > 0.28 && u < 0.88 && *k == leg))
            .collect()
    }
    #[allow(clippy::needless_range_loop)] // Fixed XYZ coordinates mirror the controller equations.
    fn targets(&mut self, t: f64) -> [[f64; 3]; 4] {
        let g = 0.0015 * 2.035 * 9. * 0.94;
        let elapsed = (t - 1.).max(0.);
        let phase = (elapsed / 0.6) as i64;
        let u = (elapsed / 0.6) % 1.;
        let order = [0, 3, 1, 2];
        let k = order[(phase % 4) as usize];
        let prev = order[((phase + 3) % 4) as usize];
        self.base = [-0.36 + 0.08 * elapsed, 0., g + 0.235];
        if t < 1. {
            return self.nominal;
        }
        if phase != self.phase {
            if self.phase >= 0 {
                self.feet[order[(self.phase % 4) as usize]] = self.target;
            }
            self.origin = self.feet[k];
            self.target = self.nominal[k];
            self.target[0] += 0.08 * (elapsed + 0.6 * 1.65);
            self.phase = phase;
        }
        let offset = |leg: usize| if leg.is_multiple_of(2) { -0.008 } else { 0.008 };
        self.base[1] = offset(prev) * (1. - smooth(u / 0.25)) + offset(k) * smooth(u / 0.25);
        let mut targets = self.feet;
        let swing = ((u - 0.28) / 0.60).clamp(0., 1.);
        for j in 0..3 {
            targets[k][j] = self.origin[j] + (self.target[j] - self.origin[j]) * smooth(swing);
        }
        targets[k][2] += 0.04 * (std::f64::consts::PI * swing).sin().powi(2);
        targets
    }
}
#[allow(clippy::needless_range_loop)] // Pivoted fixed-size matrix elimination.
fn solve6(mut a: [[f64; 6]; 6], mut b: [f64; 6]) -> Option<[f64; 6]> {
    for k in 0..6 {
        let pivot = (k..6).max_by(|&i, &j| a[i][k].abs().total_cmp(&a[j][k].abs()))?;
        if a[pivot][k].abs() < 1e-12 {
            return None;
        }
        a.swap(k, pivot);
        b.swap(k, pivot);
        let div = a[k][k];
        for j in k..6 {
            a[k][j] /= div;
        }
        b[k] /= div;
        for i in 0..6 {
            if i != k {
                let f = a[i][k];
                for j in k..6 {
                    a[i][j] -= f * a[k][j];
                }
                b[i] -= f * b[k];
            }
        }
    }
    Some(b)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn linear_solver() {
        let mut a = [[0.; 6]; 6];
        for (i, row) in a.iter_mut().enumerate() {
            row[i] = 2.;
        }
        assert_eq!(solve6(a, [2.; 6]), Some([1.; 6]));
        assert!(solve6([[0.; 6]; 6], [1.; 6]).is_none());
    }
    #[test]
    fn symmetric_ik() {
        let a = ik([0.1946, 0.14795, -0.215], 0);
        let b = ik([0.1946, -0.14795, -0.215], 1);
        for j in 0..3 {
            assert!((a[j] - b[j]).abs() < 1e-12);
        }
    }
}
