"""Original industrial robotics lab. Run through the trusted Studio Agent Bridge.

Geometry is in metres. No downloaded meshes, photographs, or employer assets.
STUDIO_PARAMETERS accepts lighting_theme ('day' or 'night'), camera, and render_views.
"""

import json
import math
from collections import Counter
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

P = globals().get("STUDIO_PARAMETERS", {})
OUT = Path(globals().get("STUDIO_OUTPUT_DIR", "."))
OUT.mkdir(parents=True, exist_ok=True)
TEXTURES = OUT / "textures"
TEXTURES.mkdir(exist_ok=True)
THEME = P.get("lighting_theme", "day")
if THEME not in ("day", "night"):
    raise ValueError("lighting_theme must be day or night")

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for col in list(bpy.data.collections):
    if col.name not in {"Collection"}:
        bpy.data.collections.remove(col)
ASSETS = bpy.data.collections.new("StudioEnvironment")
RIG = bpy.data.collections.new("PresentationRig")
SOURCES = bpy.data.collections.new("SourceGeometry")
for col in (ASSETS, RIG, SOURCES):
    bpy.context.scene.collection.children.link(col)
SOURCES.hide_render = True
SOURCES.hide_viewport = True
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1
scene["studio_scene"] = "Industrial AI Robotics Lab"
scene["studio_authorship"] = "Original synthetic demonstration; no third-party scene assets."
scene["room_clear_dimensions_m"] = [12.0, 9.0, 4.2]
scene["lighting_theme"] = THEME
scene["engineering_boundary"] = (
    "Visualization environment. No building, robot safety, or electrical compliance certification."
)
MESH = {}
MAT = {}


def texture_image(name, values, non_color=False):
    h, w = values.shape[:2]
    pixels = np.ones((h, w, 4), dtype=np.float32)
    if values.ndim == 2:
        pixels[:, :, :3] = values[:, :, None]
    else:
        pixels[:, :, :3] = values[:, :, :3]
    image = bpy.data.images.new(name, width=w, height=h, alpha=False)
    if non_color:
        image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(pixels.ravel())
    image.filepath_raw = str(TEXTURES / (name + ".png"))
    image.file_format = "PNG"
    image.save()
    image.pack()
    image["studio_provenance"] = "original-procedural"
    return image


def material(name, color, metallic=0.0, roughness=0.5, emission=0, textured=False):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.diffuse_color = (*color, 1)
    bs = m.node_tree.nodes.get("Principled BSDF")
    bs.inputs["Base Color"].default_value = (*color, 1)
    bs.inputs["Metallic"].default_value = metallic
    bs.inputs["Roughness"].default_value = roughness
    if emission:
        bs.inputs["Emission Color"].default_value = (*color, 1)
        bs.inputs["Emission Strength"].default_value = emission
    if textured:
        rng = np.random.default_rng(sum(name.encode("ascii")))
        noise = rng.random((256, 256), dtype=np.float32)
        yy, xx = np.mgrid[:256, :256]
        streak = np.sin(yy * 0.43) * 0.012
        grain = (noise - 0.5) * 0.075 + streak
        base = np.clip(np.array(color)[None, None, :] * (1 + grain[:, :, None]), 0, 1)
        rough = np.clip(roughness + grain * 0.55, 0, 1)
        normals = np.zeros((256, 256, 3), dtype=np.float32)
        normals[:, :, 0] = 0.5 + (noise - 0.5) * 0.05
        normals[:, :, 1] = 0.5 + np.sin(xx * 0.6) * 0.015
        normals[:, :, 2] = 1
        for suffix, data, target in [
            ("BaseColor", base, "Base Color"),
            ("Roughness", rough, "Roughness"),
            ("Normal", normals, None),
        ]:
            tex = m.node_tree.nodes.new("ShaderNodeTexImage")
            tex.name = name + "_" + suffix
            tex.image = texture_image(tex.name, data, suffix != "BaseColor")
            if target:
                m.node_tree.links.new(tex.outputs["Color"], bs.inputs[target])
            else:
                normal = m.node_tree.nodes.new("ShaderNodeNormalMap")
                normal.inputs["Strength"].default_value = 0.22
                m.node_tree.links.new(tex.outputs["Color"], normal.inputs["Color"])
                m.node_tree.links.new(normal.outputs["Normal"], bs.inputs["Normal"])
    MAT[name] = m
    return m


material("Warm_Panel", (0.68, 0.71, 0.70), 0.12, 0.48, textured=True)
material("Floor_Epoxy", (0.24, 0.29, 0.31), 0.08, 0.42, textured=True)
material("Graphite", (0.035, 0.049, 0.063), 0.5, 0.35, textured=True)
material("Steel", (0.40, 0.48, 0.52), 0.85, 0.28, textured=True)
material("Teal_Paint", (0.025, 0.33, 0.34), 0.35, 0.32, textured=True)
material("Amber_Paint", (0.94, 0.41, 0.045), 0.2, 0.36)
material("Rubber", (0.011, 0.016, 0.023), 0.0, 0.72)
material("White_Type", (0.83, 0.89, 0.88), 0.05, 0.42)
material("PCB", (0.026, 0.16, 0.105), 0.22, 0.38)
material("Red_Stop", (0.65, 0.025, 0.025), 0.2, 0.32)
material("Screen", (0.012, 0.07, 0.105), 0.15, 0.28, 0.4)
material("Cyan_Light", (0.08, 0.7, 0.8), 0.1, 0.23, 2)
material("Green_LED", (0.10, 0.75, 0.32), 0.0, 0.22, 2.5)
material("Amber_LED", (1.0, 0.32, 0.025), 0.0, 0.25, 2.0)
material("Light_Diffuser", (0.80, 0.88, 1.0), 0.0, 0.4, 3.0)
material("Window_Tint", (0.065, 0.15, 0.20), 0.60, 0.20)
material("Wood", (0.35, 0.20, 0.10), 0, 0.55, textured=True)


