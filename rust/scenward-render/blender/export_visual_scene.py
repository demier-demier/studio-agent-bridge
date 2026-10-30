"""Export evaluated Blender meshes without triangle decimation.

Usage: blender --disable-autoexec --background scene.blend --python this.py -- --output NEW.json
Exports visual geometry and supported scalar material values; physics is declared separately.
"""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Matrix


def export_current_scene(output):
    if Path(output).exists():
        raise ValueError("Refusing to overwrite an environment")
    bpy.context.view_layer.update()
    objects = []
    deps = bpy.context.evaluated_depsgraph_get()
    source_triangles = 0
    for obj in sorted(bpy.context.scene.objects, key=lambda o: o.name):
        if obj.type != "MESH" or obj.hide_render:
            continue
        if any(c.hide_render for c in obj.users_collection):
            continue
        evaluated = obj.evaluated_get(deps)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        dynamic = obj.get("sw_dynamic_body")
        transform = obj.matrix_world
        if dynamic:
            transform = Matrix.Translation(-obj.location) @ transform
        vertices = [list(transform @ v.co) for v in mesh.vertices]
        by_material = {}
        for tri in mesh.loop_triangles:
            by_material.setdefault(tri.material_index, []).append(list(tri.vertices))
        for index, faces in by_material.items():
            mat = obj.material_slots[index].material if index < len(obj.material_slots) else None
            bs = mat.node_tree.nodes.get("Principled BSDF") if mat and mat.use_nodes else None
            color = (
                list(bs.inputs["Base Color"].default_value)
                if bs
                else list(mat.diffuse_color)
                if mat
                else [0.3, 0.3, 0.3, 1]
            )
            rough = float(bs.inputs["Roughness"].default_value) if bs else 0.5
            metal = float(bs.inputs["Metallic"].default_value) if bs else 0
            objects.append(
                {
                    "name": obj.name + f"/material{index}",
                    "mesh": {"vertices": vertices, "faces": faces},
                    "color": color,
                    "roughness": max(0.04, min(1.0, rough)),
                    "metallic": metal,
                    "dynamic_body": dynamic,
                }
            )
            source_triangles += len(faces)
        evaluated.to_mesh_clear()
    Path(output).write_text(
        json.dumps(
            {"schema": "scenward-blender-environment/v1", "objects": objects}, separators=(",", ":")
        )
    )
    return objects, source_triangles


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    objects, triangles = export_current_scene(a.output)
    print(
        json.dumps(
            {
                "objects": len(objects),
                "triangles": triangles,
                "decimation": False,
                "note": "Physics colliders and texture/node fidelity are separate contracts.",
            }
        )
    )
