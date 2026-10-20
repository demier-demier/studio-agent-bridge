"""Manifest-driven native environment construction; imported by dispatch in Unreal."""

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import unreal
from environment_contract import (
    compare_bounds,
    guard_map_creation,
    local_file,
    transform,
    validate_manifest,
)


def vector(value):
    return [float(value.x), float(value.y), float(value.z)]


def package_file(project, path, extension=".uasset"):
    return project.parent / "Content" / (path.split(".")[0].removeprefix("/Game/") + extension)


def save(asset):
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
        raise RuntimeError("Could not save native asset " + asset.get_path_name())


def file_record(path, kind):
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("Missing native file " + str(path))
    return {"path": str(path), "kind": kind}


def native_transform(record):
    mapped = transform(record)
    result = unreal.Transform()
    result.translation = unreal.Vector(*mapped["translationCm"])
    result.rotation = unreal.Quat(*mapped["rotationQuaternionXyzw"])
    result.scale3d = unreal.Vector(*mapped["scale"])
    return result


def view_rotation(record):
    """Blender camera/light local -Z forward, +Y up → Unreal +X forward/+Z up."""
    w, x, y, z = record["transform"]["rotationQuaternionWxyz"]
    forward = [-2 * (x * z + w * y), 2 * (y * z - w * x), -(1 - 2 * (x * x + y * y))]
    up = [2 * (x * y - w * z), -(1 - 2 * (x * x + z * z)), 2 * (y * z + w * x)]
    return unreal.MathLibrary.make_rot_from_xz(unreal.Vector(*forward), unreal.Vector(*up))


def spawn(kind, record):
    mapped = transform(record)
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
        kind, unreal.Vector(*mapped["translationCm"])
    )
    if actor is None:
        raise RuntimeError("Could not spawn " + record["name"])
    actor.set_actor_label(record["name"])
    actor.set_actor_transform(native_transform(record), False, True)
    actor.set_actor_hidden_in_game(not record.get("visibleRender", True))
    actor.set_actor_hidden(not record.get("visibleViewport", True))
    actor.set_folder_path("Environment/" + str(record.get("category", "uncategorized")))
    actor.set_editor_property(
        "tags",
        [
            unreal.Name("StudioEnvironment"),
            unreal.Name("source_id:" + record["id"]),
            unreal.Name("category:" + str(record.get("category", "unspecified"))),
        ],
    )
    return actor


def texture_asset(source, folder, name, normal=False, srgb=True):
    task = unreal.AssetImportTask()
    task.filename = str(source)
    task.destination_path = folder
    task.destination_name = name
    task.automated = True
    task.replace_existing = False
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    asset = unreal.load_asset(folder + "/" + name)
    if not isinstance(asset, unreal.Texture2D):
        raise RuntimeError("Texture import failed")
    asset.set_editor_property("srgb", bool(srgb and not normal))
    if normal:
        asset.set_editor_property(
            "compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP
        )
        asset.set_editor_property("flip_green_channel", True)
    save(asset)
    return asset