def move_collection(obj, collection):
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    collection.objects.link(obj)


def mesh_object(name, key, create, location, scale, mat, category, bevel=0):
    if key not in MESH:
        create()
        ob = bpy.context.object
        MESH[key] = ob.data
        ob.data.name = "Prototype_" + str(len(MESH)).zfill(3) + "_" + key.split(":")[0]
        ob.data.materials.append(MAT[mat])
    else:
        ob = bpy.data.objects.new(name, MESH[key])
        ASSETS.objects.link(ob)
    ob.name = name
    move_collection(ob, ASSETS)
    ob.location = location
    ob.scale = scale
    ob.material_slots[0].link = "OBJECT"
    ob.material_slots[0].material = MAT[mat]
    ob["studio_asset"] = True
    ob["studio_provenance"] = "original-procedural"
    ob["studio_category"] = category
    ob["studio_prototype_key"] = key
    if bevel:
        mod = ob.modifiers.new("Editable_edge_bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 2
        mod.limit_method = "ANGLE"
        mod.harden_normals = True
    return ob


def box(name, loc, dims, mat="Graphite", cat="props", bevel=0.02):
    return mesh_object(
        name, "box", lambda: bpy.ops.mesh.primitive_cube_add(size=1), loc, dims, mat, cat, bevel
    )


def cylinder(name, loc, radius, depth, mat="Steel", cat="props", axis="Z", verts=32):
    o = mesh_object(
        name,
        f"cylinder:{verts}",
        lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=1, depth=1),
        loc,
        (radius, radius, depth),
        mat,
        cat,
        0.025,
    )
    o.rotation_euler = {"Z": (0, 0, 0), "X": (0, math.pi / 2, 0), "Y": (math.pi / 2, 0, 0)}[axis]
    for p in o.data.polygons:
        p.use_smooth = len(p.vertices) == 4
    return o


def rod(name, start, end, radius, mat="Steel", cat="utilities"):
    a, b = Vector(start), Vector(end)
    o = cylinder(name, (a + b) / 2, radius, (b - a).length, mat, cat)
    o.rotation_euler = (b - a).to_track_quat("Z", "Y").to_euler()
    return o


def torus(name, loc, major, minor, mat="Rubber", cat="robotics", axis="Z"):
    ratio = round(minor / major, 4)
    o = mesh_object(
        name,
        f"torus:{ratio}",
        lambda: bpy.ops.mesh.primitive_torus_add(
            major_radius=1, minor_radius=ratio, major_segments=32, minor_segments=8
        ),
        loc,
        (major, major, major),
        mat,
        cat,
    )
    o.rotation_euler = {"Z": (0, 0, 0), "X": (0, math.pi / 2, 0), "Y": (math.pi / 2, 0, 0)}[axis]
    for p in o.data.polygons:
        p.use_smooth = True
    return o


def text(
    name, content, loc, size=0.1, mat="White_Type", rotation=(math.pi / 2, 0, 0), cat="signage"
):
    key = "text:" + content
    if key not in MESH:
        c = bpy.data.curves.new(name + "_EditableText", "FONT")
        c.body = content
        c.size = 1
        c.extrude = 0.001
        c.resolution_u = 3
        c.align_x = "CENTER"
        c.align_y = "CENTER"
        source = bpy.data.objects.new(name + "_SOURCE", c)
        SOURCES.objects.link(source)
        ob = source.copy()
        ob.data = source.data.copy()
        ASSETS.objects.link(ob)
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.object.convert(target="MESH")
        ob.select_set(False)
        MESH[key] = ob.data
        ob.data.materials.append(MAT[mat])
        bpy.data.objects.remove(ob, do_unlink=True)
    ob = bpy.data.objects.new(name, MESH[key])
    ASSETS.objects.link(ob)
    ob.location = loc
    ob.scale = (size, size, size)
    ob.rotation_euler = rotation
    ob.material_slots[0].link = "OBJECT"
    ob.material_slots[0].material = MAT[mat]
    ob["studio_asset"] = True
    ob["studio_provenance"] = "original-procedural"
    ob["studio_category"] = cat
    ob["studio_prototype_key"] = key
    return ob


def cable(name, points, radius=0.018, mat="Rubber", cat="utilities"):
    c = bpy.data.curves.new(name + "_EditableCurve", "CURVE")
    c.dimensions = "3D"
    c.resolution_u = 8
    c.bevel_depth = radius
    c.bevel_resolution = 2
    sp = c.splines.new("BEZIER")
    sp.bezier_points.add(len(points) - 1)
    for p, co in zip(sp.bezier_points, points):
        p.co = co
        p.handle_left_type = "AUTO"
        p.handle_right_type = "AUTO"
    source = bpy.data.objects.new(name + "_SOURCE", c)
    SOURCES.objects.link(source)
    ob = source.copy()
    ob.data = c.copy()
    ASSETS.objects.link(ob)
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.convert(target="MESH")
    ob.name = name
    ob.data.materials.append(MAT[mat])
    ob["studio_asset"] = True
    ob["studio_provenance"] = "original-procedural"
    ob["studio_category"] = cat
    ob["studio_prototype_key"] = name
    ob.select_set(False)
    return ob


def bolts(prefix, coords, axis="Z"):
    for i, coord in enumerate(coords):
        cylinder(f"{prefix}_Washer_{i:02}", coord, 0.025, 0.009, "Steel", "fasteners", axis)
        offset = {"Z": (0, 0, 0.008), "Y": (0, -0.008, 0), "X": (0.008, 0, 0)}[axis]
        cylinder(
            f"{prefix}_Hex_{i:02}",
            Vector(coord) + Vector(offset),
            0.014,
            0.014,
            "Graphite",
            "fasteners",
            axis,
            6,
        )


