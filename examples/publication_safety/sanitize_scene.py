"""Create a shareable native copy by overwriting path buffers through Blender RNA.

Use only on a separate work copy. The caller saves it and scans decompressed bytes.
No binary data is edited and no geometry construction is performed.
"""

import json
from pathlib import Path


def sanitize(bpy):
    padding = "//" + "_" * 900
    for image in bpy.data.images:
        if image.packed_file:
            colorspace = image.colorspace_settings.name
            alpha_mode = image.alpha_mode
            relative = "//textures/" + image.name + ".png"
            image.filepath = padding
            image.filepath = relative
            for packed in image.packed_files:
                packed.filepath = padding
                packed.filepath = relative
            image.colorspace_settings.name = colorspace
            image.alpha_mode = alpha_mode
        elif image.source == "FILE" and image.filepath:
            raise ValueError("Cannot publish an unpacked image")
    for scene in bpy.data.scenes:
        scene.render.filepath = padding
        scene.render.filepath = "//render.png"
        scene.render.use_stamp = False
        scene.render.use_stamp_filename = False
    browsers = 0
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params:
                    space.params.directory = padding.encode()
                    space.params.directory = b"//"
                    space.params.filename = "_" * 250
                    space.params.filename = "scene.blend"
                    browsers += 1
    return {
        "method": "Native RNA long benign overwrite then relative path assignment",
        "packed_images": sum(bool(image.packed_file) for image in bpy.data.images),
        "file_browser_spaces_cleaned": browsers,
        "requires_decompressed_byte_scan": True,
    }


if __name__ == "__main__":
    import bpy

    output = Path(globals().get("STUDIO_OUTPUT_DIR", "."))
    (output / "publication-sanitization.json").write_text(
        json.dumps(sanitize(bpy), indent=2) + "\n"
    )
