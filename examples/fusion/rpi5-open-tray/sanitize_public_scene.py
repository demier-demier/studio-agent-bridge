"""Remove machine-specific output paths from the public scene work copy."""

import bpy

bpy.context.scene.render.filepath = "//rpi5-open-tray.png"
bpy.context.scene.render.use_stamp = False
bpy.context.scene.render.use_stamp_filename = False
if any(image.filepath for image in bpy.data.images if image.source == "FILE"):
    raise ValueError("Unexpected external image path in this texture-free native case")
for scene in bpy.data.scenes:
    for key in scene.keys():
        value = scene[key]
        if isinstance(value, str) and "/Users/" in value:
            raise ValueError("Unexpected local path in scene metadata")

# Saved editor file-browser state can retain a home directory even in a
# background-created scene. Remove only these unused UI screens from the copy.
for screen in list(bpy.data.screens):
    if any(space.type == "FILE_BROWSER" for area in screen.areas for space in area.spaces):
        if screen.users == 0:
            bpy.data.screens.remove(screen)
        else:
            for area in screen.areas:
                for space in area.spaces:
                    if space.type == "FILE_BROWSER" and space.params:
                        space.params.directory = b"//"