# Architecture: complete shell, modular interior panels and removable ceiling.
box("Foundation", (0, 0, -0.16), (12.4, 9.4, 0.26), "Graphite", "architecture")
for ix in range(12):
    for iy in range(9):
        box(
            f"FloorTile_{ix:02}_{iy:02}",
            (ix - 5.5, iy - 4, -0.015),
            (0.988, 0.988, 0.06),
            "Floor_Epoxy",
            "architecture",
            0.006,
        )
for x in (-6.08, 6.08):
    box("SideWall_Structure", (x, 0, 2.1), (0.16, 9.2, 4.2), "Graphite", "architecture")
    for iy in range(9):
        for iz in range(3):
            box(
                "SideWall_Cladding",
                (x * 0.986, iy - 4, 0.67 + iz * 1.29),
                (0.08, 0.975, 1.255),
                "Warm_Panel",
                "architecture",
                0.009,
            )
    box("Wall_Skirting", (x * 0.978, 0, 0.12), (0.06, 9.0, 0.20), "Steel", "architecture")
for ix in range(12):
    x = ix - 5.5
    box("RearWall_Structure", (x, 4.58, 2.1), (1, 0.16, 4.2), "Graphite", "architecture")
    for iz in range(3):
        box(
            "RearWall_Cladding",
            (x, 4.47, 0.67 + iz * 1.29),
            (0.977, 0.06, 1.255),
            "Warm_Panel",
            "architecture",
            0.009,
        )
    if abs(x) > 1:
        box(
            "FrontWall_Panel",
            (x, -4.55, 2.1),
            (0.98, 0.18, 4.2),
            "Warm_Panel",
            "architecture",
            0.01,
        )
box("Door_Header", (0, -4.55, 3.64), (2.02, 0.20, 1.12), "Warm_Panel", "architecture")
for x in (-1.03, 1.03):
    box("Door_Jamb", (x, -4.47, 1.55), (0.10, 0.20, 3.1), "Steel", "architecture")
box("Door_Lintel", (0, -4.47, 3.1), (2.16, 0.20, 0.12), "Steel", "architecture")
box("Door_Threshold", (0, -4.48, 0.025), (2.1, 0.22, 0.04), "Steel", "architecture")
# Sliding door is visibly open on its rail, leaving a usable entrance.
box("DoorLeaf_Open", (2.05, -4.39, 1.52), (1.88, 0.10, 3.0), "Graphite", "architecture")
box("DoorLeaf_Window", (2.05, -4.325, 1.95), (1.45, 0.015, 1.2), "Window_Tint", "architecture")
rod("Door_Handle", (2.72, -4.27, 1.15), (2.72, -4.27, 1.65), 0.025)
for y in (-2.5, 0, 2.5):
    box("ObservationWindow_Frame", (-5.91, y, 2.35), (0.12, 2.05, 1.5), "Graphite", "architecture")
    box(
        "ObservationWindow_Glass",
        (-5.835, y, 2.35),
        (0.014, 1.86, 1.30),
        "Window_Tint",
        "architecture",
    )
    box("ObservationWindow_Mullion", (-5.80, y, 2.35), (0.04, 0.045, 1.36), "Steel", "architecture")
for x in (-4, -2, 0, 2, 4):
    box("Ceiling_CrossBeam", (x, 0, 4.10), (0.10, 9.0, 0.20), "Graphite", "ceiling")
for y in (-3, 0, 3):
    box("Ceiling_LongBeam", (0, y, 4.10), (12.0, 0.1, 0.20), "Graphite", "ceiling")
for ix in range(6):
    for iy in range(3):
        box(
            "Ceiling_RemovablePanel",
            (ix * 2 - 5, iy * 3 - 3, 4.19),
            (1.94, 2.94, 0.08),
            "Warm_Panel",
            "ceiling",
            0.005,
        )
for x in (-4.7, 4.7):
    box("Rear_StructuralColumn", (x, 4.32, 2.05), (0.16, 0.18, 4.1), "Graphite", "architecture")
    bolts("Column_Anchor", [(x + s * 0.045, 4.205, z) for s in (-1, 1) for z in (0.25, 3.8)], "Y")
box("Rear_IdentityPanel", (-1.35, 4.36, 2.85), (5.7, 0.10, 1.0), "Graphite", "signage")
text("Lab_Identity", "AUTONOMY  /  LAB", (-1.35, 4.295, 2.95), 0.38)
text("Lab_Subtitle", "ROBOTICS  +  EMBODIED INTELLIGENCE", (-1.35, 4.29, 2.57), 0.12, "Cyan_Light")
for x in (-4.3, 4.3):
    box("Rear_AccentRail", (x, 4.35, 3.6), (2.7, 0.04, 0.04), "Cyan_Light", "signage")
# Floor zoning, crosswalk and inlaid hazard borders.
for x in (-2.0, 2.0):
    box("Cell_Border_Long", (x, 0.1, 0.0155), (0.055, 3.75, 0.001), "Amber_Paint", "signage", 0)
for y in (-1.78, 1.98):
    box("Cell_Border_Short", (0, y, 0.0155), (4.05, 0.055, 0.001), "Amber_Paint", "signage", 0)
for i in range(10):
    b = box(
        "Cell_Hazard_Stripe",
        (-1.8 + i * 0.4, -1.61, 0.0155),
        (0.18, 0.24, 0.001),
        "Amber_Paint",
        "signage",
        0,
    )
    b.rotation_euler.z = -0.45
text("Floor_Cell", "CELL 01", (0, -2.25, 0.0165), 0.26, "White_Type", (0, 0, 0))
text("Floor_Path", "SERVICE  /  02", (3.65, -1.95, 0.0165), 0.17, "White_Type", (0, 0, math.pi / 2))

