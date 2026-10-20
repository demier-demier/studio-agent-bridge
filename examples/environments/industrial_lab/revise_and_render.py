"""Revise an existing lab without rebuilding geometry; run through Studio Agent Bridge.

Keep the input .blend. Parameters: lighting_theme day/night, cart_offset_x_m,
camera, render_views. Renders are Cycles CPU at 1440x1000, 48 samples.
"""

import json
import math
from pathlib import Path

import bpy

P = globals().get("STUDIO_PARAMETERS", {})
OUT = Path(globals().get("STUDIO_OUTPUT_DIR", "."))
scene = bpy.context.scene
before = {
    "theme": scene.get("lighting_theme"),
    "meshes": len([o for o in scene.objects if o.type == "MESH"]),
}
theme = P.get("lighting_theme", scene.get("lighting_theme", "day"))
if theme not in ("day", "night"):
    raise ValueError("lighting_theme must be day or night")
presets = {
    "day": {
        "Ceiling_Practical": (650, (0.88, 0.93, 1.0)),
        "Entrance_SoftFill": (550, (1.0, 0.77, 0.53)),
        "Robot_WarmEdge": (250, (1.0, 0.58, 0.25)),
        "Rack_CoolEdge": (250, (0.32, 0.67, 1.0)),
        "diffuser": (3.0, (0.80, 0.88, 1.0, 1.0)),
    },
    "night": {
        "Ceiling_Practical": (180, (0.30, 0.55, 1.0)),
        "Entrance_SoftFill": (160, (1.0, 0.42, 0.16)),
        "Robot_WarmEdge": (400, (1.0, 0.55, 0.20)),
        "Rack_CoolEdge": (250, (0.32, 0.67, 1.0)),
        "diffuser": (1.5, (0.25, 0.48, 1.0, 1.0)),
    },
}


def lighting_state():
    result = {
        o.name: {"energy": o.data.energy, "color": list(o.data.color)}
        for o in bpy.data.collections["PresentationRig"].objects
        if o.type == "LIGHT"
    }
    diffuser = bpy.data.materials["Light_Diffuser"].node_tree.nodes.get("Principled BSDF")
    result["diffuser"] = {
        "strength": diffuser.inputs["Emission Strength"].default_value,
        "color": list(diffuser.inputs["Emission Color"].default_value),
    }
    return result


before["lighting"] = lighting_state()
for ob in bpy.data.collections["PresentationRig"].objects:
    if ob.type == "LIGHT":
        key = next((k for k in presets[theme] if ob.name.startswith(k)), None)
        if key:
            ob.data.energy, ob.data.color = presets[theme][key]
m = bpy.data.materials["Light_Diffuser"].node_tree.nodes.get("Principled BSDF")
m.inputs["Emission Strength"].default_value, m.inputs["Emission Color"].default_value = presets[
    theme
]["diffuser"]
scene["lighting_theme"] = theme
offset = float(P.get("cart_offset_x_m", 0))
if not math.isfinite(offset) or abs(offset) > 1.0:
    raise ValueError("Cart movement must stay within the one metre demonstration limit")
cart = [
    o
    for o in bpy.data.collections["StudioEnvironment"].objects
    if o.name.startswith(("Cart_", "Case_Latch"))
]
before["cart_world_positions_m"] = {o.name: list(o.matrix_world.translation) for o in cart}
for ob in cart:
    ob.location.x += offset
bpy.context.view_layer.update()
scene["cart_revision_offset_x_m"] = scene.get("cart_revision_offset_x_m", 0.0) + offset
scene.view_settings.exposure = -0.3
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 48
scene.cycles.use_denoising = True
scene.render.resolution_x = 1440
scene.render.resolution_y = 1000
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.use_stamp = False
scene.render.use_stamp_filename = False
views = P.get("render_views", ["Overview"])
for view in views:
    camera = bpy.data.objects.get("Camera_" + view)
    if camera is None or camera.type != "CAMERA":
        raise ValueError("Unknown environment camera: " + view)
    scene.camera = camera
    scene.render.filepath = str(OUT / ("view-" + view.lower() + "-" + theme + ".png"))
    bpy.ops.render.render(write_still=True)
scene.camera = bpy.data.objects["Camera_" + P.get("camera", "Overview")]
scene.render.filepath = "//render.png"
# Packed textures keep the native file self-contained; relative references are portable.
for image in bpy.data.images:
    if image.packed_file:
        image.filepath = "//textures/" + image.name + ".png"
after = {
    "theme": scene.get("lighting_theme"),
    "meshes": len([o for o in scene.objects if o.type == "MESH"]),
}
after["cart_world_positions_m"] = {o.name: list(o.matrix_world.translation) for o in cart}
after["lighting"] = lighting_state()
assert before["meshes"] == after["meshes"]
for name, position in before["cart_world_positions_m"].items():
    measured = after["cart_world_positions_m"][name]
    assert abs((measured[0] - position[0]) - offset) < 1e-5
    assert abs(measured[1] - position[1]) < 1e-5
    assert abs(measured[2] - position[2]) < 1e-5
report = {
    "before": before,
    "after": after,
    "cart_translation_x_m": offset,
    "cart_objects_moved": len(cart) if offset else 0,
    "rendered_views": views,
    "geometry_rebuilt": False,
}
(OUT / "revision-evidence.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
