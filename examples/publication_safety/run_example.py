#!/usr/bin/env python3
"""Create measured versioned publication copies while preserving raw inputs.

Native stages use a real Engine, separate workspaces and pinned source files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from studio_agent_bridge.adapters import blender  # noqa: E402
from studio_agent_bridge.adapters import unreal as unreal_adapter  # noqa: E402
from studio_agent_bridge.engine import Engine  # noqa: E402
from studio_agent_bridge.workspace import file_digest  # noqa: E402

OWNER = "studio-publication-safety-example/v1"
SCENES = ("scene-day.blend", "scene-night.blend")
SCRIPTS = ("fingerprint_scene.py", "sanitize_scene.py", "native_evidence.py")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def hashes():
    paths = (
        "src/studio_agent_bridge/engine.py",
        "src/studio_agent_bridge/workspace.py",
        "src/studio_agent_bridge/source_binding.py",
        "src/studio_agent_bridge/adapters/blender.py",
        "src/studio_agent_bridge/host_scripts/blender/runner.py",
        "src/studio_agent_bridge/host_scripts/blender/source_resources.py",
        *("examples/publication_safety/" + name for name in SCRIPTS),
    )
    return {name: file_digest(REPOSITORY / name) for name in paths}


def verified_original_members(archive):
    names = archive.namelist()
    if len(names) != len(set(names)) or any(name.endswith("/") for name in names):
        raise ValueError("Source archive must have unique regular member names")
    manifest_name = "industrial-lab/manifest.json"
    manifest = json.loads(archive.read(manifest_name))
    expected = {"industrial-lab/" + name for name in manifest}
    if set(names) - {manifest_name} != expected:
        raise ValueError("Source archive members do not match the complete manifest")
    payloads = {name: archive.read(name) for name in names}
    for name, data in payloads.items():
        if name == manifest_name:
            continue
        if {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} != manifest[
            name.removeprefix("industrial-lab/")
        ]:
            raise ValueError("Original archive member failed manifest verification")
    return payloads


def prepare(root, source):
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new empty work root; original evidence is never overwritten.")
    root.mkdir(parents=True)
    original = root / "original.zip"
    shutil.copyfile(source, original)
    if file_digest(source) != file_digest(original):
        raise ValueError("Private raw archive copy does not match the input.")
    source_digest = file_digest(original)
    workspace = root / "workspace"
    extracted = {}
    with zipfile.ZipFile(original) as archive:
        verified_original_members(archive)
        manifest = json.loads(archive.read("industrial-lab/manifest.json"))
        for name in SCENES:
            data = archive.read("industrial-lab/" + name)
            record = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            if record != manifest[name]:
                raise ValueError("Native input does not match the public archive manifest.")
            path = workspace / "source" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            extracted[name] = record
    state = {
        "owner": OWNER,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "prepared",
        "source_archive": {
            "filename": source.name,
            "sha256": source_digest,
            "bytes": original.stat().st_size,
            "preserved_copy": "original.zip",
        },
        "source_scenes": extracted,
        "actions": {},
        "scenes": {},
    }
    write_json(root / "state.json", state)
    return state


def read_state(root):
    state = json.loads((root / "state.json").read_text())
    if state.get("owner") != OWNER:
        raise ValueError("This example does not own that work root.")
    return state


class Audit:
    def __init__(self, root):
        self.records, self.phase, self.root = [], "setup", root
        sys.addaudithook(self.observe)

    def observe(self, event, args):
        if event != "subprocess.Popen":
            return
        executable = Path(os.fsdecode(args[0])).name
        record = {
            "phase": self.phase,
            "executable": executable,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.records.append(record)
        with (self.root / "process-audit.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def count(self, phase):
        return sum(
            item["phase"] == phase
            and item["executable"] in {"Blender", "blender", "UnrealEditor", "UnrealEditor-Cmd"}
            for item in self.records
        )


def engine_for(root, args):
    config = {
        "allow_scripts": True,
        "blender_timeout_seconds": 900,
        "unreal_timeout_seconds": 900,
        "unreal_project": "unreal/PublicationLab/PublicationLab.uproject",
    }
    if args.blender:
        config["blender_executable"] = args.blender
    if args.unreal:
        config["unreal_executable"] = args.unreal
    return Engine(root / "workspace", config, lock_dir=root / "locks")


def act(engine, state, root, audit, job_id, label, action, parameters):
    if label in state["actions"]:
        raise ValueError("Refusing to repeat an existing action label.")
    before = engine.inspect_job(job_id)
    audit.phase = label
    result = engine.execute_action(job_id, action, parameters, before["revision"])
    record = {
        "action": action,
        "status": result["status"],
        "revision_before": before["revision"],
        "revision_after": result["revision"],
        "native_process_launches": audit.count(label),
        "result": result,
    }
    state["actions"][label] = record
    write_json(root / "raw-evidence" / (label + ".json"), record)
    write_json(root / "state.json", state)
    if record["status"] != "verified":
        raise RuntimeError(
            "Native operation did not verify: "
            + result.get("result", {}).get("summary", record["status"])
        )
    return record


def output_blend(record):
    return next(
        item["path"]
        for item in record["result"]["result"]["artifacts"]
        if item["kind"] == "blend" and "/source_snapshot/" not in item["path"]
    )


def read_fingerprint(root, record):
    return json.loads(
        (root / "workspace" / record["result"]["run_dir"] / "scene-fingerprint.json").read_text()
    )


def native(root, args, state):
    if state["stage"] != "prepared":
        raise ValueError("Native stage requires prepared inputs.")
    workspace = root / "workspace"
    scripts = workspace / f"scripts/publication_safety/attempt-{args.attempt}"
    if scripts.exists():
        raise ValueError("Use a new explicit attempt number to preserve prior script evidence.")
    scripts.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        shutil.copyfile(REPOSITORY / "examples/publication_safety" / name, scripts / name)
    engine, audit = engine_for(root, args), Audit(root)
    probe = blender.probe(engine.config)
    if not probe["available"]:
        raise RuntimeError("Blender unavailable")
    state["blender_version"] = probe["version"]
    state["execution_sources"] = hashes()
    state.setdefault("native_attempts", []).append(
        {"attempt": args.attempt, "execution_sources": hashes()}
    )
    job = engine.create_job(
        "publication-native-copies",
        "blender",
        "Sanitize paths in separate native copies and independently verify measured scene content",
    )
    state["blender_job"] = job["id"]
    for name in SCENES:
        parameters = {
            "input_blend": "source/" + name,
            "expected_source_sha256": state["source_scenes"][name]["sha256"],
            "script_path": f"scripts/publication_safety/attempt-{args.attempt}/native_evidence.py",
            "script_parameters": {"mode": "sanitize"},
            "render": False,
            "export_collection": "StudioEnvironment",
            "include_presentation": True,
        }
        sanitized = act(
            engine,
            state,
            root,
            audit,
            job["id"],
            f"attempt-{args.attempt}-" + name + "-sanitize",
            "run_script",
            parameters,
        )
        report = read_fingerprint(root, sanitized)
        clean = output_blend(sanitized)
        parameters.update(
            input_blend=clean,
            expected_source_sha256=file_digest(workspace / clean),
            script_parameters={"mode": "read"},
        )
        reopened = act(
            engine,
            state,
            root,
            audit,
            job["id"],
            f"attempt-{args.attempt}-" + name + "-fresh-fingerprint",
            "run_script",
            parameters,
        )
        fresh = read_fingerprint(root, reopened)
        spec = importlib.util.spec_from_file_location(
            "scene_compare", REPOSITORY / "examples/publication_safety/fingerprint_scene.py"
        )
        comparison_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(comparison_module)
        baseline_details = json.loads(
            (
                workspace / sanitized["result"]["run_dir"] / "fingerprint-before-details.json"
            ).read_text()
        )
        reopened_details = json.loads(
            (
                workspace / reopened["result"]["run_dir"] / "fingerprint-before-details.json"
            ).read_text()
        )
        comparison = comparison_module.compare_records(baseline_details, reopened_details)
        if not report["comparison"]["passed"] or not comparison["passed"]:
            raise ValueError("Saved native publication copy changed a measured scene field.")
        state["scenes"][name] = {
            "source": state["source_scenes"][name],
            "output_path": clean,
            "publication": {
                "sha256": file_digest(workspace / clean),
                "bytes": (workspace / clean).stat().st_size,
            },
            "before": report["before"],
            "after_in_memory": report["after"],
            "after_reopen": fresh["before"],
            "sanitization": report["sanitization"],
            "fingerprints_equal": report["before"] == report["after"] == fresh["before"],
            "comparison": {"in_memory": report["comparison"], "reopened": comparison},
        }
        write_json(root / "state.json", state)
    assert hashes() == state["execution_sources"], "Source changed during native execution."
    assert file_digest(root / "original.zip") == state["source_archive"]["sha256"]
    state["stage"] = "native-complete"
    write_json(root / "state.json", state)
    return state


def recheck_observations(root, state):
    spec = importlib.util.spec_from_file_location(
        "scene_comparator", REPOSITORY / "examples/publication_safety/fingerprint_scene.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, scene in state["scenes"].items():
        found = {}
        for label, record in state["actions"].items():
            if record["status"] == "verified" and name in label:
                kind = "sanitized" if label.endswith("-sanitize") else "reopened"
                found[kind] = root / "workspace" / record["result"]["run_dir"]
        original = json.loads((found["sanitized"] / "fingerprint-before-details.json").read_text())
        memory = json.loads((found["sanitized"] / "fingerprint-after-details.json").read_text())
        reopened = json.loads((found["reopened"] / "fingerprint-before-details.json").read_text())
        comparisons = {
            "in_memory": module.compare_records(original, memory),
            "reopened": module.compare_records(original, reopened),
        }
        if not all(item["passed"] for item in comparisons.values()):
            raise ValueError("Recorded observations failed current acceptance comparator")
        scene["comparison"] = comparisons
        scene["raw_evaluated_hashes"] = {
            label: module.digest(value["evaluated_geometry"])
            for label, value in (
                ("before", original),
                ("after_in_memory", memory),
                ("after_reopen", reopened),
            )
        }
    state["acceptance_validation"] = {
        "comparator_sha256": file_digest(
            REPOSITORY / "examples/publication_safety/fingerprint_scene.py"
        ),
        "recorded_observations_rechecked": True,
        "scope": "Final typed/numeric acceptance comparator rerun on retained raw native observations; original native execution script hashes remain unchanged",
    }
    write_json(root / "state.json", state)


def unreal(root, args, state):
    if state["stage"] != "transfer-complete":
        raise ValueError("Sanitized transfer must finish first")
    engine, audit = engine_for(root, args), Audit(root)
    core = {
        name: file_digest(REPOSITORY / name)
        for name in (
            "src/studio_agent_bridge/engine.py",
            "src/studio_agent_bridge/adapters/unreal.py",
            "src/studio_agent_bridge/host_scripts/unreal/dispatch.py",
            "src/studio_agent_bridge/host_scripts/unreal/environment.py",
            "src/studio_agent_bridge/host_scripts/unreal/environment_contract.py",
        )
    }
    state["unreal_execution_sources"] = core
    state["unreal_version"] = unreal_adapter.probe(engine.config)["version"]
    job = engine.create_job(
        "publication-transfer-v2",
        "unreal",
        "Build and reopen a new target from the sanitized versioned transfer",
    )
    state["unreal_job"] = job["id"]
    act(
        engine,
        state,
        root,
        audit,
        job["id"],
        "unreal-create-owned-project",
        "create_project",
        {"name": "PublicationLab"},
    )
    destination = "/Game/StudioAgentBridge/Environments/PublicationLabV2"
    parameters = {
        "environment_manifest": "clean-transfer/environment-manifest.json",
        "destination": destination,
        "name": "PublicationLabV2",
        "level_path": destination + "/Maps/PublicationLabV2",
        "rendering": "gpu",
    }
    act(engine, state, root, audit, job["id"], "unreal-v2-gpu-build", "build_scene", parameters)
    record = act(
        engine, state, root, audit, job["id"], "unreal-v2-fresh-inspection", "inspect", parameters
    )
    value = record["result"]["result"]["observations"]["native"]["verification"]
    state["unreal_target"] = {
        "status": "verified",
        "mesh_actors": value["objectCount"],
        "mesh_assets": len(value["meshAssets"]),
        "materials": len(value["materials"]),
        "lights": len(value["lights"]),
        "cameras": len(value["cameras"]),
        "shared_mesh_instances": value["sharedMeshInstances"],
        "reopened": value["reopened"],
        "transform_checks_passed": value["transformChecksPassed"],
        "max_mesh_bounds_residual_cm": max(
            item["boundsCheck"]["maxResidualCm"] for item in value["meshAssets"]
        ),
        "manifest_sha256": value["manifestSha256"],
        "appearance_validated": False,
    }
    assert all(file_digest(REPOSITORY / name) == sha for name, sha in core.items())
    state["stage"] = "unreal-complete"
    write_json(root / "state.json", state)
    return state


def bounded_payload(path, workspace):
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(workspace.resolve())
        or not path.is_file()
    ):
        raise ValueError("Publication input must be a regular file in the owned workspace")
    with path.open("rb") as stream:
        data = stream.read(512 * 1024 * 1024 + 1)
    if len(data) > 512 * 1024 * 1024:
        raise ValueError("Publication member exceeds its byte bound")
    return data


def publish(root, state, output_directory=None):
    from payload_binding import bind_payloads, create_output_directory

    from studio_agent_bridge.publication import publish_native_archive, scan_archive, scan_blend

    if state["stage"] not in {"unreal-complete", "publication-complete"}:
        raise ValueError("Fresh Unreal validation must finish before publication packaging")
    fragments = [Path.home().name]
    workspace = root / "workspace"
    with zipfile.ZipFile(root / "original.zip") as source:
        original = verified_original_members(source)
    scene_bytes = {
        name: bounded_payload(workspace / value["output_path"], workspace)
        for name, value in state["scenes"].items()
    }
    clean = workspace / "clean-transfer"
    transfer_bytes = {
        path.relative_to(clean).as_posix(): bounded_payload(path, workspace)
        for path in clean.rglob("*")
        if path.is_file()
    }
    if state["fbx"]["publication_manifest_sha256"] != state["unreal_target"]["manifest_sha256"]:
        raise ValueError("FBX manifest evidence differs from native target evidence")
    payloads = bind_payloads(
        original,
        scene_bytes,
        transfer_bytes,
        {name: value["publication"] for name, value in state["scenes"].items()},
        state["fbx"]["records"],
        state["unreal_target"]["manifest_sha256"],
    )
    scenes = []
    for name, value in state["scenes"].items():
        scan = scan_blend(payloads["industrial-lab/" + name], forbidden_fragments=fragments)
        if not scan["passed"]:
            raise ValueError("Cleaned native copy failed publication scan")
        scenes.append(
            {
                "filename": name,
                **{
                    key: value[key]
                    for key in (
                        "source",
                        "publication",
                        "before",
                        "after_in_memory",
                        "after_reopen",
                        "comparison",
                        "raw_evaluated_hashes",
                    )
                },
                "normalized_fingerprints_equal": value["fingerprints_equal"],
                "scan": scan,
            }
        )
    actions = []
    failures = []
    for label, record in state["actions"].items():
        actions.append(
            {
                "id": label,
                "action": record["action"],
                "status": record["status"],
                "revision_before": record["revision_before"],
                "revision_after": record["revision_after"],
                "native_process_launches": record["native_process_launches"],
            }
        )
        if record["status"] != "verified":
            reason = "Fingerprint measurement rejected a candidate before native save; no output accepted"
            if label == "scene-day.blend-sanitize":
                reason = "Initial reader did not support INT16_2D mesh attributes; stopped before sanitization"
            elif label.startswith("attempt-2-"):
                reason = "Initial text-curve type accessor was incompatible with Blender5.2; stopped before sanitization"
            elif label.startswith("attempt-3-"):
                reason = "Before/after fingerprints differed and the candidate was rejected before save; detailed after-state was not retained for this attempt"
            elif label.startswith("attempt-4-"):
                reason = "Exact evaluated comparison rejected a recomputed UV float difference; later acceptance uses a declared numeric bound"
            folder = workspace / "scripts/publication_safety"
            if label.startswith("attempt-"):
                folder /= label.split("-scene-")[0]
            failures.append(
                {
                    "action_id": label,
                    "status": record["status"],
                    "accepted_output": False,
                    "detail": reason,
                    "script_hashes": {name: file_digest(folder / name) for name in SCRIPTS},
                }
            )
    payloads["industrial-lab/README.txt"] = (
        "Industrial AI Laboratory — version 2 publication copies\n\n"
        "Open scene-day.blend or scene-night.blend in Blender. Packed textures are included.\n"
        "These scenes were sanitized through native RNA path properties, saved, independently reopened, and compared using explicit current-frame fingerprints. They are new publication copies with new file hashes, not byte-identical historical originals.\n"
        "FBX path metadata was rewritten through Blender's official codec. All typed numeric arrays and non-path properties were preserved and the complete target was built and freshly inspected in a new Unreal project. No new image or renderer-equivalence claim is made.\n\n"
        "publication.json contains source-to-publication hashes, measured comparisons and limitations. evidence.json is preserved byte-for-byte historical evidence; its original native and transfer hashes describe those earlier inputs. manifest.json describes the actual members of this v2 archive.\n"
        "The bundled scripts are preserved historical source, including the older sanitizer. For current sanitation and publication use examples/publication_safety/README.md and sanitize_scene.py in the repository. Do not treat the historical sanitizer alone as a publication gate.\n"
        "https://github.com/demier-demier/studio-agent-bridge/tree/main/examples/publication_safety\n"
    ).encode()
    unchanged = [
        {
            "member": name.removeprefix("industrial-lab/"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        for name, data in original.items()
        if name != "industrial-lab/manifest.json" and payloads.get(name) == data
    ]
    old_manifest = json.loads(original["industrial-lab/transfer/environment-manifest.json"])
    target = state["unreal_target"]
    checks = {
        "frozen_payloads_match_native_and_transfer_evidence": True,
        "all_original_payload_members_match_manifest": True,
        "raw_original_archive_preserved": file_digest(root / "original.zip")
        == state["source_archive"]["sha256"],
        "raw_native_inputs_preserved": all(
            file_digest(workspace / "source" / name) == value["sha256"]
            for name, value in state["source_scenes"].items()
        ),
        "both_scenes_independently_reopened": len(scenes) == 2
        and all(value["comparison"]["reopened"]["passed"] for value in scenes),
        "non_evaluated_scene_categories_exact": all(
            value["comparison"]["reopened"]["exact_categories_equal"] for value in scenes
        ),
        "evaluated_scene_values_within_declared_bound": all(
            value["comparison"]["reopened"]["max_evaluated_float_residual"]
            <= value["comparison"]["reopened"]["evaluated_float_tolerance"]
            for value in scenes
        ),
        "both_native_byte_scans_passed": all(value["scan"]["passed"] for value in scenes),
        "fbx_typed_trees_preserved_except_allowed_paths": state["fbx"]["typed_comparison_passed"],
        "all_fbx_inputs_preserved": all(
            value["source_preserved"] for value in state["fbx"]["records"]
        ),
        "transfer_manifest_changes_are_hashes_and_sizes_only": state["fbx"][
            "manifest_changes_only_file_hashes_and_byte_counts"
        ],
        "historical_evidence_bytes_preserved": payloads["industrial-lab/evidence.json"]
        == original["industrial-lab/evidence.json"],
        "historical_scripts_bytes_preserved": all(
            payloads[name] == data
            for name, data in original.items()
            if name.startswith("industrial-lab/scripts/")
        ),
        "texture_and_glb_bytes_preserved": all(
            payloads[name] == data
            for name, data in original.items()
            if name.endswith((".png", ".glb"))
        ),
        "unreal_fresh_reopen_verified": target["status"] == "verified"
        and target["reopened"] is True,
        "unreal_structure_counts_match_original": target["mesh_actors"]
        == old_manifest["totals"]["meshInstances"]
        and target["mesh_assets"] == old_manifest["totals"]["uniqueAssets"]
        and target["materials"] == old_manifest["totals"]["materials"]
        and target["lights"] == old_manifest["totals"]["lights"]
        and target["cameras"] == old_manifest["totals"]["cameras"],
        "unreal_transforms_verified": target["transform_checks_passed"] is True,
        "unreal_loaded_sanitized_manifest": target["manifest_sha256"]
        == state["fbx"]["publication_manifest_sha256"],
    }
    if not all(value is True for value in checks.values()):
        raise ValueError("Publication evidence check failed")
    proof = {
        "schema_version": "publication-safety-native-example/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "kind": "observed-native-execution",
            "generator": "examples/publication_safety/run_example.py",
            "generator_sha256": file_digest(Path(__file__)),
            "native_execution_sources": state["execution_sources"],
            "acceptance_validation": state["acceptance_validation"],
            "unreal_execution_sources": state["unreal_execution_sources"],
            "publication_gate_sha256": file_digest(
                REPOSITORY / "src/studio_agent_bridge/publication.py"
            ),
            "payload_binding_sha256": file_digest(Path(__file__).with_name("payload_binding.py")),
            "synthetic_adapter_used": False,
        },
        "source_archive": state["source_archive"],
        "blender_version": state["blender_version"],
        "scenes": scenes,
        "fbx": state["fbx"],
        "unchanged_members": {
            "count": len(unchanged),
            "all_byte_identical": True,
            "members": unchanged,
        },
        "unreal": {
            "host_version": state["unreal_version"],
            "attempts": [a for a in actions if a["id"].startswith("unreal-")],
            "target": target,
        },
        "actions": actions,
        "measurement_attempt_history": failures,
        "checks": checks,
        "limitations": [
            "Scene fingerprints cover explicit current-frame data: base meshes and attributes, object transforms/hierarchy/modifiers, evaluated geometry, curves/text, materials/nodes, packed images, lights/cameras/worlds and scene settings. They do not serialize every possible .blend field or test animation across frames.",
            "Non-evaluated categories compare exact typed canonical values. Evaluated floats compare component-by-component with absolute tolerance1e-6; raw maximum residuals and hashes are retained. Rounded evaluated hashes are informational, not acceptance criteria.",
            "The final stricter typed/numeric comparator was rerun on preserved raw observations after native execution. Its hash is recorded separately from the frozen scripts used inside Blender.",
            "Only six exact FBX path-metadata field shapes may change. Codec reparse comparisons preserve all other ordered typed properties and numeric arrays; the source FBX files and historical evidence remain preserved.",
            "Byte scans check configured account fragments and path patterns, including decompressed recognized .blend formats. They are not a universal detector of all private or encoded data.",
            "Unreal validation covers native static target structure and transforms. No new image, visual parity, simulation, gameplay, navigation or GPU-performance validation is claimed.",
        ],
    }
    payloads["industrial-lab/publication.json"] = (
        json.dumps(proof, indent=2, allow_nan=False) + "\n"
    ).encode()
    manifest = {
        name.removeprefix("industrial-lab/"): {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        for name, data in payloads.items()
        if name != "industrial-lab/manifest.json"
    }
    payloads["industrial-lab/manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    output = Path(output_directory).resolve() if output_directory else root / "public-exports"
    output = create_output_directory(root, output)
    archive = output / "industrial-lab-native-v2.zip"
    publication = publish_native_archive(
        payloads, archive, version=2, forbidden_fragments=fragments
    )
    scan = scan_archive(archive, forbidden_fragments=fragments)
    if not scan["passed"]:
        raise ValueError("Published archive failed independent rescan")
    proof["archive"] = {
        "filename": archive.name,
        "sha256": publication["sha256"],
        "bytes": publication["bytes"],
        "version": 2,
        "members": len(payloads),
        "scan_passed": True,
    }
    proof["checks"]["all_archive_member_scans_passed"] = True
    with (output / "observed-native-proof.json").open("x") as stream:
        json.dump(proof, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if "archive" in state:
        state.setdefault("prior_publications", []).append(state["archive"])
    state["archive"] = proof["archive"]
    state["publication_output_directory"] = output.relative_to(root).as_posix()
    state["stage"] = "publication-complete"
    write_json(root / "state.json", state)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=REPOSITORY / "web/public/assets/environment/industrial-lab-native-v2.zip",
    )
    parser.add_argument(
        "--stage", required=True, choices=("prepare", "native", "transfer", "unreal", "publish")
    )
    parser.add_argument("--blender")
    parser.add_argument("--unreal")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="New output directory inside the private work root; never writes committed proof files",
    )
    parser.add_argument(
        "--blender-python",
        type=Path,
        default=Path("/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13"),
    )
    parser.add_argument(
        "--fbx-modules",
        type=Path,
        default=Path(
            "/Applications/Blender.app/Contents/Resources/5.2/scripts/addons_core/io_scene_fbx"
        ),
    )
    args = parser.parse_args()
    root = args.work_root.resolve()
    if args.stage == "prepare":
        state = prepare(root, args.source_archive.resolve())
    elif args.stage == "native":
        state = native(root, args, read_state(root))
    elif args.stage == "transfer":
        with (root / "fbx-rewrite.log").open("x") as log:
            subprocess.run(
                [
                    str(args.blender_python),
                    str(Path(__file__).with_name("rewrite_transfer.py")),
                    "--work-root",
                    str(root),
                    "--fbx-modules",
                    str(args.fbx_modules),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        state = read_state(root)
    elif args.stage == "unreal":
        state = unreal(root, args, read_state(root))
    else:
        state = read_state(root)
        recheck_observations(root, state)
        publish(root, state, args.output_directory)
    print(json.dumps({"stage": state["stage"], "actions": len(state["actions"])}))


if __name__ == "__main__":
    main()
