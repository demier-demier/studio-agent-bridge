"""Package measured native evidence for the static browser explorer.

Pass the private example run's public-exports directory. No application is
launched here; this only validates and copies the already reviewed artifacts.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DESTINATION = REPO / "web/public/assets/source-binding"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_exports", type=Path)
    args = parser.parse_args()
    proof_path = REPO / "examples/source_binding/observed-native-proof.json"
    proof = json.loads(proof_path.read_text())
    assert proof["provenance"]["kind"] == "observed-native-execution"
    assert proof["provenance"]["synthetic_adapter_used"] is False
    assert all(value is True for value in proof["checks"].values())
    actions = {action["id"]: action for action in proof["actions"]}
    stale = actions["stale-source-rejected"]
    edit = actions["pinned-cart-edit"]
    target = proof["unreal"]["attempts"][-1]
    measured = target["verification"]
    assert target["status"] == "verified" and measured["reopened"]
    assert stale["native_process_launches"] == 0
    assert proof["unreal"]["appearance_validated"] is False
    assert proof["unreal"]["attempts"][0]["status"] == "failed"
    assert proof["unreal"]["explicit_retry"]["previous_native_process_exited"] is True

    DESTINATION.mkdir(parents=True, exist_ok=True)
    for entry in [*proof["renders"].values(), proof["native_download"]]:
        name = entry["filename"]
        assert Path(name).name == name
        content = (args.public_exports / name).read_bytes()
        assert len(content) == entry["bytes"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        shutil.copy2(args.public_exports / name, DESTINATION / name)
    shutil.copy2(proof_path, DESTINATION / "observed-native-proof.json")
    shutil.copy2(
        REPO / "examples/source_binding/observed-resource-proof.json",
        DESTINATION / "observed-resource-proof.json",
    )

    width = proof["renders"]["before"]["width"]
    height = proof["renders"]["before"]["height"]
    count = len(proof["cart_translation_measurements_m"])
    movement = proof["request"]["cart_offset_x_m"] * 100
    binding = edit["source_binding"]
    mesh_count = proof["before_after"]["after"]["meshes"]
    case = {
        "schema": "source-binding-browser-case/v1",
        "kind": "recorded-native-execution",
        "proof_sha256": hashlib.sha256(proof_path.read_bytes()).hexdigest(),
        "render": {
            "host": proof["host_versions"]["blender"] + " / Cycles",
            "caption": f"{width} × {height} native renders; same Overview camera and settings. Drag to compare the cart in the foreground. Only metadata was removed from the published images.",
        },
        "metrics": {
            "movement": f"+{movement:g} cm",
            "components": str(count),
            "meshes": f"{mesh_count:,}",
            "stale-launches": str(stale["native_process_launches"]),
        },
        "steps": [
            {
                "title": "Reject the stale version",
                "detail": "The submitted source hash did not match the bytes copied from the scene. Blender was never launched for this attempt. The engine still recorded the failed action.",
                "result": f"0 native launches · job revision {stale['revision_before']} → {stale['revision_after']}",
                "status": "blocked",
            },
            {
                "title": "Edit from the preserved snapshot",
                "detail": f"The expected, source and snapshot hashes matched. The trusted script moved {count} cart components by {movement:g} cm along X and saved a separate editable scene.",
                "result": f"Verified edit · job revision {edit['revision_before']} → {edit['revision_after']}",
                "status": "verified",
            },
            {
                "title": "Reopen and measure again",
                "detail": f"Fresh Blender processes loaded the edited file, rechecked cart positions and rendered it. The original source and snapshots stayed unchanged; the scene retained {mesh_count:,} meshes.",
                "result": "Native reopen + matching-camera render verified",
                "status": "verified",
            },
            {
                "title": "Check the actual target",
                "detail": "The first Unreal build failed in NullRHI viewport handling. After confirming that process had exited, an explicit GPU-mode build used a new destination. A separate editor process reopened the saved level.",
                "result": f"{measured['objectCount']:,} mesh actors · target structure verified",
                "status": "verified",
            },
        ],
        "identity": {
            "Reviewed source SHA-256": proof["request"]["expected_source_sha256"],
            "Snapshot used for the edit": binding["snapshot_sha256"],
            "Original source after all actions": proof["source_sha256_after_actions"],
            "Published native ZIP SHA-256": proof["native_download"]["sha256"],
        },
        "checks": [
            "Stale source rejected before Blender launch",
            "Copied bytes match the reviewed hash",
            "Source and snapshots preserved",
            "Cart X movement measured; Y/Z unchanged",
            "Cart positions persisted after reopening",
            "Mesh count retained",
            "Matching render camera and settings",
            "Unreal saved level reopened separately",
        ],
        "unreal": {
            "summary": "Unreal 5.7.4 reopened the revised laboratory in a fresh GPU-enabled editor process. This run checked static structure and placement; it did not capture an Unreal render.",
            "checks": [
                f"{measured['objectCount']:,} mesh actors, {measured['mesh_asset_count']} reusable mesh assets and {measured['material_count']} materials.",
                f"{measured['light_count']} lights and {measured['camera_count']} cameras retained.",
                "Instance transforms, material properties and mesh bounds matched the exported manifest.",
                f"Maximum observed mesh-bounds residual: {measured['max_mesh_bounds_residual_cm']:.8f} cm.",
                "Both the successful build and fresh inspection exited with code 0.",
            ],
        },
        "limitations": [
            "The initial NullRHI environment build crashed in viewport actor placement. Its failure and the explicit GPU retry are retained in the raw evidence.",
            "GPU mode was used for structure checks. Unreal appearance, navigation, robot control and packaged gameplay were not validated in this run.",
            "Blender checks cover cart positions, mesh count and native reopen invariants, not every unrelated transform or mesh byte.",
            "Only .blend bytes are pinned. The resource gate covers known built-in types, not every add-on, script or external dependency.",
            "Trusted Python remains privileged. Read-only snapshots are not an OS sandbox or a rollback mechanism.",
            "Downloads contain publication copies re-saved to remove local metadata. Their hashes are distinct from the unchanged execution source where applicable.",
        ],
        "provenance": f"Observed {proof['generated_at'][:10]}. Original public laboratory, real Blender and Unreal adapters, no synthetic host substitution. Native files and image hashes are included in the downloadable evidence.",
    }
    write_json(DESTINATION / "case.json", case)
    manifest = {}
    for path in sorted(DESTINATION.iterdir()):
        if path.name == "manifest.json":
            continue
        content = path.read_bytes()
        manifest[path.name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    write_json(DESTINATION / "manifest.json", manifest)
    print(f"Packaged {len(manifest)} source-binding assets with exact hashes.")


if __name__ == "__main__":
    main()
