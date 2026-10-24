#!/usr/bin/env python3
"""Validate the resource gate with real Blender and tiny original test-only scenes."""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from run_example import (
    REPOSITORY,
    NativeLaunchAudit,
    act,
    engine_for,
    file_digest,
    implementation_hashes,
    native_file,
    pinned,
    require_verified,
    write_json,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--blender")
    parser.add_argument("--unreal", help=argparse.SUPPRESS)
    parser.add_argument(
        "--public-output",
        type=Path,
        default=REPOSITORY / "examples/source_binding/observed-resource-proof.json",
    )
    args = parser.parse_args(argv)
    root = args.work_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new empty root; earlier fixture evidence is preserved.")
    scripts = root / "workspace/scripts"
    scripts.mkdir(parents=True)
    for name in ("build_image_fixture.py", "edit_fixture.py"):
        shutil.copyfile(Path(__file__).with_name("fixtures") / name, scripts / name)
    source_hashes = implementation_hashes()
    state = {"owner": "studio-source-binding-resource-check/v1", "actions": {}}
    engine = engine_for(root, args)
    audit = NativeLaunchAudit(root)
    cases = []
    for packed in (True, False):
        label = "packed" if packed else "unpacked"
        job = engine.create_job(
            "resource-" + label,
            "blender",
            "Measure native resource gating with an original test-only image fixture.",
        )
        created = act(
            engine,
            job["id"],
            "run_script",
            {
                "script_path": "scripts/build_image_fixture.py",
                "script_parameters": {"packed": packed},
                "render": False,
            },
            label + "-fixture-create",
            root,
            state,
            audit,
        )
        require_verified(created)
        input_blend = native_file(created, "blend")
        observed_image = json.loads(
            (root / "workspace" / created["run_dir"] / "fixture-observations.json").read_text()
        )
        attempted = act(
            engine,
            job["id"],
            "run_script",
            {
                **pinned(root / "workspace", input_blend),
                "script_path": "scripts/edit_fixture.py",
                "render": False,
            },
            label + "-edit-attempt",
            root,
            state,
            audit,
        )
        marker = root / "workspace" / attempted["run_dir"] / "requested-script-ran.json"
        if packed:
            require_verified(attempted)
            assert marker.is_file()
        else:
            assert attempted["status"] == "failed"
            assert attempted["native_process_launches"] == 1
            assert not marker.exists()
        observations = attempted["result"]["result"].get("observations", {})
        cases.append(
            {
                "id": label + "-file-image",
                "fixture_image": observed_image,
                "status": attempted["status"],
                "summary": attempted["result"]["result"].get("summary"),
                "native_process_launches": attempted["native_process_launches"],
                "requested_script_executed": marker.exists(),
                "source_binding": observations.get("source_binding"),
                "source_resources": observations.get("source_resources"),
                "blender_version": observations.get("blender_version"),
                "checks": {
                    "actual_file_image": observed_image["image_source"] == "FILE",
                    "actual_packed_state": observed_image["packed"] is packed,
                    "requested_script_matches_policy": marker.exists() is packed,
                    "expected_status": attempted["status"] == ("verified" if packed else "failed"),
                },
            }
        )
    assert implementation_hashes() == source_hashes
    assert all(value is True for case in cases for value in case["checks"].values())
    report = {
        "schema_version": "source-binding-resource-example/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "kind": "observed-native-execution",
            "generator": "examples/source_binding/check_resources.py",
            "execution_sources": source_hashes,
            "fixture_sha256": {
                name: file_digest(scripts / name)
                for name in ("build_image_fixture.py", "edit_fixture.py")
            },
            "synthetic_adapter_used": False,
            "test_only_geometry": True,
        },
        "cases": cases,
        "limitations": [
            "These are minimal authored test scenes executed by real Blender; they are separate from the detailed lab edit.",
            "The unpacked resource is rejected after opening the native file. This is intentionally one native launch, not a pre-launch hash rejection.",
            "The check validates this image boundary; it is not an exhaustive test of every Blender resource type.",
        ],
    }
    encoded = json.dumps(report)
    for forbidden in (str(root), str(REPOSITORY), "/Users/", "/home/"):
        assert forbidden not in encoded, "Private paths must not enter public resource evidence."
    write_json(args.public_output, report)
    print(
        json.dumps(
            {
                "cases": len(cases),
                "checks_passed": sum(len(case["checks"]) for case in cases),
                "native": True,
            }
        )
    )


if __name__ == "__main__":
    main()
