"""Native Unreal Python entry point. Executed by UnrealEditor-Cmd, not CPython."""

import json
import math
import os
import re
import runpy
import sys
import traceback
from pathlib import Path

import unreal


def vector(v):
    return [round(float(v.x), 6), round(float(v.y), 6), round(float(v.z), 6)]


def package_file(project, asset_path, extension=".uasset"):
    package = asset_path.split(".")[0]
    return project.parent / "Content" / (package.removeprefix("/Game/") + extension)


def native_file(path, kind):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Native file missing or empty: " + path.name)
    return {"path": str(path), "kind": kind}


def save(asset):
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
        raise RuntimeError("Failed to save native asset " + asset.get_path_name())


def inspect_assets(paths):
    records = []
    for path in paths:
        asset = unreal.load_asset(path)
        if asset is None:
            raise RuntimeError("Saved asset cannot be loaded: " + path)
        record = {"path": asset.get_path_name(), "class": asset.get_class().get_name()}
        if isinstance(asset, unreal.StaticMesh):
            bounds = asset.get_bounding_box()
            record.update(
                {
                    "bounds_min_cm": vector(bounds.min),
                    "bounds_max_cm": vector(bounds.max),
                    "size_cm": vector(bounds.max - bounds.min),
                    "triangles_lod0": asset.get_num_triangles(0),
                    "lods": asset.get_num_lods(),
                    "material_slots": len(asset.get_editor_property("static_materials")),
                }
            )
        records.append(record)
    return records


def actor_inventory():
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    return [
        {
            "name": actor.get_actor_label(),
            "class": actor.get_class().get_name(),
            "location_cm": vector(actor.get_actor_location()),
            "scale": vector(actor.get_actor_scale3d()),
        }
        for actor in actors
    ]


def inspect_sequence(sequence, world):
    bindings = []
    for binding in sequence.get_bindings():
        objects = sequence.locate_bound_objects(binding, world)
        tracks = []
        for track in binding.get_tracks():
            sections = []
            for section in track.get_sections():
                channels = section.get_all_channels()
                sections.append(
                    {
                        "channels": len(channels),
                        "keys_per_channel": [len(channel.get_keys()) for channel in channels],
                    }
                )
            tracks.append({"class": track.get_class().get_name(), "sections": sections})
        bindings.append(
            {
                "name": binding.get_name(),
                "resolved_objects": [obj.get_name() for obj in objects],
                "tracks": tracks,
            }
        )
    cuts = []
    for track in sequence.get_tracks():
        if isinstance(track, unreal.MovieSceneCameraCutTrack):
            for section in track.get_sections():
                binding = sequence.resolve_binding_id(section.get_camera_binding_id())
                objects = sequence.locate_bound_objects(binding, world)
                cuts.append({"resolved_camera_objects": [obj.get_name() for obj in objects]})
    if not bindings or not cuts or not all(cut["resolved_camera_objects"] for cut in cuts):
        raise RuntimeError(
            "Persisted camera sequence does not resolve its camera in the loaded map"
        )
    return {
        "path": sequence.get_path_name(),
        "fps": sequence.get_display_rate().numerator,
        "frames": sequence.get_playback_end() - sequence.get_playback_start(),
        "bindings": bindings,
        "camera_cuts": cuts,
    }


def create_project(project):
    path = "/Game/StudioAgentBridge/Maps/Bootstrap"
    world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
    if not unreal.EditorLoadingAndSavingUtils.save_map(world, path):
        raise RuntimeError("Bootstrap map could not be saved")
    return {
        "summary": "Native Unreal project initialized and bootstrap map saved.",
        "verification": {
            "passed": True,
            "engine_version": unreal.SystemLibrary.get_engine_version(),
            "world_class": world.get_class().get_name(),
            "python_api_loaded": True,
            "map": path,
        },
        "files": [native_file(package_file(project, path, ".umap"), "unreal-level")],
    }


