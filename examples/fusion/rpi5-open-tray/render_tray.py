"""Render the actual Fusion-exported STL in Blender; no reconstructed substitute.

Studio run_script parameters: stl_path (workspace relative). Output keeps the
mesh in meters; the editable feature history remains exclusively in Fusion F3D.
"""

import json
from pathlib import Path

import bpy
from mathutils import Vector

output = Path(STUDIO_OUTPUT_DIR)  # noqa: F821
workspace = output.parents[3]
asset = (workspace / STUDIO_PARAMETERS["stl_path"]).resolve()  # noqa: F821
if not asset.is_relative_to(workspace) or asset.suffix.lower() != ".stl" or not asset.is_file():
    raise ValueError("Expected a workspace-contained exported STL")
bpy.ops.wm.stl_import(filepath=str(asset), global_scale=0.001, use_scene_unit=False)
tray = bpy.context.object
tray.name = "Fusion-exported original open tray"
bpy.context.view_layer.update()
dimensions = [float(v) * 1000 for v in tray.dimensions]
if any(abs(a - b) > 0.02 for a, b in zip(dimensions, (101, 72, 11))):
    raise ValueError("Imported STL differs from verified default CAD bounds: " + str(dimensions))
material = bpy.data.materials.new("Teal prototype polymer - illustrative material")
material.diffuse_color = (0.035, 0.43, 0.40, 1)
material.use_nodes = True
bsdf = material.node_tree.nodes.get("Principled BSDF")
bsdf.inputs["Base Color"].default_value = material.diffuse_color
bsdf.inputs["Roughness"].default_value = 0.32
tray.data.materials.append(material)

bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -0.0001))
floor = bpy.context.object
floor.name = "Presentation floor"
floor_mat = bpy.data.materials.new("Dark studio floor")
floor_mat.diffuse_color = (0.018, 0.025, 0.040, 1)
floor_mat.use_nodes = True
floor_mat.node_tree.nodes.get("Principled BSDF").inputs[
    "Base Color"
].default_value = floor_mat.diffuse_color
floor.data.materials.append(floor_mat)
target = Vector((0.0505, 0.036, 0.004))
bpy.ops.object.camera_add(location=(0.158, -0.095, 0.146))
camera = bpy.context.object
camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
camera.data.type = "ORTHO"
camera.data.ortho_scale = 0.145
camera.data.clip_start = 0.001
camera.data.clip_end = 10
bpy.context.scene.camera = camera
for name, location, energy, size in [
    ("Key softbox", (0.02, -0.06, 0.18), 10, 0.13),
    ("Rim softbox", (0.15, 0.14, 0.12), 8, 0.10),
    ("Fill softbox", (-0.10, 0.08, 0.08), 5, 0.10),
]:
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.name = name
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
    light.rotation_euler = (target - light.location).to_track_quat("-Z", "Y").to_euler()
world = bpy.data.worlds.new("Prototype studio world")
world.use_nodes = True
world.node_tree.nodes.get("Background").inputs["Color"].default_value = (0.10, 0.13, 0.20, 1)
world.node_tree.nodes.get("Background").inputs["Strength"].default_value = 0.3
bpy.context.scene.world = world
bpy.context.scene.view_settings.exposure = -3.0
bpy.context.scene["geometry_provenance"] = (
    "Actual original Fusion STL, scaled from millimetres to metres"
)
bpy.context.scene["physical_fit"] = "Not tested"
(output / "cad-to-blender-measurements.json").write_text(
    json.dumps(
        {
            "input_filename": asset.name,
            "input_units": "mm",
            "scene_units": "m",
            "scale": 0.001,
            "measured_tray_dimensions_mm": dimensions,
            "max_bounds_error_mm": max(abs(a - b) for a, b in zip(dimensions, (101, 72, 11))),
            "physical_fit_tested": False,
        },
        indent=2,
    )
)
