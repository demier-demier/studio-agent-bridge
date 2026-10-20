"""Sanitize a saved work copy for sharing; retain embedded textures and native geometry."""

import json
from pathlib import Path

import bpy

OUT = Path(globals().get("STUDIO_OUTPUT_DIR", "."))
for image in bpy.data.images:
    if image.packed_file:
        relative = "//textures/" + image.name + ".png"
        image.filepath = relative
        for packed in image.packed_files:
            packed.filepath = relative
    elif image.source == "FILE" and image.filepath:
        raise ValueError("Unpacked file texture is not portable: " + image.name)
# The RNA string uses a fixed-size native buffer. Overwrite its old tail before
# assigning the short relative path, avoiding stale path bytes in compressed .blend.
scene = bpy.context.scene
scene.render.filepath = "//" + "_" * 900
scene.render.filepath = "//render.png"
scene.render.use_stamp = False
scene.render.use_stamp_filename = False
for screen in list(bpy.data.screens):
    if any(space.type == "FILE_BROWSER" for area in screen.areas for space in area.spaces):
        if screen.users == 0:
            bpy.data.screens.remove(screen)
        else:
            for area in screen.areas:
                for space in area.spaces:
                    if space.type == "FILE_BROWSER" and space.params:
                        space.params.directory = b"//"
for datablocks in (bpy.data.objects, bpy.data.scenes, bpy.data.images, bpy.data.materials):
    for block in datablocks:
        for key in block.keys():
            value = block[key]
            if isinstance(value, str) and any(
                marker in value for marker in ("/Users/", "/var/folders/")
            ):
                raise ValueError("Machine-specific metadata on " + block.name)
report = {
    "packed_images": sum(bool(i.packed_file) for i in bpy.data.images),
    "render_path": scene.render.filepath,
    "private_metadata_check": "passed",
    "geometry_changed": False,
    "note": "A raw decompressed-byte scan is also required after saving.",
}
(OUT / "portable-scene-checks.json").write_text(json.dumps(report, indent=2) + "\n")