def import_asset(project, parameters):
    task = unreal.AssetImportTask()
    task.filename = parameters["source"]
    task.destination_path = parameters["destination"]
    task.automated = True
    task.replace_existing = False
    task.save = True
    options = unreal.FbxImportUI()
    options.automated_import_should_detect_type = False
    options.import_as_skeletal = False
    options.import_mesh = True
    options.import_materials = True
    options.import_textures = True
    options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
    options.static_mesh_import_data.combine_meshes = False
    options.static_mesh_import_data.convert_scene = True
    options.static_mesh_import_data.convert_scene_unit = True
    options.static_mesh_import_data.transform_vertex_to_absolute = True
    options.static_mesh_import_data.import_uniform_scale = 1.0
    task.options = options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = list(task.imported_object_paths)
    if not paths:
        raise RuntimeError("AssetImportTask returned no imported objects")
    records = inspect_assets(paths)
    meshes = [record for record in records if record["class"] == "StaticMesh"]
    if not meshes or any(record["triangles_lod0"] <= 0 for record in meshes):
        raise RuntimeError("Import produced no nonempty static meshes")
    lower = [min(record["bounds_min_cm"][i] for record in meshes) for i in range(3)]
    upper = [max(record["bounds_max_cm"][i] for record in meshes) for i in range(3)]
    total = [round(b - a, 6) for a, b in zip(lower, upper)]
    expected = parameters.get("expected_size_cm")
    scale_check = None
    if expected is not None:
        if len(expected) != 3 or any(
            not math.isfinite(float(n)) or float(n) <= 0 for n in expected
        ):
            raise ValueError("expected_size_cm must contain three positive finite values")
        scale_check = all(
            abs(actual - float(want)) <= max(0.5, float(want) * 0.03)
            for actual, want in zip(sorted(total), sorted(expected))
        )
        if not scale_check:
            raise RuntimeError(
                "FBX import dimensions differ from expected centimeter bounds: " + repr(total)
            )
    files = []
    for path in unreal.EditorAssetLibrary.list_assets(
        parameters["destination"], recursive=True, include_folder=False
    ):
        asset = unreal.load_asset(path)
        if asset is not None:
            save(asset)
            files.append(native_file(package_file(project, path), "unreal-asset"))
    return {
        "summary": "FBX imported into native Unreal assets; geometry and saved packages inspected.",
        "verification": {
            "passed": True,
            "assets": records,
            "mesh_count": len(meshes),
            "aggregate_size_cm": total,
            "expected_size_cm": expected,
            "scale_check": scale_check,
            "unit_policy": "Blender meters stored as FBX centimeters; Unreal convert_scene_unit=True; uniform scale=1",
        },
        "files": files,
        "limitations": [
            "FBX preserves geometry/material slots; procedural modifiers and Blender node graphs remain editable in the .blend source, not in Unreal."
        ],
    }


def material(project, name, color, metallic, roughness):
    folder = "/Game/StudioAgentBridge/Materials"
    path = folder + "/" + name
    asset = unreal.load_asset(path)
    if asset is None:
        asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, folder, unreal.Material, unreal.MaterialFactoryNew()
        )
        base = unreal.MaterialEditingLibrary.create_material_expression(
            asset, unreal.MaterialExpressionConstant3Vector, -500, 0
        )
        base.constant = unreal.LinearColor(*color, 1)
        unreal.MaterialEditingLibrary.connect_material_property(
            base, "", unreal.MaterialProperty.MP_BASE_COLOR
        )
        for value, prop, y in (
            (metallic, unreal.MaterialProperty.MP_METALLIC, 150),
            (roughness, unreal.MaterialProperty.MP_ROUGHNESS, 300),
        ):
            node = unreal.MaterialEditingLibrary.create_material_expression(
                asset, unreal.MaterialExpressionConstant, -500, y
            )
            node.r = value
            unreal.MaterialEditingLibrary.connect_material_property(node, "", prop)
        unreal.MaterialEditingLibrary.recompile_material(asset)
    save(asset)
    return asset, native_file(package_file(project, path), "unreal-material")