# Robot workcell: supported island, fixture, articulated body, joints and cable loom.
for x in (-1.05, 1.05):
    for y in (-0.65, 0.85):
        box("Cell_Table_Leg", (x, y, 0.43), (0.13, 0.13, 0.82), "Graphite", "furniture")
        cylinder("Cell_LevelingFoot", (x, y, 0.08), 0.12, 0.09, "Rubber", "furniture")
        cylinder("Cell_FootThread", (x, y, 0.14), 0.035, 0.10, "Steel", "furniture")
for y in (-0.65, 0.85):
    box("Cell_Table_Stretcher", (0, y, 0.28), (2.15, 0.07, 0.10), "Steel", "furniture")
box("Cell_Table_Subframe", (0, 0.1, 0.82), (2.65, 2.0, 0.16), "Graphite", "furniture")
box("Cell_Table_Top", (0, 0.1, 0.94), (2.8, 2.15, 0.10), "Steel", "robotics", 0.025)
box("Cell_FixturePlate", (0.55, -0.16, 1.01), (1.05, 1.12, 0.055), "Graphite", "robotics")
for x in np.linspace(0.12, 0.97, 6):
    for y in np.linspace(-0.60, 0.28, 6):
        cylinder(
            "Fixture_TappedPort",
            (float(x), float(y), 1.041),
            0.014,
            0.005,
            "Rubber",
            "robotics",
            verts=16,
        )
bolts("Cell_Top", [(x, y, 0.997) for x in (-1.25, 1.25) for y in (-0.83, 1.03)])
root = bpy.data.objects.new("Robot_JointHierarchy", None)
ASSETS.objects.link(root)
root["studio_category"] = "robotics"
robot_objects = []


def robo(o):
    o.parent = root
    robot_objects.append(o)
    return o


robo(cylinder("Robot_BaseMount", (-0.72, 0.30, 1.04), 0.36, 0.08, "Graphite", "robotics"))
robo(cylinder("Robot_BaseTurntable", (-0.72, 0.30, 1.18), 0.27, 0.22, "Teal_Paint", "robotics"))
robo(torus("Robot_BaseBearing", (-0.72, 0.30, 1.29), 0.235, 0.023, "Steel"))
bolts(
    "Robot_Base",
    [
        (-0.72 + math.cos(t) * 0.30, 0.30 + math.sin(t) * 0.30, 1.09)
        for t in np.linspace(0, math.tau, 8, endpoint=False)
    ],
)
shoulder = Vector((-0.72, 0.30, 1.48))
elbow = Vector((-0.98, 0.40, 2.42))
wrist = Vector((0.20, 0.19, 2.15))
tool = Vector((0.52, 0.08, 1.68))
for i, point in enumerate((shoulder, elbow, wrist)):
    robo(
        cylinder(
            f"Robot_JointHousing_{i}",
            point,
            (0.26, 0.22, 0.16)[i],
            0.40,
            "Teal_Paint",
            "robotics",
            "Y",
        )
    )
    for s in (-1, 1):
        c = point + Vector((0, s * 0.22, 0))
        robo(
            cylinder(
                f"Robot_JointCap_{i}_{s}",
                c,
                (0.20, 0.17, 0.12)[i],
                0.045,
                "Graphite",
                "robotics",
                "Y",
            )
        )
        robo(
            cylinder(
                f"Robot_JointEncoder_{i}_{s}",
                c + Vector((0, s * 0.025, 0)),
                0.07,
                0.020,
                "Steel",
                "robotics",
                "Y",
            )
        )
    bolts(
        f"Joint_{i}",
        [
            (
                point.x + math.cos(t) * (0.16, 0.13, 0.09)[i],
                point.y - 0.25,
                point.z + math.sin(t) * (0.16, 0.13, 0.09)[i],
            )
            for t in np.linspace(0, math.tau, 6, endpoint=False)
        ],
        "Y",
    )
for i, (a, b, width) in enumerate(
    ((shoulder, elbow, 0.26), (elbow, wrist, 0.22), (wrist, tool, 0.14))
):
    middle = (a + b) / 2
    link = box(
        f"Robot_Link_{i}",
        middle,
        (width, width * 1.15, (b - a).length * 0.82),
        "Teal_Paint",
        "robotics",
        0.14,
    )
    link.rotation_euler = (b - a).to_track_quat("Z", "Y").to_euler()
    robo(link)
    cover = box(
        f"Robot_LinkServiceCover_{i}",
        middle + Vector((0, -width * 0.62, 0)),
        (width * 0.61, 0.025, (b - a).length * 0.52),
        "Graphite",
        "robotics",
        0.08,
    )
    cover.rotation_euler = link.rotation_euler
    robo(cover)
robo(cylinder("Robot_WristCoupling", tool, 0.12, 0.12, "Steel", "robotics"))
robo(box("Robot_GripperActuator", (0.52, 0.08, 1.54), (0.32, 0.20, 0.16), "Graphite", "robotics"))
for x in (0.39, 0.65):
    robo(
        box("Robot_GripperFinger", (x, 0.08, 1.40), (0.045, 0.12, 0.22), "Steel", "robotics", 0.04)
    )
    for z in (1.32, 1.35, 1.38, 1.41):
        robo(
            box(
                "Robot_GripperSerration",
                (x, 0.005, z),
                (0.045, 0.025, 0.012),
                "Rubber",
                "robotics",
                0,
            )
        )
guide_centers = [
    elbow.lerp(wrist, float(t)) + Vector((0, 0.22, 0.20)) for t in np.linspace(0.12, 0.9, 5)
]
robo(
    cable(
        "Robot_FlexibleLoom",
        [(-0.90, 0.50, 1.12), (-1.17, 0.55, 1.8), (-1.08, 0.60, 2.60)]
        + [tuple(v) for v in guide_centers]
        + [(0.57, 0.25, 1.71)],
        0.030,
    )
)
for v in guide_centers:
    eyelet = torus("Loom_Clamp", v, 0.06, 0.01, "Steel", "robotics", "X")
    eyelet.rotation_euler = (wrist - elbow).to_track_quat("Z", "Y").to_euler()
    robo(eyelet)
    robo(
        rod(
            "Loom_ClampStandoff",
            v - Vector((0, 0, 0.19)),
            v - Vector((0, 0, 0.05)),
            0.012,
            "Steel",
            "robotics",
        )
    )
