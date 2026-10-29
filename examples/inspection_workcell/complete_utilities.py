"""Add original synthetic utility details to the reviewed inspection station.

Execute with the trusted Blender bridge, STUDIO_PARAMETERS and STUDIO_OUTPUT_DIR.
Modes: build, inspect, render. Views: close, wide. Device: CPU, METAL or AUTO.
Editable cable centreline splines are retained for independent geometric readback.
This is visual assembly geometry, without electrical or safety certification.
"""

import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

P = globals().get("STUDIO_PARAMETERS", {})
OUT = Path(globals()["STUDIO_OUTPUT_DIR"])
MODE = P.get("mode", "build")
VIEW = P.get("view", "close")
DEVICE = P.get("device", "AUTO")
PREFIX = "InspectionV2_"
ORIGINAL_SHA256 = "dd2e62edea37ca62a53d49cb3a215f56c51f522e56a78fa63c879fc47b4f560c"
ROUTE_NAMES = ("Camera_L", "Camera_R", "Illuminator_L", "Illuminator_R", "HMI", "PowerSupply")
if MODE not in {"build", "inspect", "render"}:
    raise ValueError("Unknown utility mode")
if VIEW not in {"close", "wide"}:
    raise ValueError("Unknown utility view")
if DEVICE not in {"CPU", "METAL", "AUTO"}:
    raise ValueError("Device must be CPU, METAL or AUTO")
if not isinstance(P.get("render", False), bool):
    raise ValueError("render must be boolean")
scene = bpy.context.scene
assets = bpy.data.collections.get("StudioEnvironment")
rig = bpy.data.collections.get("PresentationRig")
if assets is None or rig is None or scene.get("studio_scene") != "Industrial AI Robotics Lab":
    raise ValueError("The reviewed industrial lab is required")
if MODE == "build" and any(o.name.startswith(PREFIX) for o in bpy.data.objects):
    raise ValueError("Utility revision already exists; refusing duplicate construction")
if MODE != "build" and scene.get("inspection_utilities_revision") != 1:
    raise ValueError("A built utility revision is required")


def mesh_record(o):
    return [o.name, [list(row) for row in o.matrix_world],
            [list(v.co) for v in o.data.vertices],
            [list(p.vertices) for p in o.data.polygons],
            [s.material.name if s.material else None for s in o.material_slots]]


def digest_meshes(objects):
    records = [mesh_record(o) for o in sorted(objects, key=lambda ob: ob.name) if o.type == "MESH"]
    return hashlib.sha256(json.dumps(records, separators=(",", ":"), allow_nan=False).encode()).hexdigest(), len(records)


