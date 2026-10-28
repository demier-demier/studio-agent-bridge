"""Render measured trajectories. Decorative chassis parts do not affect physics."""

import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

args = sys.argv[sys.argv.index("--") + 1 :]
run = Path(args[0])
data = np.load(run / "trajectory.npz")
particles = data["particles"]
poses = data["geom"]
out = run / "render"
out.mkdir(exist_ok=True)
macro = "--macro" in args
if macro:
    out = run / "render-macro"
    out.mkdir(exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 20
scene.cycles.use_denoising = True
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "METAL"
prefs.get_devices()
for device in prefs.devices:
    device.use = device.type == "METAL"
print("Render devices:", [(d.name, d.type, d.use) for d in prefs.devices], flush=True)
if any(d.type == "METAL" and d.use for d in prefs.devices):
    scene.cycles.device = "GPU"
if "--cpu" in args:
    scene.cycles.device = "CPU"
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.fps = 30
scene.world = bpy.data.worlds.new("World")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.14, 0.19, 0.26, 1)
scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.25
scene.view_settings.view_transform = "AgX"


def material(name, color, metal=0, rough=0.4):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    p = m.node_tree.nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Metallic"].default_value = metal
    p.inputs["Roughness"].default_value = rough
    return m


white = material("Ceramic white housing", (0.7, 0.76, 0.78), 0.25, 0.28)
dark = material("Graphite elastomer", (0.025, 0.033, 0.04), 0.1, 0.5)
steel = material("Machined titanium", (0.35, 0.42, 0.48), 0.85, 0.2)
orange = material("Safety orange", (0.95, 0.22, 0.025), 0.3, 0.3)
sand = material("Physical coarse grains", (0.5, 0.33, 0.15), 0, 0.8)
nodes = sand.node_tree.nodes
links = sand.node_tree.links
noise = nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 210
ramp = nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.elements[0].color = (0.13, 0.06, 0.018, 1)
ramp.color_ramp.elements[1].color = (0.72, 0.53, 0.27, 1)
links.new(noise.outputs["Fac"], ramp.inputs[0])
links.new(ramp.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])


def cube(name, location, scale, mat, bevel=0.003, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    o = bpy.context.object
    o.name = name
    o.dimensions = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    o.data.materials.append(mat)
    if bevel:
        mod = o.modifiers.new("Edge machining", "BEVEL")
        mod.width = bevel
        mod.segments = 3
        o.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
    if parent:
        o.parent = parent
    return o


def sphere(name, location, radius, mat, sub=2, parent=None):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=sub, radius=radius, location=location)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(mat)
    for p in o.data.polygons:
        p.use_smooth = True
    if parent:
        o.parent = parent
    return o


def cyl(name, loc, radius, depth, mat, parent=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=radius, depth=depth, location=loc)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(mat)
    mod = o.modifiers.new("Edge highlight", "BEVEL")
    mod.width = 0.001
    mod.segments = 2
    if parent:
        o.parent = parent
    return o


cube(
    "Laboratory floor",
    (0, 0, -0.035),
    (200, 200, 0.04),
    material("Backdrop", (0.025, 0.045, 0.065), 0.3, 0.5),
    0,
)
cube("Tray floor", (0, 0, -0.007), (0.95, 0.45, 0.014), dark)
for x in [-0.468, 0.468]:
    cube("Tray end", (x, 0, 0.015), (0.016, 0.45, 0.045), steel)
for y in [-0.218, 0.218]:
    cube("Tray edge", (0, y, 0.012), (0.95, 0.016, 0.04), steel)
for x in [-0.44, 0.44]:
    for y in [-0.20, 0.20]:
        cyl("Tray fastener", (x, y, 0.025), 0.004, 0.003, dark)

mesh = bpy.data.meshes.new("Measured grain positions")
mesh.from_pydata(particles[0].tolist(), [], [])
mesh.update()
grain_obj = bpy.data.objects.new("All simulated grains", mesh)
scene.collection.objects.link(grain_obj)
group = bpy.data.node_groups.new("One rendered grain per simulated particle", "GeometryNodeTree")
group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
n = group.nodes
links = group.links
inp = n.new("NodeGroupInput")
outn = n.new("NodeGroupOutput")
ico = n.new("GeometryNodeMeshIcoSphere")
ico.inputs["Radius"].default_value = float(data["radius"])
ico.inputs["Subdivisions"].default_value = 1
mat = n.new("GeometryNodeSetMaterial")
mat.inputs["Material"].default_value = sand
links.new(ico.outputs["Mesh"], mat.inputs["Geometry"])
instance = n.new("GeometryNodeInstanceOnPoints")
links.new(inp.outputs[0], instance.inputs["Points"])
links.new(mat.outputs["Geometry"], instance.inputs["Instance"])
links.new(instance.outputs["Instances"], outn.inputs[0])
mod = grain_obj.modifiers.new("Measured particle instances", "NODES")
mod.node_group = group

