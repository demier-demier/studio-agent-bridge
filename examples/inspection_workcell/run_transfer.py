"""Transfer one verified utility revision into a fresh private native workspace.

Run with the repository virtualenv. The input must be a completed run_utilities
directory, and the output must not exist. No resume, overwrite, publication or
automatic retry is performed. Native scripts run with the user's privileges.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
OWNER = "inspection-utilities-transfer/v1"
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_RENDER_BYTES = 32 * 1024 * 1024
SOURCE_SCRIPTS = ("complete_utilities.py", "verify_utilities.py", "disconnect_route.py")
STAGED_SCRIPTS = {
    "export_utilities.py": "examples/inspection_workcell/export_utilities.py",
    "environment_export.py": "src/studio_agent_bridge/host_scripts/blender/environment_export.py",
    "native_evidence.py": "examples/publication_safety/native_evidence.py",
    "fingerprint_scene.py": "examples/publication_safety/fingerprint_scene.py",
    "sanitize_scene.py": "examples/publication_safety/sanitize_scene.py",
    "sanitize_fbx.py": "examples/publication_safety/sanitize_fbx.py",
    "run_transfer.py": "examples/inspection_workcell/run_transfer.py",
}
CORE_SOURCES = (
    "engine.py", "workspace.py", "source_binding.py", "adapters/blender.py",
    "adapters/unreal.py", "host_scripts/blender/runner.py",
    "host_scripts/blender/source_resources.py", "host_scripts/unreal/dispatch.py",
    "host_scripts/unreal/environment.py", "host_scripts/unreal/environment_contract.py",
    "publication.py",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record(path):
    data = Path(path).read_bytes()
    return {"sha256": sha(data), "bytes": len(data)}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def absolute_path(path):
    """Normalize lexical dot segments without resolving away symlink evidence."""
    original = Path(path).absolute()
    if any(p.is_symlink() for p in (original, *original.parents)):
        raise ValueError("Native evidence must not traverse symlinks")
    return Path(os.path.abspath(original))


def local_file(root, relative):
    """Require an existing regular file without traversal or symlink components."""
    root = absolute_path(root)
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Expected a relative file inside the owned workspace")
    path = root / relative
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Native evidence must not traverse symlinks")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Missing native evidence or path escaped its workspace")
    return path


def native_artifact(result):
    candidates = [a for a in result["result"]["artifacts"]
                  if a["kind"] == "blend" and "/source_snapshot/" not in a["path"]]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one saved native scene artifact")
    return candidates[0]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_source(source_run):
    """Bind the accepted state, native artifact, readback and original scripts."""
    source_run = absolute_path(source_run)
    state_path = local_file(source_run, "state.json")
    state_bytes = state_path.read_bytes()
    state = json.loads(state_bytes)
    if state.get("owner") != "inspection-utilities-run/v1" or state.get("status") != "native-verified":
        raise ValueError("Source must be a completed native-verified run_utilities run")
    if state.get("original_source_unchanged") is not True:
        raise ValueError("Source run lacks preserved-input evidence")
    if (state.get("comparison", {}).get("existing_measured_mesh_fields_equal") is not True
            or state.get("comparison", {}).get("utility_readback_passed") is not True):
        raise ValueError("Source run lacks accepted geometry readback")
    workspace = source_run / "workspace"
    for name in SOURCE_SCRIPTS:
        script = local_file(workspace, "scripts/" + name)
        if record(script)["sha256"] != state["script_hashes"].get(name):
            raise ValueError("Source run script differs from its execution hash: " + name)
    built = state["actions"]["build"]
    fresh = state["actions"]["fresh-readback"]
    if built["status"] != "verified" or fresh["status"] != "verified":
        raise ValueError("Source build and fresh readback must both be verified")
    artifact = native_artifact(built)
    if artifact != state["native"] or artifact["kind"] != "blend":
        raise ValueError("Accepted native artifact differs from the verified build")
    binding = fresh["result"].get("observations", {}).get("source_binding", {})
    if (binding.get("source_path") != artifact["path"]
            or binding.get("expected_source_sha256") != artifact["sha256"]
            or binding.get("source_sha256_after_action") != artifact["sha256"]
            or binding.get("snapshot_sha256_after_action") != artifact["sha256"]
            or binding.get("source_unchanged") is not True
            or binding.get("snapshot_unchanged") is not True):
        raise ValueError("Fresh utility readback is not bound to the accepted native bytes")
    path = local_file(workspace, artifact["path"])
    if path.suffix != ".blend" or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Expected a bounded native Blender source")
    payload = path.read_bytes()
    expected = {"sha256": artifact["sha256"], "bytes": artifact["bytes"]}
    if {"sha256": sha(payload), "bytes": len(payload)} != expected:
        raise ValueError("Native source differs from its verified artifact hash or size")
    readback_path = local_file(workspace, fresh["run_dir"] + "/utility-readback.json")
    readback = json.loads(readback_path.read_text())
    if readback != state["readbacks"]["fresh"] or readback.get("passed") is not True:
        raise ValueError("Fresh readback evidence is missing, changed or failed")
    negative = state.get("negative_check", {})
    if (negative.get("reported_status") != "failed"
            or negative.get("readback", {}).get("passed") is not False
            or negative.get("original_native_unchanged") is not True):
        raise ValueError("Source run lacks its rejected disconnected-copy check")
    failure = negative["readback"]
    endpoint = failure.get("measurements", {}).get("routes", {}).get("Camera_L", {}).get("attachments", {}).get("end", {})
    gap = endpoint.get("endpoint_to_gland_centre_delta_m")
    if (negative.get("rejected_endpoint") != "Camera_L.end"
            or not isinstance(gap, (int, float)) or isinstance(gap, bool)
            or not math.isfinite(gap) or not 0.099 <= gap <= 0.101
            or negative.get("measured_gap_m") != gap
            or endpoint.get("checks", {}).get("endpoint_to_gland_centre_within_1mm") is not False
            or failure.get("errors") != []
            or any(key not in readback or failure.get(key) != readback[key]
                   for key in ("original_mesh_count", "original_fields_sha256"))):
        raise ValueError("Source negative check does not isolate the measured Camera_L.end defect")
    return state, state_bytes, path, payload


def prepare(source_run, output):
    output = absolute_path(output)
    source_run = absolute_path(source_run)
    if output.exists() or output.is_symlink():
        raise ValueError("Use a new output directory; prior evidence is never overwritten")
    source, state_bytes, native_path, payload = verify_source(source_run)
    scripts = {name: (REPOSITORY / relative).read_bytes()
               for name, relative in STAGED_SCRIPTS.items()}
    core = {name: record(REPOSITORY / "src/studio_agent_bridge" / name)["sha256"]
            for name in CORE_SOURCES}
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    (workspace / "source").mkdir(parents=True)
    (workspace / "scripts").mkdir()
    (output / "raw-evidence").mkdir()
    (workspace / "source/inspection-utilities.blend").write_bytes(payload)
    (output / "source-state.json").write_bytes(state_bytes)
    for name, content in scripts.items():
        (workspace / "scripts" / name).write_bytes(content)
    state = {
        "owner": OWNER, "status": "prepared", "actions": {},
        "source_run": str(source_run),
        "source_state": {"sha256": sha(state_bytes), "bytes": len(state_bytes)},
        "source_native": {"path": str(native_path), "sha256": sha(payload), "bytes": len(payload)},
        "source_fresh_readback": {
            "path": str(source_run / "workspace" /
                        source["actions"]["fresh-readback"]["run_dir"] / "utility-readback.json"),
            **record(source_run / "workspace" /
                     source["actions"]["fresh-readback"]["run_dir"] / "utility-readback.json"),
        },
        "source_run_script_hashes": source["script_hashes"],
        "script_hashes": {name: sha(content) for name, content in scripts.items()},
        "core_source_hashes": core,
        "limits": {"max_assets": 256, "max_objects": 5000, "native_timeout_seconds": 900},
        "visual_review": "pending", "published": False,
    }
    write_json(output / "state.json", state)
    return workspace, state


def transfer_members(manifest):
    assets = {item["exportFile"] for item in manifest["assets"]}
    textures = {item["path"] for material in manifest["materials"]
                for item in material.get("textures", {}).values()}
    if assets & textures:
        raise ValueError("Mesh and texture bundle paths overlap")
    return assets, textures


def rewrite_worker(config_path):
    """Run only with Blender's bundled Python so its FBX codec can import NumPy."""
    config = json.loads(Path(config_path).read_text())
    workspace = absolute_path(config["workspace"])
    source = workspace / "source-transfer"
    clean = workspace / "clean-transfer"
    if clean.exists():
        raise FileExistsError("Clean transfer requires a fresh directory")
    helper = load_module("utility_fbx_sanitizer", workspace / "scripts/sanitize_fbx.py")
    original_bytes = (source / "environment-manifest.json").read_bytes()
    original = json.loads(original_bytes)
    changed = json.loads(original_bytes)
    assets, textures = transfer_members(original)
    clean.mkdir()
    for relative in sorted(textures | {"environment-manifest.json"}):
        destination = clean / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(local_file(source, relative).read_bytes())
    reports = []
    for asset in changed["assets"]:
        relative = asset["exportFile"]
        before = local_file(source, relative)
        if record(before) != {"sha256": asset["fileSha256"], "bytes": asset["exportBytes"]}:
            raise ValueError("Original FBX differs from its exported manifest")
        destination = clean / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        report = helper.sanitize_fbx(
            before, destination, bundle_root=clean,
            manifest_path=clean / "environment-manifest.json",
            module_directory=Path(config["fbx_modules"]),
            forbidden_fragments=config["forbidden_fragments"],
        )
        report["member"] = relative
        reports.append(report)
        asset.update(fileSha256=report["output_sha256"], exportBytes=report["output_bytes"])
    normalized = json.loads(json.dumps(changed))
    for before, after in zip(original["assets"], normalized["assets"]):
        after["fileSha256"], after["exportBytes"] = before["fileSha256"], before["exportBytes"]
    if normalized != original:
        raise ValueError("FBX sanitation changed semantic manifest fields")
    if (source / "environment-manifest.json").read_bytes() != original_bytes:
        raise ValueError("Source manifest changed during FBX sanitation")
    write_json(clean / "environment-manifest.json", changed)
    if {p.relative_to(clean).as_posix() for p in clean.rglob("*") if p.is_file()} != assets | textures | {"environment-manifest.json"}:
        raise ValueError("Clean transfer has an unexpected or missing member")
    summary = {
        "files": len(reports), "records": reports,
        "source_manifest": record(source / "environment-manifest.json"),
        "clean_manifest": record(clean / "environment-manifest.json"),
        "manifest_changes_only_file_hashes_and_byte_counts": True,
        "all_typed_tree_checks_passed": all(r["typed_tree_matches_exact_rewrite"] for r in reports),
    }
    target = Path(config["report"])
    with target.open("x") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.write("\n")


