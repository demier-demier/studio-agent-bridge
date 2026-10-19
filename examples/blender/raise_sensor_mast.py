"""Example agent-authored revision, executed inside an existing rover .blend.

Copy this file into your configured workspace, then call run_script with an
input_blend and script_parameters={"extra_height_m": 0.15}. Source stays intact.
"""

import bpy

extra_height = float(STUDIO_PARAMETERS.get("extra_height_m", 0.15))  # noqa: F821 - host injects parameters
if not 0.02 <= extra_height <= 0.30:
    raise ValueError("This design example accepts an additional height from 0.02m to 0.30m.")

lower_tube = bpy.data.objects["Mast_lower_tube"]
lower_tube.dimensions.z += extra_height
lower_tube.location.z += extra_height / 2

for name in (
    "Mast_upper_tube",
    "Lidar_turntable",
    "Lidar_scan_window",
    "Lidar_top_cover",
    "Stereo_camera_housing",
    "Stereo_lens_1",
    "Stereo_lens_2",
):
    bpy.data.objects[name].location.z += extra_height

bpy.context.scene["revision_note"] = "Sensor mast raised through a trusted native Python operation."
bpy.context.scene["mast_extension_m"] = extra_height
bpy.context.view_layer.update()
