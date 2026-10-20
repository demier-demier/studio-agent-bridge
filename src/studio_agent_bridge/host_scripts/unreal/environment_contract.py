"""Pure validation and coordinate mapping shared by the host and boundary tests."""

import hashlib
import json
import math
import re
from pathlib import Path


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", value):
        raise ValueError("Environment identifiers must be bounded ASCII package names")
    return value


def numbers(value, length, name):
    if (
        not isinstance(value, list)
        or len(value) != length
        or any(
            not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x)
            for x in value
        )
    ):
        raise ValueError("Invalid finite vector: " + name)
    return [float(x) for x in value]


def local_file(root, relative, expected_hash=None):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("Bundle file paths must be relative")
    candidate = root / relative
    if any(part.is_symlink() for part in (candidate, *candidate.parents) if part != root):
        raise ValueError("Environment bundle files must not traverse symlinks")
    path = candidate.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing file or path escapes the environment bundle")
    if path.stat().st_size > 128_000_000:
        raise ValueError("Individual bundle file exceeds 128 MB")
    if expected_hash is not None:
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            raise ValueError("Missing or invalid bundle file SHA-256")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Bundle file hash mismatch: " + relative)
    return path


def transform(record):
    value = record.get("transform", record)
    shear = record.get("shearResidual", value.get("shearResidual", 0))
    if not isinstance(shear, (int, float)) or not math.isfinite(shear) or abs(shear) > 1e-5:
        raise ValueError("Sheared transforms cannot be transferred exactly as Unreal TRS")
    location = numbers(value["translationMeters"], 3, "translationMeters")
    rotation = numbers(value["rotationQuaternionWxyz"], 4, "rotationQuaternionWxyz")
    scale = numbers(value["scale"], 3, "scale")
    length = math.sqrt(sum(x * x for x in rotation))
    if abs(length - 1) > 0.001 or any(abs(x) < 0.000001 or abs(x) > 10000 for x in scale):
        raise ValueError("Transform quaternion must be unit length and scales nonzero/bounded")
    if any(abs(x) > 10000 for x in location):
        raise ValueError("Environment coordinate exceeds 10 km bound")
    # Blender RH Z-up to Unreal LH Z-up; FBX native converter also reflects Y.
    w, x, y, z = rotation
    return {
        "translationCm": [100 * location[0], -100 * location[1], 100 * location[2]],
        "rotationQuaternionXyzw": [-x, y, -z, w],
        "scale": scale,
    }


def bounds_cm(bounds):
    low = numbers(bounds["min"], 3, "bounds.min")
    high = numbers(bounds["max"], 3, "bounds.max")
    if any(a > b for a, b in zip(low, high)):
        raise ValueError("Bounds minimum exceeds maximum")
    return [100 * low[0], -100 * high[1], 100 * low[2]], [
        100 * high[0],
        -100 * low[1],
        100 * high[2],
    ]


def compare_bounds(bounds, observed_min, observed_max, tolerance_cm=0.05):
    expected_min, expected_max = bounds_cm(bounds)
    residual = max(
        abs(a - b)
        for a, b in zip(expected_min + expected_max, list(observed_min) + list(observed_max))
    )
    if residual > tolerance_cm:
        raise ValueError(f"Imported mesh bounds mismatch: {residual:.6f} cm")
    return {
        "expectedMinCm": expected_min,
        "expectedMaxCm": expected_max,
        "maxResidualCm": residual,
        "toleranceCm": tolerance_cm,
        "passed": True,
    }


def guard_map_creation(destination, level_path, exists=False):
    if not level_path.startswith(destination.rstrip("/") + "/"):
        raise ValueError("New environment level must stay inside its fresh destination namespace")
    if exists:
        raise ValueError("Refusing to overwrite an existing environment map")


def validate_manifest(path):
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > 16_000_000:
        raise ValueError("Missing environment manifest or manifest exceeds 16 MB")
    value = json.loads(path.read_text())
    if value.get("schemaVersion") != "environment-manifest/v1":
        raise ValueError("Expected environment-manifest/v1")
    if value.get("units", {}).get("length") != "meter":
        raise ValueError("Environment manifest lengths must be meters")
    coords = value.get("coordinateSystem", {})
    if coords.get("up") != "Z" or coords.get("handedness") != "right":
        raise ValueError("Expected Blender right-handed Z-up coordinates")
    assets, objects = value.get("assets"), value.get("objects")
    if not isinstance(assets, list) or not 1 <= len(assets) <= 256:
        raise ValueError("Expected 1–256 unique assets")
    if not isinstance(objects, list) or not 1 <= len(objects) <= 5000:
        raise ValueError("Expected 1–5000 environment objects")
    ids = set()
    for asset in assets:
        name = identifier(asset["id"])
        if name in ids:
            raise ValueError("Duplicate environment asset ID")
        ids.add(name)
        source = local_file(path.parent, asset["exportFile"], asset["fileSha256"])
        if source.suffix.lower() != ".fbx":
            raise ValueError("Environment meshes require FBX assets")
        bounds_cm(asset["boundsLocal"])
    object_ids = set()
    for obj in objects:
        name = identifier(obj["id"])
        if name in object_ids or obj["assetId"] not in ids:
            raise ValueError("Duplicate object or missing referenced asset")
        object_ids.add(name)
        transform(obj)
    materials = value.get("materials", [])
    if not isinstance(materials, list) or len(materials) > 512:
        raise ValueError("Invalid environment materials")
    material_ids = set()
    for material in materials:
        name = identifier(material.get("id", material["name"]))
        if name in material_ids:
            raise ValueError("Duplicate material identifier")
        material_ids.add(name)
        pbr = material.get("pbr", {})
        if "baseColor" in pbr:
            numbers(pbr["baseColor"], 4, "baseColor")
        for key in ("metallic", "roughness", "alpha"):
            val = pbr.get(key, 0.5)
            if not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 <= val <= 1:
                raise ValueError("PBR scalar must be finite and within [0,1]: " + key)
        for texture in material.get("textures", {}).values():
            local_file(path.parent, texture["path"], texture["sha256"])
    for kind in ("lights", "cameras"):
        records = value.get(kind, [])
        if not isinstance(records, list) or len(records) > 128:
            raise ValueError("Invalid environment " + kind)
        for record in records:
            identifier(record["id"])
            transform(record)
    return value
