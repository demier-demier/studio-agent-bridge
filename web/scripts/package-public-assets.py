"""Prepare a metadata-clean native download without changing geometry/pixels.

Inputs are local verified bridge artifacts supplied explicitly by the operator.
The original delivery is preserved. CI builds from committed assets and checks
their hashes, so native applications are not needed by the static website build.
"""

import argparse
import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path


def strip_png_metadata(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Expected PNG")
    output = bytearray(data[:8])
    offset = 8
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        chunk = data[offset : offset + length + 12]
        if kind not in {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}:
            output.extend(chunk)
        offset += length + 12
    return bytes(output)


def digest(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-zip", type=Path, required=True)
    parser.add_argument("--sanitized-blend", type=Path, required=True)
    args = parser.parse_args()
    web = Path(__file__).resolve().parents[1]
    assets = web / "public/assets"
    payloads = {}
    with zipfile.ZipFile(args.native_zip) as source:
        for name in source.namelist():
            if name.endswith("/") or name.endswith("/manifest.json"):
                continue
            data = source.read(name)
            if name.endswith(".png"):
                data = strip_png_metadata(data)
            if name.endswith(".blend"):
                data = args.sanitized_blend.read_bytes()
            payloads[name] = data
    root = next(iter(payloads)).split("/")[0]
    evidence_name = root + "/observed-native-proof.json"
    proof = json.loads(payloads[evidence_name])
    proof["blender_reopen"]["original_scene_sha256"] = proof["blender_reopen"]["scene_sha256"]
    proof["blender_reopen"]["original_render_sha256"] = proof["blender_reopen"]["render_sha256"]
    proof["blender_reopen"]["scene_sha256"] = digest(args.sanitized_blend.read_bytes())["sha256"]
    proof["blender_reopen"]["render_sha256"] = digest(payloads[root + "/rpi5-open-tray.png"])[
        "sha256"
    ]
    proof["public_packaging"] = {
        "png_processing": "Metadata chunks removed; critical image chunks unchanged",
        "blend_processing": "Native work copy with relative render path and no saved file-browser screen; reopened by bridge",
        "native_cad_geometry_changed": False,
    }
    payloads[evidence_name] = (json.dumps(proof, indent=2) + "\n").encode()
    manifest = {name.removeprefix(root + "/"): digest(data) for name, data in payloads.items()}
    payloads[root + "/manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as out:
        for name in sorted(payloads):
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 20, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            out.writestr(entry, payloads[name])
    (assets / "rpi5-native-case.zip").write_bytes(result.getvalue())
    for name in ("rpi5-render.png", "fusion-native.png"):
        path = assets / name
        path.write_bytes(strip_png_metadata(path.read_bytes()))
    for name in ("rpi5-open-tray.png", "rpi5-fusion-native.png"):
        path = web.parent / "docs/images" / name
        path.write_bytes(strip_png_metadata(path.read_bytes()))
    public_proof = json.loads((assets / "evidence.json").read_text())
    public_proof["blender_reopen"] = proof["blender_reopen"]
    public_proof["public_packaging"] = proof["public_packaging"]
    public_proof["public_packaging"]["native_zip_sha256"] = digest(result.getvalue())["sha256"]
    (assets / "evidence.json").write_text(json.dumps(public_proof, indent=2) + "\n")
    assets_manifest = {
        p.name: digest(p.read_bytes())
        for p in sorted(assets.iterdir())
        if p.is_file() and p.name != "manifest.json"
    }
    (assets / "manifest.json").write_text(json.dumps(assets_manifest, indent=2) + "\n")
    print(
        json.dumps({"assets": len(assets_manifest), "zip": assets_manifest["rpi5-native-case.zip"]})
    )


if __name__ == "__main__":
    main()
