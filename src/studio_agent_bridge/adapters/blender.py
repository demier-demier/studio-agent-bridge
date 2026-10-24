"""Headless Blender adapter; native scripts run with the user's privileges.

The workspace guard is an orchestration boundary, not a Python sandbox. Blender's
automatic file scripts are disabled; explicitly authorized scripts remain powerful.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from studio_agent_bridge.host_scripts.blender.environment_export import validate_manifest
from studio_agent_bridge.source_binding import (
    SourceBindingError,
    check_source,
    expected_digest,
    prepare_source,
    relative_input,
    write_evidence,
)
from studio_agent_bridge.workspace import file_digest

ACTIONS = frozenset(
    {"create_demo", "inspect", "import_asset", "export_asset", "render", "run_script"}
)
HOST_RUNNER = Path(__file__).resolve().parent.parent / "host_scripts" / "blender" / "runner.py"


def _executable(config: dict) -> str | None:
    configured = config.get("blender_executable")
    if configured:
        candidate = Path(str(configured)).expanduser()
        return str(candidate) if candidate.is_file() else shutil.which(str(configured))
    candidates = [shutil.which("blender")]
    if sys.platform == "darwin":
        candidates.append("/Applications/Blender.app/Contents/MacOS/Blender")
    if os.name == "nt":
        root = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Blender Foundation"
        candidates.extend(
            str(path) for path in sorted(root.glob("Blender */blender.exe"), reverse=True)
        )
    return next((str(path) for path in candidates if path and Path(path).is_file()), None)


def probe(config: dict) -> dict:
    executable = _executable(config)
    if not executable:
        return {
            "status": "blocked",
            "available": False,
            "summary": "Blender executable not found.",
            "actions": sorted(ACTIONS),
        }
    try:
        process = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=15, check=False
        )
        version = process.stdout.splitlines()[0] if process.stdout else "unknown"
        return {
            "status": "verified" if process.returncode == 0 else "failed",
            "available": process.returncode == 0,
            "version": version,
            "actions": sorted(ACTIONS),
            "headless": True,
            "trusted_scripts_enabled": config.get("allow_scripts") is True,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "blocked",
            "available": False,
            "summary": str(error),
            "actions": sorted(ACTIONS),
        }


def _input(workspace: Path, value: Any, suffixes: set[str]) -> Path:
    return relative_input(workspace, value, suffixes)


def _artifact(workspace: Path, path: Path, kind: str) -> dict:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "kind": kind,
        "sha256": file_digest(path),
        "bytes": path.stat().st_size,
    }


def _environment_evidence(run_dir: Path, manifest_path: Path, split_assets: bool) -> dict:
    """Check the native host's interchange files before trusting its manifest."""
    manifest_path = manifest_path.resolve()
    if (
        not manifest_path.is_relative_to(run_dir)
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 16_000_000
    ):
        raise ValueError("Environment manifest is missing, outside its run, or too large.")
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    referenced_files = {}

    def verify(relative, expected):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("Environment file path must be relative.")
        path = (run_dir / relative).resolve()
        if not path.is_relative_to(run_dir) or not path.is_file():
            raise ValueError("Environment reference is missing or escapes its run directory.")
        if path.stat().st_size > 128_000_000:
            raise ValueError("Environment reference exceeds the 128 MB bound.")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            raise ValueError("Environment file hash mismatch: " + relative)
        referenced_files[relative] = actual

    material_ids = {material["id"] for material in manifest["materials"]}
    for asset in manifest["assets"]:
        if any(slot not in material_ids | {None} for slot in asset["materialSlots"]):
            raise ValueError("Environment asset references an unknown material.")
        if asset.get("exportFile"):
            verify(asset["exportFile"], asset.get("fileSha256"))
        elif split_assets:
            raise ValueError("Requested split mesh asset is missing.")
    for material in manifest["materials"]:
        for texture in material.get("textures", {}).values():
            verify(texture["path"], texture["sha256"])
    counts = manifest["totals"]
    if counts["uniqueAssets"] != len(manifest["assets"]) or counts["meshInstances"] != len(
        manifest["objects"]
    ):
        raise ValueError("Environment manifest totals disagree with records.")
    return {
        "schema": manifest["schemaVersion"],
        "totals": counts,
        "bounds_m": manifest["boundsWorld"],
        "referenced_file_hashes": referenced_files,
        "limitations": manifest["limitations"],
        "checks": {
            "manifest_structure_valid": True,
            "referenced_files_match_hashes": True,
            "split_assets_complete": bool(
                split_assets and all(a.get("exportFile") for a in manifest["assets"])
            ),
        },
    }


