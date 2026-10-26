"""Package explicitly selected native evidence and original environment artifacts.

The configuration is local-only and contains source paths. Public output retains
measured values and artifact hashes, but omits local logs and absolute paths.
"""

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from studio_agent_bridge.publication import (  # noqa: E402
    PublicationError,
    publish_directory,
    publish_native_archive,
    scan_archive,
    validate_payloads,
)


def read(path):
    return json.loads(Path(path).read_text())


def fingerprint(path):
    data = Path(path).read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def build_package(config: dict, dest: Path):
    """Build only in private staging; native reopen is not a byte-privacy scan."""
    repo = REPOSITORY
    spec = importlib.util.spec_from_file_location(
        "public_asset_metadata", Path(__file__).with_name("package-public-assets.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bundle = Path(config["day_bundle"])
    day_native = Path(config["day_native_run"])
    night = Path(config["night_native_run"])
    revision = Path(config["night_revision_run"])
    source_manifest = read(bundle / "environment-manifest.json")
    unreal_proof = read(config["unreal_proof"])
    if (
        unreal_proof.get("status") != "verified"
        or unreal_proof["nativeProcess"]["returncode"] != 0
        or unreal_proof["nativeProcess"]["freshProcessReopen"] is not True
        or unreal_proof["verification"]["passed"] is not True
        or unreal_proof["source"]["manifestSha256"]
        != fingerprint(bundle / "environment-manifest.json")["sha256"]
    ):
        raise ValueError("Unreal proof must verify the same final environment bundle")
    day_proof = read(day_native / "verification.json")
    night_proof = read(night / "verification.json")
    glb_proof = read(config["glb_checks"])
    glb_proof = glb_proof.get("glb", glb_proof)
    if (
        glb_proof.get("perObjectMaterialAssignmentsChecked") is not True
        or glb_proof.get("assignedMaterialNamesPreserved") is not True
        or glb_proof.get("resourcesEmbedded") is not True
        or glb_proof.get("sha256") != fingerprint(bundle / "industrial-lab.glb")["sha256"]
    ):
        raise ValueError("Public GLB needs current per-object material and embedded-resource proof")
    for proof in (day_proof, night_proof):
        if proof.get("all_checks_passed") is not True:
            raise ValueError("Both native scenes require successful reopen verification")
    for asset in source_manifest["assets"]:
        if fingerprint(bundle / asset["exportFile"])["sha256"] != asset["fileSha256"]:
            raise ValueError("Native mesh bundle changed after export")
    for material in source_manifest["materials"]:
        for texture in material["textures"].values():
            if fingerprint(bundle / texture["path"])["sha256"] != texture["sha256"]:
                raise ValueError("Native texture bundle changed after export")

    # PNG metadata removal leaves IDAT pixel data untouched.
    views = []
    cameras = {camera["name"]: camera["transform"] for camera in source_manifest["cameras"]}
    for view in config["views"]:
        path = Path(view["source"])
        if view["host"].startswith("Unreal") and (
            fingerprint(path)["sha256"] != unreal_proof["render"]["sha256"]
        ):
            raise ValueError("Unreal gallery render must match the verified native capture")
        filename = view["id"] + ".png"
        (dest / filename).write_bytes(module.strip_png_metadata(path.read_bytes()))
        views.append(
            {
                key: value
                for key, value in {
                    "id": view["id"],
                    "label": view["label"],
                    "host": view["host"],
                    "caption": view["caption"],
                    "file": filename,
                    "camera": cameras.get(view.get("camera")),
                }.items()
                if value is not None
            }
        )
    (dest / "industrial-lab.glb").write_bytes((bundle / "industrial-lab.glb").read_bytes())
    (dest / "environment-manifest.json").write_bytes(
        (bundle / "environment-manifest.json").read_bytes()
    )
    evidence = {
        "observed_date": config["observed_date"],
        "blender_version": day_proof["blender_version"],
        "scene_totals": source_manifest["totals"],
        "bounds_m": source_manifest["boundsWorld"],
        "baseline": {
            "native_reopen_checks": day_proof["native_reopen"]["checks"],
            "scene": fingerprint(day_native / "scene.blend"),
            "glb": fingerprint(bundle / "industrial-lab.glb"),
            "glb_checks": glb_proof,
        },
        "revision": {
            "native_reopen_checks": night_proof["native_reopen"]["checks"],
            "scene": fingerprint(night / "scene.blend"),
            "changes": read(revision / "revision-evidence.json"),
        },
        "unreal": unreal_proof,
        "public_packaging": "PNG text metadata removed without pixel changes. Native scenes were reopened, then all archive members were byte-scanned under the locally configured publication policy, including decoded .blend bytes. This pattern scan is not a universal private-data guarantee; see publication-scan.json.",
    }
    (dest / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    payloads = {
        "scene-day.blend": (day_native / "scene.blend").read_bytes(),
        "scene-night.blend": (night / "scene.blend").read_bytes(),
        "industrial-lab.glb": (bundle / "industrial-lab.glb").read_bytes(),
        "evidence.json": (dest / "evidence.json").read_bytes(),
        "transfer/environment-manifest.json": (bundle / "environment-manifest.json").read_bytes(),
    }
    for asset in source_manifest["assets"]:
        payloads["transfer/" + asset["exportFile"]] = (bundle / asset["exportFile"]).read_bytes()
    for material in source_manifest["materials"]:
        for texture in material["textures"].values():
            payloads["transfer/" + texture["path"]] = (bundle / texture["path"]).read_bytes()
    for script in (repo / "examples/environments/industrial_lab").glob("*.py"):
        payloads["scripts/" + script.name] = script.read_bytes()
    payloads["scripts/environment_export.py"] = (
        repo / "src/studio_agent_bridge/host_scripts/blender/environment_export.py"
    ).read_bytes()
    payloads["README.txt"] = (
        "Original Industrial AI Laboratory — Studio Agent Bridge\n\n"
        "Open scene-day.blend or scene-night.blend in Blender. Textures are packed.\n"
        "The transfer/ directory contains a versioned scene manifest, local FBX meshes\n"
        "and PBR textures for the Studio Agent Bridge Unreal environment importer.\n"
        "The GLB is an evaluated visualization mesh, not the editable Blender source.\n"
        "Complete source, reproduction and chat instructions:\n"
        "https://bernardo-demier-ai-lab.fancy-fly-9200.chatgpt.site/studio-agent-bridge-source.zip\n"
        "After extracting the source archive, open examples/environments/industrial_lab/README.md.\n"
        "The repository copy is also available after the matching source commit is published:\n"
        "https://github.com/demier-demier/studio-agent-bridge/tree/main/examples/environments/industrial_lab\n"
        "Read evidence.json for observed checks and transfer limitations.\n"
        "Original procedural scene, scripts and textures by Bernardo Demier; MIT.\n"
        "Native applications and their licenses are separate.\n"
    ).encode()
    payloads["manifest.json"] = (
        json.dumps(
            {
                name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in sorted(payloads.items())
            },
            indent=2,
        )
        + "\n"
    ).encode()
    version = config["publication_version"]
    zip_path = dest / f"industrial-lab-native-v{version}.zip"
    publication = publish_native_archive(
        {"industrial-lab/" + name: data for name, data in payloads.items()},
        zip_path,
        version=version,
        forbidden_fragments=config["publication_forbidden_fragments"],
    )
    (dest / "publication-scan.json").write_text(json.dumps(publication, indent=2) + "\n")
    totals = source_manifest["totals"]
    case = {
        "views": views,
        "mesh": "industrial-lab.glb",
        "stats": [
            {"value": f"{totals['meshInstances']:,}", "label": "placed mesh instances"},
            {"value": str(totals["uniqueAssets"]), "label": "shared evaluated mesh assets"},
            {"value": str(totals["materials"]), "label": "PBR materials"},
            {"value": f"{totals['instanceTriangles']:,}", "label": "evaluated scene triangles"},
        ],
        "observed": config["observed"],
        "limitations": config["limitations"],
        "downloads": [
            {"file": zip_path.name, "label": "Native scenes, transfer bundle & scripts"},
            {"file": "industrial-lab.glb", "label": "Actual exported GLB"},
            {"file": "evidence.json", "label": "Recorded native evidence"},
            {"file": "environment-manifest.json", "label": "Environment scene manifest"},
        ],
    }
    (dest / "case.json").write_text(json.dumps(case, indent=2) + "\n")
    asset_manifest = {
        path.name: fingerprint(path)
        for path in sorted(dest.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    (dest / "manifest.json").write_text(json.dumps(asset_manifest, indent=2) + "\n")
    return {"files": len(asset_manifest), "zip": fingerprint(zip_path)}


def package(config: dict) -> dict:
    version = config.get("publication_version")
    if type(version) is not int or version < 2:
        raise PublicationError(
            "publication_version must be an explicit integer >=2; existing packages are never overwritten."
        )
    destination = Path(
        config.get(
            "publication_output", REPOSITORY / "web/public/assets/environment" / f"v{version}"
        )
    )
    if destination.name != f"v{version}" or destination.exists() or destination.is_symlink():
        raise PublicationError("publication_output must be a new v<VERSION> directory.")
    fragments = config.get("publication_forbidden_fragments")
    # Validate local-only policy before writing staging data; do not echo fragments.
    validate_payloads({"policy-check.txt": b""}, forbidden_fragments=fragments)
    with tempfile.TemporaryDirectory(prefix="studio-environment-package-") as temporary:
        staged = Path(temporary)
        result = build_package(config, staged)
        # Freeze exactly the bytes scanned below, then compare the private copied
        # directory to this manifest before its exclusive publication rename.
        payloads = {path.name: path.read_bytes() for path in staged.iterdir()}
        validate_payloads(
            {name: data for name, data in payloads.items() if not name.endswith(".zip")},
            forbidden_fragments=fragments,
        )
        for name, data in payloads.items():
            if name.endswith(".zip"):
                scan_archive(data, forbidden_fragments=fragments)
        expected_files = {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in payloads.items()
        }
        publish_directory(staged, destination, expected_files=expected_files)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(json.dumps(package(read(parser.parse_args().config))))


if __name__ == "__main__":
    main()
