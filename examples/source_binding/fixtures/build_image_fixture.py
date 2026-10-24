"""Original test-only scene: one cube and one tiny packed or external image."""

import json
from pathlib import Path

import bpy

parameters = globals().get("STUDIO_PARAMETERS", {})
output = Path(globals()["STUDIO_OUTPUT_DIR"])
packed = parameters["packed"]
if type(packed) is not bool:
    raise ValueError("packed must be a boolean")

temporary = bpy.data.images.new("FixturePixels", width=2, height=2)
temporary.pixels = [0.12, 0.55, 0.7, 1.0] * 4
temporary.filepath_raw = str(output / "fixture-image.png")
temporary.file_format = "PNG"
temporary.save()
bpy.data.images.remove(temporary)
image = bpy.data.images.load(str(output / "fixture-image.png"), check_existing=False)
image.name = "SourceBindingFixtureImage"
if packed:
    image.pack()
image.filepath = "//fixture-image.png"
for file in image.packed_files:
    file.filepath = "//fixture-image.png"

bpy.ops.mesh.primitive_cube_add(size=0.5)
cube = bpy.context.object
cube.name = "SourceBindingFixtureCube"
material = bpy.data.materials.new("SourceBindingFixtureMaterial")
material.use_nodes = True
texture = material.node_tree.nodes.new("ShaderNodeTexImage")
texture.image = image
material.node_tree.links.new(
    texture.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
)
cube.data.materials.append(material)
report = {
    "purpose": "original test-only resource boundary fixture",
    "image_source": image.source,
    "image_filepath": image.filepath,
    "packed": bool(image.packed_file),
    "packed_files": len(image.packed_files),
    "image_size": list(image.size),
}
(output / "fixture-observations.json").write_text(json.dumps(report, indent=2) + "\n")
assert report["image_source"] == "FILE"
assert report["packed"] is packed