# Original inspection target: nested machined rings held on a fixture; no proprietary part.
cylinder("Inspection_TargetBase", (0.52, 0.08, 1.11), 0.23, 0.12, "Teal_Paint", "robotics")
torus("Inspection_TargetRing", (0.52, 0.08, 1.23), 0.16, 0.047, "Steel", "robotics")
for x in (0.16, 0.90):
    box("Fixture_ClampBlock", (x, 0.08, 1.12), (0.15, 0.28, 0.15), "Amber_Paint", "robotics")
    cylinder("Fixture_Adjuster", (x, 0.08, 1.22), 0.035, 0.08, "Steel", "robotics")
box("Cell_ControlBox", (-1.43, -0.50, 0.97), (0.16, 0.45, 0.35), "Graphite", "robotics")
cylinder("Cell_EmergencyStop", (-1.52, -0.50, 1.02), 0.07, 0.05, "Red_Stop", "robotics", "X")
text("Cell_Serial", "A-01", (-0.72, -0.15, 1.26), 0.08)

# Equipped electronics workbench, separate drawers, T-slot frame and small apparatus.
for x in (-4.8, -1.8):
    for y in (2.66, 3.40):
        box("Bench_TSlotLeg", (x, y, 0.49), (0.075, 0.075, 0.93), "Steel", "furniture")
        cylinder("Bench_RubberFoot", (x, y, 0.045), 0.065, 0.055, "Rubber", "furniture")
box("Bench_Top", (-3.3, 3.03, 0.98), (3.25, 0.97, 0.10), "Wood", "furniture", 0.018)
box("Bench_ESDMat", (-3.1, 2.98, 1.037), (2.45, 0.65, 0.012), "Teal_Paint", "furniture", 0.008)
box("Bench_DrawerCabinet", (-4.35, 3.04, 0.55), (0.82, 0.77, 0.79), "Graphite", "furniture")
for i in range(4):
    z = 0.28 + i * 0.18
    box("Bench_DrawerFront", (-4.35, 2.642, z), (0.76, 0.035, 0.16), "Warm_Panel", "furniture")
    box("Bench_DrawerPull", (-4.35, 2.60, z), (0.32, 0.04, 0.027), "Steel", "furniture")
# Pegboard and tool outlines modeled as physical details.
box("Bench_Pegboard", (-3.3, 4.25, 1.65), (3.3, 0.06, 1.15), "Graphite", "furniture")
for ix in range(20):
    for iz in range(6):
        cylinder(
            "Pegboard_Port",
            (-4.78 + ix * 0.155, 4.211, 1.22 + iz * 0.16),
            0.013,
            0.005,
            "Steel",
            "furniture",
            "Y",
            12,
        )
for i in range(6):
    x = -4.60 + i * 0.18
    rod("Tool_ScrewdriverShaft", (x, 4.16, 1.65), (x, 4.16, 1.87), 0.009)
    cylinder("Tool_ScrewdriverGrip", (x, 4.16, 1.57), 0.025, 0.16, "Amber_Paint", "props")
for i in range(3):
    x = -3.1 + i * 0.35
    box("Bench_Bin", (x, 4.02, 1.29), (0.28, 0.24, 0.17), "Teal_Paint", "props")
    box("Bench_BinLabel", (x, 3.894, 1.30), (0.15, 0.006, 0.06), "White_Type", "props", 0)


def screen(prefix, x, y, z, width=0.78, height=0.46):
    box(prefix + "_StandBase", (x, y, z - 0.38), (0.36, 0.24, 0.035), "Graphite", "electronics")
    box(prefix + "_Stand", (x, y + 0.045, z - 0.21), (0.05, 0.07, 0.34), "Steel", "electronics")
    box(prefix + "_Bezel", (x, y, z), (width, 0.065, height), "Graphite", "electronics", 0.025)
    box(
        prefix + "_Display",
        (x, y - 0.038, z),
        (width - 0.045, 0.006, height - 0.045),
        "Screen",
        "electronics",
        0.007,
    )
    for i, length in enumerate((0.60, 0.44, 0.54, 0.31, 0.49)):
        box(
            prefix + "_UIline",
            (x - 0.03, y - 0.043, z + 0.12 - i * 0.055),
            (length * width, 0.003, 0.012),
            "Cyan_Light",
            "electronics",
            0,
        )
    for i in range(4):
        box(
            prefix + "_UIbars",
            (x + width * 0.30, y - 0.045, z - 0.10 + i * 0.055),
            (0.027, 0.003, 0.025 + i * 0.01),
            "Green_LED",
            "electronics",
            0,
        )


screen("Bench_Monitor", -3.15, 3.34, 1.49)
box("Bench_KeyboardBase", (-3.15, 2.80, 1.065), (0.62, 0.22, 0.035), "Graphite", "electronics")
for row in range(4):
    for col in range(13):
        box(
            "Keyboard_Key",
            (-3.42 + col * 0.045, 2.72 + row * 0.045, 1.088),
            (0.036, 0.035, 0.014),
            "Warm_Panel",
            "electronics",
            0.06,
        )
box("Scope_Case", (-4.35, 3.30, 1.24), (0.50, 0.28, 0.36), "Warm_Panel", "electronics")
box("Scope_Screen", (-4.44, 3.15, 1.25), (0.25, 0.012, 0.22), "Screen", "electronics")
for i in range(4):
    cylinder(
        "Scope_Knob", (-4.15, 3.14, 1.16 + i * 0.065), 0.021, 0.02, "Graphite", "electronics", "Y"
    )
