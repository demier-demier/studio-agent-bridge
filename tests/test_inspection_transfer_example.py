"""Verify handoff byte binding before any private output or native launch."""
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / "examples/inspection_workcell/run_transfer.py"
spec = importlib.util.spec_from_file_location("inspection_transfer_example", PATH)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def source_run(tmp_path):
    root = tmp_path / "source-run"
    workspace = root / "workspace"
    native = workspace / "jobs/source/runs/000004-run_script/scene.blend"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"reviewed native scene")
    artifact = {"path": native.relative_to(workspace).as_posix(),
                "kind": "blend", **example.record(native)}
    binding = {
        "source_path": artifact["path"], "expected_source_sha256": artifact["sha256"],
        "source_sha256_after_action": artifact["sha256"],
        "snapshot_sha256_after_action": artifact["sha256"],
        "source_unchanged": True, "snapshot_unchanged": True,
    }
    fresh_dir = "jobs/source/runs/000006-run_script"
    readback = {"passed": True, "original_mesh_count": 1820, "original_fields_sha256": "a" * 64}
    (workspace / fresh_dir).mkdir()
    example.write_json(workspace / fresh_dir / "utility-readback.json", readback)
    (workspace / "scripts").mkdir()
    hashes = {}
    for name in example.SOURCE_SCRIPTS:
        p = workspace / "scripts" / name
        p.write_bytes(name.encode())
        hashes[name] = example.record(p)["sha256"]
    state = {
        "owner": "inspection-utilities-run/v1", "status": "native-verified",
        "original_source_unchanged": True,
        "comparison": {"existing_measured_mesh_fields_equal": True, "utility_readback_passed": True},
        "script_hashes": hashes, "native": artifact,
        "actions": {
            "build": {"status": "verified", "result": {"artifacts": [artifact]}},
            "fresh-readback": {"status": "verified", "run_dir": fresh_dir,
                               "result": {"observations": {"source_binding": binding}}},
        },
        "readbacks": {"fresh": readback},
        "negative_check": {"reported_status": "failed", "readback": {
            **readback, "passed": False, "errors": [],
            "measurements": {"routes": {"Camera_L": {"attachments": {"end": {
                "endpoint_to_gland_centre_delta_m": 0.1,
                "checks": {"endpoint_to_gland_centre_within_1mm": False},
            }}}}},
        }, "original_native_unchanged": True,
            "rejected_endpoint": "Camera_L.end", "measured_gap_m": 0.1},
    }
    example.write_json(root / "state.json", state)
    return root, native, state


@pytest.mark.parametrize("changed", ["native", "readback", "script", "binding", "status", "artifact"])
def test_changed_or_unbound_source_is_rejected_before_output_creation(tmp_path, changed):
    source, native, state = source_run(tmp_path)
    if changed == "native":
        native.write_bytes(b"changed native scene")
    elif changed == "readback":
        p = source / "workspace" / state["actions"]["fresh-readback"]["run_dir"] / "utility-readback.json"
        example.write_json(p, {"passed": True, "original_mesh_count": 1819})
    elif changed == "script":
        (source / "workspace/scripts/verify_utilities.py").write_text("changed")
    elif changed == "binding":
        state["actions"]["fresh-readback"]["result"]["observations"]["source_binding"]["expected_source_sha256"] = "0" * 64
    elif changed == "status":
        state["status"] = "running-render-wide"
    else:
        state["native"] = {**state["native"], "path": "unrelated.blend"}
    example.write_json(source / "state.json", state)
    output = tmp_path / "new-transfer"
    with pytest.raises(ValueError):
        example.prepare(source, output)
    assert not output.exists()


def test_existing_output_is_preserved_even_when_source_is_missing(tmp_path):
    output = tmp_path / "previous"
    output.mkdir()
    retained = output / "evidence.json"
    retained.write_text("retained")
    with pytest.raises(ValueError, match="new output"):
        example.prepare(tmp_path / "missing", output)
    assert retained.read_text() == "retained"


def test_native_symlink_is_rejected_before_output_creation(tmp_path):
    source, native, _ = source_run(tmp_path)
    target = tmp_path / "elsewhere.blend"
    target.write_bytes(native.read_bytes())
    native.unlink()
    native.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        example.prepare(source, tmp_path / "transfer")
    assert not (tmp_path / "transfer").exists()


def test_preparation_pins_exact_state_native_and_staged_scripts(tmp_path):
    source, native, _ = source_run(tmp_path)
    original_state = (source / "state.json").read_bytes()
    original_native = native.read_bytes()
    workspace, state = example.prepare(source, tmp_path / "transfer")
    assert (workspace / "source/inspection-utilities.blend").read_bytes() == original_native
    assert (workspace.parent / "source-state.json").read_bytes() == original_state
    assert state["source_state"] == {"sha256": example.sha(original_state), "bytes": len(original_state)}
    assert all(example.record(workspace / "scripts" / name)["sha256"] == digest
               for name, digest in state["script_hashes"].items())
    assert native.read_bytes() == original_native
    assert json.loads((workspace.parent / "state.json").read_text())["status"] == "prepared"


