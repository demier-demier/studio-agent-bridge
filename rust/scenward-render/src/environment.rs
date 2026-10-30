//! Explicit Blender evaluated-mesh interchange; no script execution at import.
use crate::cache::Mesh;
use anyhow::{ensure, Result};
use serde::Deserialize;
use std::{fs::File, path::Path};
#[derive(Deserialize)]
pub struct Object {
    pub name: String,
    pub mesh: Mesh,
    pub color: [f32; 4],
    pub roughness: f32,
    pub metallic: f32,
    pub dynamic_body: Option<String>,
}
#[derive(Deserialize)]
pub struct Environment {
    pub schema: String,
    pub objects: Vec<Object>,
}
impl Environment {
    pub fn load(path: &Path) -> Result<Self> {
        ensure!(
            std::fs::metadata(path)?.len() <= 100_000_000,
            "Environment exceeds 100 MB"
        );
        let data: Self = serde_json::from_reader(File::open(path)?)?;
        ensure!(
            data.schema == "scenward-blender-environment/v1",
            "Unknown environment schema"
        );
        ensure!(
            !data.objects.is_empty() && data.objects.len() <= 10000,
            "Object count outside bounds"
        );
        let mut vertices = 0;
        let mut faces = 0;
        for o in &data.objects {
            vertices += o.mesh.vertices.len();
            faces += o.mesh.faces.len();
            ensure!(
                vertices <= 3_000_000 && faces <= 2_000_000,
                "Mesh budget exceeded"
            );
            ensure!(
                !o.mesh.vertices.is_empty()
                    && o.mesh.vertices.iter().flatten().all(|v| v.is_finite()),
                "Invalid mesh vertices"
            );
            ensure!(
                o.mesh
                    .faces
                    .iter()
                    .flatten()
                    .all(|i| (*i as usize) < o.mesh.vertices.len()),
                "Invalid triangle index"
            );
            ensure!(
                o.color.iter().all(|v| v.is_finite())
                    && (0.04..=1.).contains(&o.roughness)
                    && (0.0..=1.).contains(&o.metallic),
                "Invalid material"
            );
            ensure!(
                o.name.len() <= 512
                    && o.dynamic_body
                        .as_ref()
                        .is_none_or(|n| n.len() <= 256 && !n.contains('\0')),
                "Invalid object label"
            );
        }
        Ok(data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn read(value: serde_json::Value) -> Result<Environment> {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!(
            "scenward-environment-{}-{}.json",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        std::fs::write(&p, serde_json::to_vec(&value).unwrap()).unwrap();
        let result = Environment::load(&p);
        std::fs::remove_file(p).unwrap();
        result
    }
    fn scene() -> serde_json::Value {
        serde_json::json!({"schema":"scenward-blender-environment/v1","objects":[{"name":"fixture","mesh":{"vertices":[[0,0,0],[1,0,0],[0,1,0]],"faces":[[0,1,2]]},"color":[0.2,0.3,0.4,1],"roughness":0.5,"metallic":0.2,"dynamic_body":null}]})
    }
    #[test]
    fn retains_triangle_and_material() {
        let s = read(scene()).unwrap();
        assert_eq!(s.objects[0].mesh.faces, vec![[0, 1, 2]]);
        assert_eq!(s.objects[0].roughness, 0.5);
    }
    #[test]
    fn rejects_invalid_indices_and_material() {
        let mut s = scene();
        s["objects"][0]["mesh"]["faces"][0][2] = 99.into();
        assert!(read(s).is_err());
        let mut s = scene();
        s["objects"][0]["roughness"] = (-1).into();
        assert!(read(s).is_err());
    }
    #[test]
    fn rejects_unsupported_schema() {
        let mut s = scene();
        s["schema"] = "other".into();
        assert!(read(s).is_err());
    }
}