wave = [(-4.55 + i * 0.018, 3.14, 1.24 + math.sin(i * 0.8) * 0.045) for i in range(13)]
cable("Scope_Waveform", wave, 0.003, "Green_LED", "electronics")
box("SolderStation", (-2.12, 3.25, 1.16), (0.30, 0.24, 0.23), "Graphite", "electronics")
box("SolderStation_Display", (-2.12, 3.123, 1.19), (0.16, 0.007, 0.06), "Cyan_Light", "electronics")
cable(
    "SolderIron_Cable",
    [(-2.12, 3.13, 1.10), (-1.85, 2.95, 1.05), (-2.20, 2.78, 1.05), (-2.34, 2.97, 1.08)],
    0.008,
)
rod("SolderIron_Grip", (-2.34, 2.97, 1.08), (-2.5, 3.10, 1.09), 0.019, "Amber_Paint", "electronics")
rod("SolderIron_Tip", (-2.5, 3.10, 1.09), (-2.57, 3.16, 1.09), 0.006, "Steel", "electronics")
box("Prototype_PCB", (-3.8, 2.83, 1.06), (0.23, 0.18, 0.012), "PCB", "electronics")
for i in range(4):
    box(
        "PCB_IC", (-3.87 + i * 0.045, 2.83, 1.075), (0.027, 0.065, 0.016), "Graphite", "electronics"
    )
# Bench stool has support, castors and upholstered seat.
cylinder("Stool_Seat", (-3.1, 1.95, 0.62), 0.29, 0.10, "Graphite", "furniture")
cylinder("Stool_GasStrut", (-3.1, 1.95, 0.34), 0.045, 0.50, "Steel", "furniture")
torus("Stool_FootRing", (-3.1, 1.95, 0.28), 0.23, 0.015, "Steel", "furniture")
for i in range(5):
    t = i * math.tau / 5
    q = (-3.1 + math.cos(t) * 0.32, 1.95 + math.sin(t) * 0.32, 0.09)
    rod("Stool_BaseSpoke", (-3.1, 1.95, 0.16), q, 0.025, "Steel", "furniture")
    cylinder("Stool_Castor", q, 0.055, 0.04, "Rubber", "furniture", "Y")

# Three server racks: separate rails, chassis units, grille, handles, LEDs, patch cables.
for r in range(3):
    x = 2.0 + r * 1.15
    y = 3.65
    box(f"Rack{r}_Base", (x, y, 0.13), (0.92, 1.05, 0.20), "Graphite", "compute")
    box(f"Rack{r}_Top", (x, y, 2.35), (0.92, 1.05, 0.12), "Graphite", "compute")
    for dx in (-0.42, 0.42):
        for dy in (-0.47, 0.47):
            box(
                f"Rack{r}_CornerPost",
                (x + dx, y + dy, 1.25),
                (0.055, 0.055, 2.15),
                "Steel",
                "compute",
            )
    box(f"Rack{r}_BackPanel", (x, y + 0.50, 1.23), (0.83, 0.04, 2.1), "Graphite", "compute")
    for u in range(9):
        z = 0.38 + u * 0.21
        box(f"Rack{r}_Unit{u}_Chassis", (x, y, z), (0.77, 0.91, 0.18), "Graphite", "compute")
        box(f"Rack{r}_Unit{u}_Face", (x, y - 0.47, z), (0.77, 0.025, 0.17), "Steel", "compute")
        for v in range(14):
            box(
                "Server_VentSlit",
                (x - 0.30 + v * 0.037, y - 0.487, z + 0.015),
                (0.014, 0.006, 0.10),
                "Rubber",
                "compute",
                0.02,
            )
        for s in (-1, 1):
            box(
                "Server_PullHandle",
                (x + s * 0.345, y - 0.52, z),
                (0.018, 0.06, 0.11),
                "Graphite",
                "compute",
            )
        for j in range(3):
            cylinder(
                "Server_StatusLED",
                (x + 0.255 + j * 0.022, y - 0.491, z - 0.045),
                0.006,
                0.005,
                "Green_LED" if j != 2 else "Amber_LED",
                "compute",
                "Y",
                12,
            )
    text(f"Rack{r}_Label", "COMPUTE / " + str(r + 1).zfill(2), (x, y - 0.535, 2.35), 0.066)
    for j in range(3):
        cable(
            f"Rack{r}_PatchCable{j}",
            [
                (x - 0.24 + j * 0.10, y - 0.50, 2.02),
                (x - 0.28 + j * 0.10, y - 0.70, 1.75),
                (x - 0.19 + j * 0.10, y - 0.64, 1.53),
            ],
            0.008,
            "Teal_Paint",
        )
text("Compute_Header", "COMPUTE", (3.15, 4.27, 2.90), 0.30, "Graphite")
# Mobile diagnostics cart next to the cell.
box("Cart_Top", (3.75, 0.25, 1.02), (1.15, 0.72, 0.07), "Warm_Panel", "furniture")
box("Cart_LowerShelf", (3.75, 0.25, 0.25), (1.10, 0.67, 0.055), "Graphite", "furniture")
for x in (3.27, 4.23):
    for y in (-0.02, 0.52):
        rod("Cart_Post", (x, y, 0.16), (x, y, 1.07), 0.024, "Steel", "furniture")
        cylinder("Cart_Wheel", (x, y, 0.12), 0.08, 0.05, "Rubber", "furniture", "Y")
screen("Cart_DiagnosticMonitor", 3.75, 0.37, 1.46, 0.80, 0.48)
box("Cart_Tablet", (3.75, 0.03, 1.07), (0.38, 0.22, 0.025), "Graphite", "electronics")
box("Cart_TabletScreen", (3.75, 0.03, 1.085), (0.34, 0.18, 0.003), "Cyan_Light", "electronics", 0)
box("Cart_InstrumentsCase", (3.75, 0.25, 0.43), (0.64, 0.43, 0.30), "Teal_Paint", "props")
for x in (3.5, 4.0):
    box("Case_Latch", (x, 0.02, 0.50), (0.04, 0.03, 0.09), "Steel", "props")