def test_relative_parent_segments_are_normalized_without_following_symlinks(tmp_path, monkeypatch):
    source, _, _ = source_run(tmp_path)
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)
    workspace, state = example.prepare(Path("../source-run"), Path("../transfer"))
    assert workspace == tmp_path / "transfer/workspace"
    assert state["source_run"] == str(source)
    link = working / "linked"
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        example.absolute_path(link)


@pytest.mark.parametrize("defect", ["gap", "missing_gap", "wrong_endpoint", "digest", "unexpected_error"])
def test_negative_check_must_match_the_specific_measured_defect(tmp_path, defect):
    source, _, state = source_run(tmp_path)
    negative = state["negative_check"]
    endpoint = negative["readback"]["measurements"]["routes"]["Camera_L"]["attachments"]["end"]
    if defect == "gap":
        negative["measured_gap_m"] = endpoint["endpoint_to_gland_centre_delta_m"] = 0.01
    elif defect == "missing_gap":
        del endpoint["endpoint_to_gland_centre_delta_m"]
    elif defect == "wrong_endpoint":
        negative["rejected_endpoint"] = "HMI.end"
    elif defect == "digest":
        negative["readback"]["original_fields_sha256"] = "b" * 64
    else:
        negative["readback"]["errors"] = ["unrelated native error"]
    example.write_json(source / "state.json", state)
    with pytest.raises(ValueError, match="measured Camera_L.end"):
        example.prepare(source, tmp_path / "transfer")
    assert not (tmp_path / "transfer").exists()


def test_both_unreal_results_must_pass_the_exact_manifest_checks():
    manifest = {"totals": {"meshInstances": 2, "uniqueAssets": 1, "materials": 1, "lights": 1, "cameras": 1}}
    verification = {"passed": True, "reopened": True, "transformChecksPassed": True,
                    "manifestSha256": "expected", "objectCount": 2,
                    "meshAssets": [{"boundsCheck": {"passed": True}}],
                    "materials": [{"passed": True}], "lights": [{}], "cameras": [{}]}
    result = {"status": "verified", "result": {"observations": {"native": {"verification": verification}}}}
    assert example.verified_unreal_result(result, manifest, "expected") is verification
    verification["manifestSha256"] = "another-revision"
    with pytest.raises(ValueError, match="exact cleaned transfer"):
        example.verified_unreal_result(result, manifest, "expected")


def png_chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)


def valid_png(size):
    header = struct.pack(">IIBBBBB", *size, 8, 2, 0, 0, 0)
    pixels = (b"\0" + bytes(size[0] * 3)) * size[1]
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) +
            png_chunk(b"IDAT", zlib.compress(pixels)) + png_chunk(b"IEND", b""))


def test_render_claim_requires_all_three_pngs_and_exact_dimensions(tmp_path):
    renders = tmp_path / "renders"
    renders.mkdir()
    with pytest.raises(ValueError, match="three expected"):
        example.verified_render_images(tmp_path)
    for name, size in {"unreal-close-build.png": (1100, 850),
                       "unreal-close-reopened.png": (1100, 850),
                       "unreal-wide-reopened.png": (1280, 720)}.items():
        (renders / name).write_bytes(valid_png(size))
    assert len(example.verified_render_images(tmp_path)) == 3
    (renders / "unreal-wide-reopened.png").write_bytes(b"")
    with pytest.raises(ValueError, match="nonempty PNG"):
        example.verified_render_images(tmp_path)


def test_valid_png_with_native_zero_padding_is_accepted_without_changing_bytes():
    original = valid_png((2, 2))
    padded = original + bytes(4096)
    metadata = example.png_metadata(padded, (2, 2))
    assert metadata["zero_padding_bytes"] == 4096
    assert metadata["png_bytes_through_iend"] == len(original)
    assert metadata["png_crc_chunks_verified"] == 3
    assert padded == original + bytes(4096)


@pytest.mark.parametrize("defect", ["nonzero_trailer", "truncated", "crc", "dimensions", "missing_idat", "iend_payload"])
def test_invalid_png_framing_is_rejected(defect):
    data = valid_png((2, 2))
    size = (2, 2)
    if defect == "nonzero_trailer":
        data += b"\0\0private-data"
    elif defect == "truncated":
        data = data[:-3]
    elif defect == "crc":
        changed = bytearray(data)
        changed[32] ^= 1
        data = bytes(changed)
    elif defect == "dimensions":
        size = (3, 2)
    elif defect == "missing_idat":
        data = data[:33] + png_chunk(b"IEND", b"")
    else:
        data = data[:-12] + png_chunk(b"IEND", b"unexpected")
    with pytest.raises(ValueError):
        example.png_metadata(data, size)
