"""Executed only inside Blender. Read one JSON job and emit measured results."""

import importlib.util
import json
import runpy
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

_environment_spec = importlib.util.spec_from_file_location(
    "studio_environment_export", Path(__file__).with_name("environment_export.py")
)
environment = importlib.util.module_from_spec(_environment_spec)
_environment_spec.loader.exec_module(environment)


def asset_objects(collection_name=None, include_presentation=False):
    return environment.export_selection(bpy, collection_name, include_presentation)[0]


def bounds(objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    scale = bpy.context.scene.unit_settings.scale_length
    points = []
    for obj in objects:
        if obj.type == "MESH":
            evaluated = obj.evaluated_get(depsgraph)
            points.extend(
                (evaluated.matrix_world @ Vector(corner)) * scale for corner in evaluated.bound_box
            )
    if not points:
        return {"min": [0.0] * 3, "max": [0.0] * 3, "dimensions": [0.0] * 3}
    minimum = [min(point[axis] for point in points) for axis in range(3)]
    maximum = [max(point[axis] for point in points) for axis in range(3)]
    return {
        "min": minimum,
        "max": maximum,
        "dimensions": [maximum[i] - minimum[i] for i in range(3)],
    }


def inspect_scene(collection_name=None, include_presentation=False):
    bpy.context.view_layer.update()
    objects = sorted(bpy.context.scene.objects, key=lambda obj: obj.name)
    asset = asset_objects(collection_name, include_presentation)
    instances = environment.mesh_instances(bpy, asset)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    return {
        "object_count": len(objects),
        "mesh_count": len(meshes),
        "asset_mesh_count": len(instances),
        "export_selection": environment.export_selection(
            bpy, collection_name, include_presentation
        )[1],
        "object_names": [obj.name for obj in objects],
        "modifier_count": sum(len(obj.modifiers) for obj in objects),
        "material_count": len(bpy.data.materials),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "polygons": sum(len(obj.data.polygons) for obj in meshes),
        "unit_system": bpy.context.scene.unit_settings.system,
        "unit_scale": bpy.context.scene.unit_settings.scale_length,
        "asset_bounds_m": environment.measured_bounds(bpy, instances),
        "frame_range": [bpy.context.scene.frame_start, bpy.context.scene.frame_end],
        "animated_objects": [
            obj.name for obj in objects if obj.animation_data and obj.animation_data.action
        ],
        "render_settings": {
            "engine": bpy.context.scene.render.engine,
            "resolution": [
                bpy.context.scene.render.resolution_x,
                bpy.context.scene.render.resolution_y,
            ],
            "camera": bpy.context.scene.camera.name if bpy.context.scene.camera else None,
        },
        "objects": [
            {
                "name": obj.name,
                "type": obj.type,
                "dimensions_m": [
                    value * bpy.context.scene.unit_settings.scale_length for value in obj.dimensions
                ],
                "parent": obj.parent.name if obj.parent else None,
                "modifiers": [{"name": mod.name, "type": mod.type} for mod in obj.modifiers],
                "materials": [
                    slot.material.name if slot.material else None for slot in obj.material_slots
                ],
            }
            for obj in objects
        ],
    }


def import_asset(path):
    if path.suffix.lower() in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif path.suffix.lower() == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path), use_custom_normals=True)
    else:
        raise ValueError("Unsupported asset format")


def export_assets(directory, formats, stem, collection_name=None, include_presentation=False):
    old_selection = list(bpy.context.selected_objects)
    old_active = bpy.context.view_layer.objects.active
    selected = asset_objects(collection_name, include_presentation)
    if not any(obj.type == "MESH" or obj.instance_type == "COLLECTION" for obj in selected):
        raise ValueError("Export selection contains no mesh geometry")
    outputs = []
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected:
            obj.select_set(True)
        if "glb" in formats:
            path = directory / (stem + ".glb")
            checks = environment.export_scene_glb(bpy, path, selected, include_presentation)
            check_path = directory / (stem + ".glb.checks.json")
            check_path.write_text(json.dumps(checks, indent=2, allow_nan=False), encoding="utf-8")
            outputs.append({"kind": "glb", "path": str(path)})
            outputs.append({"kind": "glb-checks", "path": str(check_path)})
        if "fbx" in formats:
            path = directory / (stem + ".fbx")
            bpy.ops.export_scene.fbx(
                filepath=str(path),
                use_selection=True,
                object_types={"MESH", "EMPTY", "CAMERA", "LIGHT"}
                if include_presentation
                else {"MESH", "EMPTY"},
                use_mesh_modifiers=True,
                mesh_smooth_type="FACE",
                add_leaf_bones=False,
                bake_anim=False,
                axis_forward="-Z",
                axis_up="Y",
                apply_unit_scale=True,
                apply_scale_options="FBX_SCALE_UNITS",
                path_mode="AUTO",
                use_custom_props=True,
            )
            outputs.append({"kind": "fbx", "path": str(path)})
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in old_selection:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = old_active
    return outputs