def build_scene(project, parameters):
    world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    files = []
    body_mat, body_file = material(project, "M_CeramicBlue", (0.025, 0.23, 0.42), 0.6, 0.32)
    floor_mat, floor_file = material(project, "M_Stage", (0.045, 0.055, 0.075), 0.15, 0.64)
    files.extend([body_file, floor_file])

    def spawn(klass, name, location, rotation=(0, 0, 0)):
        actor = subsystem.spawn_actor_from_class(
            klass, unreal.Vector(*location), unreal.Rotator(*rotation)
        )
        if actor is None:
            raise RuntimeError("Could not spawn actor " + name)
        actor.set_actor_label(name)
        return actor

    paths = parameters.get("asset_paths") or unreal.EditorAssetLibrary.list_assets(
        "/Game/StudioAgentBridge/Imports", recursive=True, include_folder=False
    )
    mesh_records = []
    for path in paths:
        mesh = unreal.load_asset(path)
        if not isinstance(mesh, unreal.StaticMesh):
            continue
        actor = spawn(unreal.StaticMeshActor, "Asset_" + mesh.get_name(), (0, 0, 0))
        actor.static_mesh_component.set_static_mesh(mesh)
        if actor.static_mesh_component.get_num_materials() == 0:
            actor.static_mesh_component.set_material(0, body_mat)
        mesh_records.append({"asset": mesh.get_path_name(), "actor": actor.get_actor_label()})
    if not mesh_records:
        raise RuntimeError("Import at least one static mesh before building the scene")
    floor = spawn(unreal.StaticMeshActor, "Stage_Floor", (0, 0, -7))
    floor.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cube"))
    floor.static_mesh_component.set_material(0, floor_mat)
    floor.set_actor_scale3d(unreal.Vector(8, 8, 0.1))
    key = spawn(unreal.DirectionalLight, "Light_Key", (0, 0, 500), (-40, -35, 0))
    key.light_component.set_intensity(4.0)
    fill = spawn(unreal.PointLight, "Light_Fill", (-150, 250, 280))
    fill.light_component.set_intensity(22000)
    fill.light_component.set_attenuation_radius(1000)
    spawn(unreal.SkyLight, "Light_Sky", (0, 0, 300))
    camera = spawn(unreal.CineCameraActor, "Camera_Inspection", (400, -400, 250), (-20, 135, 0))
    camera.get_cine_camera_component().set_editor_property("current_focal_length", 35.0)
    # Bind possessables only after the world has its permanent package name.
    # Binding against an Untitled world can appear valid until a fresh process
    # loads the saved map, at which point the camera no longer resolves.
    level_path = "/Game/StudioAgentBridge/Maps/" + parameters["name"]
    if not unreal.EditorLoadingAndSavingUtils.save_map(world, level_path):
        raise RuntimeError("Initial level save failed")
    sequence_info = None
    if parameters.get("sequence", True):
        seq_name = parameters["name"] + "_CameraMove"
        seq_path = "/Game/StudioAgentBridge/Sequences/" + seq_name
        sequence = unreal.load_asset(seq_path)
        if sequence is not None:
            raise RuntimeError(
                "Sequence already exists; use a new scene name to preserve existing edits"
            )
        sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            seq_name,
            "/Game/StudioAgentBridge/Sequences",
            unreal.LevelSequence,
            unreal.LevelSequenceFactoryNew(),
        )
        sequence.set_display_rate(unreal.FrameRate(24, 1))
        sequence.set_playback_start(0)
        sequence.set_playback_end(96)
        binding = sequence.add_possessable(camera)
        track = binding.add_track(unreal.MovieScene3DTransformTrack)
        section = track.add_section()
        section.set_range(0, 96)
        channels = section.get_all_channels()
        if len(channels) != 9:
            raise RuntimeError("Unexpected transform channel count")
        start = [400, -400, 250, 0, -20, 135, 1, 1, 1]
        end = [300, -300, 220, 0, -20, 135, 1, 1, 1]
        for channel, first, last in zip(channels, start, end):
            channel.add_key(unreal.FrameNumber(0), first)
            channel.add_key(unreal.FrameNumber(95), last)
        cut_track = sequence.add_track(unreal.MovieSceneCameraCutTrack)
        cut_section = cut_track.add_section()
        cut_section.set_range(0, 96)
        cut_section.set_camera_binding_id(sequence.get_binding_id(binding))
        save(sequence)
        sequence_actor = spawn(unreal.LevelSequenceActor, "Sequence_Inspection", (0, 0, 0))
        sequence_actor.set_sequence(sequence)
        files.append(native_file(package_file(project, seq_path), "unreal-sequence"))
        sequence_info = {
            "path": seq_path,
            "fps": 24,
            "frames": 96,
            "camera_binding": binding.get_name(),
            "transform_channels": len(channels),
            "keys_per_channel": [len(channel.get_keys()) for channel in channels],
            "camera_cut_track": True,
        }
    if not unreal.EditorLoadingAndSavingUtils.save_map(world, level_path):
        raise RuntimeError("Level save failed")
    files.append(native_file(package_file(project, level_path, ".umap"), "unreal-level"))
    reopened_world = unreal.EditorLoadingAndSavingUtils.load_map(
        str(package_file(project, level_path, ".umap"))
    )
    if reopened_world is None:
        raise RuntimeError("Saved map could not be reopened")
    if sequence_info:
        sequence_info["reopened_binding_check"] = inspect_sequence(sequence, reopened_world)
    actors = actor_inventory()
    required = {"Stage_Floor", "Light_Key", "Light_Fill", "Light_Sky", "Camera_Inspection"} | {
        record["actor"] for record in mesh_records
    }
    if not required.issubset({actor["name"] for actor in actors}):
        raise RuntimeError("Reopened level did not retain required actors")
    config_file = project.parent / "Config" / "DefaultEngine.ini"
    config_file.write_text(
        re.sub(
            r"(?m)^EditorStartupMap=.*$", "EditorStartupMap=" + level_path, config_file.read_text()
        )
    )
    files.append(native_file(config_file, "unreal-config"))
    return {
        "summary": "Native level saved and reopened with imported geometry, editable materials, lights and a camera.",
        "verification": {
            "passed": True,
            "level": level_path,
            "actors": actors,
            "imported_mesh_actors": mesh_records,
            "sequence": sequence_info,
            "reopened": True,
        },
        "files": files,
        "limitations": [
            "Camera transform animation and a camera-cut track are authored; Movie Render Queue output is not included in this preset.",
            "Headless verification checks scene structure; final lighting and appearance need a rendered editor review.",
        ],
    }


