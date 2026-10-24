"""Sanitize native path fields before sharing a separate scene copy.

Blender RNA strings may retain bytes beyond their current null terminator.
Write a long benign value before the final relative value; never edit binary data.
"""

import json
from pathlib import Path

import bpy

output = Path(globals()["STUDIO_OUTPUT_DIR"])
padding = "//" + "_" * 900
for image in bpy.data.images:
    if image.packed_file:
        relative = "//textures/" + image.name + ".png"
        image.filepath = padding
        image.filepath = relative
        for packed in image.packed_files:
            packed.filepath = padding
            packed.filepath = relative
    elif image.source == "FILE" and image.filepath:
        raise ValueError("Cannot publish an unpacked image")

scene = bpy.context.scene
scene.render.filepath = padding
scene.render.filepath = "//render.png"
scene.render.use_stamp = False
scene.render.use_stamp_filename = False
browser_count = 0
for screen in bpy.data.screens:
    for area in screen.areas:
        for space in area.spaces:
            if space.type == "FILE_BROWSER" and space.params:
                space.params.directory = padding.encode()
                space.params.directory = b"//"
                space.params.filename = "_" * 250
                space.params.filename = "scene.blend"
                browser_count += 1

(output / "public-copy-sanitization.json").write_text(
    json.dumps(
        {
            "method": "Native RNA property overwrite followed by native save",
            "packed_images": sum(bool(image.packed_file) for image in bpy.data.images),
            "file_browser_spaces_cleaned": browser_count,
            "geometry_changed": False,
            "requires_decompressed_byte_scan": True,
        },
        indent=2,
    )
    + "\n"
)
