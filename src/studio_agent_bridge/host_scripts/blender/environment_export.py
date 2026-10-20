"""Measured static scene interchange. Blender imports stay inside host functions.

The manifest is the layout contract; FBX assets contain evaluated LOCAL geometry.
Full-scene GLB/FBX remain useful previews, not the authority for instance placement.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from pathlib import Path

SCHEMA = "environment-manifest/v1"


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Scene contains non-finite numeric values")
    return 0.0 if value == 0 else value


def identifier(prefix, name):
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")[:48] or "item"
    return f"{prefix}_{slug}_{hashlib.sha256(name.encode()).hexdigest()[:10]}"


def matrix_transform(matrix, scale_length=1.0):
    """Serialize a Blender column-vector transform; translation is METERS."""
    if not math.isfinite(scale_length) or scale_length <= 0:
        raise ValueError("Unit scale must be positive and finite")
    translation, rotation, scale = matrix.decompose()
    rows = [[finite(v) for v in row] for row in matrix]
    for axis in range(3):
        rows[axis][3] *= scale_length
    return {
        "matrixWorld": rows,
        "translationMeters": [finite(v * scale_length) for v in translation],
        "rotationQuaternionWxyz": [finite(v) for v in rotation],
        "scale": [finite(v) for v in scale],
    }


def bounds_from_points(points):
    points = [[finite(v) for v in point] for point in points]
    if not points:
        return {"min": [0.0] * 3, "max": [0.0] * 3, "dimensions": [0.0] * 3}
    minimum = [min(p[i] for p in points) for i in range(3)]
    maximum = [max(p[i] for p in points) for i in range(3)]
    return {
        "min": minimum,
        "max": maximum,
        "dimensions": [maximum[i] - minimum[i] for i in range(3)],
    }


def combine_bounds(values):
    return bounds_from_points([corner for b in values for corner in (b["min"], b["max"])])


def export_selection(bpy, collection_name=None, include_presentation=False):
    """Explicit collection wins; otherwise environment/tags/legacy/scene in order."""
    scene = bpy.context.scene
    if collection_name is not None:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            raise ValueError(f"Export collection not found: {collection_name}")
        objects = list(collection.all_objects)
        selection = {"strategy": "explicit-collection", "collection": collection_name}
    elif bpy.data.collections.get("StudioEnvironment"):
        objects = list(bpy.data.collections["StudioEnvironment"].all_objects)
        selection = {"strategy": "environment-collection", "collection": "StudioEnvironment"}
    elif any(obj.get("studio_asset") is True for obj in scene.objects):
        objects = [obj for obj in scene.objects if obj.get("studio_asset") is True]
        # Parent empties carry layout; retain them without selecting unrelated children.
        for obj in list(objects):
            parent = obj.parent
            while parent:
                if parent not in objects:
                    objects.append(parent)
                parent = parent.parent
        selection = {"strategy": "studio_asset-tag", "collection": None}
    elif bpy.data.collections.get("InspectionRover"):
        objects = list(bpy.data.collections["InspectionRover"].all_objects)
        selection = {"strategy": "legacy-rover-collection", "collection": "InspectionRover"}
    else:
        objects = list(scene.objects)
        selection = {"strategy": "scene", "collection": None}
    objects = [obj for obj in objects if obj.get("studio_export") is not False]
    if include_presentation:
        rig = bpy.data.collections.get("PresentationRig")
        candidates = rig.all_objects if rig else scene.objects
        objects += [obj for obj in candidates if obj.type in {"CAMERA", "LIGHT"}]
    else:
        objects = [obj for obj in objects if obj.type not in {"CAMERA", "LIGHT"}]
    unique = {obj.name: obj for obj in objects if obj.get("studio_export") is not False}
    return [unique[name] for name in sorted(unique)], selection


def mesh_instances(bpy, selected):
    """Include linked meshes and evaluated collection/geometry instances."""
    selected_names = {obj.name for obj in selected}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    entries = []
    for item in depsgraph.object_instances:
        obj = item.object
        if obj.type != "MESH":
            continue
        original = obj.original
        parent = item.parent.original if item.parent else None
        if item.is_instance:
            if parent is None or parent.name not in selected_names:
                continue
            identity = (
                original.name
                + "@"
                + parent.name
                + ":"
                + ".".join(str(v) for v in item.persistent_id if v != 2147483647)
            )
        else:
            if original.name not in selected_names:
                continue
            identity = original.name
        if original.get("studio_export") is False:
            continue
        entries.append(
            {
                "identity": identity,
                "object": obj,
                "original": original,
                "matrix": item.matrix_world.copy(),
                "instance": bool(item.is_instance),
                "parent": parent if item.is_instance else original.parent,
            }
        )
    return sorted(entries, key=lambda entry: entry["identity"])


def measured_bounds(bpy, entries):
    unit = bpy.context.scene.unit_settings.scale_length
    return combine_bounds(
        [
            bounds_from_points(
                [(entry["matrix"] @ vertex.co) * unit for vertex in entry["object"].data.vertices]
            )
            for entry in entries
        ]
    )


def _geometry_digest(mesh, material_names):
    mesh.calc_loop_triangles()
    digest = hashlib.sha256()
    for vertex in mesh.vertices:
        digest.update(struct.pack("<3d", *(finite(v) for v in vertex.co)))
    for triangle in mesh.loop_triangles:
        digest.update(struct.pack("<4I", *triangle.vertices, triangle.material_index))
    for loop in mesh.loops:
        digest.update(struct.pack("<I", loop.vertex_index))
    for normal in mesh.corner_normals:
        digest.update(struct.pack("<3d", *(finite(v) for v in normal.vector)))
    for layer in mesh.uv_layers:
        digest.update(layer.name.encode())
        for uv in layer.data:
            digest.update(struct.pack("<2d", *(finite(v) for v in uv.uv)))
    for layer in mesh.color_attributes:
        digest.update((layer.name + ":" + layer.domain + ":" + layer.data_type).encode())
        for color in layer.data:
            digest.update(struct.pack("<4d", *(finite(v) for v in color.color)))
    digest.update(json.dumps(material_names, ensure_ascii=False).encode())
    return digest.hexdigest()


def _copy_texture(bpy, image, directory, limitations):
    if image.get("studio_export") is False:
        limitations.add(f"Texture {image.name}: {image.source} images are not baked/exported")
        return None
    if image.source not in {"FILE", "GENERATED"}:
        raise ValueError(
            f"Texture {image.name}: bake {image.source} images to a static packed file before export"
        )
    data = bytes(image.packed_file.data) if image.packed_file else None
    suffix = Path(image.filepath).suffix.lower()
    if data is None:
        source = Path(bpy.path.abspath(image.filepath, library=image.library))
        if source.is_file():
            if source.stat().st_size > 128_000_000:
                raise ValueError(f"Texture {image.name} exceeds 128 MB")
            data = source.read_bytes()
        else:
            raise ValueError(
                f"Texture {image.name}: referenced image is missing; pack/save it before export"
            )
    if not data:
        raise ValueError(f"Texture {image.name}: no image bytes")
    if len(data) > 128_000_000:
        raise ValueError(f"Texture {image.name} exceeds 128 MB")
    if suffix not in {".png", ".jpg", ".jpeg", ".exr", ".tga", ".bmp", ".tif", ".tiff"}:
        suffix = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr", "TARGA": ".tga"}.get(
            image.file_format
        )
        if suffix is None:
            raise ValueError(f"Texture {image.name}: unsupported image format {image.file_format}")
    digest = hashlib.sha256(data).hexdigest()
    path = directory / "assets" / "textures" / (digest[:20] + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise ValueError("Texture filename hash collision")
    path.write_bytes(data)
    return {
        "path": path.relative_to(directory).as_posix(),
        "sha256": digest,
        "bytes": len(data),
        "colorSpace": image.colorspace_settings.name,
        "width": image.size[0],
        "height": image.size[1],
        "name": image.name,
        "sourceKind": "packed-image" if image.packed_file else "external-image",
        "provenance": str(
            image.get("studio_provenance") or "not-specified; ownership is not inferred"
        ),
    }


def material_record(bpy, material, directory, limitations):
    base = list(material.diffuse_color)
    pbr = {
        "baseColor": base,
        "metallic": finite(material.metallic),
        "roughness": finite(material.roughness),
        "alpha": finite(base[3]),
        "emissionColor": [0.0, 0.0, 0.0, 1.0],
        "emissionStrength": 0.0,
    }
    textures = {}
    if material.use_nodes and material.node_tree:
        outputs = [
            node
            for node in material.node_tree.nodes
            if node.type == "OUTPUT_MATERIAL" and node.is_active_output
        ]
        surface = outputs[0].inputs.get("Surface") if outputs else None
        node = surface.links[0].from_node if surface and surface.is_linked else None
        if node and node.type == "BSDF_PRINCIPLED":
            fields = {
                "Base Color": "baseColor",
                "Metallic": "metallic",
                "Roughness": "roughness",
                "Alpha": "alpha",
                "Emission Color": "emissionColor",
                "Emission Strength": "emissionStrength",
            }
            for socket_name, field in fields.items():
                socket = node.inputs.get(socket_name)
                if socket:
                    value = socket.default_value
                    pbr[field] = (
                        [finite(v) for v in value] if hasattr(value, "__len__") else finite(value)
                    )
            for socket_name, key in {
                "Base Color": "baseColor",
                "Metallic": "metallic",
                "Roughness": "roughness",
                "Alpha": "alpha",
                "Normal": "normal",
                "Emission Color": "emission",
            }.items():
                socket = node.inputs.get(socket_name)
                if not socket or not socket.is_linked:
                    continue
                linked = socket.links[0].from_node
                normal_strength = None
                if key == "normal" and linked.type == "NORMAL_MAP":
                    normal_strength = finite(linked.inputs["Strength"].default_value)
                    normal_color = linked.inputs["Color"]
                    linked = normal_color.links[0].from_node if normal_color.is_linked else None
                if linked and linked.type == "TEX_IMAGE" and linked.image:
                    texture = _copy_texture(bpy, linked.image, directory, limitations)
                    if texture:
                        texture["channel"] = socket.links[0].from_socket.name
                        texture["normalStrength"] = normal_strength
                        textures[key] = texture
                    vector = linked.inputs.get("Vector")
                    if vector and vector.is_linked:
                        limitations.add(
                            f"Material {material.name}: texture mapping nodes are not translated; UVs required"
                        )
                else:
                    limitations.add(
                        f"Material {material.name}: linked {socket_name} requires baking; scalar fallback only"
                    )
        else:
            limitations.add(
                f"Material {material.name}: non-Principled surface is represented by viewport PBR defaults"
            )
    pbr["emission"] = pbr["emissionColor"][:3]
    return {
        "id": identifier("mat", material.name),
        "name": material.name,
        "pbr": pbr,
        "textures": textures,
        "doubleSided": not material.use_backface_culling,
    }


def _glb_chunks(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 256_000_000:
        raise ValueError("Missing GLB or file exceeds 256 MB")
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("Truncated GLB")
    magic, version, total = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise ValueError("Invalid GLB header")
    chunks, offset = [], 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("Truncated GLB chunk")
        size, kind = struct.unpack_from("<I4s", data, offset)
        offset += 8
        if size % 4 or offset + size > len(data):
            raise ValueError("Invalid GLB chunk length")
        chunks.append((kind, data[offset : offset + size]))
        offset += size
    if not chunks or chunks[0][0] != b"JSON":
        raise ValueError("GLB JSON must be the first chunk")
    return chunks


def validate_glb_export(
    path, expected_mesh_instances, expected_material_names, expected_bindings=None
):
    """Catch material loss in shared-mesh export, without claiming rendered parity."""
    chunks = _glb_chunks(path)
    document = json.loads(chunks[0][1])
    nodes = [node for node in document.get("nodes", []) if "mesh" in node]
    if len(nodes) != expected_mesh_instances:
        raise ValueError("GLB mesh instance count differs from selected evaluated objects")
    actual_materials = {material.get("name") for material in document.get("materials", [])}
    missing = set(expected_material_names) - actual_materials
    if missing:
        raise ValueError("GLB lost assigned materials: " + ", ".join(sorted(missing)))
    if expected_bindings is not None:
        actual_bindings = {}
        for node in nodes:
            primitives = document["meshes"][node["mesh"]]["primitives"]
            actual_bindings[node["name"]] = {
                document["materials"][primitive["material"]].get("name")
                for primitive in primitives
                if "material" in primitive
            }
        if actual_bindings != expected_bindings:
            raise ValueError("GLB per-object material assignments differ from evaluated sources")
    if any("uri" in item for item in document.get("images", []) + document.get("buffers", [])):
        raise ValueError("GLB must embed its images and binary buffers")
    return {
        "meshInstances": len(nodes),
        "meshDefinitions": len(document.get("meshes", [])),
        "materials": len(document.get("materials", [])),
        "images": len(document.get("images", [])),
        "assignedMaterialNamesPreserved": True,
        "perObjectMaterialAssignmentsChecked": expected_bindings is not None,
        "resourcesEmbedded": True,
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "limitation": "Structural checks do not prove renderer or arbitrary shader equivalence.",
    }


def export_scene_glb(bpy, path, selected, include_presentation=False):
    """Export temporary evaluated objects with material slots bound to their meshes.

    Blender's apply-modifiers glTF path can lose per-object material overrides on
    linked meshes. These copies bake modifiers once, deduplicate the evaluated
    geometry/material combination, and never replace or transform source data.
    """
    entries = mesh_instances(bpy, selected)
    if not entries or len(entries) > 5000:
        raise ValueError("GLB requires 1–5000 evaluated mesh instances")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    old_selection = list(bpy.context.selected_objects)
    old_active = bpy.context.view_layer.objects.active
    copies, temporary_meshes, mesh_cache, by_identity = [], [], {}, {}
    parents, material_names, bindings = {}, set(), {}
    try:
        for entry in entries:
            source = entry["original"]
            mesh = bpy.data.meshes.new_from_object(
                entry["object"], preserve_all_data_layers=True, depsgraph=depsgraph
            )
            temporary_meshes.append(mesh)
            slots = [slot.material for slot in entry["object"].material_slots]
            digest = _geometry_digest(
                mesh, [material.name if material else None for material in slots]
            )
            bindings[entry["identity"]] = set()
            for triangle in mesh.loop_triangles:
                if triangle.material_index < len(slots) and slots[triangle.material_index]:
                    material_names.add(slots[triangle.material_index].name)
                    bindings[entry["identity"]].add(slots[triangle.material_index].name)
            if digest in mesh_cache:
                temporary_meshes.remove(mesh)
                bpy.data.meshes.remove(mesh)
                mesh = mesh_cache[digest]
            else:
                for index, material in enumerate(slots):
                    if material is not None:
                        if index < len(mesh.materials):
                            mesh.materials[index] = material
                        else:
                            mesh.materials.append(material)
                mesh.name = "glb_mesh_" + digest[:20]
                mesh_cache[digest] = mesh
            obj = bpy.data.objects.new(identifier("glb", entry["identity"]), mesh)
            bpy.context.scene.collection.objects.link(obj)
            copies.append(obj)
            obj.matrix_world = entry["matrix"]
            obj["studio_source_name"] = entry["identity"]
            obj["studio_category"] = source.get("studio_category", "uncategorized")
            by_identity[entry["identity"]] = obj
            parents[entry["identity"]] = entry["parent"].name if entry["parent"] else None
        for source in selected:
            if source.type != "EMPTY" and not (
                include_presentation and source.type in {"CAMERA", "LIGHT"}
            ):
                continue
            obj = source.copy()
            copies.append(obj)
            obj.name = identifier("glb", source.name)
            obj.parent = None
            obj.constraints.clear()
            obj.animation_data_clear()
            if obj.type == "EMPTY":
                # Evaluated collection instances are already explicit mesh copies.
                obj.instance_type = "NONE"
            bpy.context.scene.collection.objects.link(obj)
            obj.matrix_world = source.matrix_world.copy()
            obj["studio_source_name"] = source.name
            by_identity[source.name] = obj
            parents[source.name] = source.parent.name if source.parent else None
        for identity, obj in by_identity.items():
            parent = by_identity.get(parents[identity])
            if parent:
                matrix = obj.matrix_world.copy()
                obj.parent = parent
                obj.matrix_world = matrix
        bpy.ops.object.select_all(action="DESELECT")
        for obj in copies:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = copies[0]
        bpy.context.view_layer.update()
        bpy.ops.export_scene.gltf(
            filepath=str(path),
            export_format="GLB",
            use_selection=True,
            export_apply=False,
            export_animations=False,
            export_yup=True,
            export_cameras=include_presentation,
            export_lights=include_presentation,
            export_extras=True,
        )
        # Temporary Blender datablocks need unique names; restore source names
        # in GLB JSON while preserving the binary chunk and all buffer offsets.
        chunks = _glb_chunks(path)
        document = json.loads(chunks[0][1])
        for node in document.get("nodes", []):
            if node.get("extras", {}).get("studio_source_name"):
                node["name"] = node["extras"]["studio_source_name"]
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
        payload += b" " * (-len(payload) % 4)
        chunks[0] = (b"JSON", payload)
        content = b"".join(struct.pack("<I4s", len(data), kind) + data for kind, data in chunks)
        Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, len(content) + 12) + content)
        return validate_glb_export(path, len(entries), material_names, bindings)
    finally:
        for obj in reversed(copies):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in temporary_meshes:
            bpy.data.meshes.remove(mesh)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in old_selection:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = old_active


def _asset_fbx(bpy, mesh, directory, asset_id, materials):
    from mathutils import Matrix

    path = directory / "assets" / "meshes" / (asset_id + ".fbx")
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = bpy.data.objects.new(asset_id, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = Matrix.Identity(4)
    # Object-linked material overrides must survive the unique asset copy.
    for index, material in enumerate(materials):
        if material is not None:
            if index < len(mesh.materials):
                mesh.materials[index] = material
            else:
                mesh.materials.append(material)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.export_scene.fbx(
            filepath=str(path),
            use_selection=True,
            object_types={"MESH"},
            use_mesh_modifiers=False,
            mesh_smooth_type="FACE",
            add_leaf_bones=False,
            bake_anim=False,
            axis_forward="-Z",
            axis_up="Y",
            apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_UNITS",
            path_mode="AUTO",
        )
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
    return {
        "path": path.relative_to(directory).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def validate_manifest(manifest):
    """Independent structural validation, runnable without a native application."""
    if manifest.get("schemaVersion") != SCHEMA:
        raise ValueError("Unsupported environment manifest schema")
    if manifest.get("units", {}).get("length") != "meter":
        raise ValueError("Manifest distances must be meters")
    assets = manifest.get("assets", [])
    objects = manifest.get("objects", [])
    if not 1 <= len(assets) <= 256 or not 1 <= len(objects) <= 5000:
        raise ValueError("Environment exceeds asset/object limits")
    asset_ids = {asset["id"] for asset in assets}
    if len(asset_ids) != len(assets):
        raise ValueError("Duplicate asset ID")
    ids = {obj["id"] for obj in objects}
    group_ids = {group["id"] for group in manifest.get("groups", [])}
    if len(ids) != len(objects):
        raise ValueError("Duplicate object ID")
    for obj in objects:
        if obj.get("assetId") not in asset_ids or obj.get("parentId") not in ids | group_ids | {
            None
        }:
            raise ValueError("Unknown object asset or parent")
        matrix = obj["transform"]["matrixWorld"]
        if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
            raise ValueError("Invalid transform dimensions")
        if any(not math.isfinite(v) for row in matrix for v in row):
            raise ValueError("Non-finite transform")
        if any(abs(matrix[3][i] - expected) > 1e-8 for i, expected in enumerate([0, 0, 0, 1])):
            raise ValueError("Non-affine transform")
    for asset in assets:
        if asset["vertices"] < 1 or asset["triangles"] < 1:
            raise ValueError("Empty mesh asset")
        if asset.get("exportFile"):
            path = Path(asset["exportFile"])
            if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".fbx":
                raise ValueError("Unsafe asset export path")
    return manifest


def export_environment(
    directory,
    *,
    collection_name=None,
    include_presentation=False,
    split_assets=False,
    max_assets=256,
    max_objects=5000,
):
    import bpy

    directory = Path(directory)
    if not 1 <= max_assets <= 256 or not 1 <= max_objects <= 5000:
        raise ValueError("Environment limits out of range")
    selected, selection = export_selection(bpy, collection_name, include_presentation)
    entries = mesh_instances(bpy, selected)
    if not entries or len(entries) > max_objects:
        raise ValueError("No mesh instances selected, or object limit exceeded")
    unsupported = [
        obj.name for obj in selected if obj.type not in {"MESH", "EMPTY", "LIGHT", "CAMERA"}
    ]
    if unsupported:
        raise ValueError(
            "Convert exportable text/curves/other geometry to mesh first: "
            + ", ".join(unsupported[:10])
        )
    unit = finite(bpy.context.scene.unit_settings.scale_length)
    if unit <= 0:
        raise ValueError("Unit scale must be positive")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    assets, objects, materials = {}, [], {}
    limitations = {
        "Static evaluated mesh snapshot at the recorded frame; native blend retains editable source history.",
        "Procedural shaders, simulations, animation and drivers are not reproduced by this manifest.",
        "Manifest uses Blender right-handed Z-up coordinates; Unreal requires explicit handedness and meter-to-centimeter conversion.",
        "PBR transfer does not prove identical renderer output, exposure, color management or light intensity.",
    }
    if not split_assets:
        limitations.add("Per-asset FBX files were not requested; assets are manifest-only")
    old_active = bpy.context.view_layer.objects.active
    old_selection = list(bpy.context.selected_objects)
    ids = {entry["identity"]: identifier("obj", entry["identity"]) for entry in entries}
    group_ids = {obj.name: identifier("group", obj.name) for obj in selected if obj.type == "EMPTY"}
    created_meshes = []
    try:
        for entry in entries:
            obj, original, matrix = entry["object"], entry["original"], entry["matrix"]
            mesh = bpy.data.meshes.new_from_object(
                obj, preserve_all_data_layers=True, depsgraph=depsgraph
            )
            created_meshes.append(mesh)
            slots = [slot.material for slot in obj.material_slots]
            slot_names = [mat.name if mat else None for mat in slots]
            geometry_hash = _geometry_digest(mesh, slot_names)
            asset_id = "mesh_" + geometry_hash[:20]
            if not mesh.vertices or not mesh.loop_triangles:
                raise ValueError("Selected mesh has no surface: " + original.name)
            local_bounds = bounds_from_points([vertex.co * unit for vertex in mesh.vertices])
            if asset_id not in assets:
                if len(assets) >= max_assets:
                    raise ValueError(
                        "Unique evaluated mesh limit exceeded; reuse prototypes or raise max_assets"
                    )
                for material in slots:
                    if material and material.name not in materials:
                        materials[material.name] = material_record(
                            bpy, material, directory, limitations
                        )
                asset = {
                    "id": asset_id,
                    "name": original.data.name,
                    "sourceObject": original.name,
                    "meshDataName": original.data.name,
                    "provenance": str(
                        original.get("studio_provenance")
                        or "not-specified; ownership is not inferred"
                    ),
                    "geometrySha256": geometry_hash,
                    "vertices": len(mesh.vertices),
                    "triangles": len(mesh.loop_triangles),
                    "boundsLocal": local_bounds,
                    "materialSlots": [
                        identifier("mat", mat.name) if mat else None for mat in slots
                    ],
                    "uvMaps": [layer.name for layer in mesh.uv_layers],
                    "geometry": {"localUnits": "meter", "evaluatedModifiers": True},
                    "exportFile": None,
                    "fileSha256": None,
                }
                if split_assets:
                    exported = _asset_fbx(bpy, mesh, directory, asset_id, slots)
                    asset.update(
                        exportFile=exported["path"],
                        fileSha256=exported["sha256"],
                        exportBytes=exported["bytes"],
                    )
                assets[asset_id] = asset
            parent_identity = entry["parent"].name if entry["parent"] else None
            parent_id = ids.get(parent_identity) or group_ids.get(parent_identity)
            transform = matrix_transform(matrix, unit)
            rotation_matrix = matrix.to_3x3()
            decomposed = matrix.decompose()
            # Transform.scale cannot faithfully encode shear; matrixWorld is always retained.
            from mathutils import Matrix

            recomposed = Matrix.LocRotScale(*decomposed)
            shear = max(abs(matrix[r][c] - recomposed[r][c]) for r in range(3) for c in range(3))
            if shear > 1e-5:
                limitations.add(
                    f"Object {original.name}: shear requires baking or a matrix-aware target"
                )
            bounds_world = bounds_from_points(
                [(matrix @ vertex.co) * unit for vertex in mesh.vertices]
            )
            objects.append(
                {
                    "id": ids[entry["identity"]],
                    "name": entry["identity"],
                    "sourceObject": original.name,
                    "assetId": asset_id,
                    "parentId": parent_id,
                    "sourceParent": entry["parent"].name if entry["parent"] else None,
                    "collections": sorted(c.name for c in original.users_collection),
                    "category": str(original.get("studio_category", "uncategorized")),
                    "transform": transform,
                    "boundsWorld": bounds_world,
                    "visibleRender": not original.hide_render,
                    "visibleViewport": not original.hide_viewport,
                    "evaluatedInstance": entry["instance"],
                    "negativeDeterminant": rotation_matrix.determinant() < 0,
                    "shearResidual": shear,
                    "sourceModifiers": [
                        {"name": mod.name, "type": mod.type} for mod in original.modifiers
                    ],
                }
            )
            bpy.data.meshes.remove(mesh)
            created_meshes.remove(mesh)
    finally:
        for mesh in created_meshes:
            bpy.data.meshes.remove(mesh)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in old_selection:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = old_active
    cameras, lights = [], []
    for obj in selected:
        if obj.type not in {"CAMERA", "LIGHT"}:
            continue
        common = {
            "id": identifier(obj.type.lower(), obj.name),
            "name": obj.name,
            "transform": matrix_transform(obj.matrix_world, unit),
            "localForward": [0, 0, -1],
            "localUp": [0, 1, 0],
        }
        if obj.type == "CAMERA":
            cameras.append(
                {
                    **common,
                    "type": obj.data.type,
                    "lensMm": finite(obj.data.lens),
                    "sensorWidthMm": finite(obj.data.sensor_width),
                    "sensorHeightMm": finite(obj.data.sensor_height),
                    "clipStart": finite(obj.data.clip_start * unit),
                    "clipEnd": finite(obj.data.clip_end * unit),
                    "orthoScale": finite(obj.data.ortho_scale * unit),
                    "active": bpy.context.scene.camera == obj,
                }
            )
        else:
            lights.append(
                {
                    **common,
                    "type": obj.data.type,
                    "color": [finite(v) for v in obj.data.color],
                    "energy": finite(obj.data.energy),
                    "energyUnit": "Blender native; not a calibrated Unreal intensity",
                    "size": finite(getattr(obj.data, "size", 0) * unit),
                    "sizeY": finite(getattr(obj.data, "size_y", 0) * unit),
                    "shape": getattr(obj.data, "shape", None),
                    "spotSize": finite(getattr(obj.data, "spot_size", 0)),
                    "spotBlend": finite(getattr(obj.data, "spot_blend", 0)),
                }
            )
    groups = [
        {
            "name": obj.name,
            "id": identifier("group", obj.name),
            "parent": obj.parent.name if obj.parent else None,
            "transform": matrix_transform(obj.matrix_world, unit),
        }
        for obj in selected
        if obj.type == "EMPTY"
    ]
    manifest = {
        "schemaVersion": SCHEMA,
        "generator": "studio-agent-bridge",
        "blenderVersion": bpy.app.version_string,
        "scene": bpy.context.scene.name,
        "frame": bpy.context.scene.frame_current,
        "selection": selection,
        "units": {
            "length": "meter",
            "metersPerBlenderUnit": unit,
            "blenderUnitSystem": bpy.context.scene.unit_settings.system,
        },
        "coordinateSystem": {
            "up": "Z",
            "handedness": "right",
            "matrixLayout": "row-major arrays; column-vector multiplication",
            "matrixTranslationUnit": "meter",
        },
        "fbxExport": {
            "axisForward": "-Z",
            "axisUp": "Y",
            "applyUnitScale": True,
            "applyScaleOptions": "FBX_SCALE_UNITS",
            "objectTransform": "identity",
        },
        "assets": list(assets.values()),
        "objects": objects,
        "groups": groups,
        "materials": list(materials.values()),
        "cameras": cameras,
        "lights": lights,
        "boundsWorld": combine_bounds([obj["boundsWorld"] for obj in objects]),
        "totals": {
            "uniqueAssets": len(assets),
            "meshInstances": len(objects),
            "materials": len(materials),
            "cameras": len(cameras),
            "lights": len(lights),
            "uniqueVertices": sum(a["vertices"] for a in assets.values()),
            "uniqueTriangles": sum(a["triangles"] for a in assets.values()),
            "instanceTriangles": sum(assets[obj["assetId"]]["triangles"] for obj in objects),
        },
        "limitations": sorted(limitations),
    }
    validate_manifest(manifest)
    manifest_path = directory / "environment-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    outputs = [{"kind": "environment-manifest", "path": str(manifest_path)}]
    outputs += [
        {"kind": "environment-asset-fbx", "path": str(directory / asset["exportFile"])}
        for asset in assets.values()
        if asset["exportFile"]
    ]
    outputs += [
        {"kind": "texture", "path": str(path)}
        for path in sorted((directory / "assets" / "textures").glob("*"))
    ]
    return manifest, outputs
