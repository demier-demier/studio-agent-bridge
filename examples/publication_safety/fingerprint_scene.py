"""Deterministic, bounded scene observations for the publication example.

This measures explicit native scene fields at the current frame. It is not a
complete .blend serialization, renderer equivalence test, or animation test.
"""

import hashlib
import json
import math


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def quantized(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [quantized(item) for item in value]
    if isinstance(value, dict):
        return {key: quantized(item) for key, item in value.items()}
    return value


def compare_records(before, after):
    result = {
        "passed": True,
        "evaluated_float_tolerance": 1e-6,
        "max_evaluated_float_residual": 0.0,
        "evaluated_float_values_compared": 0,
        "exact_categories_equal": True,
    }
    try:
        canonical(before)
        canonical(after)
    except (TypeError, ValueError):
        result["passed"] = False
        result["exact_categories_equal"] = False
        result["rejection_reason"] = "Non-finite or unsupported observation value"
        return result

    def compare(left, right):
        if isinstance(left, float) and isinstance(right, float):
            if not math.isfinite(left) or not math.isfinite(right):
                return False
            difference = abs(left - right)
            result["evaluated_float_values_compared"] += 1
            result["max_evaluated_float_residual"] = max(
                result["max_evaluated_float_residual"], difference
            )
            return difference <= result["evaluated_float_tolerance"]
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            return left.keys() == right.keys() and all(
                compare(left[key], right[key]) for key in left
            )
        if isinstance(left, list):
            return len(left) == len(right) and all(compare(a, b) for a, b in zip(left, right))
        return left == right

    result["exact_categories_equal"] = all(
        canonical(before[key]) == canonical(after.get(key))
        for key in before
        if key != "evaluated_geometry"
    )
    result["passed"] = (
        before.keys() == after.keys()
        and result["exact_categories_equal"]
        and compare(before["evaluated_geometry"], after["evaluated_geometry"])
    )
    return result


def primitive(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite native value cannot be fingerprinted.")
        return value
    if isinstance(value, set):
        return sorted(value)
    try:
        return [primitive(item) for item in value]
    except TypeError:
        raise ValueError("Unsupported fingerprint value type: " + type(value).__name__) from None


def properties(block):
    """Writable scalar/array RNA values; exclude paths, runtime pointers and UI."""
    result = {}
    for prop in block.bl_rna.properties:
        key = prop.identifier
        if prop.is_readonly or key in {
            "name",
            "name_full",
            "rna_type",
            "select",
            "location",
            "width",
            "height",
        }:
            continue
        if any(word in key for word in ("filepath", "directory", "file_path")):
            continue
        if prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            continue
        try:
            result[key] = primitive(getattr(block, key))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("Unmeasurable native property: " + key) from error
    return result


def mesh_record(mesh, attributes=True):
    record = {
        "vertices": [list(vertex.co) for vertex in mesh.vertices],
        "edges": [list(edge.vertices) for edge in mesh.edges],
        "polygons": [
            {
                "vertices": list(poly.vertices),
                "material": poly.material_index,
                "smooth": poly.use_smooth,
            }
            for poly in mesh.polygons
        ],
        "loops": [[loop.vertex_index, loop.edge_index] for loop in mesh.loops],
        "materials": [material.name if material else None for material in mesh.materials],
        "uv_layers": {
            layer.name: [list(item.uv) for item in layer.data] for layer in mesh.uv_layers
        },
    }
    if attributes:
        fields = {
            "FLOAT": "value",
            "INT": "value",
            "BOOLEAN": "value",
            "STRING": "value",
            "FLOAT_VECTOR": "vector",
            "FLOAT_COLOR": "color",
            "BYTE_COLOR": "color",
            "FLOAT2": "vector",
            "INT8": "value",
            "INT32_2D": "value",
            "INT16_2D": "value",
            "QUATERNION": "value",
        }
        record["attributes"] = {}
        for attribute in mesh.attributes:
            field = fields.get(attribute.data_type)
            if field is None:
                raise ValueError("Unmeasured mesh attribute type: " + attribute.data_type)
            values = [primitive(getattr(item, field)) for item in attribute.data]
            record["attributes"][attribute.name] = {
                "domain": attribute.domain,
                "type": attribute.data_type,
                "values": values,
            }
    return record


def tree_record(tree):
    if tree is None:
        return None
    nodes = []
    for node in sorted(tree.nodes, key=lambda item: item.name):
        sockets = []
        for index, socket in enumerate(node.inputs):
            sockets.append(
                {
                    "index": index,
                    "identifier": socket.identifier,
                    "default": primitive(socket.default_value)
                    if hasattr(socket, "default_value")
                    else None,
                }
            )
        nodes.append(
            {
                "name": node.name,
                "type": node.bl_idname,
                "properties": properties(node),
                "inputs": sockets,
                "image": node.image.name if getattr(node, "image", None) else None,
                "group": node.node_tree.name if getattr(node, "node_tree", None) else None,
            }
        )
    return {
        "nodes": nodes,
        "links": sorted(
            [
                [
                    link.from_node.name,
                    link.from_socket.identifier,
                    link.to_node.name,
                    link.to_socket.identifier,
                ]
                for link in tree.links
            ]
        ),
    }


def fingerprint(bpy):
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    records = {}
    records["base_meshes"] = {
        mesh.name: mesh_record(mesh) for mesh in sorted(bpy.data.meshes, key=lambda x: x.name)
    }
    records["objects"] = {}
    records["evaluated_geometry"] = {}
    for obj in sorted(bpy.data.objects, key=lambda x: x.name):
        records["objects"][obj.name] = {
            "type": obj.type,
            "data": obj.data.name if obj.data else None,
            "parent": obj.parent.name if obj.parent else None,
            "parent_type": obj.parent_type,
            "matrix_world": [list(row) for row in obj.matrix_world],
            "matrix_local": [list(row) for row in obj.matrix_local],
            "matrix_parent_inverse": [list(row) for row in obj.matrix_parent_inverse],
            "collections": sorted(collection.name for collection in obj.users_collection),
            "material_slots": [
                [slot.link, slot.material.name if slot.material else None]
                for slot in obj.material_slots
            ],
            "hide_render": obj.hide_render,
            "hide_viewport": obj.hide_viewport,
            "modifiers": [
                {"name": mod.name, "type": mod.type, "properties": properties(mod)}
                for mod in obj.modifiers
            ],
        }
        if obj.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            evaluated = obj.evaluated_get(graph)
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
            try:
                records["evaluated_geometry"][obj.name] = mesh_record(mesh)
            finally:
                evaluated.to_mesh_clear()
    records["instances"] = sorted(
        [
            {
                "object": item.object.original.name,
                "parent": item.parent.original.name if item.parent else None,
                "instance": item.is_instance,
                "matrix": [list(row) for row in item.matrix_world],
            }
            for item in graph.object_instances
        ],
        key=canonical,
    )
    records["curves_and_text"] = {}
    for curve in sorted(bpy.data.curves, key=lambda x: x.name):
        records["curves_and_text"][curve.name] = {
            "type": curve.bl_rna.identifier,
            "properties": properties(curve),
            "fonts": {
                key: getattr(curve, key).name
                for key in ("font", "font_bold", "font_italic", "font_bold_italic")
                if hasattr(curve, key) and getattr(curve, key)
            },
            "splines": [
                {
                    "type": spline.type,
                    "properties": properties(spline),
                    "points": [[*point.co, point.radius, point.tilt] for point in spline.points],
                    "bezier": [
                        {
                            "co": list(point.co),
                            "left": list(point.handle_left),
                            "right": list(point.handle_right),
                            "left_type": point.handle_left_type,
                            "right_type": point.handle_right_type,
                            "radius": point.radius,
                            "tilt": point.tilt,
                        }
                        for point in spline.bezier_points
                    ],
                }
                for spline in curve.splines
            ],
        }
    records["materials"] = {
        material.name: {
            "properties": properties(material),
            "nodes": tree_record(material.node_tree),
        }
        for material in sorted(bpy.data.materials, key=lambda x: x.name)
    }
    records["node_groups"] = {
        tree.name: tree_record(tree) for tree in sorted(bpy.data.node_groups, key=lambda x: x.name)
    }
    records["images"] = {}
    for image in sorted(bpy.data.images, key=lambda x: x.name):
        records["images"][image.name] = {
            "source": image.source,
            "size": list(image.size),
            "colorspace": image.colorspace_settings.name,
            "alpha_mode": image.alpha_mode,
            "packed_bytes_sha256": [
                hashlib.sha256(bytes(packed.packed_file.data)).hexdigest()
                for packed in image.packed_files
            ],
        }
    records["lights"] = {
        light.name: {"properties": properties(light), "nodes": tree_record(light.node_tree)}
        for light in sorted(bpy.data.lights, key=lambda x: x.name)
    }
    records["cameras"] = {
        camera.name: {"properties": properties(camera), "dof": properties(camera.dof)}
        for camera in sorted(bpy.data.cameras, key=lambda x: x.name)
    }
    records["worlds"] = {
        world.name: {"properties": properties(world), "nodes": tree_record(world.node_tree)}
        for world in sorted(bpy.data.worlds, key=lambda x: x.name)
    }
    records["scenes"] = {
        scene.name: {
            "frame": scene.frame_current,
            "camera": scene.camera.name if scene.camera else None,
            "world": scene.world.name if scene.world else None,
            "units": properties(scene.unit_settings),
            "view": properties(scene.view_settings),
        }
        for scene in bpy.data.scenes
    }
    categories = {
        name: {
            "sha256": digest(quantized(value) if name == "evaluated_geometry" else value),
            "records": len(value),
            "numeric_encoding": "rounded_to_6_decimal_places"
            if name == "evaluated_geometry"
            else "exact_json_numbers",
        }
        for name, value in records.items()
    }
    return {
        "schema_version": "native-scene-fingerprint/v1",
        "blender_version": bpy.app.version_string,
        "categories": categories,
        "combined_sha256": digest(categories),
        "scope": "Explicit current-frame scene properties; no complete file or renderer equivalence claim",
    }, records
