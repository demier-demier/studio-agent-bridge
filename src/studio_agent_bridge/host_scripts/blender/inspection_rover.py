"""Original editable inspection rover. Dimensions are design examples in meters.

This is a visual 3D assembly, not a mechanically engineered robot or validated CAD.
All generated geometry and materials are procedural; no external assets are used.
"""

import math

import bpy
from mathutils import Vector


def material(name, color, metallic=0.0, roughness=0.4, emission=0.0):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1)
    result.use_nodes = True
    surface = result.node_tree.nodes.get("Principled BSDF")
    surface.inputs["Base Color"].default_value = (*color, 1)
    surface.inputs["Metallic"].default_value = metallic
    surface.inputs["Roughness"].default_value = roughness
    if emission:
        surface.inputs["Emission Color"].default_value = (*color, 1)
        surface.inputs["Emission Strength"].default_value = emission
    return result


def move(obj, collection):
    for previous in list(obj.users_collection):
        previous.objects.unlink(obj)
    collection.objects.link(obj)


def finish(obj, name, mat, bevel=0.01, parent=None):
    obj.name = name
    move(obj, ASSET)
    if mat:
        obj.data.materials.append(mat)
    if parent is None:
        parent = ROOT
    obj.parent = parent
    if bevel:
        modifier = obj.modifiers.new("Editable edge radii", "BEVEL")
        modifier.width = bevel
        modifier.segments = 3
    obj["component_role"] = name.replace("_", " ")
    return obj


def box(name, location, size, mat, bevel=0.02, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(obj, name, mat, bevel, parent)


def cylinder(name, location, radius, depth, mat, axis="Z", bevel=0.008, parent=None, vertices=48):
    rotation = {"Z": (0, 0, 0), "Y": (math.pi / 2, 0, 0), "X": (0, math.pi / 2, 0)}[axis]
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices, radius=radius, depth=depth, location=location, rotation=rotation
    )
    obj = bpy.context.object
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for face in obj.data.polygons:
        face.use_smooth = len(face.vertices) == 4
    return finish(obj, name, mat, bevel, parent)


def rod(name, start, end, radius, mat):
    direction = Vector(end) - Vector(start)
    obj = cylinder(name, (Vector(start) + Vector(end)) / 2, radius, direction.length, mat)
    obj.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    return obj


def light(name, position, energy, size, color):
    bpy.ops.object.light_add(type="AREA", location=position)
    obj = bpy.context.object
    obj.name = name
    move(obj, STUDIO)
    obj.data.energy = energy
    obj.data.shape = "DISK"
    obj.data.size = size
    obj.data.color = color
    obj.rotation_euler = (Vector((0, 0, 0.6)) - obj.location).to_track_quat("-Z", "Y").to_euler()


ASSET = bpy.data.collections.new("InspectionRover")
STUDIO = bpy.data.collections.new("PresentationStudio")
bpy.context.scene.collection.children.link(ASSET)
bpy.context.scene.collection.children.link(STUDIO)
ROOT = bpy.data.objects.new("ROVER_ROOT_meters", None)
ASSET.objects.link(ROOT)
ROOT["description"] = "Original modular visual inspection rover; synthetic example geometry"
ROOT["unit_contract"] = "Blender meters; Unreal import must preserve 1m = 100cm"

navy = material("Anodized graphite", (0.025, 0.045, 0.065), 0.7, 0.3)
white = material("Ceramic white enclosure", (0.72, 0.79, 0.81), 0.2, 0.32)
teal = material("Signal teal", (0.015, 0.45, 0.40), 0.35, 0.28)
rubber = material("Soft rubber", (0.012, 0.016, 0.02), 0.0, 0.65)
metal = material("Brushed alloy", (0.37, 0.43, 0.49), 0.85, 0.25)
glass = material("Optical lens", (0.02, 0.10, 0.19), 0.7, 0.12)
amber = material("Amber status", (1.0, 0.25, 0.025), 0.1, 0.3, 1.0)
led = material("Cyan status", (0.02, 0.8, 0.7), 0.1, 0.25, 1.8)
floor_mat = material("Studio floor", (0.14, 0.17, 0.19), 0.0, 0.8)

box("Chassis_structural_base", (0, 0, 0.45), (1.6, 0.76, 0.22), navy, 0.055)
box("Battery_drawer", (-0.25, 0, 0.63), (0.86, 0.68, 0.15), white, 0.028)
box("Battery_service_cover", (-0.25, 0, 0.721), (0.83, 0.65, 0.025), teal, 0.008)
box("Electronics_bay", (0.46, 0, 0.64), (0.50, 0.66, 0.20), white, 0.035)
box("Forward_bumper", (0.83, 0, 0.43), (0.08, 0.85, 0.12), rubber, 0.025)
box("Rear_bumper", (-0.83, 0, 0.43), (0.08, 0.85, 0.12), rubber, 0.025)

