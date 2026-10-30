//! Strict numeric cache reader. One reusable frame buffer; no Python or mmap.
use anyhow::{bail, ensure, Context, Result};
use regex::Regex;
use serde::Deserialize;
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::Path,
};

pub struct FrameFile {
    file: File,
    pub shape: Vec<usize>,
    offset: u64,
    bytes_per_frame: usize,
    pub values: Vec<f32>,
}

impl FrameFile {
    pub fn open(path: &Path, tail: usize) -> Result<Self> {
        let mut file = File::open(path).with_context(|| format!("Open {}", path.display()))?;
        let mut magic = [0; 8];
        file.read_exact(&mut magic)?;
        ensure!(&magic[..6] == b"\x93NUMPY", "Not a NumPy cache");
        let (len, prefix) = match (magic[6], magic[7]) {
            (1, 0) => {
                let mut b = [0; 2];
                file.read_exact(&mut b)?;
                (u16::from_le_bytes(b) as usize, 10)
            }
            (2 | 3, 0) => {
                let mut b = [0; 4];
                file.read_exact(&mut b)?;
                (u32::from_le_bytes(b) as usize, 12)
            }
            _ => bail!("Unsupported NumPy version"),
        };
        ensure!((1..=16384).contains(&len), "Unbounded NumPy header");
        let mut header = vec![0; len];
        file.read_exact(&mut header)?;
        let header = std::str::from_utf8(&header)?;
        ensure!(
            Regex::new(r#"['"]descr['"]\s*:\s*['"]<f4['"]"#)?.is_match(header),
            "Only little-endian float32 caches are supported"
        );
        ensure!(
            Regex::new(r#"['"]fortran_order['"]\s*:\s*False"#)?.is_match(header),
            "Only C-order caches are supported"
        );
        let shape_re = Regex::new(r#"['"]shape['"]\s*:\s*\(([0-9, ]+)\)"#)?;
        let capture = shape_re.captures(header).context("Missing numeric shape")?;
        let shape: Vec<usize> = capture[1]
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::parse)
            .collect::<std::result::Result<_, _>>()?;
        ensure!(
            shape.len() == 3
                && shape[0] > 0
                && shape[0] <= 100_000
                && shape[1] > 0
                && shape[1] <= 2_000_000
                && shape[2] == tail,
            "Unsupported bounded cache shape"
        );
        let bytes_per_frame = shape[1]
            .checked_mul(tail)
            .and_then(|n| n.checked_mul(4))
            .context("Cache size overflow")?;
        let offset = (prefix + len) as u64;
        let expected = (bytes_per_frame as u64)
            .checked_mul(shape[0] as u64)
            .and_then(|n| n.checked_add(offset))
            .context("Cache length overflow")?;
        ensure!(
            file.metadata()?.len() == expected,
            "Truncated cache or unexplained trailing bytes"
        );
        Ok(Self {
            file,
            shape,
            offset,
            bytes_per_frame,
            values: vec![0.; bytes_per_frame / 4],
        })
    }

    pub fn read(&mut self, frame: usize) -> Result<()> {
        ensure!(frame < self.shape[0], "Frame out of bounds");
        self.file.seek(SeekFrom::Start(
            self.offset + frame as u64 * self.bytes_per_frame as u64,
        ))?;
        ensure!(
            cfg!(target_endian = "little"),
            "This cache reader requires a little-endian target"
        );
        self.file
            .read_exact(bytemuck::cast_slice_mut(&mut self.values))?;
        ensure!(
            self.values.iter().all(|v| v.is_finite()),
            "Non-finite cache coordinate"
        );
        Ok(())
    }
}

#[derive(Deserialize)]
pub struct Mesh {
    pub vertices: Vec<[f32; 3]>,
    pub faces: Vec<[u32; 3]>,
}
#[derive(Deserialize)]
pub struct Geom {
    pub mesh: usize,
    pub body: String,
}
#[derive(Deserialize)]
pub struct Geometry {
    pub meshes: Vec<Mesh>,
    pub geoms: Vec<Geom>,
}
#[derive(Deserialize)]
pub struct Metadata {
    pub fps: f32,
    pub frames: usize,
    pub grain_count: usize,
    pub grain_radius_m: f32,
    pub foot_radius_m: f32,
}

pub struct Scene {
    pub particles: FrameFile,
    pub poses: FrameFile,
    pub feet: FrameFile,
    pub geometry: Geometry,
    pub meta: Metadata,
}
impl Scene {
    pub fn load(run: &Path, geometry: &Path) -> Result<Self> {
        ensure!(
            std::fs::metadata(run.join("metadata.json"))?.len() <= 8_000_000,
            "Metadata exceeds limit"
        );
        ensure!(
            std::fs::metadata(geometry)?.len() <= 64_000_000,
            "Geometry exceeds limit"
        );
        let meta: Metadata = serde_json::from_reader(File::open(run.join("metadata.json"))?)?;
        ensure!(
            meta.fps.is_finite() && meta.fps > 0. && meta.fps <= 240.,
            "Invalid frame rate"
        );
        ensure!(
            meta.grain_radius_m.is_finite() && (0.0001..=0.1).contains(&meta.grain_radius_m),
            "Invalid grain radius"
        );
        ensure!(
            meta.foot_radius_m.is_finite() && (0.001..=0.5).contains(&meta.foot_radius_m),
            "Invalid foot radius"
        );
        let geometry: Geometry = serde_json::from_reader(File::open(geometry)?)?;
        ensure!(
            !geometry.meshes.is_empty()
                && geometry.meshes.len() <= 1024
                && geometry.geoms.len() <= 100_000,
            "Geometry limits exceeded"
        );
        for mesh in &geometry.meshes {
            ensure!(
                !mesh.vertices.is_empty()
                    && mesh.vertices.len() <= 1_000_000
                    && mesh.faces.len() <= 2_000_000,
                "Mesh limits exceeded"
            );
            ensure!(
                mesh.vertices.iter().flatten().all(|v| v.is_finite()),
                "Non-finite mesh vertex"
            );
            ensure!(
                mesh.faces
                    .iter()
                    .flatten()
                    .all(|i| (*i as usize) < mesh.vertices.len()),
                "Mesh index out of bounds"
            );
        }
        ensure!(
            geometry
                .geoms
                .iter()
                .all(|g| g.mesh < geometry.meshes.len()),
            "Invalid mesh reference"
        );
        let particles = FrameFile::open(&run.join("particles.npy"), 3)?;
        let poses = FrameFile::open(&run.join("robot_poses.npy"), 12)?;
        let feet = FrameFile::open(&run.join("foot_centers.npy"), 3)?;
        ensure!(
            particles.shape == [meta.frames, meta.grain_count, 3]
                && poses.shape == [meta.frames, geometry.geoms.len(), 12]
                && feet.shape == [meta.frames, 4, 3],
            "Cache/metadata/geometry mismatch"
        );
        Ok(Self {
            particles,
            poses,
            feet,
            geometry,
            meta,
        })
    }
    pub fn read(&mut self, frame: usize) -> Result<()> {
        self.particles.read(frame)?;
        self.poses.read(frame)?;
        self.feet.read(frame)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    static NEXT: AtomicUsize = AtomicUsize::new(0);
    struct Fixture(std::path::PathBuf);
    impl Fixture {
        fn new(data: &[u8]) -> Self {
            let path = std::env::temp_dir().join(format!(
                "scenward-cache-test-{}-{}.npy",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::write(&path, data).unwrap();
            Self(path)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    fn array(header: &str, values: &[f32]) -> Vec<u8> {
        let mut data = b"\x93NUMPY\x01\x00".to_vec();
        data.extend((header.len() as u16).to_le_bytes());
        data.extend(header.as_bytes());
        for value in values {
            data.extend(value.to_le_bytes());
        }
        data
    }
    const HEADER: &str = "{'descr': '<f4', 'fortran_order': False, 'shape': (2, 1, 3), }\n";
    #[test]
    fn reads_exact_frames_and_reuses_storage() {
        let f = Fixture::new(&array(HEADER, &[1., 2., 3., 4., 5., 6.]));
        let mut a = FrameFile::open(&f.0, 3).unwrap();
        let pointer = a.values.as_ptr();
        a.read(1).unwrap();
        assert_eq!(a.values, [4., 5., 6.]);
        a.read(0).unwrap();
        assert_eq!(a.values, [1., 2., 3.]);
        assert_eq!(pointer, a.values.as_ptr());
        assert!(a.read(2).is_err());
    }
    #[test]
    fn rejects_truncated_and_trailing_payloads() {
        for n in [5, 7] {
            let f = Fixture::new(&array(HEADER, &vec![0.; n]));
            assert!(FrameFile::open(&f.0, 3).is_err());
        }
    }
    #[test]
    fn rejects_object_dtype_fortran_and_oversized_shapes() {
        for header in [
            HEADER.replace("<f4", "|O"),
            HEADER.replace("False", "True"),
            HEADER.replace("2, 1, 3", "2, 999999999999999999999999, 3"),
        ] {
            let f = Fixture::new(&array(&header, &[0.; 6]));
            assert!(FrameFile::open(&f.0, 3).is_err());
        }
    }
    #[test]
    fn rejects_nonfinite_before_gpu_upload() {
        for value in [f32::NAN, f32::INFINITY, f32::NEG_INFINITY] {
            let f = Fixture::new(&array(HEADER, &[value, 0., 0., 0., 0., 0.]));
            let mut a = FrameFile::open(&f.0, 3).unwrap();
            assert!(a.read(0).is_err());
        }
    }
    #[test]
    fn rejects_oversized_headers_and_arbitrary_input() {
        let mut data = b"\x93NUMPY\x01\x00".to_vec();
        data.extend(65535u16.to_le_bytes());
        let f = Fixture::new(&data);
        assert!(FrameFile::open(&f.0, 3).is_err());
        for seed in 0..32 {
            let f = Fixture::new(&[seed; 128]);
            assert!(FrameFile::open(&f.0, 3).is_err());
        }
    }
}