# Room services: exposed pipe runs, conduit brackets, cable tray with repeated rungs.
for z, mat in ((3.50, "Steel"), (3.70, "Teal_Paint")):
    rod("ServicePipe_Rear", (-5.6, 4.12, z), (5.5, 4.12, z), 0.045, mat)
    cable(
        "ServicePipe_Elbow",
        [(-5.6, 4.12, z), (-5.72, 4.03, z), (-5.72, 3.80, z), (-5.72, -3.8, z)],
        0.045,
        mat,
    )
    for x in range(-5, 6):
        torus("Pipe_Collar", (x, 4.12, z), 0.052, 0.009, "Graphite", "utilities", "X")
        box("Pipe_WallBracket", (x, 4.29, z), (0.07, 0.28, 0.07), "Steel", "utilities")
for y in (-2.7, 2.0):
    for x in (-4.8, 4.8):
        rod("CableTray_Suspension", (x, y, 3.70), (x, y, 4.03), 0.012)
    for dx in (-0.18, 0.18):
        box("CableTray_SideRail", (0, y + dx, 3.71), (10.1, 0.035, 0.09), "Steel", "utilities")
    for ix in range(40):
        box(
            "CableTray_Rung",
            (-4.85 + ix * 0.25, y, 3.68),
            (0.035, 0.40, 0.025),
            "Steel",
            "utilities",
        )
    for j in range(4):
        rod(
            "CableTray_Loom",
            (-5.0, y - 0.11 + j * 0.07, 3.74),
            (5.0, y - 0.11 + j * 0.07, 3.74),
            0.018,
            "Rubber",
        )
box("Utility_Box", (5.82, 2.9, 1.3), (0.20, 1.0, 1.2), "Graphite", "utilities")
for y in (2.64, 3.16):
    cable("Utility_DownConduit", [(5.69, y, 3.5), (5.69, y, 2.9), (5.69, y, 1.9)], 0.025, "Steel")
box("Wall_ClimateUnit", (4.9, 4.18, 3.28), (1.2, 0.46, 0.54), "Warm_Panel", "utilities")
for z in np.linspace(3.08, 3.45, 8):
    box("Climate_Louver", (4.9, 3.94, float(z)), (1.02, 0.035, 0.018), "Graphite", "utilities")
# Fire extinguisher and emergency wall station: decorative props, not certification.
cylinder("Safety_Extinguisher", (-5.65, -2.9, 0.62), 0.11, 0.52, "Red_Stop", "props")
cylinder("Safety_ExtinguisherNeck", (-5.65, -2.9, 0.92), 0.035, 0.10, "Steel", "props")
box("Safety_ExtinguisherLever", (-5.65, -2.9, 0.99), (0.15, 0.05, 0.035), "Graphite", "props")
cable(
    "Safety_ExtinguisherHose",
    [(-5.61, -2.9, 0.93), (-5.47, -2.9, 0.87), (-5.47, -2.9, 0.59)],
    0.012,
)

# Close-up details: tool vision module, sensor connectors, power/data and controls.
robo(
    box("Robot_ToolVisionHousing", (0.75, 0.10, 1.65), (0.16, 0.20, 0.18), "Teal_Paint", "robotics")
)
robo(box("Robot_ToolVisionBracket", (0.65, 0.18, 1.68), (0.24, 0.055, 0.035), "Steel", "robotics"))
robo(
    cylinder("Robot_ToolVisionLensBarrel", (0.75, 0.10, 1.53), 0.052, 0.08, "Graphite", "robotics")
)
robo(cylinder("Robot_ToolVisionLens", (0.75, 0.10, 1.485), 0.040, 0.008, "Window_Tint", "robotics"))
robo(torus("Robot_ToolVisionRingLight", (0.75, 0.10, 1.50), 0.060, 0.010, "Cyan_Light", "robotics"))
for x in (0.71, 0.79):
    robo(cylinder("Robot_SensorConnector", (x, 0.10, 1.765), 0.016, 0.05, "Steel", "robotics"))
robo(
    cable(
        "Robot_SensorHarness",
        [(0.71, 0.10, 1.80), (0.77, 0.23, 1.93), (0.35, 0.38, 2.0)],
        0.009,
        "Rubber",
    )
)
bolts("Vision_Bracket", [(0.65, 0.18, 1.704), (0.56, 0.18, 1.704)])
text("Cell_EStopLabel", "STOP", (-1.385, -0.73, 0.99), 0.045, "White_Type")
box("Cell_StatusPanel", (0.55, -1.005, 0.84), (0.65, 0.026, 0.10), "Graphite", "robotics")
text("Cell_StatusText", "VISION  /  READY", (0.55, -1.025, 0.84), 0.052, "Cyan_Light")
for j in range(4):
    x = -3.7 + j * 0.19
    box("Bench_PowerSocket", (x, 4.165, 1.12), (0.12, 0.04, 0.10), "Warm_Panel", "electronics")
    for dx in (-0.018, 0.018):
        cylinder(
            "Bench_SocketPin",
            (x + dx, 4.139, 1.12),
            0.007,
            0.006,
            "Graphite",
            "electronics",
            "Y",
            12,
        )
for i in range(2):
    cable(
        "Bench_InstrumentLead",
        [
            (-4.13 + i * 0.08, 3.14, 1.12),
            (-4.02 + i * 0.07, 2.98, 1.08),
            (-3.85 + i * 0.10, 2.73, 1.08),
            (-3.74 + i * 0.06, 2.85, 1.08),
        ],
        0.006,
        "Red_Stop" if i == 0 else "Rubber",
    )
    cylinder(
        "Scope_InputConnector",
        (-4.13 + i * 0.08, 3.14, 1.10),
        0.015,
        0.028,
        "Steel",
        "electronics",
        "Y",
    )
