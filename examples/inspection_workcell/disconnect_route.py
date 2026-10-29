"""Negative geometry fixture; run only on an isolated bridge source snapshot.

Displace a cable endpoint while preserving the anchor and all route metadata.
The independent readback must reject this copy. Never edit the reviewed input.
"""
import json
from pathlib import Path

import bpy

route = bpy.data.objects.get("InspectionV2_Route_Camera_L")
if route is None or route.type != "CURVE" or len(route.data.splines) != 1:
    raise ValueError("Expected the authored camera cable curve")
spline = route.data.splines[0]
if spline.type != "POLY" or len(spline.points) < 2:
    raise ValueError("Expected an editable polyline route")
route.data = route.data.copy()
route.data.splines[0].points[-1].co.x += 0.1
bpy.context.view_layer.update()
Path(globals()["STUDIO_OUTPUT_DIR"], "injected-failure.json").write_text(
    json.dumps({"route": route.name, "local_endpoint_displacement_m": [0.1, 0, 0],
                "anchor_or_metadata_changed": False}) + "\n"
)
