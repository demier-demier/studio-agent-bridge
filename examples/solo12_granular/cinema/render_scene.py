"""Cinema rendering of recorded Solo12 poses and granular states.

No body/foot/particle position is fabricated by this renderer. Only camera,
lighting and material appearance vary. Particle trajectories are memory mapped.
"""

import argparse
import json
import math
import platform
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from device_selection import DEVICES, configure_device, resolve_device  # noqa: E402


def options():
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path, nargs="?")
    parser.add_argument("--geometry", type=Path, help="Override the packaged robot geometry JSON")
    parser.add_argument("--shot", choices=["hero", "contact", "trail", "top"], default="hero")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--still", type=int)
    parser.add_argument("--samples", type=int, default=96)
    parser.add_argument("--resolution", nargs=2, type=int, default=[1920, 1080])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--save-blend", action="store_true")
    parser.add_argument(
        "--device",
        choices=DEVICES,
        help="Explicit Cycles backend; GPU selection never falls back to CPU",
    )
    parser.add_argument("--cpu", action="store_true", help="Compatibility alias for --device CPU")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Probe the selected backend, print detected devices and exit without loading a run",
    )
    parser.add_argument("--cpu-threads", type=int, default=8)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--foot", type=int, choices=range(4), default=1)
    parser.add_argument("--grain-subdivision", type=int, choices=[1, 2], default=1)
    result = parser.parse_args(args)
    if result.run is None and not result.list_devices:
        parser.error("run is required unless --list-devices is used")
    try:
        result.device = resolve_device(result.device, cpu=result.cpu, system=platform.system())
    except ValueError as exc:
        parser.error(str(exc))
    return result


def material(name, color, metallic=0.0, roughness=0.4):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    p = mat.node_tree.nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = (*color, 1.0)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = roughness
    return mat


def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def validate_inputs(args, particles, poses, feet, geometry, meta):
    """Reject malformed numeric caches before allocating Blender geometry."""
    if particles.ndim != 3 or particles.shape[2] != 3 or particles.shape[1] < 1:
        raise ValueError("particles.npy must have shape (frames, nonempty particles, 3)")
    if poses.shape[1:] != (len(geometry["geoms"]), 12):
        raise ValueError("robot_poses.npy does not match the supplied robot geometry")
    if feet.shape[1:] != (4, 3) or not len(particles) == len(poses) == len(feet):
        raise ValueError("Foot and robot caches must have the same nonzero frame count")
    if not len(particles) or any(a.dtype.kind != "f" for a in (particles, poses, feet)):
        raise ValueError("Frame caches must contain floating point arrays")
    if args.samples < 1 or args.cpu_threads < 1 or args.step < 1:
        raise ValueError("Samples, CPU threads and frame step must be positive")
    if any(v < 16 or v > 16384 for v in args.resolution):
        raise ValueError("Render dimensions must be between 16 and 16384 pixels")
    fps = float(meta.get("fps", 30))
    if not math.isfinite(fps) or fps <= 0 or fps != int(fps):
        raise ValueError("Metadata fps must be a positive integer")
    for name in ("grain_radius_m", "foot_radius_m"):
        if name in meta and (not math.isfinite(float(meta[name])) or float(meta[name]) <= 0):
            raise ValueError(f"Invalid {name}")
    for mesh in geometry["meshes"]:
        vertices = np.asarray(mesh["vertices"], dtype=float)
        faces = np.asarray(mesh["faces"])
        if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
            raise ValueError("Robot mesh vertices must be finite 3D coordinates")
        if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in "iu":
            raise ValueError("Robot mesh faces must contain integer triangles")
        if faces.size and (faces.min() < 0 or faces.max() >= len(vertices)):
            raise ValueError("Robot mesh triangle index is out of bounds")
    for geom in geometry["geoms"]:
        if not isinstance(geom["mesh"], int) or not 0 <= geom["mesh"] < len(geometry["meshes"]):
            raise ValueError("Robot geometry mesh reference is out of bounds")
    end = len(poses) if args.end is None else args.end
    if args.still is not None:
        indices = [args.still]
    else:
        if not 0 <= args.start < end <= len(poses):
            raise ValueError("Requested frame range is outside the recorded cache")
        indices = range(args.start, end, args.step)
    if any(not 0 <= i < len(poses) for i in indices):
        raise ValueError("Requested still frame is outside the recorded cache")
    if not np.isfinite(poses).all() or not np.isfinite(feet).all():
        raise ValueError("Camera framing requires finite robot/foot paths for the entire run")
    for name in ("surface_z", "floor_z"):
        if name in meta and not math.isfinite(float(meta[name])):
            raise ValueError(f"Invalid {name}")
    # Validate all selected states before writing any render output. mmap limits RAM use.
    for i in indices:
        if any(not np.isfinite(a[i]).all() for a in (particles, poses, feet)):
            raise ValueError(f"Non-finite simulation state at frame {i}")
    return indices