def inspect(project, parameters):
    level = parameters.get("level_path", "/Game/StudioAgentBridge/Maps/Bootstrap")
    file = package_file(project, level, ".umap")
    world = unreal.EditorLoadingAndSavingUtils.load_map(str(file)) if file.is_file() else None
    if world is None:
        raise RuntimeError("Requested native level could not be opened")
    sequence_report = None
    files = [native_file(file, "unreal-level")]
    if parameters.get("sequence_path"):
        sequence = unreal.load_asset(parameters["sequence_path"])
        if not isinstance(sequence, unreal.LevelSequence):
            raise RuntimeError("Requested sequence could not be loaded")
        sequence_report = inspect_sequence(sequence, world)
        files.append(
            native_file(package_file(project, parameters["sequence_path"]), "unreal-sequence")
        )
    paths = list(
        unreal.EditorAssetLibrary.list_assets(
            "/Game/StudioAgentBridge", recursive=True, include_folder=False
        )
    )
    return {
        "summary": "Native saved level and assets inspected in a fresh editor process.",
        "verification": {
            "passed": True,
            "engine_version": unreal.SystemLibrary.get_engine_version(),
            "level": level,
            "actors": actor_inventory(),
            "assets": inspect_assets(paths),
            "sequence": sequence_report,
        },
        "files": files,
    }


def main():
    request = json.loads(Path(os.environ["STUDIO_AGENT_UNREAL_REQUEST"]).read_text())
    response = Path(request["response"])
    project = Path(request["project"])
    action = request["action"]
    parameters = request["parameters"]
    result = {"schema": 1, "success": False, "action": action}
    try:
        if parameters.get("environment_manifest") and action in {
            "build_scene",
            "import_asset",
            "inspect",
        }:
            sys.path.insert(0, str(Path(__file__).parent))
            import environment

            if request.get("editor_session") and parameters.get("render_output"):
                parameters["_defer_capture"] = True
            result.update(
                environment.run(
                    project,
                    parameters,
                    inspect_only=action == "inspect",
                    import_only=action == "import_asset",
                )
            )
        elif action == "create_project":
            result.update(create_project(project))
        elif action == "import_asset":
            result.update(import_asset(project, parameters))
        elif action == "build_scene":
            result.update(build_scene(project, parameters))
        elif action == "inspect":
            result.update(inspect(project, parameters))
        elif action == "run_script":
            runpy.run_path(
                parameters["script_path"],
                init_globals={"STUDIO_REQUEST": request},
                run_name="__main__",
            )
            result.update(
                {
                    "summary": "Trusted script completed; custom semantic correctness is not independently verified.",
                    "verification": {
                        "passed": False,
                        "script_completed": True,
                        "engine_version": unreal.SystemLibrary.get_engine_version(),
                    },
                    "files": [
                        native_file(Path(path), "custom-output")
                        for path in parameters.get("expected_artifacts", [])
                    ],
                }
            )
        else:
            raise ValueError("Unsupported native operation")
        result["success"] = True
    except Exception as error:
        result.update(
            {
                "summary": str(error),
                "error": type(error).__name__,
                "traceback": traceback.format_exc(),
            }
        )
    if result["success"] and parameters.get("_defer_capture"):
        # Full editor ticks are required for a populated scene/render state.
        # A commandlet can export a valid but black PNG even with Metal loaded.
        try:
            unreal.AutomationLibrary.finish_loading_before_screenshot()
            unreal.EditorPythonScripting.set_keep_python_script_alive(True)
            world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
            prepared_capture = environment.prepare_capture(world, parameters)
        except Exception as error:
            result.update(success=False, summary=str(error), traceback=traceback.format_exc())
            response.write_text(json.dumps(result, indent=2) + "\n")
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            return
        pending = {"frames": 0, "handle": None}

        def capture_after_frames(delta_seconds):
            pending["frames"] += 1
            if pending["frames"] < 120:
                return
            unreal.unregister_slate_post_tick_callback(pending["handle"])
            try:
                world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
                result["verification"]["render"] = environment.capture(
                    world, parameters, prepared_capture
                )
                result["verification"]["render"]["warmupFrames"] = pending["frames"]
                result["files"].append(native_file(Path(parameters["render_output"]), "render"))
            except Exception as error:
                result.update(success=False, summary=str(error), traceback=traceback.format_exc())
            response.write_text(json.dumps(result, indent=2) + "\n")
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)

        pending["handle"] = unreal.register_slate_post_tick_callback(capture_after_frames)
        return
    response.write_text(json.dumps(result, indent=2) + "\n")
    if request.get("editor_session"):
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)
        return
    if not result["success"]:
        raise RuntimeError(result["summary"])


main()