def verified_unreal_result(result, manifest, manifest_sha256):
    verification = result["result"]["observations"]["native"]["verification"]
    totals = manifest["totals"]
    if (result["status"] != "verified" or verification.get("passed") is not True
            or verification.get("reopened") is not True
            or verification.get("transformChecksPassed") is not True
            or verification.get("manifestSha256") != manifest_sha256
            or verification.get("objectCount") != totals["meshInstances"]
            or len(verification.get("meshAssets", [])) != totals["uniqueAssets"]
            or len(verification.get("materials", [])) != totals["materials"]
            or len(verification.get("lights", [])) != totals["lights"]
            or len(verification.get("cameras", [])) != totals["cameras"]
            or not all(asset.get("boundsCheck", {}).get("passed") is True
                       for asset in verification.get("meshAssets", []))
            or not all(material.get("passed") is True for material in verification.get("materials", []))):
        raise ValueError("Reopened Unreal evidence does not match the exact cleaned transfer")
    return verification


def png_metadata(data, expected_size):
    """Check bounded PNG framing and every CRC; retain only-zero native padding."""
    if not 45 <= len(data) <= MAX_RENDER_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Expected a bounded nonempty PNG")
    offset, chunks, idat_bytes = 8, 0, 0
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG chunk payload")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError("PNG chunk CRC mismatch")
        if chunks == 0:
            if kind != b"IHDR" or length != 13 or struct.unpack(">II", payload[:8]) != expected_size:
                raise ValueError("PNG IHDR does not match the recorded render dimensions")
        elif kind == b"IHDR":
            raise ValueError("PNG has a duplicate IHDR")
        chunks += 1
        if kind == b"IDAT":
            idat_bytes += length
        if kind == b"IEND":
            if length != 0 or idat_bytes == 0:
                raise ValueError("PNG requires nonempty IDAT and zero-length IEND")
            padding = data[end:]
            if any(padding):
                raise ValueError("PNG has nonzero trailing data after IEND")
            return {"dimensions": list(expected_size), "png_crc_chunks_verified": chunks,
                    "png_bytes_through_iend": end, "zero_padding_bytes": len(padding)}
        offset = end
    raise ValueError("PNG is missing IEND")