def main():
    args = options()
    if args.list_devices:
        selection = configure_device(
            bpy.context.scene, bpy.context.preferences.addons["cycles"].preferences, args.device
        )
        print(json.dumps(selection, indent=2), flush=True)
        return
    metadata_path = args.run / "metadata.json"
    meta = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    particles = np.load(args.run / "particles.npy", mmap_mode="r", allow_pickle=False)
    poses = np.load(args.run / "robot_poses.npy", mmap_mode="r", allow_pickle=False)
    feet = np.load(args.run / "foot_centers.npy", mmap_mode="r", allow_pickle=False)
    # An explicit geometry argument wins. Do not follow absolute/private paths in old metadata.
    geometry_path = args.geometry or HERE.parent / "robot/preview-geometry.json"
    geometry = json.loads(geometry_path.read_text())
    render_frames = validate_inputs(args, particles, poses, feet, geometry, meta)
    if "surface_z" not in meta:
        meta["surface_z"] = float(np.quantile(particles[0, :, 2], 0.98))
    radius = float(meta.get("grain_radius_m", 0.001))
    foot_radius = float(meta.get("foot_radius_m", 0.015))
    output = args.out or args.run / ("render-" + args.shot)
    if output.is_symlink():
        raise ValueError("Render output must not be a symlink")
    for frame in render_frames:
        destination = output / f"frame-{frame:04d}.png"
        if destination.is_symlink():
            raise ValueError("Render output files must not be symlinks")
        if destination.exists() and not args.skip_existing:
            raise FileExistsError(
                f"Refusing to overwrite {destination.name}; choose a new output or --skip-existing"
            )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    device_selection = configure_device(
        scene, bpy.context.preferences.addons["cycles"].preferences, args.device
    )
    output.mkdir(parents=True, exist_ok=True)
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = True
    scene.cycles.adaptive_threshold = 0.012
    scene.cycles.max_bounces = 6
    scene.cycles.diffuse_bounces = 3
    scene.cycles.glossy_bounces = 3
    scene.cycles.transmission_bounces = 2
    scene.cycles.use_light_tree = True
    scene.render.use_persistent_data = True
    scene.render.threads_mode = "FIXED" if args.device == "CPU" else "AUTO"
    if args.device == "CPU":
        scene.render.threads = args.cpu_threads
    scene.render.resolution_x, scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.fps = int(meta.get("fps", 30))
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.world = bpy.data.worlds.new("Black studio environment")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.045, 0.065, 0.10, 1)
    bg.inputs[1].default_value = 0.18

    graphite = material("Graphite actuator housings", (0.025, 0.035, 0.047), 0.48, 0.3)
    aluminium = material("Machined satin aluminium", (0.36, 0.43, 0.50), 0.8, 0.26)
    bodymat = material("Body anodized graphite", (0.07, 0.10, 0.13), 0.7, 0.25)
    rubber = material("Elastomer contact pads", (0.009, 0.012, 0.014), 0, 0.7)
    floor = material("Dark studio floor", (0.018, 0.024, 0.033), 0.1, 0.54)
    grain_mat = material("Physical quartz grains", (0.49, 0.31, 0.13), 0, 0.64)
    nodes = grain_mat.node_tree.nodes
    links = grain_mat.node_tree.links
    info = nodes.new("ShaderNodeObjectInfo")
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.19, 0.11, 0.045, 1)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (0.58, 0.41, 0.20, 1)
    links.new(info.outputs["Random"], ramp.inputs[0])
    links.new(ramp.outputs[0], nodes["Principled BSDF"].inputs["Base Color"])
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1350
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.00008
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], nodes["Principled BSDF"].inputs["Normal"])

    mesh_data = []
    for i, raw in enumerate(geometry["meshes"]):
        mesh = bpy.data.meshes.new(f"ODRI Solo12 official mesh {i}")
        mesh.from_pydata(raw["vertices"], [], raw["faces"])
        mesh.update()
        for p in mesh.polygons:
            p.use_smooth = True
        mesh.set_sharp_from_angle(angle=math.radians(35))
        mesh_data.append(mesh)
    robot = []
    for raw in geometry["geoms"]:
        obj = bpy.data.objects.new(raw["body"], mesh_data[raw["mesh"]])
        scene.collection.objects.link(obj)
        # Object-linked slots allow shared mesh instances to use their proper finish.
        if len(obj.data.materials) == 0:
            obj.data.materials.append(graphite)
        slot = obj.material_slots[0]
        slot.link = "OBJECT"
        slot.material = (
            bodymat
            if raw["body"] == "base_link"
            else (aluminium if "LOWER" in raw["body"] else graphite)
        )
        normal = obj.modifiers.new("Manufactured surface normals", "WEIGHTED_NORMAL")
        normal.keep_sharp = True
        normal.weight = 30
        robot.append(obj)
    pads = []
    for i in range(4):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=foot_radius)
        obj = bpy.context.object
        obj.name = f"Actual contact pad {i}"
        obj.data.materials.append(rubber)
        for poly in obj.data.polygons:
            poly.use_smooth = True
        pads.append(obj)

    grain_mesh = bpy.data.meshes.new("Recorded particle centers")
    grain_mesh.vertices.add(particles.shape[1])
    grain_mesh.vertices.foreach_set("co", np.asarray(particles[0]).reshape(-1))
    grain_mesh.update()
    grain_obj = bpy.data.objects.new("All measured granular particles", grain_mesh)
    scene.collection.objects.link(grain_obj)
    group = bpy.data.node_groups.new("One sphere per physical grain", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = group.nodes
    node_links = group.links
    inp = n.new("NodeGroupInput")
    out = n.new("NodeGroupOutput")
    ico = n.new("GeometryNodeMeshIcoSphere")
    ico.inputs["Radius"].default_value = radius
    ico.inputs["Subdivisions"].default_value = args.grain_subdivision
    smooth = n.new("GeometryNodeSetShadeSmooth")
    smooth.domain = "FACE"
    smooth.inputs["Shade Smooth"].default_value = True
    node_links.new(ico.outputs["Mesh"], smooth.inputs["Geometry"])
    smat = n.new("GeometryNodeSetMaterial")
    smat.inputs["Material"].default_value = grain_mat
    node_links.new(smooth.outputs["Geometry"], smat.inputs["Geometry"])
    inst = n.new("GeometryNodeInstanceOnPoints")
    node_links.new(inp.outputs[0], inst.inputs["Points"])
    node_links.new(smat.outputs[0], inst.inputs["Instance"])
    node_links.new(inst.outputs["Instances"], out.inputs[0])
    mod = grain_obj.modifiers.new("GPU grain instances", "NODES")
    mod.node_group = group
    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, float(meta.get("floor_z", -0.002))))
    bpy.context.object.name = "Studio ground beneath simulation bed"
    bpy.context.object.data.materials.append(floor)

    base_path = np.asarray(poses[:, 0, :3])
    start_base = base_path[0]
    final_base = base_path[-1]
    center = (start_base + final_base) * 0.5
    travel = float(np.linalg.norm(final_base[:2] - start_base[:2]))
    span = max(0.90, travel + 0.7)
    for name, offset, power, size, color in [
        ("Warm grazing key", (-0.45, 0.65, 0.18), 22, 0.13, (1, 0.84, 0.63)),
        ("Cool broad fill", (0.1, -0.75, 1.0), 22, 0.8, (0.64, 0.78, 1)),
        ("Rear strip", (0.55, 0.45, 0.75), 55, 0.45, (0.68, 0.86, 1)),
    ]:
        lamp = bpy.data.lights.new(name, "AREA")
        lamp.energy = power
        lamp.shape = "DISK"
        lamp.size = size
        lamp.color = color
        obj = bpy.data.objects.new(name, lamp)
        scene.collection.objects.link(obj)
        obj.location = Vector((float(center[0]), float(center[1]), 0)) + Vector(offset)
        aim(obj, (float(center[0]), float(center[1]), 0.12))

    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.name = "Measured-motion camera"
    camera.data.lens = 55
    camera.data.clip_start = 0.002
    camera.data.clip_end = 200
    scene.camera = camera
    # Half-speed XY camera tracking keeps the robot large while retaining visible
    # forward progress against the terrain and existing footprint trail.
    if args.shot == "hero":
        camera.location = (
            float(center[0]) + 0.50 * span,
            float(center[1]) - 1.50 * span,
            0.60 * span,
        )
        aim(camera, (float(center[0]), float(center[1]), 0.14))
        camera.data.lens = 52
    elif args.shot == "top":
        camera.location = (float(center[0]), float(center[1]) - 0.15, 1.65 * span)
        aim(camera, (float(center[0]), float(center[1]), 0))
        camera.data.lens = 48

    records = []
    for frame in render_frames:
        if args.skip_existing and (output / f"frame-{frame:04d}.png").exists():
            existing = bpy.data.images.load(
                str(output / f"frame-{frame:04d}.png"), check_existing=False
            )
            try:
                if tuple(existing.size) != tuple(args.resolution) or not existing.has_data:
                    raise ValueError("Existing image is corrupt or uses a different resolution")
            finally:
                bpy.data.images.remove(existing)
            continue
        grain_mesh.vertices.foreach_set("co", np.asarray(particles[frame]).reshape(-1))
        grain_mesh.update()
        for obj, pose in zip(robot, poses[frame]):
            transform = Matrix(np.asarray(pose[3:]).reshape(3, 3).tolist()).to_4x4()
            transform.translation = Vector(pose[:3])
            obj.matrix_world = transform
        for obj, position in zip(pads, feet[frame]):
            obj.location = position
        if args.shot == "hero":
            target = np.asarray(center).copy()
            target[:2] = 0.5 * base_path[frame, :2] + 0.5 * center[:2]
            target[2] = 0.14
            camera.location = Vector(target) + Vector((0.38, -1.32, 0.39))
            aim(camera, target)
            camera.data.lens = 55
        elif args.shot == "contact":
            # Track torso translation in XY, not swing-foot height, so lift and
            # touchdown remain legible rather than locked to the camera center.
            target = np.asarray(base_path[frame]).copy()
            target[:2] += np.asarray(feet[0, args.foot, :2]) - base_path[0, :2]
            target[2] = float(meta["surface_z"]) + 0.025
            camera.location = Vector(target) + Vector((0.22, -0.32, 0.18))
            aim(camera, target)
            camera.data.lens = 62
            camera.data.dof.use_dof = True
            camera.data.dof.focus_distance = (camera.location - Vector(target)).length
            camera.data.dof.aperture_fstop = 8
        elif args.shot == "trail":
            target = (np.asarray(feet[0, 3]) + np.asarray(feet[-1, 3])) * 0.5
            target[2] = float(meta["surface_z"])
            camera.location = Vector(target) + Vector((-0.34, -0.36, 0.28))
            aim(camera, target)
            camera.data.lens = 58
            camera.data.dof.use_dof = False
        scene.frame_set(frame + 1)
        destination = output / f"frame-{frame:04d}.png"
        scene.render.filepath = str(destination)
        t = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        records.append(
            {
                "frame": frame,
                "source_time": frame / scene.render.fps,
                "render_seconds": time.perf_counter() - t,
                "path": destination.name,
            }
        )
        (output / "render-evidence.json").write_text(
            json.dumps(
                {
                    "shot": args.shot,
                    "samples": args.samples,
                    "resolution": args.resolution,
                    "source_run": args.run.name,
                    "particles": particles.shape[1],
                    "rendered_grain_radius_m": radius,
                    "robot_geometry": geometry_path.name,
                    "device": args.device,
                    "device_selection": device_selection,
                    "frames_rendered_in_this_invocation": records,
                },
                indent=2,
            )
            + "\n"
        )
        print("RENDERED", json.dumps(records[-1]), flush=True)
    if args.save_blend:
        blend_path = output / f"{args.shot}-scene.blend"
        if blend_path.exists():
            raise FileExistsError("Refusing to overwrite an existing Blender scene")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))


if __name__ == "__main__":
    main()