def setup_render(settings=None):
    settings = settings or {}
    engine = settings.get("engine", "CYCLES")
    resolution = settings.get("resolution", [1200, 900])
    samples = settings.get("samples", 32)
    if (
        engine not in {"CYCLES", "BLENDER_EEVEE"}
        or not isinstance(resolution, list)
        or len(resolution) != 2
        or any(type(v) is not int or not 64 <= v <= 3840 for v in resolution)
        or resolution[0] * resolution[1] > 8294400
        or type(samples) is not int
        or not 1 <= samples <= 256
    ):
        raise ValueError(
            "Invalid render settings: engine, 64–3840 dimensions (max 8.3 MP), samples 1–256"
        )
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = resolution[0]
    scene.render.resolution_y = resolution[1]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    if scene.camera is None:
        raise ValueError("The scene has no active camera. Add one before requesting a render.")


def main(job):
    directory = Path(job["output_dir"])
    action = job["action"]
    if job.get("input_blend"):
        bpy.ops.wm.open_mainfile(filepath=job["input_blend"], load_ui=False, use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.unit_settings.system = "METRIC"
        bpy.context.scene.unit_settings.scale_length = 1.0
    outputs = []
    collection_name = job.get("export_collection")
    include_presentation = job.get("include_presentation", False)
    stem = "inspection_rover" if action == "create_demo" else "scene"
    if action == "create_demo":
        demo_path = Path(__file__).with_name("inspection_rover.py")
        runpy.run_path(str(demo_path), run_name="__main__")
    elif action in {"import_asset", "verify_export"}:
        import_asset(Path(job["asset_path"]))
    elif action == "run_script":
        runpy.run_path(
            job["script_path"],
            init_globals={
                "STUDIO_PARAMETERS": job.get("script_parameters", {}),
                "STUDIO_OUTPUT_DIR": str(directory),
            },
            run_name="__main__",
        )
    elif action not in {"inspect", "export_asset", "render"}:
        raise ValueError("Unknown Blender operation")
    if job.get("render"):
        setup_render(job.get("render_settings"))
        bpy.context.scene.render.filepath = str(directory / "preview.png")
    if action not in {"inspect", "verify_export"}:
        path = directory / (stem + ".blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True, check_existing=False)
        outputs.append({"kind": "blend", "path": str(path)})
    if action in {"create_demo", "export_asset"}:
        outputs.extend(
            export_assets(
                directory,
                job.get("export_formats", ["glb", "fbx"]),
                stem,
                collection_name,
                include_presentation,
            )
        )
        if job.get("environment_manifest", action == "export_asset"):
            manifest, environment_outputs = environment.export_environment(
                directory,
                collection_name=collection_name,
                include_presentation=include_presentation,
                split_assets=job.get("split_assets", False),
                max_assets=job.get("max_assets", 256),
                max_objects=job.get("max_objects", 5000),
            )
            outputs.extend(environment_outputs)
    if job.get("render"):
        path = directory / "preview.png"
        bpy.ops.render.render(write_still=True)
        outputs.append({"kind": "png", "path": str(path)})
    return {
        "status": "ok",
        "blender_version": bpy.app.version_string,
        "scene": inspect_scene(collection_name, include_presentation),
        "outputs": outputs,
    }


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    job = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    try:
        result = main(job)
    except Exception as error:
        result = {"status": "error", "error": str(error), "traceback": traceback.format_exc()}
        Path(job["result_path"]).write_text(json.dumps(result, indent=2), encoding="utf-8")
        raise
    Path(job["result_path"]).write_text(json.dumps(result, indent=2), encoding="utf-8")
