"""Select only the real CAD tray for the adapter's GLB export collection.

Run through the Blender bridge on the saved visualization .blend. This changes
only the new work copy's collection membership, not the mesh or source file.
"""

import bpy

tray = bpy.data.objects.get("Fusion-exported original open tray")
if tray is None or tray.type != "MESH":
    raise ValueError("The verified Fusion tray mesh is required")
collection = bpy.data.collections.get("InspectionRover")
if collection is None:
    # This is the bridge's current export selection convention, shared with its
    # original rover case. No rover geometry is introduced into this fixture.
    collection = bpy.data.collections.new("InspectionRover")
    bpy.context.scene.collection.children.link(collection)
if list(collection.objects):
    raise ValueError("Export collection must initially be empty")
collection.objects.link(tray)
bpy.context.scene["public_asset"] = "Original Raspberry Pi 5 reference mounting fixture"
bpy.context.scene["browser_scope"] = "Read-only mesh visualization; CAD history stays in F3D"
