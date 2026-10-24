"""Original test-only edit; its marker must be absent when resource validation rejects."""

import json
from pathlib import Path

import bpy

output = Path(globals()["STUDIO_OUTPUT_DIR"])
cube = bpy.data.objects["SourceBindingFixtureCube"]
before = list(cube.location)
cube.location.x += 0.1
bpy.context.view_layer.update()
(output / "requested-script-ran.json").write_text(
    json.dumps({"before": before, "after": list(cube.location), "requested_script_ran": True})
    + "\n"
)
