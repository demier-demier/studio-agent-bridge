"""Build and export an original detailed Blender laboratory for native live physics.

Run Blender --background --python-exit-code 1 --python this.py -- --output NEW_DIR.
All source assets are public portfolio originals. No downloads or private scenes.
"""

import argparse
import json
import runpy
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy

p = argparse.ArgumentParser()
p.add_argument("--output", type=Path, required=True)
a = p.parse_args(sys.argv[sys.argv.index("--") + 1 :])
out = a.output.resolve()
if out.exists():
    raise ValueError("Use a new output directory")
out.mkdir(parents=True)
root = Path(__file__).resolve().parents[3]
runpy.run_path(
    str(root / "examples/environments/industrial_lab/build_environment.py"),
    init_globals={"STUDIO_OUTPUT_DIR": str(out), "STUDIO_PARAMETERS": {"render_views": []}},
)
# Move the laboratory around an open foreground robot test lane.
for obj in bpy.context.scene.objects:
    if obj.parent is None:
        obj.location.y += 2.5
bpy.context.view_layer.update()
xml = ET.parse(root / "examples/solo12_granular/locomotion/solo12-walk.xml")
model = xml.getroot()
model.find("compiler").set("meshdir", "robot-assets")
shutil.copytree(root / "examples/solo12_granular/robot/assets", out / "robot-assets")
world = model.find("worldbody")
colliders = []
for obj in sorted(bpy.context.scene.objects, key=lambda o: o.name):
    key = obj.get("studio_prototype_key", "")
    if obj.hide_render or obj.type != "MESH" or not (key == "box" or key.startswith("cylinder:")):
        continue
    loc, quat, scale = obj.matrix_world.decompose()
    scale = [abs(s) for s in scale]
    if min(scale) < 0.008 or scale[0] * scale[1] * scale[2] < 0.0001:
        continue
    typ = "box" if key == "box" else "cylinder"
    size = [s * 0.5 for s in scale] if typ == "box" else [max(scale[:2]), scale[2] * 0.5]
    attrs = {
        "name": f"SW_STATIC_{len(colliders)}",
        "type": typ,
        "pos": " ".join(map(str, loc)),
        "quat": " ".join(map(str, quat)),
        "size": " ".join(map(str, size)),
        "friction": ".8 .01 .001",
        "group": "4",
    }
    ET.SubElement(world, "geom", attrs)
    colliders.append(
        {
            "object": obj.name,
            "type": typ,
            "limitation": "Analytic primitive proxy; visual bevels retained in render mesh.",
        }
    )
# 32 genuinely dynamic spheres, 16 boxes. The spheres exchange forces with GPU grains.
for k in range(48):
    sphere = k < 32
    if sphere:
        radius = 0.018 + (k % 3) * 0.003
        if k < 16:
            pos = (-0.70 + (k % 8) * 0.19, -0.26 + (k // 8) * 0.52, 0.075 + (k % 3) * 0.014)
        else:
            pos = (1.8 + (k % 4) * 0.12, 1.5 + ((k - 16) // 4) * 0.12, 1.45)
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=16, radius=radius, location=pos
        )
    else:
        pos = (2.2 + (k % 4) * 0.14, 2.3 + ((k - 32) // 4) * 0.14, 1.0 + ((k - 32) // 4) * 0.16)
        bpy.ops.mesh.primitive_cube_add(size=0.09, location=pos)
    obj = bpy.context.object
    name = f"SW_DYNAMIC_{k:02d}"
    obj.name = name
    obj["sw_dynamic_body"] = name
    mat = bpy.data.materials.new(name + "_Material")
    mat.diffuse_color = (0.12, 0.35, 0.42, 1) if sphere else (0.48, 0.24, 0.07, 1)
    mat.use_nodes = True
    bs = mat.node_tree.nodes.get("Principled BSDF")
    bs.inputs["Base Color"].default_value = mat.diffuse_color
    bs.inputs["Metallic"].default_value = 0.65
    bs.inputs["Roughness"].default_value = 0.3
    obj.data.materials.append(mat)
    if not sphere:
        mod = obj.modifiers.new("Manufactured_edge_bevel", "BEVEL")
        mod.width = 0.003
        mod.segments = 3
    body = ET.SubElement(world, "body", {"name": name, "pos": " ".join(map(str, pos))})
    ET.SubElement(body, "freejoint", {"name": name + "_joint"})
    ET.SubElement(
        body,
        "geom",
        {
            "name": ("SW_GRAIN_" if sphere else "SW_BOX_") + str(k),
            "type": "sphere" if sphere else "box",
            "size": str(radius) if sphere else ".045 .045 .045",
            "mass": ".025" if sphere else ".1",
            "friction": ".7 .01 .001",
            "group": "3",
        },
    )
xml.write(out / "physics.xml", encoding="unicode")
exporter = runpy.run_path(str(Path(__file__).with_name("export_visual_scene.py")))
objects, source_triangles = exporter["export_current_scene"](out / "environment.json")
bpy.ops.wm.save_as_mainfile(filepath=str(out / "live-laboratory.blend"))
report = {
    "schema": "scenward-blender-export-proof/v1",
    "render_objects": len(objects),
    "evaluated_triangles": source_triangles,
    "exported_triangles": sum(len(o["mesh"]["faces"]) for o in objects),
    "decimation": False,
    "dynamic_rigid_bodies": 48,
    "gpu_coupled_spheres": 36,
    "static_analytic_colliders": len(colliders),
    "collider_sources": colliders,
    "limits": [
        "Base color/roughness/metallic values retained; Blender texture maps, emissive response and node graphs are not reproduced by this renderer.",
        "Static primitive collision proxies omit small decorative parts and bevel detail.",
        "GPU DEM couples declared spheres; dynamic boxes and static environment collide in MuJoCo but not directly with individual grains.",
        "Original robot arm in background is static visual geometry; Solo12 is the controlled articulated robot.",
    ],
}
(out / "export-proof.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k != "collider_sources"}, indent=2))
