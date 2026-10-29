"""Export the inspected utility revision from a separate bridge run snapshot.

Copy the current host_scripts/blender/environment_export.py beside this script.
The bridge binds the input scene to its reviewed hash and saves this run as a
distinct native file. Its six cable curves become static meshes here; keep the
upstream utility scene as the editable native source. This is not a publication
sanitizer: native path buffers and FBX metadata still require separate checks.
"""

import hashlib
import importlib.util
import json
import math
from pathlib import Path

import bpy

REQUIRED_ROUTES = (
    "InspectionV2_Route_Camera_L",
    "InspectionV2_Route_Camera_R",
    "InspectionV2_Route_Illuminator_L",
    "InspectionV2_Route_Illuminator_R",
    "InspectionV2_Route_HMI",
    "InspectionV2_Route_PowerSupply",
)
REVISION_KEY = "inspection_utilities_revision"
REVISION = 1
MAX_ASSETS = 256
MAX_OBJECTS = 5000
BOUNDS_TOLERANCE_M = 1e-6
OUT = Path(globals()["STUDIO_OUTPUT_DIR"])
PARAMETERS = globals().get("STUDIO_PARAMETERS", {})


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def measure(obj):
    """Measure actual evaluated surfaces, in world metres, before/after conversion."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        mesh.calc_loop_triangles()
        scale = bpy.context.scene.unit_settings.scale_length
        points = [(evaluated.matrix_world @ vertex.co) * scale for vertex in mesh.vertices]
        if not points or not mesh.loop_triangles:
            raise ValueError("Route has no evaluated surface: " + obj.name)
        if not all(math.isfinite(value) for point in points for value in point):
            raise ValueError("Route has non-finite evaluated coordinates: " + obj.name)
        return {
            "type": obj.type,
            "vertices": len(mesh.vertices),
            "triangles": len(mesh.loop_triangles),
            "bounds_world_m": [
                [min(point[axis] for point in points) for axis in range(3)],
                [max(point[axis] for point in points) for axis in range(3)],
            ],
        }
    finally:
        evaluated.to_mesh_clear()


def validate_source():
    if bpy.context.scene.get(REVISION_KEY) != REVISION:
        raise ValueError("Expected the independently inspected utility revision")
    collection = bpy.data.collections.get("StudioEnvironment")
    rig = bpy.data.collections.get("PresentationRig")
    if collection is None or rig is None:
        raise ValueError("The complete environment and presentation collections are required")
    routes = {
        obj.name: obj for obj in bpy.context.scene.objects
        if obj.name.startswith("InspectionV2_Route_")
    }
    if set(routes) != set(REQUIRED_ROUTES):
        raise ValueError("Expected exactly the six named inspected cable routes")
    exported_names = {obj.name for obj in collection.all_objects}
    for name in REQUIRED_ROUTES:
        obj = routes[name]
        if obj.type != "CURVE" or not obj.data.splines:
            raise ValueError("Expected an editable source cable curve: " + name)
        if any(spline.type != "POLY" for spline in obj.data.splines):
            raise ValueError("Expected inspected POLY cable splines: " + name)
        if (name not in exported_names or obj.get("studio_asset") is not True
                or obj.get("studio_category") != "inspection_utilities"
                or obj.get("studio_export") is False or obj.hide_render):
            raise ValueError("Cable is not a visible tagged environment asset: " + name)
    return routes


def main():
    # A fresh bridge run directory is required; never replace export evidence.
    for name in ("export-conversion.json", "environment-manifest.json",
                 "inspection-utilities.glb", "glb-export-checks.json"):
        if (OUT / name).exists():
            raise FileExistsError("Use a fresh export run directory")
    if not isinstance(PARAMETERS, dict):
        raise ValueError("Script parameters must be a JSON object")
    routes = validate_source()
    helper = Path(__file__).with_name("environment_export.py")
    spec = importlib.util.spec_from_file_location("utility_environment_interchange", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = {
        "schema": "inspection-utilities-export-conversion/v1",
        "source_revision": {REVISION_KEY: REVISION},
        "script_parameters": PARAMETERS,
        "export_helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        "required_routes": list(REQUIRED_ROUTES),
        "conversion_scope": "Only the six named curves in this bridge run snapshot",
        "editable_source": "The upstream source-bound native utility scene remains the editable source; this exchange scene stores the six routes as meshes.",
        "editability_loss": "The six cable objects lose spline and curve-parameter editing in this exchange output only.",
        "comparison_scope": "Measured evaluated surface counts and world bounds only; complete geometry identity is not asserted.",
        "bounds_tolerance_m": BOUNDS_TOLERANCE_M,
        "routes": [],
        "limits": {"max_assets": MAX_ASSETS, "max_objects": MAX_OBJECTS},
        "publication_sanitized": False,
        "status": "converting",
    }
    write_json("export-conversion.json", report)
    if bpy.context.mode != "OBJECT":
        raise ValueError("Expected Object mode for isolated route conversion")
    original_names = {obj.name for obj in bpy.context.scene.objects}
    other_types = {obj.name: obj.type for obj in bpy.context.scene.objects
                   if obj.name not in REQUIRED_ROUTES}
    for name in REQUIRED_ROUTES:
        obj = routes[name]
        before = measure(obj)
        spline_summary = [
            {"type": spline.type, "points": len(spline.points),
             "cyclic": bool(spline.use_cyclic_u)}
            for spline in obj.data.splines
        ]
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.convert(target="MESH", keep_original=False)
        bpy.context.view_layer.update()
        converted = bpy.data.objects.get(name)
        if converted is None or converted.type != "MESH":
            raise ValueError("Cable conversion did not retain its exact mesh identity: " + name)
        after = measure(converted)
        residual = max(abs(a - b) for before_side, after_side in
                       zip(before["bounds_world_m"], after["bounds_world_m"])
                       for a, b in zip(before_side, after_side))
        record = {
            "name": name, "source_splines": spline_summary,
            "before": before, "after": after,
            "max_bounds_residual_m": residual,
            "bounds_within_tolerance": residual <= BOUNDS_TOLERANCE_M,
            "vertex_count_equal": before["vertices"] == after["vertices"],
            "triangle_count_equal": before["triangles"] == after["triangles"],
            "geometry_identity": "not_asserted",
        }
        report["routes"].append(record)
        write_json("export-conversion.json", report)
        if not record["bounds_within_tolerance"]:
            raise ValueError("Cable conversion exceeded measured bounds tolerance: " + name)
    if {obj.name for obj in bpy.context.scene.objects} != original_names:
        raise ValueError("Route conversion changed the scene object identity set")
    if {obj.name: obj.type for obj in bpy.context.scene.objects
            if obj.name not in REQUIRED_ROUTES} != other_types:
        raise ValueError("Route conversion changed an unrelated object type")
    manifest, outputs = module.export_environment(
        OUT, collection_name="StudioEnvironment", include_presentation=True,
        split_assets=True, max_assets=MAX_ASSETS, max_objects=MAX_OBJECTS,
    )
    manifest_routes = [entry for entry in manifest["objects"]
                       if entry["name"] in REQUIRED_ROUTES]
    if (len(manifest_routes) != len(REQUIRED_ROUTES)
            or {entry["name"] for entry in manifest_routes} != set(REQUIRED_ROUTES)
            or any(entry["sourceObject"] != entry["name"] or not entry["visibleRender"]
                   for entry in manifest_routes)):
        raise ValueError("Environment manifest omitted or changed an exact visible cable identity")
    assets = {asset["id"]: asset for asset in manifest["assets"]}
    if any(entry["assetId"] not in assets
           or not assets[entry["assetId"]]["exportFile"] for entry in manifest_routes):
        raise ValueError("Environment manifest contains a cable without an exported FBX asset")
    selected, selection = module.export_selection(bpy, "StudioEnvironment", True)
    glb = OUT / "inspection-utilities.glb"
    checks = module.export_scene_glb(bpy, glb, selected, include_presentation=True)
    glb_document = json.loads(module._glb_chunks(glb)[0][1])
    glb_routes = [node["name"] for node in glb_document.get("nodes", [])
                  if "mesh" in node and node.get("name") in REQUIRED_ROUTES]
    if len(glb_routes) != len(REQUIRED_ROUTES) or set(glb_routes) != set(REQUIRED_ROUTES):
        raise ValueError("GLB omitted an exact converted cable identity")
    checks["requiredCableIdentitiesPreserved"] = sorted(glb_routes)
    write_json("glb-export-checks.json", checks)
    # Relative visible resource paths are convenient, but do not sanitize native
    # fixed-size buffers. The separate publication workflow must scan saved bytes.
    for image in bpy.data.images:
        if image.packed_file:
            image.filepath = "//textures/" + image.name + ".png"
            for packed in image.packed_files:
                packed.filepath = image.filepath
    bpy.context.scene.render.filepath = "//render.png"
    report.update(
        status="exported", all_six_routes_in_manifest=True,
        all_six_routes_in_glb=True, selection=selection,
        actual_unique_assets=len(manifest["assets"]),
        actual_mesh_instances=len(manifest["objects"]),
        manifest="environment-manifest.json",
        manifest_sha256=hashlib.sha256((OUT / "environment-manifest.json").read_bytes()).hexdigest(),
        glb="inspection-utilities.glb", glb_sha256=checks["sha256"],
        exported_file_count=len(outputs),
        manifest_route_bindings=[{
            "name": entry["name"], "id": entry["id"], "asset_id": entry["assetId"],
            "export_file": assets[entry["assetId"]]["exportFile"],
        } for entry in manifest_routes],
    )
    write_json("export-conversion.json", report)
    print(json.dumps({"status": report["status"], "routes": len(report["routes"]),
                      "assets": report["actual_unique_assets"],
                      "mesh_instances": report["actual_mesh_instances"]}))


main()
