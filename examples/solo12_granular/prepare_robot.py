"""Check vendored Solo12 assets and optionally export fresh render geometry.

No upstream control code is imported or executed. The bundled MJCF is the
explicitly modified model used in the demonstration, not a fresh URDF conversion.
"""

import argparse
import hashlib
import json
from pathlib import Path

import mujoco
from locomotion.controller import initialize

ROOT = Path(__file__).resolve().parent


def export_geometry(model, data):
    meshes = []
    for index in range(model.nmesh):
        vertex_start = model.mesh_vertadr[index]
        face_start = model.mesh_faceadr[index]
        vertices = model.mesh_vert[vertex_start : vertex_start + model.mesh_vertnum[index]]
        faces = model.mesh_face[face_start : face_start + model.mesh_facenum[index]]
        meshes.append({"vertices": vertices.tolist(), "faces": faces.tolist()})
    geoms = []
    for index in range(model.ngeom):
        if model.geom_type[index] == mujoco.mjtGeom.mjGEOM_MESH and model.geom_group[index] == 1:
            geoms.append(
                {
                    "mesh": int(model.geom_dataid[index]),
                    "pos": data.geom_xpos[index].tolist(),
                    "rot": data.geom_xmat[index].tolist(),
                    "body": model.body(model.geom_bodyid[index]).name,
                }
            )
    return {"meshes": meshes, "geoms": geoms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write render geometry to a new JSON file.")
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("Output already exists; choose a new geometry file.")
    source = json.loads((ROOT / "robot/PROVENANCE.json").read_text())
    for name, expected in source["mesh_sha256"].items():
        actual = hashlib.sha256((ROOT / "robot/assets" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Bundled mesh digest mismatch: {name}")
    model = mujoco.MjModel.from_xml_path(str(ROOT / "locomotion/solo12-walk.xml"))
    data = mujoco.MjData(model)
    initialize(model, data)
    geometry = export_geometry(model, data)
    if model.nu != 12 or model.nq != 19 or model.nv != 18 or len(geometry["geoms"]) != 13:
        raise ValueError("Unexpected robot model dimensions")
    if args.output:
        args.output.write_text(json.dumps(geometry, separators=(",", ":")) + "\n")
    print(
        json.dumps(
            {
                "checked_meshes": len(source["mesh_sha256"]),
                "actuators": model.nu,
                "visual_mesh_instances": len(geometry["geoms"]),
                "model_mass_kg_including_experimental_pads": float(model.body_mass.sum()),
                "geometry_exported": bool(args.output),
                "source": source["upstream"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
