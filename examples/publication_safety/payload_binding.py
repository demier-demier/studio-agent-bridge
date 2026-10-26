"""Bind immutable publication payloads to the evidence actually measured."""

import hashlib
import json
from pathlib import Path

PREFIX = "industrial-lab/"


def create_output_directory(work_root, output_directory):
    root = Path(work_root).resolve()
    output = Path(output_directory).resolve()
    if not output.is_relative_to(root):
        raise ValueError("Publication output must stay inside the owned private work root")
    output.mkdir(exist_ok=False)
    return output


def require_record(data, record):
    if not isinstance(data, bytes):
        raise ValueError("Publication payload must be immutable bytes")
    if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("Publication bytes differ from their recorded evidence")


def bind_payloads(original, scenes, transfer, scene_records, fbx_records, expected_manifest_sha256):
    original, scenes, transfer = dict(original), dict(scenes), dict(transfer)
    outer = json.loads(original[PREFIX + "manifest.json"])
    if set(original) != {PREFIX + name for name in outer} | {PREFIX + "manifest.json"}:
        raise ValueError("Original member set differs from its complete manifest")
    for name, record in outer.items():
        require_record(original[PREFIX + name], record)
    if set(scenes) != set(scene_records) or set(scenes) != {"scene-day.blend", "scene-night.blend"}:
        raise ValueError("Both exact native publication scenes are required")
    for name, data in scenes.items():
        require_record(data, scene_records[name])
    original_transfer = {
        name.removeprefix(PREFIX + "transfer/")
        for name in original
        if name.startswith(PREFIX + "transfer/")
    }
    if set(transfer) != original_transfer:
        raise ValueError("Clean transfer must have exactly the original transfer member set")
    manifest_bytes = transfer["environment-manifest.json"]
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise ValueError("Transfer manifest differs from the one reopened in Unreal")
    manifest = json.loads(manifest_bytes)
    reports = {record["member"].removeprefix("transfer/"): record for record in fbx_records}
    asset_paths = {asset["exportFile"] for asset in manifest["assets"]}
    if len(reports) != len(fbx_records) or set(reports) != asset_paths:
        raise ValueError("FBX evidence does not match the complete manifest asset set")
    for asset in manifest["assets"]:
        path = asset["exportFile"]
        require_record(
            transfer[path], {"sha256": asset["fileSha256"], "bytes": asset["exportBytes"]}
        )
        report = reports[path]
        require_record(
            transfer[path], {"sha256": report["output_sha256"], "bytes": report["output_bytes"]}
        )
    texture_paths = set()
    for material in manifest["materials"]:
        for texture in material.get("textures", {}).values():
            path = texture["path"]
            texture_paths.add(path)
            require_record(transfer[path], texture)
            if transfer[path] != original[PREFIX + "transfer/" + path]:
                raise ValueError("Publication textures must preserve verified original bytes")
    if set(transfer) != asset_paths | texture_paths | {"environment-manifest.json"}:
        raise ValueError("Transfer contains an unbound or missing content member")
    payloads = dict(original)
    payloads.update({PREFIX + name: data for name, data in scenes.items()})
    payloads.update({PREFIX + "transfer/" + name: data for name, data in transfer.items()})
    return payloads