class SourceResourceError(RuntimeError):
    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.evidence = evidence


def _launch(executable: str, job: dict, run_dir: Path, label: str, timeout: int) -> dict:
    job_path = run_dir / f"{label}.job.json"
    result_path = run_dir / f"{label}.result.json"
    job_path.write_text(
        json.dumps({**job, "result_path": str(result_path)}, indent=2), encoding="utf-8"
    )
    command = [
        executable,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "3",
        "--python",
        str(HOST_RUNNER),
        "--",
        str(job_path),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        (run_dir / f"{label}.log.txt").write_text(
            completed.stdout + "\n" + completed.stderr, encoding="utf-8"
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        (run_dir / f"{label}.log.txt").write_text(
            output + "\nBlender process timed out.\n", encoding="utf-8"
        )
        raise RuntimeError(f"Blender exceeded the {timeout}-second operation limit.") from error
    if completed.returncode != 0 or not result_path.is_file():
        raise RuntimeError(
            f"Blender process failed (exit {completed.returncode}); inspect {label}.log.txt."
        )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "ok":
        if result.get("source_resources"):
            raise SourceResourceError(result["error"], result["source_resources"])
        raise RuntimeError(result.get("error", "Blender operation failed."))
    return result


def _source_artifacts(workspace: Path, run_dir: Path, binding: dict | None, artifacts: list):
    """Input evidence is metadata; it cannot stand in for a new native output."""
    paths = {item["path"] for item in artifacts}
    candidates = list(run_dir.glob("source-binding*.json"))
    if binding and binding.get("snapshot_path"):
        candidates.append(workspace / binding["snapshot_path"])
    for path in candidates:
        if (
            path.is_file()
            and not path.is_symlink()
            and path.relative_to(workspace).as_posix() not in paths
        ):
            artifacts.append(_artifact(workspace, path, "metadata"))
            paths.add(path.relative_to(workspace).as_posix())


def _validated_outputs(run_dir: Path, outputs: Any, snapshot: str | None) -> list[dict]:
    if not isinstance(outputs, list):
        raise ValueError("Host outputs must be a list.")
    seen = set()
    for item in outputs:
        path = Path(item["path"])
        if path.is_symlink():
            raise ValueError("Host outputs must not be symlinks.")
        path = path.resolve()
        if not path.is_relative_to(run_dir) or not path.is_file():
            raise ValueError(
                "Host reported a missing output or an output outside its run directory."
            )
        if str(path) == snapshot or path in seen:
            raise ValueError("Host output aliases source input or duplicates another output.")
        if item["kind"] == "blend" and path.suffix.lower() != ".blend":
            raise ValueError("Native scene outputs must be .blend files.")
        seen.add(path)
        item["path"] = str(path)
    return outputs


def execute(workspace: Path, request: dict, run_dir: Path, config: dict) -> dict:
    """Execute one operation and independently reopen native/exported artifacts."""
    limitations = [
        "Blender scripts execute as the current OS user; this is not a sandbox.",
        "Mesh interchange preserves measured geometry, not CAD feature history or all Blender shaders/modifiers.",
        "Successful Blender roundtrip does not prove import or appearance in Unreal Engine.",
    ]
    artifacts: list[dict] = []
    binding = None
    verification: dict[str, Any] = {}
    native_attempted = False
    source_checked = False
    try:
        workspace = workspace.resolve()
        run_dir = run_dir.resolve()
        if not run_dir.is_relative_to(workspace):
            raise ValueError("Run directory must be inside the workspace.")
        run_dir.mkdir(parents=True, exist_ok=True)
        if any(run_dir.iterdir()):
            raise ValueError("Blender requires an empty run directory to preserve earlier outputs.")
        action = request.get("action")
        parameters = request.get("parameters", {})
        if action not in ACTIONS or not isinstance(parameters, dict):
            raise ValueError("Unsupported Blender action or parameters.")
        expected = expected_digest(action, parameters)
        if action == "run_script" and config.get("allow_scripts") is not True:
            return {
                "status": "blocked",
                "summary": "Trusted scripts require allow_scripts=true in server configuration.",
                "artifacts": [],
                "observations": {},
                "limitations": limitations,
            }
        executable = _executable(config)
        if not executable:
            return {
                "status": "blocked",
                "summary": "Blender executable not found.",
                "artifacts": [],
                "observations": {},
                "limitations": limitations,
            }
        timeout = int(config.get("blender_timeout_seconds", 180))
        if not 5 <= timeout <= 900:
            raise ValueError("blender_timeout_seconds must be between 5 and 900.")
        job: dict[str, Any] = {"action": action, "output_dir": str(run_dir)}
        source_hashes: dict[str, str] = {}
        for field, extensions in (
            ("input_blend", {".blend"}),
            ("asset_path", {".glb", ".gltf", ".fbx"}),
            ("script_path", {".py"}),
        ):
            if field in parameters:
                path = _input(workspace, parameters[field], extensions)
                job[field] = str(path)
                if field != "input_blend":
                    source_hashes[path.relative_to(workspace).as_posix()] = file_digest(path)
        required = {
            "inspect": "input_blend",
            "export_asset": "input_blend",
            "render": "input_blend",
            "import_asset": "asset_path",
            "run_script": "script_path",
        }
        if action in required and required[action] not in job:
            raise ValueError(f"{action} requires {required[action]}.")
        job["export_formats"] = parameters.get("export_formats", ["glb", "fbx"])
        if (
            not isinstance(job["export_formats"], list)
            or not job["export_formats"]
            or any(item not in {"glb", "fbx"} for item in job["export_formats"])
        ):
            raise ValueError("export_formats must contain glb and/or fbx.")
        job["render"] = parameters.get("render", action in {"create_demo", "render"}) is True
        collection = parameters.get("export_collection")
        if collection is not None and (
            not isinstance(collection, str)
            or not collection.strip()
            or len(collection) > 128
            or any(ord(c) < 32 for c in collection)
        ):
            raise ValueError(
                "export_collection must be a nonempty collection name up to 128 characters."
            )
        job["export_collection"] = collection
        for field, default in (
            ("include_presentation", False),
            ("environment_manifest", action == "export_asset"),
            ("split_assets", False),
        ):
            value = parameters.get(field, default)
            if type(value) is not bool:
                raise ValueError(f"{field} must be a boolean.")
            job[field] = value
        if job["split_assets"] and not job["environment_manifest"]:
            raise ValueError("split_assets requires environment_manifest=true.")
        for field, maximum in (("max_assets", 256), ("max_objects", 5000)):
            value = parameters.get(field, maximum)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{field} must be an integer from 1 to {maximum}.")
            job[field] = value
        render_settings = parameters.get("render_settings", {})
        if not isinstance(render_settings, dict) or set(render_settings) - {
            "engine",
            "resolution",
            "samples",
        }:
            raise ValueError("render_settings supports engine, resolution and samples only.")
        engine = render_settings.get("engine", "CYCLES")
        resolution = render_settings.get("resolution", [1200, 900])
        samples = render_settings.get("samples", 32)
        if (
            engine not in {"CYCLES", "BLENDER_EEVEE"}
            or not isinstance(resolution, list)
            or len(resolution) != 2
            or any(type(v) is not int or not 64 <= v <= 3840 for v in resolution)
            or resolution[0] * resolution[1] > 8294400
            or type(samples) is not int
            or not 1 <= samples <= 256
        ):
            raise ValueError("Invalid bounded render_settings.")
        job["render_settings"] = render_settings
        job["script_parameters"] = parameters.get("script_parameters", {})
        if not isinstance(job["script_parameters"], dict):
            raise ValueError("script_parameters must be an object.")
        if "input_blend" in job:
            binding = prepare_source(workspace, Path(job["input_blend"]), run_dir, expected)
            source_hashes[binding["source_path"]] = binding["source_sha256"]
            limitations.extend(binding["limitations"])
            if binding["mode"] == "pinned_snapshot":
                job["input_blend"] = str(workspace / binding["snapshot_path"])
                job["require_packed_source"] = True
            before_dispatch = check_source(workspace, binding, before_dispatch=True)
            write_evidence(run_dir, "source-binding-dispatch.json", before_dispatch)
        native_attempted = True
        result = _launch(executable, job, run_dir, "execute", timeout)
        verification = {
            "action": action,
            "blender_version": result["blender_version"],
            "units": "meters",
            "source_sha256": source_hashes,
            "initial_scene": result["scene"],
            "checks": [],
        }
        if binding and binding["mode"] == "pinned_snapshot":
            verification["source_resources"] = result.get("source_resources", {})
            verification["checks"].append(
                {
                    "name": "packed_source_resource_gate",
                    "passed": result.get("source_resources", {}).get("supported") is True,
                }
            )
        outputs = _validated_outputs(
            run_dir,
            result.get("outputs", []),
            str(workspace / binding["snapshot_path"])
            if binding and binding["snapshot_path"]
            else None,
        )
        native = next((item for item in outputs if item["kind"] == "blend"), None)
        if action != "inspect":
            verification["checks"].append(
                {"name": "new_native_scene_saved", "passed": native is not None}
            )
        if native:
            reopened = _launch(
                executable,
                {
                    "action": "inspect",
                    "input_blend": native["path"],
                    "output_dir": str(run_dir),
                    "export_collection": collection,
                    "include_presentation": job["include_presentation"],
                },
                run_dir,
                "reopen",
                timeout,
            )
            native_scene = reopened["scene"]
            checks = {
                "object_names_preserved": native_scene["object_names"]
                == result["scene"]["object_names"],
                "mesh_count_preserved": native_scene["mesh_count"] == result["scene"]["mesh_count"],
                "modifier_count_preserved": native_scene["modifier_count"]
                == result["scene"]["modifier_count"],
                "material_count_preserved": native_scene["material_count"]
                == result["scene"]["material_count"],
                "animated_objects_preserved": native_scene["animated_objects"]
                == result["scene"]["animated_objects"],
                "render_settings_preserved": native_scene["render_settings"]
                == result["scene"]["render_settings"],
                "dimensions_preserved": _near(
                    native_scene["asset_bounds_m"]["dimensions"],
                    result["scene"]["asset_bounds_m"]["dimensions"],
                ),
            }
            verification["native_reopen"] = {"checks": checks, "scene": native_scene}
            verification["checks"].extend(
                {"name": key, "passed": value} for key, value in checks.items()
            )
        for exported in (item for item in outputs if item["kind"] in {"glb", "fbx"}):
            imported = _launch(
                executable,
                {
                    "action": "verify_export",
                    "asset_path": exported["path"],
                    "output_dir": str(run_dir),
                },
                run_dir,
                f"roundtrip-{exported['kind']}",
                timeout,
            )
            scene = imported["scene"]
            checks = {
                "mesh_count_preserved": scene["mesh_count"] == result["scene"]["asset_mesh_count"],
                "dimensions_preserved": _near(
                    scene["asset_bounds_m"]["dimensions"],
                    result["scene"]["asset_bounds_m"]["dimensions"],
                    0.002,
                ),
            }
            verification[f"{exported['kind']}_roundtrip"] = {"checks": checks, "scene": scene}
            verification["checks"].extend(
                {"name": exported["kind"] + "_" + key, "passed": value}
                for key, value in checks.items()
            )
        for relative_path, old_hash in source_hashes.items():
            if binding and relative_path == binding["source_path"]:
                continue
            unchanged = file_digest(workspace / relative_path) == old_hash
            verification["checks"].append(
                {"name": "source_unchanged:" + relative_path, "passed": unchanged}
            )
        for item in outputs:
            path = Path(item["path"]).resolve()
            if not path.is_relative_to(run_dir):
                raise RuntimeError("Host reported an output outside its run directory.")
            artifacts.append(_artifact(workspace, path, item["kind"]))
            if item["kind"] == "environment-manifest":
                evidence = _environment_evidence(run_dir, path, job["split_assets"])
                verification["environment"] = evidence
                limitations.extend(evidence["limitations"])
                verification["checks"].extend(
                    {"name": "environment_" + name, "passed": passed}
                    for name, passed in evidence["checks"].items()
                    if name != "split_assets_complete" or job["split_assets"]
                )
        # The inspect action verifies a loaded native file without rewriting it.
        if action == "inspect":
            verification["checks"].append(
                {"name": "native_file_opened", "passed": result["scene"]["object_count"] >= 0}
            )
            artifacts.append(_artifact(workspace, Path(job["input_blend"]), "blend"))
        if binding:
            binding = check_source(workspace, binding)
            write_evidence(run_dir, "source-binding-check.json", binding)
            source_checked = True
            verification["source_binding"] = binding
            verification["checks"].append(
                {
                    "name": "source_unchanged:" + binding["source_path"],
                    "passed": binding["source_unchanged"],
                }
            )
            if binding["snapshot_path"]:
                verification["checks"].append(
                    {"name": "snapshot_unchanged", "passed": binding["snapshot_unchanged"]}
                )
        _source_artifacts(workspace, run_dir, binding, artifacts)
        verified = bool(verification["checks"]) and all(
            item["passed"] for item in verification["checks"]
        )
        verification["all_checks_passed"] = verified
        verification_path = run_dir / "verification.json"
        verification_path.write_text(json.dumps(verification, indent=2), encoding="utf-8")
        artifacts.append(_artifact(workspace, verification_path, "verification"))
        artifacts.extend(
            _artifact(workspace, path, "log") for path in sorted(run_dir.glob("*.log.txt"))
        )
        return {
            "status": "verified" if verified else "executed_unverified",
            "summary": f"Blender {action}: {result['scene']['mesh_count']} meshes; native/export checks {'passed' if verified else 'require review'}.",
            "artifacts": artifacts,
            "observations": verification,
            "limitations": limitations,
        }
    except (ValueError, RuntimeError, OSError, TypeError, KeyError, json.JSONDecodeError) as error:
        if isinstance(error, SourceBindingError) and error.evidence:
            binding = error.evidence
            if not native_attempted and not (run_dir / "source-binding-failure.json").exists():
                try:
                    write_evidence(run_dir, "source-binding-failure.json", binding)
                except OSError:
                    binding["evidence_write_failed"] = True
        if isinstance(error, SourceResourceError):
            verification["source_resources"] = error.evidence
        if binding:
            if native_attempted and not source_checked:
                binding = check_source(workspace, binding)
                try:
                    write_evidence(run_dir, "source-binding-check.json", binding)
                except OSError:
                    binding["evidence_write_failed"] = True
            verification["source_binding"] = binding
        if run_dir.is_dir() and run_dir.is_relative_to(workspace):
            _source_artifacts(workspace, run_dir, binding, artifacts)
            artifacts.extend(
                _artifact(workspace, path, "log") for path in sorted(run_dir.glob("*.log.txt"))
            )
        return {
            "status": "failed",
            "summary": str(error),
            "artifacts": artifacts,
            "observations": verification,
            "limitations": limitations,
        }


def _near(first: list[float], second: list[float], tolerance: float = 1e-5) -> bool:
    return len(first) == len(second) and all(abs(a - b) <= tolerance for a, b in zip(first, second))