def material_digest():
    records = []
    for material in sorted(bpy.data.materials, key=lambda m: m.name):
        record = [material.name, list(material.diffuse_color), material.metallic,
                  material.roughness, material.use_nodes]
        if material.use_nodes:
            for node in sorted(material.node_tree.nodes, key=lambda n: n.name):
                values = []
                for socket in node.inputs:
                    value = getattr(socket, "default_value", None)
                    if value is not None and not isinstance(value, (str, int, float, bool)):
                        try:
                            value = list(value)
                        except TypeError:
                            value = getattr(value, "name", None)
                    values.append([socket.identifier, value])
                record.append([node.name, node.bl_idname, values])
            record.append(sorted([link.from_node.name, link.from_socket.identifier,
                                  link.to_node.name, link.to_socket.identifier]
                                 for link in material.node_tree.links))
        records.append(record)
    return hashlib.sha256(json.dumps(records, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def world_bounds(o):
    corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return [Vector(tuple(min(v[i] for v in corners) for i in range(3))),
            Vector(tuple(max(v[i] for v in corners) for i in range(3)))]


def require_object(name, centre=None, tolerance=0.002):
    o = bpy.data.objects.get(name)
    if o is None or o.type != "MESH":
        raise ValueError("Missing source attachment: " + name)
    if centre is not None and (o.matrix_world.translation - Vector(centre)).length > tolerance:
        raise ValueError("Unexpected source attachment position: " + name)
    return o


bpy.context.view_layer.update()
original = [o for o in assets.objects if not o.name.startswith(("InspectionV1_", PREFIX))]
original_digest, original_count = digest_meshes(original)
station = [o for o in assets.objects if o.name.startswith("InspectionV1_") and o.type == "MESH"]
if original_count != 1571 or original_digest != ORIGINAL_SHA256 or len(station) != 249:
    raise ValueError("Source mesh fingerprint/count differs from the reviewed revision 2")
# The original lab hash does not include the new station; its functional anchors
# are checked separately before geometry is added.
ANCHORS = {
    "InspectionV1_CameraHousing": (-4.22, 0.33, 2.07),
    "InspectionV1_CameraHousing.001": (-3.38, 0.33, 2.07),
    "InspectionV1_SensorConnector": (-4.22, 0.35, 2.18),
    "InspectionV1_SensorConnector.001": (-3.38, 0.35, 2.18),
    "InspectionV1_Illuminator": (-4.35, 0.28, 1.69),
    "InspectionV1_Illuminator.001": (-3.25, 0.28, 1.69),
    "InspectionV1_ControlCabinet": (-3.43, 0.17, 0.56),
    "InspectionV1_HMIConsole": (-3.26, -0.22, 1.07),
    "Bench_PowerSocket.003": (-3.13, 4.165, 1.12),
}
for anchor_name, expected_centre in ANCHORS.items():
    require_object(anchor_name, expected_centre)
if len([o for o in station if o.name.startswith("InspectionV1_IlluminatorCrossBracket")]) != 2:
    raise ValueError("Source must include both revision-2 illuminator cross-brackets")
for camera_name in ("Camera_InspectionPilot", "Camera_Overview"):
    camera = bpy.data.objects.get(camera_name)
    if camera is None or camera.type != "CAMERA":
        raise ValueError("Missing reviewed source camera: " + camera_name)
for material_name in ("Steel", "Rubber", "Graphite", "Amber_Paint", "Teal_Paint"):
    if bpy.data.materials.get(material_name) is None:
        raise ValueError("Missing source material: " + material_name)

existing_meshes = [o for o in scene.objects if o.type == "MESH" and not o.name.startswith(PREFIX)]
before_mesh_digest, before_mesh_count = digest_meshes(existing_meshes)
before_material_digest = material_digest()
before_counts = {t: sum(o.type == t for o in scene.objects) for t in ("MESH", "CURVE", "CAMERA")}
cache = {}
route_specs = []


def tag(o, name, material=None):
    o.name = PREFIX + name
    if o.name != PREFIX + name:
        raise ValueError("Unexpected duplicate name: " + name)
    for collection in list(o.users_collection):
        collection.objects.unlink(o)
    assets.objects.link(o)
    o["studio_asset"] = True
    o["studio_category"] = "inspection_utilities"
    o["studio_provenance"] = "original-procedural"
    if material:
        o.data.materials.append(bpy.data.materials[material])
    return o


def primitive(name, kind, dimensions, material):
    key = (kind, tuple(dimensions), material)
    if key in cache:
        o = bpy.data.objects.new(PREFIX + name, cache[key])
        return tag(o, name)
    if kind == "box":
        bpy.ops.mesh.primitive_cube_add(size=1)
        o = bpy.context.object
        o.scale = dimensions
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    elif kind == "cylinder":
        radius, depth, vertices = dimensions
        bpy.ops.mesh.primitive_cylinder_add(vertices=int(vertices), radius=radius, depth=depth)
        o = bpy.context.object
    elif kind == "torus":
        major, minor = dimensions
        bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor,
                                       major_segments=20, minor_segments=8)
        o = bpy.context.object
    else:
        raise ValueError("Unknown primitive")
    tag(o, name, material)
    cache[key] = o.data
    return o


def box(name, position, dimensions, material="Steel", bevel=0.001):
    o = primitive(name, "box", dimensions, material)
    o.location = position
    if bevel:
        mod = o.modifiers.new("Utility_edge", "BEVEL")
        mod.width, mod.segments = bevel, 2
    return o


def cylinder(name, position, radius, depth, material="Steel", normal=(0, 0, 1), vertices=24):
    o = primitive(name, "cylinder", (radius, depth, vertices), material)
    o.location = position
    o.rotation_euler = Vector(normal).to_track_quat("Z", "Y").to_euler()
    return o


def rod(name, a, b, radius=0.004, material="Steel"):
    a, b = Vector(a), Vector(b)
    return cylinder(name, (a + b) / 2, radius, (b - a).length, material, b - a)


def ring(name, position, radius, minor=0.002, normal=(0, 0, 1), material="Steel"):
    o = primitive(name, "torus", (radius, minor), material)
    o.location = position
    o.rotation_euler = Vector(normal).to_track_quat("Z", "Y").to_euler()
    return o


def gland(route_name, side, host_name, face, surface_position, radius=0.019, depth=0.05):
    """A cylinder anchor whose centre is the editable cable's exact endpoint."""
    host = require_object(host_name)
    low, high = world_bounds(host)
    axis = "XYZ".index(face[1])
    sign = 1 if face[0] == "+" else -1
    surface = Vector(surface_position)
    surface[axis] = high[axis] if sign > 0 else low[axis]
    if any(surface[i] < low[i] - 0.001 or surface[i] > high[i] + 0.001
           for i in range(3) if i != axis):
        raise ValueError("Gland attachment lies outside host face")
    normal = Vector(tuple(sign if i == axis else 0 for i in range(3)))
    centre = surface + normal * (depth / 2 - 0.001)
    label = route_name + "_" + side
    anchor = cylinder(label + "Gland", centre, radius, depth, "Graphite", normal)
    anchor["utility_attachment_target"] = host.name
    anchor["utility_attachment_face"] = face
    anchor["utility_attachment_surface_world_m"] = list(surface)
    anchor["utility_route"] = PREFIX + "Route_" + route_name
    cylinder(label + "Locknut", surface + normal * 0.006, radius * 1.16, 0.012, "Steel", normal, 6)
    ring(label + "Seal", surface + normal * 0.001, radius * 0.90, 0.0015, normal, "Rubber")
    cylinder(label + "Boot", centre + normal * (depth * 0.54), radius * 0.62,
             depth * 0.66, "Rubber", normal)
    for i in range(3):
        ring(label + "StrainRib" + str(i), centre + normal * (depth * (0.34 + i * 0.16)),
             radius * 0.63, 0.0016, normal, "Rubber")
    return anchor, centre, normal


def rounded_points(points, radius=0.055):
    """Piecewise quadratic fillets, sampled as actual editable POLY vertices."""
    points = [Vector(p) for p in points]
    if len(points) < 2 or any((b - a).length < 0.0001 for a, b in zip(points, points[1:])):
        raise ValueError("Cable path has a zero-length segment")
    result = [points[0]]
    for a, b, c in zip(points, points[1:], points[2:]):
        trim = min(radius, (b - a).length * 0.32, (c - b).length * 0.32)
        p, q = b + (a - b).normalized() * trim, b + (c - b).normalized() * trim
        result.append(p)
        for step in range(1, 7):
            t = step / 6
            result.append((1 - t) ** 2 * p + 2 * (1 - t) * t * b + t ** 2 * q)
    result.append(points[-1])
    return result


def cable(route_name, start, end, intermediates, radius):
    a, apos, _ = start
    b, bpos, _ = end
    points = rounded_points([apos, *intermediates, bpos])
    data = bpy.data.curves.new(PREFIX + "Centreline_" + route_name, "CURVE")
    data.dimensions = "3D"
    data.resolution_u = 12
    data.bevel_depth = radius
    data.bevel_resolution = 3
    data.use_fill_caps = True
    spline = data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, coordinate in zip(spline.points, points):
        point.co = (*coordinate, 1.0)
    o = bpy.data.objects.new(PREFIX + "Route_" + route_name, data)
    tag(o, "Route_" + route_name, "Rubber")
    o["utility_start_anchor"] = a.name
    o["utility_end_anchor"] = b.name
    o["utility_role"] = "visual power supply" if route_name == "PowerSupply" else "visual device harness"
    o["utility_cable_radius_m"] = radius
    return o


def support(label, cable_centre, normal, host_name, mount_point, radius=0.009):
    """Visible P-clip with a short attachment stem, fastener and existing host."""
    centre, mount = Vector(cable_centre), Vector(mount_point)
    o = ring(label + "Clip", centre, radius, 0.0025, normal, "Graphite")
    o["utility_support_target"] = host_name
    direction = centre - mount
    if direction.length > radius:
        rod(label + "Standoff", mount, centre - direction.normalized() * radius, 0.004)
    cylinder(label + "Bolt", mount, 0.006, 0.008, "Steel", direction, 6)


if MODE == "build":
    cabinet = "InspectionV1_ControlCabinet"
    # Two camera harnesses use the source model's physical sensor connector.
    for i, side in enumerate(("L", "R")):
        name = "Camera_" + side
        suffix = "" if i == 0 else ".001"
        x = -4.22 if i == 0 else -3.38
        y, trunk_x = 0.555 + i * 0.014, -3.046 + i * 0.012
        start = gland(name, "Start", "InspectionV1_SensorConnector" + suffix, "+Z", (x, 0.35, 2.2))
        end = gland(name, "End", cabinet, "+Y", (-3.25 - i * 0.08, 0.47, 0.73))
        cable(name, start, end,
              [(x, 0.35, 2.325), (x, y, 2.325), (x + 0.10, y, 2.292),
               (trunk_x - 0.08, y, 2.292), (trunk_x, y, 2.21),
               (trunk_x, 0.54, 1.11), (trunk_x, 0.72 + i * 0.017, 1.07),
               (trunk_x, 0.735 + i * 0.017, 0.88),
               (-3.25 - i * 0.08, 0.69 + i * 0.017, 0.79),
               (-3.25 - i * 0.08, 0.57, 0.73)], 0.0055)
    # Small illuminator pigtails leave their end faces and follow supported posts.
    for i, side in enumerate(("L", "R")):
        name = "Illuminator_" + side
        suffix = "" if i == 0 else ".001"
        x = -4.35 if i == 0 else -3.25
        start = gland(name, "Start", "InspectionV1_Illuminator" + suffix,
                      "+Y", (x, 0.49, 1.69), radius=0.008, depth=0.028)
        end = gland(name, "End", cabinet, "+Y", (-3.41 - i * 0.08, 0.47, 0.73),
                    radius=0.014, depth=0.04)
        if i == 0:
            upper = [(x, 0.58, 1.69), (-4.48, 0.58, 1.77), (-4.48, 0.58, 2.20),
                     (-4.40, 0.594, 2.292), (-3.095, 0.594, 2.292), (-3.015, 0.58, 2.20)]
        else:
            upper = [(x, 0.59, 1.69), (-3.12, 0.59, 1.74), (-2.997, 0.58, 1.67)]
        trunk_x = -3.015 + i * 0.018
        cable(name, start, end,
              [*upper, (trunk_x, 0.58, 1.11), (trunk_x, 0.77 + i * 0.017, 1.07),
               (trunk_x, 0.78 + i * 0.017, 0.86),
               (-3.41 - i * 0.08, 0.73 + i * 0.017, 0.78),
               (-3.41 - i * 0.08, 0.55, 0.73)], 0.004)
    start = gland("HMI", "Start", "InspectionV1_HMIConsole", "+X", (-3.08, -0.22, 1.07),
                  radius=0.014, depth=0.04)
    end = gland("HMI", "End", cabinet, "+X", (-3.155, 0.02, 0.73), radius=0.016, depth=0.04)
    cable("HMI", start, end,
          [(-2.935, -0.22, 1.07), (-2.915, -0.22, 0.99), (-2.915, -0.16, 0.80),
           (-2.97, 0.02, 0.73), (-3.055, 0.02, 0.73)], 0.006)
    # A visible cabinet inlet and floor-protected lead terminate at a real,
    # existing source socket. This is a schematic visual cord, not wiring design.
    start = gland("PowerSupply", "Start", cabinet, "+X", (-3.155, 0.29, 0.46),
                  radius=0.027, depth=0.066)
    end = gland("PowerSupply", "End", "Bench_PowerSocket.003", "-Y", (-3.13, 4.145, 1.12),
                radius=0.028, depth=0.06)
    cable("PowerSupply", start, end,
          [(-2.96, 0.29, 0.46), (-2.95, 0.54, 0.31), (-3.035, 0.70, 0.10),
           (-3.035, 0.82, 0.045), (-3.035, 3.90, 0.045),
           (-3.13, 4.08, 0.14), (-3.13, 4.08, 0.93),
           (-3.13, 4.025, 1.06), (-3.13, 4.025, 1.12)], 0.009)
    # Rear connector identification ferrules, matching the four device circuits.
    for i, x in enumerate((-3.25, -3.33, -3.41, -3.49)):
        box("CircuitMarker_" + str(i + 1), (x, 0.481, 0.775), (0.035, 0.008, 0.012), "Amber_Paint")
    box("CabinetInletFlange", (-3.152, 0.29, 0.46), (0.012, 0.10, 0.11), "Graphite")
    for j, (y, z) in enumerate(((0.254, 0.42), (0.326, 0.42), (0.254, 0.50), (0.326, 0.50))):
        cylinder("InletFlangeBolt_" + str(j), (-3.141, y, z), 0.004, 0.008, "Steel", (1, 0, 0), 6)
    # Upper cable clips bridge to the existing chain links; vertical clips bridge
    # to the source gantry and rail. No existing bracket or material is changed.
    for j, x in enumerate((-4.10, -3.80, -3.55)):
        link_number = round((x + 4.40) / 0.05)
        for k, y in enumerate((0.555, 0.594)):
            support("ChainSupport_{}_{}".format(j, k), (x, y, 2.292), (1, 0, 0),
                    "InspectionV1_CableChainLink.{:03d}".format(link_number), (x, y, 2.276), 0.008)
    support("RightCameraChainSupport", (-3.25, 0.569, 2.292), (1, 0, 0),
            "InspectionV1_CableChainLink.023", (-3.25, 0.569, 2.276), 0.008)
    for j, z in enumerate((1.86, 2.07)):
        support("LeftPostSupport_" + str(j), (-4.48, 0.58, z), (0, 0, 1),
                "InspectionV1_GantryPost", (-4.48, 0.55, z), 0.007)
    for j, z in enumerate((1.20, 1.48, 1.90)):
        # Shared two-piece clamp across the sleeve and adjacent lighting leads.
        box("TrunkClampBack_" + str(j), (-3.048, 0.555, z), (0.145, 0.018, 0.025), "Steel")
        box("TrunkClampFront_" + str(j), (-3.03, 0.601, z), (0.115, 0.012, 0.020), "Graphite")
        rod("TrunkClampBolt_" + str(j), (-3.10, 0.55, z), (-3.10, 0.609, z), 0.005)
    support("HMIFrameSupport", (-2.915, -0.18, 0.86), (0, 0, 1),
            "InspectionV1_SideRail.003", (-3.05, -0.18, 0.90), 0.010)
    # Low-profile cover over the cable crossing, with sloped shoulders and tread
    # strips. Geometry is a visual routing detail, not a trip/safety approval.
    box("FloorCableCover", (-3.035, 1.70, 0.051), (0.085, 1.84, 0.024), "Graphite", 0.005)
    for side in (-1, 1):
        vertices = [(side * 0.040, -0.92, 0.063), (side * 0.17, -0.92, 0.016),
                    (side * 0.17, 0.92, 0.016), (side * 0.040, 0.92, 0.063),
                    (side * 0.040, -0.92, 0.016), (side * 0.040, 0.92, 0.016)]
        mesh = bpy.data.meshes.new(PREFIX + "RampShoulder")
        mesh.from_pydata(vertices, [], [(0, 1, 2, 3), (0, 4, 1), (3, 2, 5), (0, 3, 5, 4), (1, 4, 5, 2)])
        mesh.update()
        o = bpy.data.objects.new(PREFIX + "RampShoulder_" + str(side), mesh)
        tag(o, "RampShoulder_" + str(side), "Rubber")
        o.location = (-3.035, 1.70, 0)
    for j in range(12):
        box("FloorCoverTread_" + str(j), (-3.035, 0.90 + j * 0.145, 0.064),
            (0.075, 0.035, 0.003), "Amber_Paint", 0.0005)
    for j, z in enumerate((0.30, 0.72, 0.97)):
        box("RearCordWallBracket_" + str(j), (-3.13, 4.2625, z), (0.028, 0.355, 0.025), "Steel")
        box("RearCordWallFoot_" + str(j), (-3.13, 4.435, z), (0.085, 0.015, 0.065), "Steel")
        support("RearCordClip_" + str(j), (-3.13, 4.08, z), (0, 0, 1),
                PREFIX + "RearCordWallBracket_" + str(j), (-3.13, 4.10, z), 0.012)
    # Camera object/data copies retain source framing while leaving every
    # pre-existing camera transform and optical setting untouched.
    for view, source_name in (("Close", "Camera_InspectionPilot"), ("Wide", "Camera_Overview")):
        source = bpy.data.objects[source_name]
        camera = source.copy()
        camera.data = source.data.copy()
        camera.name = PREFIX + "Camera_" + view
        camera.data.name = camera.name
        rig.objects.link(camera)
        camera["studio_asset"] = True
        camera["studio_provenance"] = "original-procedural"
        camera["studio_category"] = "inspection_utilities"
        camera["utility_source_camera"] = source_name
    scene["inspection_utilities_revision"] = 1
    scene["inspection_utilities_source_mesh_fields_sha256"] = before_mesh_digest
    scene["inspection_utilities_original_material_fields_sha256"] = before_material_digest


def inspect_routes():
    results = []
    for name in ROUTE_NAMES:
        o = bpy.data.objects.get(PREFIX + "Route_" + name)
        if o is None or o.type != "CURVE" or len(o.data.splines) != 1 or o.data.splines[0].type != "POLY":
            raise ValueError("Missing editable utility centreline: " + name)
        points = [o.matrix_world @ Vector(point.co[:3]) for point in o.data.splines[0].points]
        if not all(math.isfinite(v) for point in points for v in point):
            raise ValueError("Non-finite utility centreline")
        endpoints = []
        for prop, point in (("utility_start_anchor", points[0]), ("utility_end_anchor", points[-1])):
            anchor = bpy.data.objects.get(o.get(prop, ""))
            if anchor is None or anchor.type != "MESH":
                raise ValueError("Missing utility gland")
            low, high = world_bounds(anchor)
            centre = (low + high) / 2
            error = (point - centre).length
            if error > 0.0001:
                raise ValueError("Cable endpoint is not centred in its gland")
            endpoints.append({"gland": anchor.name, "host": anchor["utility_attachment_target"],
                              "face": anchor["utility_attachment_face"],
                              "curve_endpoint_world_m": list(point), "gland_geometry_centre_world_m": list(centre),
                              "endpoint_error_m": error})
        results.append({"curve": o.name, "editable_spline": "POLY", "control_points": len(points),
                        "centreline_length_m": sum((b - a).length for a, b in zip(points, points[1:])),
                        "radius_m": o.data.bevel_depth, "endpoints": endpoints})
    return results


def select_render_device(requested):
    result = {"requested": requested, "selected": "CPU", "backend": "CPU", "devices": [], "reason": "Explicit CPU request"}
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    if requested == "CPU":
        return result
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        devices = [d for d in prefs.devices if d.type == "METAL"]
        if not devices:
            raise RuntimeError("Cycles did not expose a Metal device")
        for device in prefs.devices:
            device.use = device.type == "METAL"
        scene.cycles.device = "GPU"
        result.update(selected="GPU", backend="METAL", devices=[d.name for d in devices], reason="Metal devices explicitly enabled")
    except Exception as exc:
        if requested == "METAL":
            raise RuntimeError("Explicit Metal render requested but unavailable") from exc
        scene.cycles.device = "CPU"
        result["reason"] = "AUTO selected explicit CPU because Metal was unavailable: " + str(exc)
    return result


bpy.context.view_layer.update()
after_mesh_digest, after_mesh_count = digest_meshes(existing_meshes)
after_material_digest = material_digest()
if (before_mesh_digest != after_mesh_digest or before_mesh_count != after_mesh_count
        or before_material_digest != after_material_digest):
    raise ValueError("Existing mesh fields or material fields changed")
if scene.get("inspection_utilities_source_mesh_fields_sha256") != after_mesh_digest:
    raise ValueError("Saved source mesh fields differ from the utility build")
if scene.get("inspection_utilities_original_material_fields_sha256") != after_material_digest:
    raise ValueError("Saved source material fields differ from the utility build")
route_specs = inspect_routes()
new = sorted([o for o in scene.objects if o.name.startswith(PREFIX)], key=lambda o: o.name)
after_counts = {t: sum(o.type == t for o in scene.objects) for t in before_counts}
report = {
    "schema": "studio-inspection-utilities/v1", "mode": MODE,
    "revision": scene.get("inspection_utilities_revision"),
    "source_lab_mesh_count": original_count, "source_station_mesh_count": len(station),
    "source_lab_mesh_fields_sha256": original_digest,
    "preservation": {"existing_mesh_count": after_mesh_count,
                     "before_mesh_fields_sha256": before_mesh_digest, "after_mesh_fields_sha256": after_mesh_digest,
                     "before_material_fields_sha256": before_material_digest, "after_material_fields_sha256": after_material_digest,
                     "mesh_fields_preserved": before_mesh_digest == after_mesh_digest,
                     "material_fields_preserved": before_material_digest == after_material_digest},
    "diagnostic_deltas": {"before": before_counts, "after": after_counts,
                          "delta": {key: after_counts[key] - before_counts[key] for key in before_counts}},
    "geometry": [{"name": o.name, "type": o.type, "data": o.data.name,
                  "materials": [slot.material.name if slot.material else None for slot in o.material_slots]}
                 for o in new],
    "routes": route_specs,
    "render": None,
    "limits": "Original synthetic visual routing only. No current, voltage, shielding, bend-radius standard, electrical continuity, optical, collision, structural or safety certification. Source preservation hashes cover base mesh positions, vertices, polygons, material slots and selected material/node fields, not complete Blender-file equivalence.",
}
if MODE == "render" or P.get("render", False):
    camera = bpy.data.objects[PREFIX + "Camera_" + VIEW.title()]
    scene.camera = camera
    device_report = select_render_device(DEVICE)
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = (1100, 850) if VIEW == "close" else (1280, 720)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_stamp = False
    scene.render.use_stamp_filename = False
    render_name = "utilities-" + VIEW + ".png"
    scene.render.filepath = str(OUT / render_name)
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        scene.render.filepath = "//utilities-" + VIEW + ".png"
    report["render"] = {"file": render_name, "view": VIEW, "camera": camera.name,
                        "source_camera": camera["utility_source_camera"], "device": device_report,
                        "dimensions": [scene.render.resolution_x, scene.render.resolution_y], "samples": 32}
(OUT / "utility-evidence.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
print(json.dumps({"mode": MODE, "new_objects": len(new), "routes": len(route_specs),
                  "preserved": report["preservation"]["mesh_fields_preserved"], "render": report["render"]}))