geoms = []
base = cube("Robot chassis", (0, 0, 0), (0.20, 0.10, 0.054), white)
geoms.append(base)
cube("Top armor", (0, 0, 0.031), (0.18, 0.092, 0.012), dark, 0.003, base)
cube("Orange identity stripe", (0.02, 0, 0.039), (0.025, 0.092, 0.004), orange, 0.001, base)
for x in [-0.078, -0.05, 0.05, 0.078]:
    for y in [-0.034, 0.034]:
        cyl("Chassis screw", (x, y, 0.039), 0.003, 0.002, steel, base)
for side in [-1, 1]:
    for x in np.linspace(-0.065, 0.065, 9):
        cube("Vent", (float(x), side * 0.051, 0), (0.005, 0.002, 0.023), dark, 0.001, base)
cube("Camera head", (0.112, 0, 0.007), (0.035, 0.065, 0.038), dark, 0.004, base)
for y in [-0.02, 0.02]:
    eye = cyl("Optical lens", (0.132, y, 0.01), 0.009, 0.005, steel, base)
    eye.rotation_euler[1] = math.pi / 2
    lens = sphere(
        "Lens glass",
        (0.135, y, 0.01),
        0.006,
        material("Lens", (0.01, 0.15, 0.2), 0.8, 0.08),
        2,
        base,
    )
for i in range(4):
    for j in range(2):
        body = cyl(f"Leg {i} segment {j}", (0, 0, 0), 0.009 if j == 0 else 0.007, 0.075, steel)
        cube("Leg armor", (0, 0, 0), (0.020, 0.014, 0.049), white, 0.003, body)
        for z in [-0.0375, 0.0375]:
            joint = cyl("Joint housing", (0, 0, z), 0.015, 0.018, dark, body)
            joint.rotation_euler[0] = math.pi / 2
            cap = cyl("Joint cap", (0, -0.010, z), 0.009, 0.002, orange, body)
            cap.rotation_euler[0] = math.pi / 2
        geoms.append(body)
    geoms.append(sphere(f"Physical foot {i}", (0, 0, 0), 0.022, dark, 3))


def apply(frame):
    k = max(0, min(frame, len(particles) - 1))
    mesh.vertices.foreach_set("co", particles[k].reshape(-1))
    mesh.update()
    for obj, pose in zip(geoms, poses[k]):
        mat = Matrix(pose[3:].reshape(3, 3).tolist()).to_4x4()
        mat.translation = Vector(pose[:3])
        obj.matrix_world = mat


def camera(loc, target, lens):
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.object
    cam.name = "Hero camera"
    cam.rotation_euler = (Vector(target) - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = lens
    cam.data.clip_start = 0.002
    cam.data.clip_end = 250
    scene.camera = cam


camera((0.90, -0.95, 0.70), (0, 0, 0.07), 48)
if macro:
    cam = scene.camera
    cam.location = (-0.62, -0.48, 0.48)
    cam.rotation_euler = (
        (Vector((-0.18, 0, 0.025)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    )
    cam.data.lens = 58


def light(loc, power, size, color):
    bpy.ops.object.light_add(type="AREA", location=loc)
    o = bpy.context.object
    o.data.energy = power
    o.data.shape = "DISK"
    o.data.size = size
    o.data.color = color
    o.rotation_euler = (Vector((0, 0, 0.07)) - o.location).to_track_quat("-Z", "Y").to_euler()


if macro:
    light((0, 0.45, 0.11), 12, 0.08, (1, 0.88, 0.72))
    light((-0.2, -0.4, 0.6), 8, 0.5, (0.65, 0.8, 1))
else:
    light((-0.55, -0.20, 0.30), 35, 0.18, (1, 0.88, 0.72))
    light((0.3, -0.5, 0.7), 25, 0.5, (0.55, 0.75, 1))
    light((0.4, 0.5, 0.4), 20, 0.4, (1, 0.65, 0.35))
if "--all" in args:
    for i in range(0, len(particles), 2 if macro else 1):
        apply(i)
        scene.render.filepath = str(out / f"frame-{i:04d}.png")
        bpy.ops.render.render(write_still=True)
else:
    for i in (
        [len(particles) - 1] if "--last" in args else [0, len(particles) // 2, len(particles) - 1]
    ):
        apply(i)
        scene.render.filepath = str(out / f"check-{i:04d}.png")
        bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(run / "preview-scene.blend"))
