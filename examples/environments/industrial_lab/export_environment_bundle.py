"""Export a native environment via the repository's versioned interchange helper.

Copy host_scripts/blender/environment_export.py beside this script in the workspace.
Produces original packed-texture PBR assets, instance manifest and full-scene GLB.
"""

import importlib.util
import json
from pathlib import Path

import bpy

OUT = Path(globals().get("STUDIO_OUTPUT_DIR", "."))
helper = Path(__file__).with_name("environment_export.py")
spec = importlib.util.spec_from_file_location("environment_interchange", helper)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
manifest, outputs = module.export_environment(
    OUT,
    collection_name="StudioEnvironment",
    include_presentation=True,
    split_assets=True,
    max_assets=256,
    max_objects=5000,
)
objects, selection = module.export_selection(bpy, "StudioEnvironment", True)
bpy.ops.object.select_all(action="DESELECT")
for ob in objects:
    ob.select_set(True)
bpy.context.view_layer.objects.active = next(o for o in objects if o.type == "MESH")
checks = module.export_scene_glb(
    bpy, OUT / "industrial-lab.glb", objects, include_presentation=True
)
(OUT / "glb-export-checks.json").write_text(json.dumps(checks, indent=2) + "\n")
# Portable saved document; no local path in image resources, render path or file browser.
for image in bpy.data.images:
    if image.packed_file:
        image.filepath = "//textures/" + image.name + ".png"
        for packed in image.packed_files:
            packed.filepath = image.filepath
bpy.context.scene.render.filepath = "//render.png"
for screen in list(bpy.data.screens):
    if screen.users == 0 and any(
        space.type == "FILE_BROWSER" for area in screen.areas for space in area.spaces
    ):
        bpy.data.screens.remove(screen)
    else:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params:
                    space.params.directory = b"//"
print(
    json.dumps(
        {
            "manifest": "environment-manifest.json",
            "outputs": len(outputs),
            "glb": "industrial-lab.glb",
            "selection": selection,
        }
    )
)
