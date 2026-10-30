//! Small O(N²) CPU oracle for GPU regression checks, never the production solver.
pub fn step(x: &mut [f32], v: &mut [f32]) {
    let old_x = x.to_vec();
    let old_v = v.to_vec();
    let radius = 0.0015f32;
    let mass = 2500. * 4. / 3. * std::f32::consts::PI * radius.powi(3);
    let dt = 0.00005;
    let contact = |normal: glam::Vec3, overlap: f32, velocity: glam::Vec3| {
        let vn = velocity.dot(normal);
        let magnitude = (500. * overlap - 0.0625 * vn).max(0.);
        let tangent = velocity - vn * normal;
        magnitude * normal - (0.0625f32).min(0.65 * magnitude / (tangent.length() + 1e-8)) * tangent
    };
    for i in 0..x.len() / 3 {
        let p = glam::Vec3::from_slice(&old_x[3 * i..3 * i + 3]);
        let vel = glam::Vec3::from_slice(&old_v[3 * i..3 * i + 3]);
        let mut f = glam::Vec3::new(0., 0., -9.81 * mass);
        for j in 0..x.len() / 3 {
            if i == j {
                continue;
            }
            let delta = p - glam::Vec3::from_slice(&old_x[3 * j..3 * j + 3]);
            let distance = delta.length();
            if distance > 1e-8 && distance < 2. * radius {
                f += contact(
                    delta / distance,
                    2. * radius - distance,
                    vel - glam::Vec3::from_slice(&old_v[3 * j..3 * j + 3]),
                );
            }
        }
        for a in 0..3 {
            let lo = [-0.8, -0.34, 0.][a];
            let hi = [0.8, 0.34, 0.2][a];
            let mut n = glam::Vec3::ZERO;
            n[a] = 1.;
            if lo + radius - p[a] > 0. {
                f += contact(n, lo + radius - p[a], vel);
            }
            if p[a] + radius - hi > 0. {
                f += contact(-n, p[a] + radius - hi, vel);
            }
        }
        let nv = vel + dt * f / mass;
        let np = p + dt * nv;
        x[3 * i..3 * i + 3].copy_from_slice(&np.to_array());
        v[3 * i..3 * i + 3].copy_from_slice(&nv.to_array());
    }
}