def verified_render_images(workspace):
    expected = {"unreal-close-build.png": (1100, 850),
                "unreal-close-reopened.png": (1100, 850),
                "unreal-wide-reopened.png": (1280, 720)}
    directory = workspace / "renders"
    if not directory.is_dir() or {p.name for p in directory.iterdir()} != set(expected):
        raise ValueError("Exactly the three expected Unreal PNG files are required")
    images = {}
    for name, size in expected.items():
        path = local_file(workspace, "renders/" + name)
        with path.open("rb") as stream:
            data = stream.read(MAX_RENDER_BYTES + 1)
        metadata = png_metadata(data, size)
        images["renders/" + name] = {"sha256": sha(data), "bytes": len(data), **metadata}
    return images


def run(args):
    sys.path.insert(0, str(REPOSITORY / "src"))
    from studio_agent_bridge.adapters import blender, unreal
    from studio_agent_bridge.engine import Engine
    from studio_agent_bridge.host_scripts.unreal.environment_contract import validate_manifest
    from studio_agent_bridge.publication import scan_blend, validate_payloads

    output = absolute_path(args.output)
    workspace, state = prepare(args.source_run, output)
    fragments = [Path.home().name]
    config = {"allow_scripts": True, "blender_timeout_seconds": 900,
              "unreal_timeout_seconds": 900,
              "unreal_project": "unreal/InspectionUtilities/InspectionUtilities.uproject"}
    if args.blender:
        config["blender_executable"] = args.blender
    if args.unreal:
        config["unreal_executable"] = args.unreal
    engine = Engine(workspace, config, lock_dir=output / "locks")

    def save():
        write_json(output / "state.json", state)

    def phase(name):
        state["status"] = "running-" + name
        save()

    def action(job, label, operation, parameters):
        if label in state["actions"]:
            raise ValueError("Refusing to replay a recorded action")
        phase(label)
        result = engine.execute_action(job["id"], operation, parameters,
                                       engine.inspect_job(job["id"])["revision"])
        state["actions"][label] = result
        if result["status"] == "verified" and operation == "run_script":
            state["latest_native_artifact"] = {"action": label, "artifact": native_artifact(result)}
        write_json(output / "raw-evidence" / (label + ".json"), result)
        save()
        print(label, result["status"], flush=True)
        if result["status"] != "verified":
            raise RuntimeError("Native action did not verify; inspect the retained evidence")
        return result

    def native_parameters(path, digest, script, parameters):
        return {"input_blend": path, "expected_source_sha256": digest,
                "script_path": "scripts/" + script, "script_parameters": parameters,
                "render": False, "export_collection": "StudioEnvironment",
                "include_presentation": True}

    def evidence(result, filename):
        return json.loads(local_file(workspace, result["run_dir"] + "/" + filename).read_text())

    try:
        state["host_probes"] = {"blender": blender.probe(config), "unreal": unreal.probe(config)}
        save()
        if not all(probe["available"] for probe in state["host_probes"].values()):
            raise RuntimeError("Both installed native executables are required")
        job = engine.create_job("inspection-transfer-source", "blender",
                                "Sanitize a separate editable copy, compare fresh readback and export six cable meshes")
        state["blender_job"] = job["id"]
        clean = action(job, "sanitize-editable-copy", "run_script", native_parameters(
            "source/inspection-utilities.blend", state["source_native"]["sha256"],
            "native_evidence.py", {"mode": "sanitize"}))
        editable = native_artifact(clean)
        fresh = action(job, "fresh-editable-readback", "run_script", native_parameters(
            editable["path"], editable["sha256"], "native_evidence.py", {"mode": "read"}))
        phase("compare-editable-copy")
        comparator = load_module("utility_scene_comparator", workspace / "scripts/fingerprint_scene.py")
        before = evidence(clean, "fingerprint-before-details.json")
        in_memory = evidence(clean, "fingerprint-after-details.json")
        reopened = evidence(fresh, "fingerprint-before-details.json")
        comparisons = {"in_memory": comparator.compare_records(before, in_memory),
                       "reopened": comparator.compare_records(before, reopened)}
        if not all(value["passed"] for value in comparisons.values()):
            raise ValueError("Sanitized editable copy changed measured scene content")
        scan = scan_blend(local_file(workspace, editable["path"]), forbidden_fragments=fragments)
        state["editable_scene"] = {"artifact": editable, "comparison": comparisons,
                                   "byte_scan": scan,
                                   "sanitization": evidence(clean, "scene-fingerprint.json")["sanitization"]}
        save()
        if not scan["passed"]:
            raise ValueError("Sanitized editable native copy failed its decoded byte scan")
        exported = action(job, "export-exchange-copy", "run_script", native_parameters(
            editable["path"], editable["sha256"], "export_utilities.py", {}))
        report = evidence(exported, "export-conversion.json")
        if (report.get("status") != "exported" or report.get("all_six_routes_in_manifest") is not True
                or report.get("all_six_routes_in_glb") is not True):
            raise ValueError("Six-cable conversion/export verification is incomplete")
        state["export"] = {"conversion": report, "glb_checks": evidence(exported, "glb-export-checks.json"),
                           "exchange_scene": native_artifact(exported),
                           "run_dir": exported["run_dir"]}
        phase("sanitize-transfer")
        export_dir = workspace / exported["run_dir"]
        manifest = validate_manifest(export_dir / "environment-manifest.json")
        source_transfer = workspace / "source-transfer"
        source_transfer.mkdir()
        assets, textures = transfer_members(manifest)
        for relative in sorted(assets | textures | {"environment-manifest.json"}):
            destination = source_transfer / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(local_file(export_dir, relative).read_bytes())
        worker_config = output / "fbx-worker.json"
        write_json(worker_config, {"workspace": str(workspace), "fbx_modules": str(args.fbx_modules),
                                   "forbidden_fragments": fragments, "report": str(output / "fbx-proof.json")})
        with (output / "fbx-worker.log").open("x") as log:
            subprocess.run([str(args.blender_python), str(workspace / "scripts/run_transfer.py"),
                            "--rewrite-worker", str(worker_config)], check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=900)
        state["fbx"] = json.loads((output / "fbx-proof.json").read_text())
        clean_manifest_path = workspace / "clean-transfer/environment-manifest.json"
        clean_manifest = validate_manifest(clean_manifest_path)
        if record(clean_manifest_path) != state["fbx"]["clean_manifest"]:
            raise ValueError("Clean manifest differs from its codec evidence")
        clean_bundle = workspace / "clean-transfer"
        payloads = {p.relative_to(clean_bundle).as_posix(): p.read_bytes()
                    for p in clean_bundle.rglob("*") if p.is_file()}
        payloads["inspection-utilities.glb"] = (export_dir / "inspection-utilities.glb").read_bytes()
        state["transfer_byte_scan"] = validate_payloads(payloads, forbidden_fragments=fragments)
        if not state["transfer_byte_scan"]["passed"]:
            raise ValueError("Transfer or GLB bytes failed the configured publication pattern scan")
        target = engine.create_job("inspection-utilities-unreal", "unreal",
                                   "Build a fresh GPU project, capture real images and independently reopen saved resources")
        state["unreal_job"] = target["id"]
        action(target, "unreal-create-project", "create_project", {"name": "InspectionUtilities"})
        destination = "/Game/StudioAgentBridge/Environments/InspectionUtilities"
        parameters = {"environment_manifest": "clean-transfer/environment-manifest.json",
                      "destination": destination, "name": "InspectionUtilities",
                      "level_path": destination + "/Maps/InspectionUtilities",
                      "simple_collision": True, "rendering": "gpu",
                      "camera_name": "InspectionV2_Camera_Close", "render_size": [1100, 850],
                      "render_exposure": args.render_exposure,
                      "render_output": "renders/unreal-close-build.png"}
        action(target, "unreal-gpu-build", "build_scene", parameters)
        parameters["render_output"] = "renders/unreal-close-reopened.png"
        reopened_target = action(target, "unreal-fresh-inspect-close", "inspect", parameters)
        parameters.update(camera_name="InspectionV2_Camera_Wide", render_size=[1280, 720],
                          render_output="renders/unreal-wide-reopened.png")
        wide_target = action(target, "unreal-fresh-inspect-wide", "inspect", parameters)
        verification = verified_unreal_result(reopened_target, clean_manifest, state["fbx"]["clean_manifest"]["sha256"])
        verified_unreal_result(wide_target, clean_manifest, state["fbx"]["clean_manifest"]["sha256"])
        state["unreal_target"] = {
            "reopened": True, "transform_checks_passed": True,
            "manifest_sha256": verification["manifestSha256"],
            "mesh_actors": verification["objectCount"], "mesh_assets": len(verification["meshAssets"]),
            "materials": len(verification["materials"]), "lights": len(verification["lights"]),
            "cameras": len(verification["cameras"]),
            "close_and_wide_reopen_checks_passed": True,
            "images": verified_render_images(workspace),
            "visual_review": "pending", "appearance_validated": False,
        }
        phase("final-source-integrity")
        if (record(Path(state["source_run"]) / "state.json") != state["source_state"]
                or record(Path(state["source_native"]["path"])) !=
                {k: state["source_native"][k] for k in ("sha256", "bytes")}):
            raise ValueError("Accepted source state or native file changed during transfer")
        if record(Path(state["source_fresh_readback"]["path"])) != {
                k: state["source_fresh_readback"][k] for k in ("sha256", "bytes")}:
            raise ValueError("Accepted fresh utility readback changed during transfer")
        for name, digest in state["script_hashes"].items():
            if record(workspace / "scripts" / name)["sha256"] != digest:
                raise ValueError("A staged execution script changed during transfer")
        for name, digest in state["core_source_hashes"].items():
            if record(REPOSITORY / "src/studio_agent_bridge" / name)["sha256"] != digest:
                raise ValueError("A core execution source changed during transfer")
        if record(local_file(workspace, editable["path"])) != {k: editable[k] for k in ("sha256", "bytes")}:
            raise ValueError("Export changed the accepted editable native scene")
        state.update(status="native-transfer-verified", source_bytes_unchanged=True,
                     original_editable_curves_preserved=True, published=False)
        save()
        print(json.dumps({"status": state["status"], "visual_review": "pending", "published": False}))
        return state
    except BaseException as error:
        state["failure"] = {"phase": state["status"], "type": type(error).__name__, "message": str(error)}
        state["status"] = "failed"
        save()
        raise


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--rewrite-worker":
        rewrite_worker(Path(sys.argv[2]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blender")
    parser.add_argument("--unreal")
    parser.add_argument("--render-exposure", type=float, default=-7)
    parser.add_argument("--blender-python", type=Path,
                        default=Path("/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13"))
    parser.add_argument("--fbx-modules", type=Path,
                        default=Path("/Applications/Blender.app/Contents/Resources/5.2/scripts/addons_core/io_scene_fbx"))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
