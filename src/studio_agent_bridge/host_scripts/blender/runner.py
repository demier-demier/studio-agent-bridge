"""Executed only inside Blender. Read one JSON job and emit measured results."""

import json
import runpy
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector


def asset_objects():
    collection = bpy.data.collections.get("InspectionRover")
    return list(collection.all_objects) if collection else list(bpy.context.scene.objects)


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


def inspect_scene():
    bpy.context.view_layer.update()
    objects = sorted(bpy.context.scene.objects, key=lambda obj: obj.name)
    asset = asset_objects()
    meshes = [obj for obj in objects if obj.type == "MESH"]
    return {
        "object_count": len(objects),
        "mesh_count": len(meshes),
        "asset_mesh_count": sum(obj.type == "MESH" for obj in asset),
        "object_names": [obj.name for obj in objects],
        "modifier_count": sum(len(obj.modifiers) for obj in objects),
        "material_count": len(bpy.data.materials),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "polygons": sum(len(obj.data.polygons) for obj in meshes),
        "unit_system": bpy.context.scene.unit_settings.system,
        "unit_scale": bpy.context.scene.unit_settings.scale_length,
        "asset_bounds_m": bounds(asset),
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


def export_assets(directory, formats, stem):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in asset_objects():
        obj.select_set(True)
    outputs = []
    if "glb" in formats:
        path = directory / (stem + ".glb")
        bpy.ops.export_scene.gltf(
            filepath=str(path),
            export_format="GLB",
            use_selection=True,
            export_apply=True,
            export_animations=False,
            export_yup=True,
        )
        outputs.append({"kind": "glb", "path": str(path)})
    if "fbx" in formats:
        path = directory / (stem + ".fbx")
        bpy.ops.export_scene.fbx(
            filepath=str(path),
            use_selection=True,
            object_types={"MESH", "EMPTY"},
            use_mesh_modifiers=True,
            mesh_smooth_type="FACE",
            add_leaf_bones=False,
            bake_anim=False,
            axis_forward="-Z",
            axis_up="Y",
            apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_UNITS",
            path_mode="AUTO",
        )
        outputs.append({"kind": "fbx", "path": str(path)})
    return outputs


def setup_render():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 900
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
        setup_render()
        bpy.context.scene.render.filepath = str(directory / "preview.png")
    if action not in {"inspect", "verify_export"}:
        path = directory / (stem + ".blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True, check_existing=False)
        outputs.append({"kind": "blend", "path": str(path)})
    if action in {"create_demo", "export_asset"}:
        outputs.extend(export_assets(directory, job.get("export_formats", ["glb", "fbx"]), stem))
    if job.get("render"):
        path = directory / "preview.png"
        bpy.ops.render.render(write_still=True)
        outputs.append({"kind": "png", "path": str(path)})
    return {
        "status": "ok",
        "blender_version": bpy.app.version_string,
        "scene": inspect_scene(),
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
