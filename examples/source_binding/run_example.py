#!/usr/bin/env python3
"""Reproduce a source-pinned native edit using the packaged original lab scene.

Requires Blender and the installed bridge dependencies. Unreal is an optional,
separate stage. Only owned workspaces and the repository's original public scene
and scripts are used. Raw host logs remain in the chosen private work root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from studio_agent_bridge.adapters import blender, unreal  # noqa: E402
from studio_agent_bridge.engine import Engine  # noqa: E402
from studio_agent_bridge.workspace import file_digest  # noqa: E402

ARCHIVE = "web/public/assets/environment/industrial-lab-native-v2.zip"
MEMBERS = {
    "scene-day.blend": "source/scene-day.blend",
    "scripts/revise_and_render.py": "scripts/environments/revise_and_render.py",
    "scripts/sanitize_scene.py": "scripts/environments/sanitize_scene.py",
    "scripts/export_environment_bundle.py": "scripts/environments/export_environment_bundle.py",
    "scripts/environment_export.py": "scripts/environments/environment_export.py",
}
OWNER = "studio-source-binding-example/v1"


def implementation_hashes() -> dict:
    return {
        relative: file_digest(REPOSITORY / relative)
        for relative in (
            "src/studio_agent_bridge/engine.py",
            "src/studio_agent_bridge/source_binding.py",
            "src/studio_agent_bridge/adapters/blender.py",
            "src/studio_agent_bridge/adapters/unreal.py",
            "src/studio_agent_bridge/host_scripts/blender/runner.py",
            "src/studio_agent_bridge/host_scripts/blender/source_resources.py",
            "src/studio_agent_bridge/host_scripts/blender/environment_export.py",
        )
    }


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n")


def read_state(root: Path) -> dict:
    state = json.loads((root / "state.json").read_text())
    if state.get("owner") != OWNER:
        raise ValueError("The work root is not owned by this example.")
    return state


def prepare(root: Path) -> dict:
    if root.exists() and any(root.iterdir()):
        raise ValueError("Preparation requires a new empty work root; earlier runs are preserved.")
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace"
    extracted = []
    with zipfile.ZipFile(REPOSITORY / ARCHIVE) as archive:
        manifest = json.loads(archive.read("industrial-lab/manifest.json"))
        for member, relative in MEMBERS.items():
            data = archive.read("industrial-lab/" + member)
            actual = hashlib.sha256(data).hexdigest()
            expected = manifest[member]
            if actual != expected["sha256"] or len(data) != expected["bytes"]:
                raise ValueError("Packaged source failed its manifest check: " + member)
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            extracted.append(
                {"member": member, "workspace_path": relative, "sha256": actual, "bytes": len(data)}
            )
    state = {
        "owner": OWNER,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive": {"path": ARCHIVE, "sha256": file_digest(REPOSITORY / ARCHIVE)},
        "extracted": extracted,
        "source_sha256": file_digest(workspace / "source/scene-day.blend"),
        "actions": {},
        "stage": "prepared",
    }
    write_json(root / "state.json", state)
    return state


class NativeLaunchAudit:
    """Observe real subprocess launches without replacing native adapter behavior."""

    def __init__(self, root: Path):
        self.path = root / "native-launch-audit.jsonl"
        self.phase = "setup"
        self.records = []
        sys.addaudithook(self.observe)

    def observe(self, event, arguments):
        if event != "subprocess.Popen":
            return
        executable = Path(os.fsdecode(arguments[0])).name
        if executable not in {"Blender", "blender", "UnrealEditor", "UnrealEditor-Cmd"}:
            return
        record = {
            "phase": self.phase,
            "executable": executable,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.records.append(record)
        with self.path.open("a") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def count(self, phase: str) -> int:
        return sum(record["phase"] == phase for record in self.records)


def engine_for(root: Path, args) -> Engine:
    config = {
        "allow_scripts": True,
        "blender_timeout_seconds": 900,
        "unreal_timeout_seconds": 900,
        "unreal_project": "unreal/SourceBindingLab/SourceBindingLab.uproject",
    }
    if args.blender:
        config["blender_executable"] = args.blender
    if args.unreal:
        config["unreal_executable"] = args.unreal
    return Engine(root / "workspace", config, lock_dir=root / "locks")


def act(
    engine: Engine,
    job_id: str,
    action: str,
    parameters: dict,
    label: str,
    root: Path,
    state: dict,
    audit: NativeLaunchAudit,
) -> dict:
    if label in state["actions"]:
        raise ValueError("Refusing to repeat a completed example action: " + label)
    audit.phase = label
    before = engine.inspect_job(job_id)
    result = engine.execute_action(job_id, action, parameters, before["revision"])
    record = {
        "action": action,
        "status": result["status"],
        "revision_before": before["revision"],
        "revision_after": result["revision"],
        "native_process_launches": audit.count(label),
        "run_dir": result.get("run_dir"),
        "result": result,
    }
    state["actions"][label] = record
    write_json(root / "raw-evidence" / (label + ".json"), record)
    write_json(root / "state.json", state)
    return record


def require_verified(record: dict) -> None:
    if record["status"] != "verified":
        summary = record["result"].get("result", {}).get("summary", "No native result")
        raise RuntimeError(
            f"Native action {record['action']} did not verify: {record['status']}: {summary}"
        )


def native_file(record: dict, kind: str) -> str:
    artifacts = record["result"]["result"]["artifacts"]
    # A source snapshot is also native file data. Select the produced scene, not
    # the preserved source, when the binding layer exposes both as artifacts.
    candidates = [
        item["path"]
        for item in artifacts
        if item["kind"] == kind and "/source_snapshot/" not in item["path"]
    ]
    if not candidates:
        raise ValueError("Expected native artifact missing: " + kind)
    return candidates[0]


def pinned(workspace: Path, relative: str) -> dict:
    return {"input_blend": relative, "expected_source_sha256": file_digest(workspace / relative)}


def native_blender(root: Path, args, state: dict) -> dict:
    if state["stage"] != "prepared":
        raise ValueError("Blender stage requires a freshly prepared workspace.")
    workspace = root / "workspace"
    engine = engine_for(root, args)
    audit = NativeLaunchAudit(root)
    audit.phase = "probe"
    probe = blender.probe(engine.config)
    if not probe["available"]:
        raise RuntimeError("Blender is unavailable.")
    state["blender_version"] = probe["version"]
    state["execution_sources"] = implementation_hashes()
    job = engine.create_job(
        "source-bound-cart",
        "blender",
        "Move the original lab cart by 0.25 metres using a pinned source and preserve the original scene.",
    )
    state["blender_job"] = job["id"]
    parameters = {
        "script_path": "scripts/environments/revise_and_render.py",
        "input_blend": "source/scene-day.blend",
        "expected_source_sha256": "0" * 64,
        "script_parameters": {
            "lighting_theme": "day",
            "cart_offset_x_m": 0.25,
            "render_views": [],
            "camera": "Overview",
        },
        "render": False,
        "export_collection": "StudioEnvironment",
        "include_presentation": True,
    }
    stale = act(
        engine, job["id"], "run_script", parameters, "stale-source-rejected", root, state, audit
    )
    assert stale["status"] == "failed"
    assert stale["native_process_launches"] == 0
    assert not (workspace / stale["run_dir"] / "scene.blend").exists()
    assert file_digest(workspace / "source/scene-day.blend") == state["source_sha256"]
    baseline_render = act(
        engine,
        job["id"],
        "render",
        {
            **pinned(workspace, "source/scene-day.blend"),
            "render_settings": {"engine": "CYCLES", "resolution": [1200, 834], "samples": 24},
            "export_collection": "StudioEnvironment",
            "include_presentation": True,
        },
        "source-baseline-render",
        root,
        state,
        audit,
    )
    require_verified(baseline_render)
    state["before_render_image"] = native_file(baseline_render, "png")
    parameters["expected_source_sha256"] = state["source_sha256"]
    edited = act(
        engine, job["id"], "run_script", parameters, "pinned-cart-edit", root, state, audit
    )
    require_verified(edited)
    edited_path = native_file(edited, "blend")
    edit_evidence = json.loads(
        (workspace / edited["run_dir"] / "revision-evidence.json").read_text()
    )
    assert edit_evidence["cart_objects_moved"] == 28
    assert edit_evidence["cart_translation_x_m"] == 0.25
    assert edit_evidence["geometry_rebuilt"] is False
    assert edit_evidence["before"]["meshes"] == edit_evidence["after"]["meshes"] == 1571
    state["edit_measurements"] = edit_evidence
    state["edited_native"] = edited_path
    # The same original script with a zero displacement reports loaded positions
    # before doing any edit. This independently observes the saved native scene.
    reread_parameters = {
        **pinned(workspace, edited_path),
        "script_path": "scripts/environments/revise_and_render.py",
        "script_parameters": {
            "lighting_theme": "day",
            "cart_offset_x_m": 0.0,
            "render_views": [],
            "camera": "Overview",
        },
        "render": False,
        "export_collection": "StudioEnvironment",
        "include_presentation": True,
    }
    reread = act(
        engine,
        job["id"],
        "run_script",
        reread_parameters,
        "fresh-cart-position-read",
        root,
        state,
        audit,
    )
    require_verified(reread)
    reread_evidence = json.loads(
        (workspace / reread["run_dir"] / "revision-evidence.json").read_text()
    )
    assert (
        reread_evidence["before"]["cart_world_positions_m"]
        == edit_evidence["after"]["cart_world_positions_m"]
    )
    state["reread_measurements"] = reread_evidence
    inspected = act(
        engine,
        job["id"],
        "inspect",
        {
            **pinned(workspace, edited_path),
            "export_collection": "StudioEnvironment",
            "include_presentation": True,
        },
        "fresh-native-inspection",
        root,
        state,
        audit,
    )
    require_verified(inspected)
    rendered = act(
        engine,
        job["id"],
        "render",
        {
            **pinned(workspace, edited_path),
            "render_settings": {"engine": "CYCLES", "resolution": [1200, 834], "samples": 24},
            "export_collection": "StudioEnvironment",
            "include_presentation": True,
        },
        "fresh-native-render",
        root,
        state,
        audit,
    )
    require_verified(rendered)
    state["render_image"] = native_file(rendered, "png")
    sanitized = act(
        engine,
        job["id"],
        "run_script",
        {
            **pinned(workspace, edited_path),
            "script_path": "scripts/environments/sanitize_scene.py",
            "render": False,
            "export_collection": "StudioEnvironment",
            "include_presentation": True,
        },
        "portable-edited-scene",
        root,
        state,
        audit,
    )
    require_verified(sanitized)
    state["portable_native"] = native_file(sanitized, "blend")
    assert file_digest(workspace / "source/scene-day.blend") == state["source_sha256"]
    assert implementation_hashes() == state["execution_sources"], (
        "Source changed during native execution; inspect provenance before export."
    )
    state["stage"] = "blender-complete"
    write_json(root / "state.json", state)
    return state


def native_unreal(root: Path, args, state: dict) -> dict:
    if state["stage"] != "blender-complete":
        raise ValueError("Unreal stage requires the verified Blender stage.")
    workspace = root / "workspace"
    source_hashes = implementation_hashes()
    state["unreal_execution_sources"] = source_hashes
    engine = engine_for(root, args)
    audit = NativeLaunchAudit(root)
    audit.phase = "probe"
    probe = unreal.probe(engine.config)
    state["unreal_version"] = probe.get("version", {})
    if not probe["available"]:
        state["unreal_status"] = "unavailable"
        write_json(root / "state.json", state)
        return state
    exported = act(
        engine,
        state["blender_job"],
        "export_asset",
        {
            **pinned(workspace, state["portable_native"]),
            "export_collection": "StudioEnvironment",
            "include_presentation": True,
            "export_formats": ["glb"],
            "environment_manifest": True,
            "split_assets": True,
            "render": False,
        },
        "edited-environment-export",
        root,
        state,
        audit,
    )
    require_verified(exported)
    manifest = native_file(exported, "environment-manifest")
    state["transfer_manifest"] = manifest
    job = engine.create_job(
        "source-bound-unreal",
        "unreal",
        "Import and independently reopen the source-bound edited original lab as a static scene.",
    )
    state["unreal_job"] = job["id"]
    created = act(
        engine,
        job["id"],
        "create_project",
        {"name": "SourceBindingLab"},
        "new-unreal-project",
        root,
        state,
        audit,
    )
    require_verified(created)
    destination = "/Game/StudioAgentBridge/Environments/SourceBoundLab"
    level = destination + "/Maps/SourceBoundLab"
    built = act(
        engine,
        job["id"],
        "build_scene",
        {
            "environment_manifest": manifest,
            "destination": destination,
            "name": "SourceBoundLab",
            "level_path": level,
            "rendering": "structure",
        },
        "unreal-edited-scene",
        root,
        state,
        audit,
    )
    if built["status"] != "verified":
        state["unreal_status"] = (
            "blocked-structure-attempt"
            if built["status"] == "blocked"
            else "failed-structure-attempt"
        )
        write_json(root / "state.json", state)
    require_verified(built)
    inspected = act(
        engine,
        job["id"],
        "inspect",
        {
            "environment_manifest": manifest,
            "destination": destination,
            "level_path": level,
            "rendering": "structure",
        },
        "unreal-fresh-reopen",
        root,
        state,
        audit,
    )
    require_verified(inspected)
    state["unreal_status"] = "verified-structure"
    assert implementation_hashes() == source_hashes, "Source changed during Unreal execution."
    state["unreal_execution_sources"] = source_hashes
    state["stage"] = "unreal-complete"
    write_json(root / "state.json", state)
    return state


def explicit_gpu_retry_basis(previous: dict | None) -> dict:
    """Distinguish a never-launched preflight block from an exited native failure."""
    if previous is None or previous.get("status") not in {"failed", "blocked"}:
        raise ValueError(
            "This explicit retry requires a recorded failed or known preflight-blocked structure attempt."
        )
    observation = previous["result"]["result"].get("observations", {})
    if previous["status"] == "blocked":
        guard = observation.get("preflight", {})
        if not (
            guard.get("decision") == "blocked"
            and guard.get("code") == unreal.ENVIRONMENT_NULLRHI_RESTRICTION
            and guard.get("action") == "build_scene"
            and guard.get("editor_session") is True
            and guard.get("rendering_mode") == "structure"
            and observation.get("native_launch_attempted") is False
            and type(previous.get("native_process_launches")) is int
            and previous["native_process_launches"] == 0
            and observation.get("returncode") is None
            and observation.get("timed_out") is None
        ):
            raise ValueError(
                "Only the known preflight block with zero observed native launches permits this explicit alternate-mode attempt."
            )
        return {
            "previous_attempt": "preflight-blocked",
            "previous_native_process_launches": 0,
            "previous_native_process_exited": None,
            "previous_returncode": None,
            "preflight_code": guard["code"],
        }
    if observation.get("returncode") is None or observation.get("timed_out") is not False:
        raise ValueError("Confirm the prior native process exited before this explicit retry.")
    return {
        "previous_attempt": "native-failure",
        "previous_native_process_exited": True,
        "previous_returncode": observation["returncode"],
    }


def native_unreal_gpu_retry(root: Path, args, state: dict) -> dict:
    """One explicit alternate-mode attempt; never an automatic fallback."""
    basis = explicit_gpu_retry_basis(state["actions"].get("unreal-edited-scene"))
    source_hashes = implementation_hashes()
    engine = engine_for(root, args)
    audit = NativeLaunchAudit(root)
    destination = "/Game/StudioAgentBridge/Environments/SourceBoundLabGpu"
    level = destination + "/Maps/SourceBoundLabGpu"
    parameters = {
        "environment_manifest": state["transfer_manifest"],
        "destination": destination,
        "level_path": level,
        "rendering": "gpu",
    }
    state["unreal_retry"] = {
        "explicit": True,
        **basis,
        "fresh_destination": destination,
        "source_hashes": source_hashes,
    }
    built = act(
        engine,
        state["unreal_job"],
        "build_scene",
        {**parameters, "name": "SourceBoundLabGpu"},
        "unreal-gpu-edited-scene",
        root,
        state,
        audit,
    )
    state["unreal_status"] = "failed-gpu-attempt"
    write_json(root / "state.json", state)
    require_verified(built)
    inspected = act(
        engine,
        state["unreal_job"],
        "inspect",
        parameters,
        "unreal-gpu-fresh-reopen",
        root,
        state,
        audit,
    )
    state["unreal_status"] = "failed-gpu-reopen"
    write_json(root / "state.json", state)
    require_verified(inspected)
    assert implementation_hashes() == source_hashes, "Source changed during Unreal execution."
    state["unreal_status"] = "verified-structure-with-gpu-editor"
    state["unreal_execution_sources"] = source_hashes
    state["stage"] = "unreal-complete"
    write_json(root / "state.json", state)
    return state


def strip_png_text(source: Path, destination: Path) -> dict:
    """Remove text/EXIF metadata while preserving every compressed pixel byte."""
    raw = source.read_bytes()
    signature = b"\x89PNG\r\n\x1a\n"
    if not raw.startswith(signature):
        raise ValueError("Native render is not a PNG.")
    cleaned = bytearray(signature)
    position = len(signature)
    pixel_before = hashlib.sha256()
    pixel_after = hashlib.sha256()
    removed = []
    width = height = None
    while position < len(raw):
        length = struct.unpack(">I", raw[position : position + 4])[0]
        kind = raw[position + 4 : position + 8]
        data = raw[position + 8 : position + 8 + length]
        end = position + 12 + length
        expected_crc = struct.unpack(">I", raw[end - 4 : end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != expected_crc:
            raise ValueError("Invalid native PNG chunk checksum.")
        if kind == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
        if kind == b"IDAT":
            pixel_before.update(data)
        if kind in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}:
            removed.append(kind.decode())
        else:
            cleaned.extend(raw[position:end])
            if kind == b"IDAT":
                pixel_after.update(data)
        position = end
    assert pixel_before.digest() == pixel_after.digest()
    destination.write_bytes(cleaned)
    return {
        "filename": destination.name,
        "width": width,
        "height": height,
        "sha256": file_digest(destination),
        "bytes": len(cleaned),
        "raw_native_sha256": file_digest(source),
        "removed_metadata_chunks": removed,
        "compressed_pixel_bytes_unchanged": True,
    }


def sanitize_downloads(root: Path, args, state: dict) -> dict:
    if state["stage"] not in {"blender-complete", "unreal-complete"}:
        raise ValueError("Sanitization requires completed native Blender evidence.")
    workspace = root / "workspace"
    script = REPOSITORY / "examples/source_binding/sanitize_public_copy.py"
    relative_script = "scripts/source_binding/sanitize_public_copy.py"
    target = workspace / relative_script
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(script, target)
    engine = engine_for(root, args)
    audit = NativeLaunchAudit(root)
    for name, source in (
        ("baseline", "source/scene-day.blend"),
        ("edited", state["portable_native"]),
    ):
        record = act(
            engine,
            state["blender_job"],
            "run_script",
            {
                **pinned(workspace, source),
                "script_path": relative_script,
                "render": False,
                "export_collection": "StudioEnvironment",
                "include_presentation": True,
            },
            "public-" + name + "-sanitization",
            root,
            state,
            audit,
        )
        require_verified(record)
        state["download_" + name + "_native"] = native_file(record, "blend")
    state["public_sanitizer_sha256"] = file_digest(script)
    write_json(root / "state.json", state)
    return state


def package_outputs(root: Path, state: dict) -> dict:
    workspace = root / "workspace"
    output = root / "public-exports"
    output.mkdir(exist_ok=True)
    native = workspace / state["download_edited_native"]
    original = workspace / state["download_baseline_native"]
    zstd = shutil.which("zstd")
    if not zstd:
        raise RuntimeError(
            "Install zstd to inspect decompressed Blender bytes before public packaging."
        )
    for candidate in (original, native):
        data = subprocess.run(
            [zstd, "-d", "-c", str(candidate)], capture_output=True, check=True
        ).stdout
        for marker in (
            b"/Users/",
            b"/home/",
            b"/var/folders/",
            b"/private/var/",
            str(root).encode(),
            Path.home().name.encode() if len(Path.home().name) >= 4 else b"/Users/",
        ):
            if marker in data:
                raise ValueError("Native file still contains a private path in decompressed bytes.")
    assert file_digest(workspace / "source/scene-day.blend") == state["source_sha256"]
    images = {
        "before": strip_png_text(workspace / state["before_render_image"], output / "before.png"),
        "after": strip_png_text(workspace / state["render_image"], output / "after.png"),
    }
    assert images["before"]["width"] == images["after"]["width"] == 1200
    assert images["before"]["height"] == images["after"]["height"] == 834
    metadata = {
        "schema_version": "source-bound-native-download/v1",
        "source_archive": state["archive"],
        "original_source_sha256": state["source_sha256"],
        "sanitized_baseline_sha256": file_digest(original),
        "edited_native_sha256": file_digest(native),
        "cart_offset_x_m": 0.25,
        "cart_objects": 28,
        "geometry_rebuilt": False,
        "blender_version": state["blender_version"],
        "private_path_scan": "Full private path prefixes and current local account identifier absent from decompressed bytes",
        "publication_copies": "Native path-field sanitization changes file bytes. The input source remains preserved separately; the downloadable baseline is not byte-identical to that input.",
        "sanitizer_sha256": state["public_sanitizer_sha256"],
        "limitations": [
            "Original visualization scene; no physical or robot safety certification.",
            "The edit is a measured native cart translation, not autonomous geometry inference.",
            "Measurements cover cart positions, mesh counts and adapter reopen invariants; complete mesh data and every non-cart transform were not independently compared.",
        ],
    }
    archive = output / "source-bound-cart-native.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:

        def add_member(name, content):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            bundle.writestr(entry, content, compresslevel=6)

        add_member("source-bound-cart/scene-baseline.blend", original.read_bytes())
        add_member("source-bound-cart/scene-edited.blend", native.read_bytes())
        add_member("source-bound-cart/evidence.json", json.dumps(metadata, indent=2) + "\n")
        add_member(
            "source-bound-cart/README.txt",
            "Original Industrial AI Robotics Lab, source-bound cart revision.\nOpen scene-baseline.blend and scene-edited.blend in Blender 5.2 or newer. Packed textures are included in each file.\nAll 28 diagnostics cart objects moved +0.25 metres on X. The script requests translation only. Measurements cover those positions, mesh counts and adapter reopen invariants, not complete mesh data or every non-cart transform.\nBoth download copies were sanitized and resaved through native path properties. Their file hashes differ from their raw inputs. The original input source is separately preserved and identified in evidence.json. This is a visual demonstration.\n",
        )
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
    assert archive.stat().st_size < 25_000_000
    packaged = {
        "images": images,
        "native_download": {
            "filename": archive.name,
            "sha256": file_digest(archive),
            "bytes": archive.stat().st_size,
            "native_sha256": file_digest(native),
            "original_native_sha256": file_digest(original),
            "input_source_sha256": state["source_sha256"],
            "original_native_filename": "scene-baseline.blend",
            "baseline_native_is_sanitized_copy": True,
            "edited_native_filename": "scene-edited.blend",
            "private_path_scan_passed": True,
            "zip_integrity_passed": True,
        },
    }
    write_json(output / "package-info.json", packaged)
    return packaged


def unreal_observations(workspace: Path, state: dict) -> dict:
    """Export allowlisted native facts, keeping raw host logs private."""
    attempts = []
    for label, record in state["actions"].items():
        if not label.startswith("unreal-"):
            continue
        result = record["result"]["result"]
        observations = result.get("observations", {})
        attempt = {
            "action_id": label,
            "status": record["status"],
            "rendering_mode": observations.get("rendering_mode"),
            "returncode": observations.get("returncode"),
            "timed_out": observations.get("timed_out"),
            "native_process_launches": record["native_process_launches"],
        }
        guard = observations.get("preflight")
        if guard:
            attempt["preflight"] = {
                key: guard[key]
                for key in (
                    "decision",
                    "code",
                    "action",
                    "editor_session",
                    "rendering_mode",
                    "engine",
                    "platform",
                    "required_parameters",
                    "basis",
                )
                if key in guard
            }
            attempt["rendering_mode"] = guard.get("rendering_mode")
            attempt["native_launch_attempted"] = observations.get("native_launch_attempted")
        verification = observations.get("native", {}).get("verification")
        if verification:
            attempt["verification"] = {
                key: verification[key]
                for key in (
                    "passed",
                    "reopened",
                    "manifestSha256",
                    "engineVersion",
                    "objectCount",
                    "uniqueMeshCount",
                    "sharedMeshInstances",
                    "transformChecksPassed",
                    "coordinatePolicy",
                    "navigation",
                )
            }
            attempt["verification"].update(
                {
                    "mesh_asset_count": len(verification["meshAssets"]),
                    "material_count": len(verification["materials"]),
                    "light_count": len(verification["lights"]),
                    "camera_count": len(verification["cameras"]),
                    "max_mesh_bounds_residual_cm": max(
                        item["boundsCheck"]["maxResidualCm"] for item in verification["meshAssets"]
                    ),
                    "all_mesh_bounds_checks_passed": all(
                        item["boundsCheck"]["passed"] for item in verification["meshAssets"]
                    ),
                    "all_material_checks_passed": all(
                        item["passed"] for item in verification["materials"]
                    ),
                }
            )
        if record["status"] == "failed":
            logs = [
                workspace / item["path"]
                for item in result.get("artifacts", [])
                if item.get("kind") == "log"
            ]
            text = "\n".join(path.read_text(errors="replace") for path in logs)
            if all(
                token in text
                for token in (
                    "Array index out of bounds",
                    "FViewport::GetHitProxy",
                    "SpawnActorFromClass",
                )
            ):
                attempt["failure"] = {
                    "category": "native-editor-viewport-assertion",
                    "detail": "Unreal exited after an array-bounds assertion in viewport hit-proxy handling during actor placement.",
                    "native_verification_produced": False,
                    "raw_log_sha256": [file_digest(path) for path in logs],
                }
            else:
                attempt["failure"] = {
                    "category": "native-action-failed",
                    "detail": "The native action failed; inspect the retained private host diagnostics.",
                    "native_verification_produced": bool(verification),
                }
        attempts.append(attempt)
    return {
        "status": state.get("unreal_status", "failed" if attempts else "not-run"),
        "attempts": attempts,
        "explicit_retry": state.get("unreal_retry"),
        "appearance_validated": False,
        "scope": "Native static structure, saved asset properties, and measured transforms; no Unreal render was captured.",
    }


def summarize(root: Path, state: dict, destination: Path) -> dict:
    if state["stage"] not in {"blender-complete", "unreal-complete"}:
        raise ValueError(
            "Complete native Blender evidence is required before exporting a public summary."
        )
    workspace = root / "workspace"
    actions = []
    for label, record in state["actions"].items():
        result = record["result"]["result"]
        observations = result.get("observations", {})
        actions.append(
            {
                "id": label,
                "action": record["action"],
                "status": record["status"],
                "summary": result.get("summary"),
                "revision_before": record["revision_before"],
                "revision_after": record["revision_after"],
                "native_process_launches": record["native_process_launches"],
                "source_binding": observations.get("source_binding"),
                "source_resources": observations.get("source_resources"),
            }
        )
    original = workspace / "source/scene-day.blend"
    native = workspace / state["download_edited_native"]
    before = state["edit_measurements"]["before"]["cart_world_positions_m"]
    after = state["edit_measurements"]["after"]["cart_world_positions_m"]
    deltas = {
        name: [after[name][axis] - position[axis] for axis in range(3)]
        for name, position in before.items()
    }
    binding_actions = [
        item
        for item in actions
        if item["source_binding"] is not None and item["status"] == "verified"
    ]
    edit_binding = next(
        item["source_binding"] for item in actions if item["id"] == "pinned-cart-edit"
    )
    inspection = state["actions"]["fresh-native-inspection"]["result"]["result"]["observations"][
        "initial_scene"
    ]
    before_render_scene = state["actions"]["source-baseline-render"]["result"]["result"][
        "observations"
    ]["initial_scene"]
    after_render_scene = state["actions"]["fresh-native-render"]["result"]["result"][
        "observations"
    ]["initial_scene"]
    checks = {
        "stale_hash_started_no_native_process": state["actions"]["stale-source-rejected"][
            "native_process_launches"
        ]
        == 0,
        "stale_hash_failed": state["actions"]["stale-source-rejected"]["status"] == "failed",
        "source_original_preserved": file_digest(original) == state["source_sha256"],
        "new_native_differs_from_source": file_digest(native) != state["source_sha256"],
        "cart_objects_measured": len(deltas) == 28,
        "cart_x_shift_within_tolerance": all(
            abs(delta[0] - 0.25) < 1e-5 for delta in deltas.values()
        ),
        "cart_y_z_preserved": all(
            abs(delta[1]) < 1e-5 and abs(delta[2]) < 1e-5 for delta in deltas.values()
        ),
        "cart_positions_persist_after_reopen": state["reread_measurements"]["before"][
            "cart_world_positions_m"
        ]
        == after,
        "mesh_count_preserved": state["edit_measurements"]["before"]["meshes"]
        == state["edit_measurements"]["after"]["meshes"]
        == 1571,
        "fresh_inspection_verified": state["actions"]["fresh-native-inspection"]["status"]
        == "verified",
        "fresh_render_verified": state["actions"]["fresh-native-render"]["status"] == "verified",
        "edit_uses_expected_source_snapshot": edit_binding["expected_source_sha256"]
        == edit_binding["source_sha256"]
        == edit_binding["snapshot_sha256"]
        == state["source_sha256"],
        "all_verified_source_bindings_pinned": bool(binding_actions)
        and all(item["source_binding"]["mode"] == "pinned_snapshot" for item in binding_actions),
        "all_snapshots_preserved": bool(binding_actions)
        and all(
            item["source_binding"]["snapshot_unchanged"] is True
            and item["source_binding"]["source_unchanged"] is True
            for item in binding_actions
        ),
        "matching_render_camera_and_settings": before_render_scene["render_settings"]
        == after_render_scene["render_settings"],
        "sanitized_public_copies_reopened": all(
            state["actions"][label]["status"] == "verified"
            and all(
                state["actions"][label]["result"]["result"]["observations"]["native_reopen"][
                    "checks"
                ].values()
            )
            for label in ("public-baseline-sanitization", "public-edited-sanitization")
        ),
    }
    assert all(value is True for value in checks.values())
    unreal_result = unreal_observations(workspace, state)
    if unreal_result["status"].startswith("verified"):
        final = unreal_result["attempts"][-1]
        verification = final["verification"]
        checks.update(
            {
                "unreal_fresh_reopen_verified": final["status"] == "verified"
                and verification["passed"] is True
                and verification["reopened"] is True,
                "unreal_object_count_preserved": verification["objectCount"]
                == state["edit_measurements"]["after"]["meshes"],
                "unreal_transforms_verified": verification["transformChecksPassed"] is True,
                "unreal_materials_and_mesh_bounds_verified": verification[
                    "all_material_checks_passed"
                ]
                is True
                and verification["all_mesh_bounds_checks_passed"] is True,
            }
        )
    assert all(value is True for value in checks.values())
    packaged = package_outputs(root, state)
    report = {
        "schema_version": "source-binding-native-example/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "kind": "observed-native-execution",
            "generator": "examples/source_binding/run_example.py",
            "archive": state["archive"],
            "extracted": state["extracted"],
            "execution_sources": state["execution_sources"],
            "unreal_execution_sources": state.get("unreal_execution_sources"),
            "generator_sha256": file_digest(Path(__file__)),
            "public_sanitizer_sha256": state.get("public_sanitizer_sha256"),
            "synthetic_adapter_used": False,
        },
        "host_versions": {
            "blender": state["blender_version"],
            "unreal": state.get("unreal_version"),
        },
        "request": {
            "source": "source/scene-day.blend",
            "expected_source_sha256": state["source_sha256"],
            "cart_offset_x_m": 0.25,
            "lighting_theme": "day",
        },
        "source_sha256_after_actions": file_digest(original),
        "portable_native": {
            "path": state["portable_native"],
            "sha256": file_digest(native),
            "bytes": native.stat().st_size,
        },
        "renders": packaged["images"],
        "native_download": packaged["native_download"],
        "public_copy_sanitization": {
            "method": "Native path properties overwritten with long benign values then relative paths; saved and independently reopened by Blender",
            "baseline_is_byte_identical_to_input": False,
            "source_input_preserved": True,
            "checks": {
                label: state["actions"][label]["result"]["result"]["observations"]["native_reopen"][
                    "checks"
                ]
                for label in ("public-baseline-sanitization", "public-edited-sanitization")
            },
            "scan_scope": "Decompressed native bytes checked for private path prefixes and the current local account identifier",
        },
        "actions": actions,
        "cart_translation_measurements_m": deltas,
        "before_after": {
            "before": state["edit_measurements"]["before"],
            "after": state["edit_measurements"]["after"],
            "reopened_positions_match_after": checks["cart_positions_persist_after_reopen"],
            "geometry_rebuilt": False,
        },
        "fresh_native_inspection": {
            key: inspection[key]
            for key in (
                "object_count",
                "mesh_count",
                "asset_mesh_count",
                "modifier_count",
                "material_count",
                "asset_bounds_m",
                "unit_system",
                "unit_scale",
                "render_settings",
            )
        },
        "checks": checks,
        "unreal_status": unreal_result["status"],
        "unreal": unreal_result,
        "limitations": [
            "This is a controlled native edit of the original bundled demonstration scene, not arbitrary natural-language modeling.",
            "Source pinning captures one self-contained packed .blend file; external resource trees and trusted script dependencies are outside that binding.",
            "Blender native reopen and measured object changes do not establish physical accuracy, safe robot operation, or a certified building layout.",
            "Measurements cover cart positions, mesh counts and adapter reopen invariants; complete mesh data and every non-cart transform were not independently compared.",
            "Unreal verification, when present, covers static scene structure and measured transforms. GPU editor mode alone does not validate appearance, simulation, or gameplay.",
            "The raw render hash describes the native output. Any metadata-stripped public image must receive its own hash.",
        ],
    }
    encoded = json.dumps(report)
    for forbidden in (str(root), str(REPOSITORY), "/Users/", "/home/"):
        if forbidden in encoded:
            raise ValueError("Public report contains a private absolute path.")
    write_json(destination, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-root",
        required=True,
        type=Path,
        help="A new owned private directory for preparation, then the same directory for later stages",
    )
    parser.add_argument(
        "--stage",
        choices=(
            "prepare",
            "blender",
            "unreal",
            "unreal-gpu-retry",
            "sanitize-downloads",
            "summarize",
        ),
        required=True,
    )
    parser.add_argument("--blender", help="Optional Blender executable")
    parser.add_argument("--unreal", help="Optional UnrealEditor-Cmd executable")
    parser.add_argument(
        "--public-output",
        type=Path,
        default=REPOSITORY / "examples/source_binding/observed-native-proof.json",
    )
    args = parser.parse_args(argv)
    root = args.work_root.resolve()
    if args.stage == "prepare":
        state = prepare(root)
    else:
        state = read_state(root)
        if args.stage == "blender":
            state = native_blender(root, args, state)
        elif args.stage == "unreal":
            state = native_unreal(root, args, state)
        elif args.stage == "unreal-gpu-retry":
            state = native_unreal_gpu_retry(root, args, state)
        elif args.stage == "sanitize-downloads":
            state = sanitize_downloads(root, args, state)
        else:
            summarize(root, state, args.public_output)
    print(
        json.dumps(
            {
                "stage": state["stage"],
                "actions_recorded": len(state["actions"]),
                "operation": args.stage,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