def make_material(record, root, folder):
    name = record.get("id", record["name"])
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder + "/Materials", unreal.Material, unreal.MaterialFactoryNew()
    )
    if material is None:
        raise RuntimeError("Material already exists or creation failed: " + name)
    pbr = record.get("pbr", {})
    base_color = pbr.get("baseColor", [0.5, 0.5, 0.5, 1])
    roughness = float(pbr.get("roughness", 0.5))
    metallic = float(pbr.get("metallic", 0))
    alpha = float(pbr.get("alpha", base_color[3] if len(base_color) > 3 else 1))
    lib = unreal.MaterialEditingLibrary
    material.set_editor_property("two_sided", bool(record.get("doubleSided", False)))

    def scalar(value, prop, y):
        node = lib.create_material_expression(material, unreal.MaterialExpressionConstant, -300, y)
        node.r = value
        lib.connect_material_property(node, "", prop)

    base = lib.create_material_expression(
        material, unreal.MaterialExpressionConstant3Vector, -300, 0
    )
    base.constant = unreal.LinearColor(*base_color[:3], 1)
    lib.connect_material_property(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
    scalar(metallic, unreal.MaterialProperty.MP_METALLIC, 120)
    scalar(roughness, unreal.MaterialProperty.MP_ROUGHNESS, 240)
    if alpha < 0.999 or "alpha" in record.get("textures", {}):
        material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        material.set_editor_property("two_sided", True)
        scalar(alpha, unreal.MaterialProperty.MP_OPACITY, 360)
    emission = pbr.get("emission", pbr.get("emissionColor", [0, 0, 0]))
    strength = float(pbr.get("emissionStrength", 0))
    if strength > 0:
        glow = lib.create_material_expression(
            material, unreal.MaterialExpressionConstant3Vector, -300, 480
        )
        glow.constant = unreal.LinearColor(*(float(c) * strength for c in emission[:3]), 1)
        lib.connect_material_property(glow, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    texture_paths = []
    for slot, texture in record.get("textures", {}).items():
        properties = {
            "baseColor": unreal.MaterialProperty.MP_BASE_COLOR,
            "normal": unreal.MaterialProperty.MP_NORMAL,
            "roughness": unreal.MaterialProperty.MP_ROUGHNESS,
            "metallic": unreal.MaterialProperty.MP_METALLIC,
            "alpha": unreal.MaterialProperty.MP_OPACITY,
            "emission": unreal.MaterialProperty.MP_EMISSIVE_COLOR,
        }
        if slot not in properties:
            continue
        source = local_file(root, texture["path"], texture["sha256"])
        tex = texture_asset(
            source,
            folder + "/Textures",
            name + "_" + slot,
            normal=slot == "normal",
            srgb=slot in {"baseColor", "emission"} and texture.get("colorSpace", "sRGB") == "sRGB",
        )
        texture_paths.append(tex.get_path_name())
        sample = lib.create_material_expression(
            material, unreal.MaterialExpressionTextureSample, -650, len(texture_paths) * 180
        )
        sample.texture = tex
        if slot == "normal":
            sample.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL
        elif slot in {"roughness", "metallic", "alpha"} or not tex.srgb:
            sample.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR
        output_channel = "RGB"
        if texture.get("channel") == "Alpha":
            output_channel = "A"
        elif slot in {"roughness", "metallic", "alpha"}:
            output_channel = "R"
        lib.connect_material_property(sample, output_channel, properties[slot])
        if slot == "normal":
            normal_strength = texture.get("normalStrength")
            if normal_strength is not None and float(normal_strength) != 1:
                factors = lib.create_material_expression(
                    material, unreal.MaterialExpressionConstant3Vector, -600, 900
                )
                factors.constant = unreal.LinearColor(
                    float(normal_strength), float(normal_strength), 1, 1
                )
                multiply = lib.create_material_expression(
                    material, unreal.MaterialExpressionMultiply, -400, 900
                )
                lib.connect_material_expressions(sample, "RGB", multiply, "A")
                lib.connect_material_expressions(factors, "", multiply, "B")
                normalize = lib.create_material_expression(
                    material, unreal.MaterialExpressionNormalize, -200, 900
                )
                lib.connect_material_expressions(multiply, "", normalize, "VectorInput")
                lib.connect_material_property(normalize, "", properties[slot])
        if slot == "emission" and strength != 1:
            multiply = lib.create_material_expression(
                material, unreal.MaterialExpressionMultiply, -350, 700
            )
            multiply.const_b = strength
            lib.connect_material_expressions(sample, "RGB", multiply, "A")
            lib.connect_material_property(multiply, "", properties[slot])
    lib.recompile_material(material)
    unreal.EditorAssetLibrary.set_metadata_tag(
        material, "StudioEnvironmentPBR", json.dumps(pbr, sort_keys=True)
    )
    save(material)
    return material, {
        "id": name,
        "path": material.get_path_name(),
        "pbr": pbr,
        "textures": texture_paths,
        "native_expression_count": lib.get_num_material_expressions(material),
        "normalStrengthPolicy": "Source strength scales tangent normal X/Y, then normalizes the vector; cross-renderer pixel parity is not claimed.",
    }


def imported_assets(project, manifest, manifest_path, destination, simple_collision):
    materials, materials_report = {}, []
    for record in manifest.get("materials", []):
        asset, report = make_material(record, manifest_path.parent, destination)
        materials[record.get("id", record["name"])] = asset
        materials[record["name"]] = asset
        materials_report.append(report)
    meshes, reports = {}, []
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    for asset in manifest["assets"]:
        task = unreal.AssetImportTask()
        task.filename = str(
            local_file(manifest_path.parent, asset["exportFile"], asset["fileSha256"])
        )
        task.destination_path = destination + "/Meshes"
        task.destination_name = asset["id"]
        task.automated = True
        task.replace_existing = False
        task.save = True
        options = unreal.FbxImportUI()
        options.automated_import_should_detect_type = False
        options.import_as_skeletal = False
        options.import_mesh = True
        options.import_materials = False
        options.import_textures = False
        options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
        options.static_mesh_import_data.combine_meshes = True
        options.static_mesh_import_data.convert_scene = True
        options.static_mesh_import_data.convert_scene_unit = True
        options.static_mesh_import_data.force_front_x_axis = False
        options.static_mesh_import_data.transform_vertex_to_absolute = True
        options.static_mesh_import_data.import_uniform_scale = 1.0
        options.static_mesh_import_data.auto_generate_collision = False
        task.options = options
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        mesh = unreal.load_asset(task.destination_path + "/" + asset["id"])
        if not isinstance(mesh, unreal.StaticMesh) or mesh.get_num_triangles(0) <= 0:
            raise RuntimeError("Imported asset is not a nonempty static mesh: " + asset["id"])
        for index, slot in enumerate(asset.get("materialSlots", [])):
            slot_id = slot.get("materialId", slot.get("name")) if isinstance(slot, dict) else slot
            if slot_id in materials:
                mesh.set_material(index, materials[slot_id])
            elif slot_id is not None:
                raise RuntimeError("Mesh references unknown material: " + str(slot_id))
        if simple_collision:
            subsystem.remove_collisions(mesh)
            subsystem.add_simple_collisions(mesh, unreal.ScriptCollisionShapeType.BOX)
        save(mesh)
        meshes[asset["id"]] = mesh
        bounds = mesh.get_bounding_box()
        reports.append(
            {
                "id": asset["id"],
                "path": mesh.get_path_name(),
                "triangles": mesh.get_num_triangles(0),
                "boundsMinCm": vector(bounds.min),
                "boundsMaxCm": vector(bounds.max),
                "simpleCollisionCount": subsystem.get_simple_collision_count(mesh),
                "materialSlots": [
                    str(x.material_slot_name) for x in mesh.get_editor_property("static_materials")
                ],
                "boundsCheck": compare_bounds(
                    asset["boundsLocal"], vector(bounds.min), vector(bounds.max)
                ),
            }
        )
    return meshes, reports, materials_report


def build_lights_and_cameras(manifest):
    lights, cameras = [], []
    for record in manifest.get("lights", []):
        kind = record.get("type", record.get("lightType", "POINT"))
        cls = {
            "AREA": unreal.RectLight,
            "SUN": unreal.DirectionalLight,
            "SPOT": unreal.SpotLight,
            "POINT": unreal.PointLight,
        }.get(kind)
        if cls is None:
            raise ValueError("Unsupported light type " + kind)
        actor = spawn(cls, record)
        actor.set_actor_rotation(view_rotation(record), False)
        actor.set_actor_scale3d(unreal.Vector(1, 1, 1))
        comp = actor.get_component_by_class(unreal.LightComponent)
        color = record.get("color", [1, 1, 1])
        comp.set_light_color(unreal.LinearColor(*color[:3], 1))
        energy = float(record.get("energy", 100))
        # Blender area/point power is watts, UE local lights use lumens here.
        # 100 lm/W is an explicit artistic transfer, not photometric equivalence.
        if kind != "SUN":
            comp.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
            comp.set_intensity(energy * 100)
            comp.set_attenuation_radius(float(record.get("rangeMeters", 12)) * 100)
        else:
            comp.set_intensity(energy * 1000)
        if kind == "AREA":
            comp.set_source_width(float(record.get("size", 1)) * 100)
            comp.set_source_height(float(record.get("sizeY", record.get("size", 1))) * 100)
        if kind == "SPOT":
            comp.set_outer_cone_angle(math.degrees(float(record.get("spotSize", math.pi / 4))) / 2)
        lights.append(
            {
                "id": record["id"],
                "actor": actor.get_actor_label(),
                "type": kind,
                "nativeIntensity": comp.intensity,
            }
        )
    for record in manifest.get("cameras", []):
        actor = spawn(unreal.CineCameraActor, record)
        actor.set_actor_rotation(view_rotation(record), False)
        actor.set_actor_scale3d(unreal.Vector(1, 1, 1))
        comp = actor.get_cine_camera_component()
        comp.set_editor_property(
            "filmback",
            unreal.CameraFilmbackSettings(
                sensor_width=float(record.get("sensorWidthMm", 36)),
                sensor_height=float(record.get("sensorHeightMm", 20.25)),
            ),
        )
        comp.set_editor_property("current_focal_length", float(record.get("lensMm", 35)))
        cameras.append(
            {
                "id": record["id"],
                "actor": actor.get_actor_label(),
                "focalLengthMm": comp.current_focal_length,
            }
        )
    return lights, cameras


def inspect_environment(manifest, destination):
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    all_actors = subsystem.get_all_level_actors()
    source_actors = {}
    for actor in all_actors:
        for tag in actor.tags:
            if str(tag).startswith("source_id:"):
                source_actors[str(tag).removeprefix("source_id:")] = actor
    observed = []
    refs = Counter()
    errors = []
    for record in manifest["objects"]:
        actor = source_actors.get(record["id"])
        if not isinstance(actor, unreal.StaticMeshActor):
            errors.append("Missing mesh actor " + record["id"])
            continue
        expected = transform(record)
        actual = actor.get_actor_transform()
        position = vector(actual.translation)
        scale = vector(actual.scale3d)
        q = actual.rotation
        quaternion = [q.x, q.y, q.z, q.w]
        dot = abs(sum(a * b for a, b in zip(quaternion, expected["rotationQuaternionXyzw"])))
        mesh = actor.static_mesh_component.static_mesh
        expected_mesh = destination + "/Meshes/" + record["assetId"]
        if mesh is None or mesh.get_path_name().split(".")[0] != expected_mesh:
            errors.append("Wrong shared mesh for " + record["id"])
        else:
            refs[mesh.get_path_name()] += 1
        if max(abs(a - b) for a, b in zip(position, expected["translationCm"])) > 0.02:
            errors.append("Position mismatch " + record["id"])
        if max(abs(a - b) for a, b in zip(scale, expected["scale"])) > 0.0001 or dot < 0.99999:
            errors.append("Rotation or scale mismatch " + record["id"])
        origin, extent = actor.get_actor_bounds(False)
        observed.append(
            {
                "id": record["id"],
                "actor": actor.get_actor_label(),
                "asset": mesh.get_path_name() if mesh else None,
                "translationCm": position,
                "rotationQuaternionXyzw": quaternion,
                "scale": scale,
                "boundsOriginCm": vector(origin),
                "boundsExtentCm": vector(extent),
            }
        )
    if errors:
        raise RuntimeError("; ".join(errors[:12]))
    rig_reports = {"lights": [], "cameras": []}
    for kind, records in ((key, manifest.get(key, [])) for key in rig_reports):
        for record in records:
            actor = source_actors.get(record["id"])
            if actor is None:
                raise RuntimeError("Missing saved rig actor " + record["id"])
            expected = transform(record)
            position = vector(actor.get_actor_location())
            if max(abs(a - b) for a, b in zip(position, expected["translationCm"])) > 0.02:
                raise RuntimeError("Saved rig position mismatch " + record["id"])
            actual_q, expected_q = (
                actor.get_actor_transform().rotation,
                view_rotation(record).quaternion(),
            )
            dot = abs(
                sum(getattr(actual_q, c) * getattr(expected_q, c) for c in ("x", "y", "z", "w"))
            )
            if dot < 0.99999:
                raise RuntimeError("Saved rig orientation mismatch " + record["id"])
            report = {
                "id": record["id"],
                "actor": actor.get_actor_label(),
                "translationCm": position,
            }
            if kind == "cameras":
                component = actor.get_cine_camera_component()
                if abs(component.current_focal_length - float(record.get("lensMm", 35))) > 0.001:
                    raise RuntimeError("Saved camera focal length mismatch")
                report.update(focalLengthMm=component.current_focal_length)
            else:
                component = actor.get_component_by_class(unreal.LightComponent)
                report.update(type=record["type"], nativeIntensity=component.intensity)
            rig_reports[kind].append(report)
    return {
        "objects": observed,
        "objectCount": len(observed),
        "uniqueMeshCount": len(refs),
        "sharedMeshInstances": sum(n - 1 for n in refs.values()),
        "sharedMeshReferences": dict(refs),
        "transformChecksPassed": True,
        "instancingRepresentation": "Individually editable StaticMeshActors reference shared StaticMesh assets; GPU instancing performance is not measured.",
        **rig_reports,
    }


def inspect_resources(manifest, destination):
    """Read the saved native assets again, including material graph inputs."""
    mesh_reports, material_reports = [], []
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    for record in manifest["assets"]:
        mesh = unreal.load_asset(destination + "/Meshes/" + record["id"])
        if not isinstance(mesh, unreal.StaticMesh):
            raise RuntimeError("Missing persisted static mesh " + record["id"])
        bounds = mesh.get_bounding_box()
        slots = mesh.get_editor_property("static_materials")
        actual_materials = [
            x.material_interface.get_path_name() if x.material_interface else None for x in slots
        ]
        for index, material_id in enumerate(record.get("materialSlots", [])):
            if material_id and (
                index >= len(actual_materials)
                or actual_materials[index] is None
                or actual_materials[index].split(".")[0]
                != destination + "/Materials/" + material_id
            ):
                raise RuntimeError("Persisted mesh material assignment mismatch " + record["id"])
        mesh_reports.append(
            {
                "id": record["id"],
                "path": mesh.get_path_name(),
                "triangles": mesh.get_num_triangles(0),
                "boundsMinCm": vector(bounds.min),
                "boundsMaxCm": vector(bounds.max),
                "boundsCheck": compare_bounds(
                    record["boundsLocal"], vector(bounds.min), vector(bounds.max)
                ),
                "simpleCollisionCount": subsystem.get_simple_collision_count(mesh),
                "materialPaths": actual_materials,
            }
        )
    lib = unreal.MaterialEditingLibrary
    for record in manifest.get("materials", []):
        material = unreal.load_asset(destination + "/Materials/" + record["id"])
        if not isinstance(material, unreal.Material):
            raise RuntimeError("Missing persisted material")
        pbr, textures = record.get("pbr", {}), record.get("textures", {})
        checks = {}
        for key, prop in (
            ("metallic", unreal.MaterialProperty.MP_METALLIC),
            ("roughness", unreal.MaterialProperty.MP_ROUGHNESS),
            ("baseColor", unreal.MaterialProperty.MP_BASE_COLOR),
        ):
            node = lib.get_material_property_input_node(material, prop)
            if node is None:
                raise RuntimeError("Missing native material graph input " + key)
            if key in textures:
                if (
                    not isinstance(node, unreal.MaterialExpressionTextureSample)
                    or node.texture is None
                ):
                    raise RuntimeError("Expected native texture input " + key)
                observed = {"texture": node.texture.get_path_name(), "srgb": node.texture.srgb}
            elif key == "baseColor":
                observed = [node.constant.r, node.constant.g, node.constant.b]
                expected = pbr.get(key, [0.5, 0.5, 0.5, 1])[:3]
                if max(abs(a - b) for a, b in zip(observed, expected)) > 1e-5:
                    raise RuntimeError("Native base color mismatch")
            else:
                observed = node.r
                if abs(observed - pbr.get(key, 0 if key == "metallic" else 0.5)) > 1e-5:
                    raise RuntimeError("Native material scalar mismatch " + key)
            checks[key] = observed
        material_reports.append(
            {
                "id": record["id"],
                "path": material.get_path_name(),
                "pbr": pbr,
                "nativeInputs": checks,
                "nativeExpressionCount": lib.get_num_material_expressions(material),
                "doubleSided": material.get_editor_property("two_sided"),
                "passed": True,
                "textures": [
                    {
                        "slot": key,
                        "path": destination + "/Textures/" + record["id"] + "_" + key,
                        "sourceSha256": texture["sha256"],
                        "colorSpace": texture.get("colorSpace"),
                        "normalStrength": texture.get("normalStrength"),
                        "greenChannelFlip": key == "normal",
                    }
                    for key, texture in textures.items()
                ],
            }
        )
    return mesh_reports, material_reports


def run(project, parameters, inspect_only=False, import_only=False):
    manifest_path = Path(parameters["environment_manifest"])
    manifest = validate_manifest(manifest_path)
    destination = parameters["destination"]
    level_path = parameters.get(
        "level_path", destination + "/Maps/" + parameters.get("name", "Environment")
    )
    files = []
    mesh_reports, material_reports, light_reports, camera_reports = [], [], [], []
    if inspect_only:
        world = unreal.EditorLoadingAndSavingUtils.load_map(
            str(package_file(project, level_path, ".umap"))
        )
        if world is None:
            raise RuntimeError("Environment map cannot be reopened")
    else:
        if not import_only:
            guard_map_creation(
                destination,
                level_path,
                package_file(project, level_path, ".umap").exists()
                or unreal.EditorAssetLibrary.does_asset_exist(level_path),
            )
        if unreal.EditorAssetLibrary.does_directory_exist(destination):
            raise RuntimeError(
                "Environment namespace already exists; choose a fresh destination to preserve edits"
            )
        meshes, mesh_reports, material_reports = imported_assets(
            project,
            manifest,
            manifest_path,
            destination,
            bool(parameters.get("simple_collision", True)),
        )
        if import_only:
            world = None
        else:
            world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
            for record in manifest["objects"]:
                actor = spawn(unreal.StaticMeshActor, record)
                actor.static_mesh_component.set_static_mesh(meshes[record["assetId"]])
            light_reports, camera_reports = build_lights_and_cameras(manifest)
            if not unreal.EditorLoadingAndSavingUtils.save_map(world, level_path):
                raise RuntimeError("Environment map save failed")
            world = unreal.EditorLoadingAndSavingUtils.load_map(
                str(package_file(project, level_path, ".umap"))
            )
            if world is None:
                raise RuntimeError("Environment map cannot be reopened")
    inspection = inspect_environment(manifest, destination) if world is not None else {}
    mesh_reports, material_reports = inspect_resources(manifest, destination)
    for path in unreal.EditorAssetLibrary.list_assets(
        destination, recursive=True, include_folder=False
    ):
        package = path.split(".")[0]
        is_map = package == level_path
        candidate = package_file(project, package, ".umap" if is_map else ".uasset")
        if candidate.is_file():
            files.append(file_record(candidate, "unreal-level" if is_map else "unreal-asset"))
    verification = {
        "passed": True,
        "level": level_path if world else None,
        "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "engineVersion": unreal.SystemLibrary.get_engine_version(),
        "reopened": world is not None,
        "meshAssets": mesh_reports,
        "materials": material_reports,
        "lights": light_reports,
        "cameras": camera_reports,
        "coordinatePolicy": "Blender RH meters Z-up → Unreal LH centimeters Z-up: reflect Y; cameras/lights rotate local -Z/+Y to +X/+Z.",
        "navigation": {
            "status": "not-built",
            "reason": "Simple collision is verified independently; a navigation bake and path query are not implied.",
        },
        **inspection,
    }
    if parameters.get("render_output") and not parameters.get("_defer_capture"):
        if parameters.get("rendering") != "gpu":
            raise ValueError("Image capture requires rendering=gpu")
        verification["render"] = capture(world, parameters)
        files.append(file_record(Path(parameters["render_output"]), "render"))
    return {
        "summary": (
            "Native environment assets saved and measured; no level was constructed."
            if import_only
            else "Native environment assets and editable shared-mesh actors saved, reopened and measured."
        ),
        "verification": verification,
        "files": files,
        "limitations": [
            "PBR values and image textures are transferred; arbitrary Blender procedural shader graphs are not equivalent.",
            "Normal texture strength scales tangent X/Y before normalization. Source parent hierarchy is flattened to world transforms and category folders.",
            "Normal textures assume Blender tangent-space OpenGL; Unreal texture green channel is flipped for DirectX. Tangent-space pixel parity is not measured.",
            "Local light watts transfer at 100 lm/W and sun energy at 1000 lux/unit as an explicit artistic approximation.",
            "Box collision is a simple proxy, not an exact collision mesh or a navigation bake.",
            "GPU batching, gameplay, runtime performance and photometric parity require separate validation.",
        ],
    }


def prepare_capture(world, parameters):
    cameras = [
        actor
        for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
        if isinstance(actor, unreal.CineCameraActor)
    ]
    if parameters.get("camera_name"):
        cameras = [
            camera for camera in cameras if camera.get_actor_label() == parameters["camera_name"]
        ]
    if not cameras:
        raise RuntimeError("No native camera available for capture")
    camera = cameras[0]
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = subsystem.spawn_actor_from_class(
        unreal.SceneCapture2D, camera.get_actor_location(), camera.get_actor_rotation()
    )
    comp = actor.get_component_by_class(unreal.SceneCaptureComponent2D)
    comp.set_editor_property("capture_every_frame", True)
    comp.set_editor_property("capture_on_movement", False)
    comp.set_editor_property("always_persist_rendering_state", True)
    comp.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
    comp.set_editor_property("fov_angle", camera.get_cine_camera_component().field_of_view)
    width, height = parameters.get("render_size", [1280, 720])
    target = unreal.RenderingLibrary.create_render_target2d(
        world, width, height, unreal.TextureRenderTargetFormat.RTF_RGBA8
    )
    comp.set_editor_property("texture_target", target)
    exposure = float(parameters.get("render_exposure", -7))
    post = unreal.PostProcessSettings()
    post.set_editor_property("override_auto_exposure_method", True)
    post.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL)
    post.set_editor_property("override_auto_exposure_apply_physical_camera_exposure", True)
    post.set_editor_property("auto_exposure_apply_physical_camera_exposure", False)
    post.set_editor_property("override_auto_exposure_bias", True)
    post.set_editor_property("auto_exposure_bias", exposure)
    comp.set_editor_property("post_process_settings", post)
    comp.set_editor_property("post_process_blend_weight", 1.0)
    return {
        "actor": actor,
        "component": comp,
        "target": target,
        "width": width,
        "height": height,
        "camera": camera.get_actor_label(),
        "exposure": exposure,
    }


def capture(world, parameters, prepared=None):
    prepared = prepared or prepare_capture(world, parameters)
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    prepared["component"].capture_scene()
    output = Path(parameters["render_output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    unreal.RenderingLibrary.export_render_target(
        world, prepared["target"], str(output.parent), output.name
    )
    unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(prepared["actor"])
    file_record(output, "render")
    return {
        "path": str(output),
        "width": prepared["width"],
        "height": prepared["height"],
        "camera": prepared["camera"],
        "mode": "GPU SceneCapture2D",
        "manualExposureCompensationEv": prepared["exposure"],
        "visualReview": "pending",
    }