# Flush metal edge on the cabinet, exposed rail screwheads and a detail label.
for z in (0.20, 0.87):
    bolts("Bench_CabinetScrews", [(-4.68, 2.617, z), (-4.02, 2.617, z)], "Y")
text("Bench_ESDLabel", "ESD  /  TEST BENCH", (-3.3, 2.533, 0.98), 0.060, "Graphite")


# Practical fixtures are real geometry, separate from lights and cameras.
def area(name, loc, target, power, color, size, size_y=None):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = power
    data.color = color
    data.shape = "RECTANGLE"
    data.size = size
    data.size_y = size_y or size
    ob = bpy.data.objects.new(name, data)
    RIG.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    ob["studio_role"] = "practical_light"
    return ob


for x in (-3.8, 0, 3.8):
    for y in (-1.6, 2.2):
        box(
            "LightFixture_Housing", (x, y, 3.98), (1.55, 0.62, 0.12), "Graphite", "lighting_fixture"
        )
        box(
            "LightFixture_Diffuser",
            (x, y, 3.905),
            (1.43, 0.52, 0.025),
            "Light_Diffuser",
            "lighting_fixture",
        )
        color = (0.30, 0.55, 1) if THEME == "night" else (0.88, 0.93, 1)
        area(
            "Ceiling_Practical",
            (x, y, 3.86),
            (x, y, 0),
            180 if THEME == "night" else 650,
            color,
            1.4,
            0.52,
        )
area(
    "Entrance_SoftFill",
    (0, -4.25, 2.6),
    (0, 0, 1.4),
    160 if THEME == "night" else 550,
    (1, 0.42, 0.16) if THEME == "night" else (1, 0.77, 0.53),
    3,
    2,
)
area(
    "Robot_WarmEdge",
    (-3.8, 0, 2.8),
    (0, 0, 1.5),
    400 if THEME == "night" else 250,
    (1, 0.55, 0.20) if THEME == "night" else (1, 0.58, 0.25),
    1.4,
    2,
)
area("Rack_CoolEdge", (5.7, 1.6, 2.8), (3, 3.2, 1.4), 250, (0.32, 0.67, 1), 1.3, 1.8)
if THEME == "night":
    diffuser = MAT["Light_Diffuser"].node_tree.nodes.get("Principled BSDF")
    diffuser.inputs["Emission Color"].default_value = (0.25, 0.48, 1.0, 1.0)
    diffuser.inputs["Emission Strength"].default_value = 1.5
world = bpy.data.worlds.new("Lab_ExteriorAmbient")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.08, 0.10, 0.14, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.15
scene.world = world


def camera(name, loc, target, lens):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    data.clip_start = 0.03
    data.clip_end = 100
    ob = bpy.data.objects.new(name, data)
    RIG.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return ob


CAMERAS = {
    "Overview": camera("Camera_Overview", (5.35, -3.95, 2.65), (-0.70, 1.20, 1.58), 22),
    "RobotDetail": camera("Camera_RobotDetail", (2.10, -2.55, 2.30), (-0.15, 0.25, 1.62), 43),
    "BenchDetail": camera("Camera_BenchDetail", (-1.2, 1.42, 1.85), (-3.55, 3.30, 1.38), 34),
    "ComputeAisle": camera("Camera_ComputeAisle", (4.95, -1.1, 1.80), (1.55, 3.15, 1.48), 30),
}
scene.camera = CAMERAS.get(P.get("camera", "Overview"), CAMERAS["Overview"])
scene.render.engine = "CYCLES"
scene.cycles.samples = 24
scene.cycles.use_denoising = True
scene.render.resolution_x = 1440
scene.render.resolution_y = 1000
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.use_file_extension = True
scene.render.film_transparent = False
scene.view_settings.view_transform = "AgX"
scene.view_settings.exposure = -0.30
scene.render.use_stamp = False
scene.render.use_stamp_filename = False
# Keep the native document pleasant to open; geometry remains individual and editable.
for ob in ASSETS.objects:
    ob.select_set(False)
for screen_data in bpy.data.screens:
    for a in screen_data.areas:
        if a.type == "VIEW_3D":
            a.spaces.active.region_3d.view_perspective = "CAMERA"
            a.spaces.active.clip_end = 200
bpy.context.view_layer.update()
meshes = [o for o in ASSETS.objects if o.type == "MESH"]
bounds = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
report = {
    "scene": "Industrial AI Robotics Lab",
    "authorship": "All geometry, typography layouts and texture maps are original generated assets.",
    "units": "metres",
    "lighting_theme": THEME,
    "room_clear_dimensions_m": [12, 9, 4.2],
    "mesh_objects": len(meshes),
    "unique_meshes": len({o.data.as_pointer() for o in meshes}),
    "categories": dict(sorted(Counter(o.get("studio_category") for o in meshes).items())),
    "materials": len(MAT),
    "packed_image_maps": len([i for i in bpy.data.images if i.packed_file]),
    "editable_bevel_modifiers": sum(len(o.modifiers) for o in meshes),
    "editable_source_curves_and_texts": len(SOURCES.objects),
    "world_bounds_m": {
        "min": [min(v[a] for v in bounds) for a in range(3)],
        "max": [max(v[a] for v in bounds) for a in range(3)],
    },
    "cameras": list(CAMERAS),
    "limits": [
        "Visual scene, not building engineering or robot controls.",
        "Window panes are opaque tinted visualization surfaces.",
        "Unreal fidelity needs separate native verification.",
    ],
}
(OUT / "environment-design-evidence.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
for view in P.get("render_views", []):
    scene.camera = CAMERAS[view]
    scene.render.filepath = str(OUT / ("view-" + view.lower() + ".png"))
    bpy.ops.render.render(write_still=True)
scene.camera = CAMERAS.get(P.get("camera", "Overview"), CAMERAS["Overview"])
scene.render.filepath = "//render.png"
