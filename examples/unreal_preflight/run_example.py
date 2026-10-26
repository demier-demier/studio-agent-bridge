#!/usr/bin/env python3
"""Measure the macOS Unreal environment preflight and an explicit GPU attempt.

All native execution uses the real Engine and adapter in a newly owned workspace.
Only manifest-verified transfer assets from the public original lab are extracted.
Raw host logs stay private. No application behavior is replaced or simulated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from studio_agent_bridge.adapters import unreal  # noqa: E402
from studio_agent_bridge.engine import Engine  # noqa: E402
from studio_agent_bridge.workspace import file_digest  # noqa: E402

OWNER = "studio-unreal-preflight-example/v1"
ARCHIVE = "web/public/assets/environment/industrial-lab-native-v2.zip"
MANIFEST = "source/transfer/environment-manifest.json"
PROJECT = "unreal/PreflightLab/PreflightLab.uproject"
DESTINATION = "/Game/StudioAgentBridge/Environments/PreflightLabGpu"
LEVEL = DESTINATION + "/Maps/PreflightLabGpu"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def implementation_hashes() -> dict:
    return {
        relative: file_digest(REPOSITORY / relative)
        for relative in (
            "src/studio_agent_bridge/engine.py",
            "src/studio_agent_bridge/workspace.py",
            "src/studio_agent_bridge/adapters/unreal.py",
            "src/studio_agent_bridge/host_scripts/unreal/dispatch.py",
            "src/studio_agent_bridge/host_scripts/unreal/environment.py",
            "src/studio_agent_bridge/host_scripts/unreal/environment_contract.py",
        )
    }


def prepare(root: Path) -> dict:
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new empty work root; earlier evidence is preserved.")
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    extracted = []
    with zipfile.ZipFile(REPOSITORY / ARCHIVE) as archive:
        records = json.loads(archive.read("industrial-lab/manifest.json"))
        for item in archive.infolist():
            if not item.filename.startswith("industrial-lab/transfer/") or item.is_dir():
                continue
            relative = PurePosixPath(item.filename.removeprefix("industrial-lab/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Archive member escapes the transfer directory.")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Archive links are not supported.")
            data = archive.read(item)
            expected = records[relative.as_posix()]
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected["sha256"] or len(data) != expected["bytes"]:
                raise ValueError("Public transfer member failed its packaged manifest check.")
            output = workspace / "source" / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            extracted.append({"member": relative.as_posix(), "sha256": digest, "bytes": len(data)})
    if not extracted or not (workspace / MANIFEST).is_file():
        raise ValueError("No public environment transfer was found.")
    source = json.loads((workspace / MANIFEST).read_text())
    state = {
        "owner": OWNER,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "prepared",
        "source_archive": {"path": ARCHIVE, "sha256": file_digest(REPOSITORY / ARCHIVE)},
        "source_manifest": {"path": MANIFEST, "sha256": file_digest(workspace / MANIFEST)},
        "source_totals": source["totals"],
        "extracted": extracted,
        "actions": {},
    }
    write_json(root / "state.json", state)
    return state


def read_state(root: Path) -> dict:
    value = json.loads((root / "state.json").read_text())
    if value.get("owner") != OWNER:
        raise ValueError("This example does not own the specified work root.")
    return value


class LaunchAudit:
    """Observe Python's real subprocess audit events, without replacing Popen."""

    def __init__(self, root: Path):
        self.path = root / "subprocess-audit.jsonl"
        self.phase = "setup"
        self.records = []
        sys.addaudithook(self.observe)

    def observe(self, event, arguments):
        if event != "subprocess.Popen":
            return
        executable = Path(os.fsdecode(arguments[0])).name
        record = {
            "phase": self.phase,
            "executable": executable,
            "native": executable in {"UnrealEditor", "UnrealEditor-Cmd"},
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.records.append(record)
        with self.path.open("a") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def counts(self, phase: str) -> dict:
        records = [record for record in self.records if record["phase"] == phase]
        return {
            "subprocess_launches": len(records),
            "native_process_launches": sum(record["native"] for record in records),
        }


def project_snapshot(workspace: Path) -> dict:
    """Fingerprint every project entry, content hash and modification timestamp."""
    project = workspace / Path(PROJECT).parent
    entries = []
    if project.exists():
        for path in sorted(project.rglob("*")):
            info = path.lstat()
            if path.is_symlink():
                raise ValueError("Unexpected symlink inside the owned project.")
            entries.append(
                {
                    "path": path.relative_to(project).as_posix(),
                    "kind": "directory" if path.is_dir() else "file",
                    "bytes": info.st_size if path.is_file() else 0,
                    "mtime_ns": info.st_mtime_ns,
                    "sha256": file_digest(path) if path.is_file() else None,
                }
            )
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "exists": project.exists(),
        "entries": len(entries),
        "files": sum(item["kind"] == "file" for item in entries),
        "bytes": sum(item["bytes"] for item in entries),
        "tree_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def engine_for(root: Path, args) -> Engine:
    config = {"unreal_project": PROJECT, "unreal_timeout_seconds": 900}
    if args.unreal:
        config["unreal_executable"] = args.unreal
    return Engine(root / "workspace", config, lock_dir=root / "locks")


def action(engine, state, root, audit, label, name, parameters):
    if label in state["actions"]:
        raise ValueError("An earlier action already uses this label; evidence is not overwritten.")
    audit.phase = label
    before = engine.inspect_job(state["job_id"])
    project_before = project_snapshot(root / "workspace")
    result = engine.execute_action(state["job_id"], name, parameters, before["revision"])
    project_after = project_snapshot(root / "workspace")
    record = {
        "action": name,
        "parameters": parameters,
        "status": result["status"],
        "revision_before": before["revision"],
        "revision_after": result["revision"],
        **audit.counts(label),
        "project_before": project_before,
        "project_after": project_after,
        "project_tree_unchanged": project_before == project_after,
        "result": result,
    }
    state["actions"][label] = record
    write_json(root / "raw-evidence" / (label + ".json"), record)
    write_json(root / "state.json", state)
    return record


def require_verified(record):
    if record["status"] != "verified":
        result = record["result"].get("result", {})
        raise RuntimeError(
            "Native action did not verify: " + result.get("summary", record["status"])
        )


def require_preflight(record):
    observations = record["result"]["result"]["observations"]
    assert record["status"] == "blocked", "Expected the real adapter's preflight rejection."
    assert observations["preflight"]["code"] == "unreal_macos_5_7_4_environment_nullrhi"
    assert observations["native_launch_attempted"] is False
    assert record["subprocess_launches"] == record["native_process_launches"] == 0
    assert record["project_tree_unchanged"] is True
    assert not record["result"]["result"]["artifacts"]


def reject(root: Path, args, state: dict) -> dict:
    if state["stage"] != "prepared":
        raise ValueError("Rejection stage requires a newly prepared workspace.")
    engine = engine_for(root, args)
    probe = unreal.probe(engine.config)
    version = probe.get("version", {})
    if (
        not probe["available"]
        or sys.platform != "darwin"
        or tuple(version.get(key) for key in ("MajorVersion", "MinorVersion", "PatchVersion"))
        != (5, 7, 4)
    ):
        raise RuntimeError("This reproduction requires installed Unreal 5.7.4 on macOS.")
    state["host_version"] = version
    state["execution_sources"] = implementation_hashes()
    audit = LaunchAudit(root)
    job = engine.create_job(
        "unreal-environment-preflight",
        "unreal",
        "Measure preflight rejection then an explicit GPU build of the public original lab.",
    )
    state["job_id"] = job["id"]
    parameters = {
        "environment_manifest": MANIFEST,
        "destination": "/Game/StudioAgentBridge/Environments/RejectedLab",
        "name": "RejectedLab",
        "rendering": "structure",
    }
    absent = action(engine, state, root, audit, "reject-before-project", "build_scene", parameters)
    require_preflight(absent)
    assert absent["project_after"]["exists"] is False
    created = action(
        engine,
        state,
        root,
        audit,
        "create-owned-project",
        "create_project",
        {"name": "PreflightLab"},
    )
    require_verified(created)
    existing = action(
        engine, state, root, audit, "reject-existing-project", "build_scene", parameters
    )
    require_preflight(existing)
    assert existing["project_before"]["exists"] is True
    assert implementation_hashes() == state["execution_sources"]
    state["stage"] = "rejections-verified"
    write_json(root / "state.json", state)
    return state


def gpu(root: Path, args, state: dict) -> dict:
    if state["stage"] != "rejections-verified":
        raise ValueError("GPU stage requires the completed rejection evidence.")
    assert implementation_hashes() == state["execution_sources"], "Source changed between stages."
    engine, audit = engine_for(root, args), LaunchAudit(root)
    parameters = {
        "environment_manifest": MANIFEST,
        "destination": DESTINATION,
        "name": "PreflightLabGpu",
        "level_path": LEVEL,
        "rendering": "gpu",
    }
    built = action(engine, state, root, audit, "explicit-gpu-build", "build_scene", parameters)
    require_verified(built)
    inspected = action(engine, state, root, audit, "fresh-gpu-inspection", "inspect", parameters)
    require_verified(inspected)
    assert implementation_hashes() == state["execution_sources"]
    state["stage"] = "native-complete"
    write_json(root / "state.json", state)
    return state


def summarize(root: Path, state: dict, output: Path) -> dict:
    if state["stage"] != "native-complete":
        raise ValueError("Native build and fresh inspection must finish before public export.")
    attempts = []
    for label, record in state["actions"].items():
        observations = record["result"]["result"].get("observations", {})
        attempts.append(
            {
                "id": label,
                "action": record["action"],
                "status": record["status"],
                "rendering": record["parameters"].get("rendering", "structure"),
                "editor_session": bool(record["parameters"].get("environment_manifest")),
                "native_process_launches": record["native_process_launches"],
                "subprocess_launches": record["subprocess_launches"],
                "revision_before": record["revision_before"],
                "revision_after": record["revision_after"],
                "preflight": observations.get("preflight"),
                "returncode": observations.get("returncode"),
                "timed_out": observations.get("timed_out"),
                "project_effects": {
                    "before": record["project_before"],
                    "after": record["project_after"],
                    "unchanged": record["project_tree_unchanged"],
                },
            }
        )
    final = state["actions"]["fresh-gpu-inspection"]["result"]["result"]["observations"]["native"][
        "verification"
    ]
    target = {
        "status": "verified",
        "mesh_actors": final["objectCount"],
        "mesh_assets": len(final["meshAssets"]),
        "materials": len(final["materials"]),
        "lights": len(final["lights"]),
        "cameras": len(final["cameras"]),
        "shared_mesh_instances": final["sharedMeshInstances"],
        "reopened": final["reopened"],
        "transform_checks_passed": final["transformChecksPassed"],
        "max_mesh_bounds_residual_cm": max(
            item["boundsCheck"]["maxResidualCm"] for item in final["meshAssets"]
        ),
        "manifest_sha256": final["manifestSha256"],
        "appearance_validated": False,
    }
    rejected = [
        state["actions"][label] for label in ("reject-before-project", "reject-existing-project")
    ]
    totals = state["source_totals"]
    checks = {
        "both_unsafe_requests_blocked": all(item["status"] == "blocked" for item in rejected),
        "rejections_started_no_subprocess": all(
            item["subprocess_launches"] == 0 for item in rejected
        ),
        "rejections_preserved_project_trees": all(
            item["project_tree_unchanged"] for item in rejected
        ),
        "rejection_created_no_project": rejected[0]["project_after"]["exists"] is False,
        "rejection_existing_project_was_present": rejected[1]["project_before"]["exists"] is True,
        "rejections_recorded_engine_lifecycle": all(
            item["revision_after"] > item["revision_before"] for item in rejected
        ),
        "explicit_gpu_build_verified": state["actions"]["explicit-gpu-build"]["status"]
        == "verified",
        "fresh_inspection_verified": state["actions"]["fresh-gpu-inspection"]["status"]
        == "verified",
        "native_scene_reopened": target["reopened"] is True,
        "mesh_actor_count_matches_source": target["mesh_actors"] == totals["meshInstances"],
        "mesh_asset_count_matches_source": target["mesh_assets"] == totals["uniqueAssets"],
        "material_count_matches_source": target["materials"] == totals["materials"],
        "rig_counts_match_source": target["lights"] == totals["lights"]
        and target["cameras"] == totals["cameras"],
        "transforms_verified": target["transform_checks_passed"] is True,
        "mesh_bounds_verified": all(item["boundsCheck"]["passed"] for item in final["meshAssets"]),
        "materials_verified": all(item["passed"] for item in final["materials"]),
        "source_manifest_preserved": file_digest(root / "workspace" / MANIFEST)
        == state["source_manifest"]["sha256"]
        == target["manifest_sha256"],
        "implementation_unchanged": implementation_hashes() == state["execution_sources"],
    }
    assert all(value is True for value in checks.values())
    report = {
        "schema_version": "unreal-preflight-native-example/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "kind": "observed-native-execution",
            "generator": "examples/unreal_preflight/run_example.py",
            "generator_sha256": file_digest(Path(__file__)),
            "source_archive": state["source_archive"],
            "source_manifest": state["source_manifest"],
            "validated_transfer_members": len(state["extracted"]),
            "execution_sources": state["execution_sources"],
            "synthetic_adapter_used": False,
            "scene": "Separate run of the original public lab transfer, not the edited-cart target",
        },
        "host_version": state["host_version"],
        "platform": sys.platform,
        "attempts": attempts,
        "source_totals": totals,
        "target": target,
        "checks": checks,
        "limitations": [
            "The rejection is an adapter preflight for the observed Unreal 5.7.4 macOS environment-build combination, not a claim that every NullRHI action fails.",
            "Engine ledger and run evidence are intentionally written for rejected requests; no native subprocess or project/import side effect was observed.",
            "The GPU build is a separately requested stage. There is no automatic fallback or replay.",
            "Native static structure, transforms, mesh bounds and material properties were measured; no Unreal image was captured or visually validated.",
            "Navigation, gameplay, simulation, GPU performance, and photometric parity were not tested.",
            "Project fingerprints compare entry names, file contents and modification timestamps around each request; raw local paths and host logs remain private.",
        ],
    }
    encoded = json.dumps(report)
    for marker in (
        str(root),
        str(REPOSITORY),
        "/Users/",
        "/home/",
        "/private/var/",
        Path.home().name,
    ):
        if len(marker) >= 4 and marker in encoded:
            raise ValueError("Public proof contains a private identifier or path.")
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("prepare", "reject", "gpu", "summarize"))
    parser.add_argument("--unreal", help="Optional UnrealEditor-Cmd executable")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY / "examples/unreal_preflight/observed-native-proof.json",
    )
    args = parser.parse_args()
    root = args.work_root.resolve()
    if args.stage == "prepare":
        state = prepare(root)
    else:
        state = read_state(root)
        if args.stage == "reject":
            state = reject(root, args, state)
        elif args.stage == "gpu":
            state = gpu(root, args, state)
        else:
            summarize(root, state, args.output)
    print(json.dumps({"stage": state["stage"], "recorded_actions": len(state["actions"])}))


if __name__ == "__main__":
    main()