for side, sign in (("port", 1), ("starboard", -1)):
    box(f"{side}_side_rail", (0, sign * 0.401, 0.57), (1.25, 0.045, 0.08), metal, 0.012)
    box(f"{side}_status_strip", (0.35, sign * 0.430, 0.60), (0.4, 0.015, 0.028), led, 0.005)
    for index, x in enumerate((-0.57, 0.57)):
        prefix = f"{side}_wheel_{index + 1}"
        cylinder(prefix + "_tire", (x, sign * 0.53, 0.255), 0.255, 0.19, rubber, "Y", 0.02)
        cylinder(prefix + "_rim", (x, sign * 0.638, 0.255), 0.155, 0.025, metal, "Y")
        cylinder(prefix + "_hub", (x, sign * 0.660, 0.255), 0.074, 0.032, teal, "Y")
        cylinder(prefix + "_axle", (x, sign * 0.39, 0.255), 0.065, 0.15, navy, "Y")
        rod(
            prefix + "_suspension",
            (x + 0.08, sign * 0.36, 0.28),
            (x - 0.08, sign * 0.36, 0.50),
            0.034,
            metal,
        )
        for bolt in range(5):
            angle = 2 * math.pi * bolt / 5
            cylinder(
                prefix + f"_fastener_{bolt + 1}",
                (x + 0.112 * math.cos(angle), sign * 0.656, 0.255 + 0.112 * math.sin(angle)),
                0.012,
                0.015,
                navy,
                "Y",
                0.001,
                vertices=6,
            )
    for index in range(6):
        box(
            f"{side}_cooling_vent_{index + 1}",
            (0.31 + index * 0.053, sign * 0.334, 0.66),
            (0.023, 0.015, 0.095),
            navy,
            0.005,
        )

for side, y in (("left", 0.27), ("right", -0.27)):
    cylinder(f"front_{side}_rangefinder_housing", (0.735, y, 0.65), 0.073, 0.08, navy, "X")
    cylinder(f"front_{side}_rangefinder_lens", (0.782, y, 0.65), 0.052, 0.012, glass, "X", 0.004)
    box(f"front_{side}_headlight", (0.78, y, 0.48), (0.025, 0.10, 0.04), led, 0.008)

cylinder("Mast_mount", (0.10, 0, 0.72), 0.16, 0.09, navy)
cylinder("Mast_lower_tube", (0.10, 0, 0.93), 0.055, 0.36, metal)
cylinder("Mast_upper_tube", (0.10, 0, 1.125), 0.042, 0.19, navy)
cylinder("Lidar_turntable", (0.10, 0, 1.25), 0.18, 0.07, navy)
cylinder("Lidar_scan_window", (0.10, 0, 1.30), 0.16, 0.04, glass)
cylinder("Lidar_top_cover", (0.10, 0, 1.335), 0.17, 0.035, teal)
box("Stereo_camera_housing", (0.20, 0, 1.08), (0.16, 0.32, 0.11), white, 0.022)
for index, y in enumerate((-0.105, 0.105)):
    cylinder(f"Stereo_lens_{index + 1}", (0.292, y, 1.08), 0.036, 0.035, glass, "X", 0.005)

cylinder("Inspection_arm_base", (-0.48, 0.0, 0.77), 0.12, 0.10, metal)
rod("Inspection_arm_lower", (-0.48, 0.0, 0.83), (-0.68, 0.0, 1.07), 0.045, teal)
cylinder("Inspection_arm_elbow", (-0.68, 0.0, 1.07), 0.074, 0.12, navy, "Y")
rod("Inspection_arm_upper", (-0.68, 0.0, 1.07), (-0.31, 0.0, 1.18), 0.035, white)
cylinder("Inspection_probe", (-0.27, 0.0, 1.18), 0.055, 0.13, navy, "X")
cylinder("Inspection_probe_window", (-0.198, 0.0, 1.18), 0.038, 0.012, amber, "X", 0.003)
rod("Radio_antenna", (-0.67, 0.25, 0.73), (-0.72, 0.25, 1.19), 0.012, rubber)

# The native scene retains a small explicit animation as an editing example.
scan = bpy.data.objects["Lidar_top_cover"]
for frame, angle in ((1, 0.0), (61, math.pi), (121, 2 * math.pi)):
    scan.rotation_euler.z = angle
    scan.keyframe_insert(data_path="rotation_euler", index=2, frame=frame)
bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = 121
bpy.context.scene.frame_set(1)

bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -0.025))
floor = bpy.context.object
floor.name = "Studio_floor_not_exported"
move(floor, STUDIO)
floor.data.materials.append(floor_mat)
bpy.ops.object.camera_add(location=(2.6, -3.6, 2.3))
camera = bpy.context.object
camera.name = "Presentation_camera"
move(camera, STUDIO)
camera.rotation_euler = (Vector((0, 0, 0.65)) - camera.location).to_track_quat("-Z", "Y").to_euler()
camera.data.type = "ORTHO"
camera.data.ortho_scale = 2.9
bpy.context.scene.camera = camera
light("Large_soft_key", (1.5, -2.0, 4.5), 800, 3.5, (0.82, 0.93, 1.0))
light("Warm_rim", (-2.0, 1.0, 2.5), 650, 2.5, (1.0, 0.79, 0.56))
light("Front_fill", (2.0, 2.5, 1.2), 400, 2.0, (0.65, 1.0, 0.91))
bpy.context.scene.world = bpy.data.worlds.new("Studio_world")
bpy.context.scene.world.use_nodes = True
bpy.context.scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (
    0.15,
    0.19,
    0.23,
    1,
)
bpy.context.scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.35
bpy.context.scene.unit_settings.system = "METRIC"
bpy.context.scene.unit_settings.scale_length = 1.0
bpy.context.view_layer.update()
