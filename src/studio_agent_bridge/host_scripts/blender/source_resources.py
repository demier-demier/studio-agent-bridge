"""Conservative gate for common external resources in a pinned .blend load.

This checks explicit Blender data fields, not blend_paths flags whose packed-data
semantics vary by version. It is not a dependency closure for arbitrary add-ons,
custom properties, Python, drivers or future Blender resource types.
"""

from collections import Counter


def inspect_resources(bpy):
    rejected = Counter()
    packed = Counter()

    def resources(name):
        return getattr(bpy.data, name, ())

    for image in resources("images"):
        source = getattr(image, "source", "FILE")
        if source in {"GENERATED", "VIEWER"}:
            continue
        if source == "FILE" and getattr(image, "packed_file", None):
            packed["images"] += 1
        elif (
            source == "TILED"
            and len(getattr(image, "packed_files", ())) >= len(getattr(image, "tiles", ())) > 0
        ):
            packed["tiled_images"] += 1
        else:
            rejected["unpacked_or_unsupported_images"] += 1

    for font in resources("fonts"):
        path = getattr(font, "filepath", "")
        if path in {"", "<builtin>"}:
            continue
        if getattr(font, "packed_file", None):
            packed["fonts"] += 1
        else:
            rejected["unpacked_fonts"] += 1

    for sound in resources("sounds"):
        if getattr(sound, "packed_file", None):
            packed["sounds"] += 1
        else:
            rejected["unpacked_sounds"] += 1

    # Linked datablocks and these disk-backed formats are deliberately unsupported.
    for name in ("libraries", "movieclips", "cache_files", "volumes"):
        rejected[name] += len(resources(name))
    for obj in resources("objects"):
        for modifier in getattr(obj, "modifiers", ()):
            kind = getattr(modifier, "type", "")
            if kind in {"MESH_CACHE", "MESH_SEQUENCE_CACHE"}:
                rejected["mesh_cache_modifiers"] += 1
            if kind == "FLUID" and getattr(modifier, "fluid_type", "") == "DOMAIN":
                rejected["fluid_domain_caches"] += 1
            cache = getattr(modifier, "point_cache", None)
            if cache and getattr(cache, "use_disk_cache", False):
                rejected["disk_point_caches"] += 1
            if kind == "NODES" and getattr(modifier, "bakes", ()):
                rejected["geometry_node_bakes"] += 1
        for system in getattr(obj, "particle_systems", ()):
            cache = getattr(system, "point_cache", None)
            if cache and getattr(cache, "use_disk_cache", False):
                rejected["disk_particle_caches"] += 1
    for scene in resources("scenes"):
        editor = getattr(scene, "sequence_editor", None)
        if editor:
            strips = getattr(editor, "strips_all", getattr(editor, "sequences_all", ()))
            for strip in strips:
                if getattr(strip, "type", "") in {"IMAGE", "MOVIE", "SOUND"}:
                    rejected["external_sequencer_strips"] += 1

    rejected = {key: count for key, count in sorted(rejected.items()) if count}
    return {
        "supported": not rejected,
        "scope": "common_builtin_resource_fields",
        "checked_before_requested_action": True,
        "packed_counts": dict(sorted(packed.items())),
        "unsupported_counts": rejected,
        "limitations": [
            "Only the .blend bytes are pinned. Known unpacked resources, linked libraries and disk caches are rejected; no external files are copied.",
            "This is not exhaustive dependency discovery for custom properties, add-ons, scripts, drivers or future resource types. Trusted scripts remain privileged.",
        ],
    }
