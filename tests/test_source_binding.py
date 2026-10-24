"""Synthetic file/adapter fault injection; no test here proves native Blender behavior."""

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio_agent_bridge import source_binding
from studio_agent_bridge.adapters import blender
from studio_agent_bridge.host_scripts.blender.source_resources import inspect_resources


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    workspace = tmp_path.resolve()
    source = workspace / "original scene.blend"
    source.write_bytes(b"synthetic source bytes, not a Blender scene")
    (workspace / "edit.py").write_text("pass\n")
    (workspace / "asset.glb").write_bytes(b"synthetic asset")
    monkeypatch.setattr(blender, "_executable", lambda _: "synthetic-host")
    calls = []
    scene = {
        "object_names": ["Test"],
        "object_count": 1,
        "mesh_count": 1,
        "modifier_count": 0,
        "material_count": 0,
        "animated_objects": [],
        "render_settings": {},
        "asset_bounds_m": {"dimensions": [1, 1, 1]},
    }

    def host(executable, job, run_dir, label, timeout):
        calls.append((label, dict(job)))
        outputs = []
        if label == "execute" and job["action"] != "inspect":
            output = run_dir / "scene.blend"
            output.write_bytes(b"synthetic new output")
            outputs = [{"kind": "blend", "path": str(output)}]
        return {
            "blender_version": "synthetic-test",
            "scene": scene,
            "outputs": outputs,
            "source_resources": {"supported": True},
        }

    monkeypatch.setattr(blender, "_launch", host)
    counter = 0

    def execute(action="run_script", parameters=None, directory=None):
        nonlocal counter
        counter += 1
        parameters = (
            parameters
            if parameters is not None
            else {
                "input_blend": source.name,
                "expected_source_sha256": sha(source.read_bytes()),
                "script_path": "edit.py",
            }
        )
        run_dir = directory or workspace / "runs" / str(counter)
        result = blender.execute(
            workspace,
            {"action": action, "parameters": parameters},
            run_dir,
            {"allow_scripts": True},
        )
        return result, run_dir

    return SimpleNamespace(
        workspace=workspace, source=source, host=host, execute=execute, calls=calls
    )


@pytest.mark.parametrize("value", [None, False, 9, [], "a" * 63, "A" * 64, "g" * 64, "a" * 65])
def test_invalid_supplied_digest_rejected_before_native_launch(setup, value):
    result, _ = setup.execute(
        "inspect", {"input_blend": setup.source.name, "expected_source_sha256": value}
    )
    assert result["status"] == "failed"
    assert "64 lowercase" in result["summary"]
    assert setup.calls == []


def test_edit_requires_pin_and_pin_requires_source(setup):
    result, _ = setup.execute(
        parameters={"input_blend": setup.source.name, "script_path": "edit.py"}
    )
    assert result["status"] == "failed"
    assert "requires expected_source_sha256" in result["summary"]
    result, _ = setup.execute(
        parameters={"script_path": "edit.py", "expected_source_sha256": "a" * 64}
    )
    assert result["status"] == "failed"
    assert "requires input_blend" in result["summary"]
    assert setup.calls == []


@pytest.mark.parametrize("action", sorted(blender.ACTIONS))
def test_wrong_digest_rejected_for_every_source_consuming_action(setup, action):
    original = setup.source.read_bytes()
    parameters = {
        "input_blend": setup.source.name,
        "expected_source_sha256": "0" * 64,
        "script_path": "edit.py",
        "asset_path": "asset.glb",
    }
    result, directory = setup.execute(action, parameters)
    assert result["status"] == "failed"
    assert setup.calls == []
    binding = result["observations"]["source_binding"]
    assert binding["expected_source_sha256"] == "0" * 64
    assert binding["snapshot_sha256"] == sha(original)
    assert binding["phase"] == "rejected_before_native"
    assert setup.source.read_bytes() == original
    assert (directory / "source_snapshot" / setup.source.name).read_bytes() == original
    assert (directory / "source-binding-failure.json").is_file()
    assert all(item["kind"] == "metadata" for item in result["artifacts"])
    assert str(setup.workspace) not in json.dumps(binding)


def test_pinned_dispatch_uses_preserved_readonly_basename_and_distinct_digests(setup):
    original = setup.source.read_bytes()
    result, directory = setup.execute()
    assert result["status"] == "verified"
    binding = result["observations"]["source_binding"]
    snapshot = directory / "source_snapshot" / setup.source.name
    assert Path(setup.calls[0][1]["input_blend"]) == snapshot
    assert setup.calls[0][1]["require_packed_source"] is True
    assert snapshot.read_bytes() == original
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o444
    assert binding["mode"] == "pinned_snapshot"
    for field in (
        "expected_source_sha256",
        "source_sha256",
        "snapshot_sha256",
        "source_sha256_after_action",
        "snapshot_sha256_after_action",
    ):
        assert binding[field] == sha(original)
    assert binding["source_unchanged"] is True and binding["snapshot_unchanged"] is True
    assert binding["bytes"] == len(original)
    artifacts = {item["path"]: item for item in result["artifacts"]}
    assert artifacts[binding["snapshot_path"]]["kind"] == "metadata"
    assert len(artifacts) == len(result["artifacts"])
    initial = json.loads((directory / "source-binding.json").read_text())
    dispatch = json.loads((directory / "source-binding-dispatch.json").read_text())
    assert initial["phase"] == "prepared" and initial["source_sha256_after_action"] is None
    assert dispatch["source_sha256_before_dispatch"] == sha(original)
    assert dispatch["source_sha256_after_action"] is None
    for path in directory.glob("source-binding*.json"):
        assert str(setup.workspace) not in path.read_text()


def test_pinned_inspection_has_one_inspected_native_artifact_not_duplicate_snapshot(setup):
    result, _ = setup.execute(
        "inspect",
        {
            "input_blend": setup.source.name,
            "expected_source_sha256": sha(setup.source.read_bytes()),
        },
    )
    assert result["status"] == "verified"
    native = [item for item in result["artifacts"] if item["kind"] == "blend"]
    assert len(native) == 1
    assert native[0]["path"] == result["observations"]["source_binding"]["snapshot_path"]
    assert len({item["path"] for item in result["artifacts"]}) == len(result["artifacts"])


@pytest.mark.parametrize("action", ["inspect", "render", "import_asset", "export_asset"])
def test_unpinned_compatibility_loads_original_path_and_reports_limit(setup, action):
    result, directory = setup.execute(
        action, {"input_blend": setup.source.name, "asset_path": "asset.glb"}
    )
    assert result["status"] == "verified"
    binding = result["observations"]["source_binding"]
    assert binding["mode"] == "unpinned" and binding["snapshot_path"] is None
    assert Path(setup.calls[0][1]["input_blend"]) == setup.source
    assert "require_packed_source" not in setup.calls[0][1]
    assert not (directory / "source_snapshot").exists()
    assert "does not bind" in binding["limitations"][0]


@pytest.mark.parametrize("which", ["source", "snapshot"])
def test_change_after_copy_before_dispatch_rejected(setup, monkeypatch, which):
    original_prepare = blender.prepare_source

    def prepare(*args, **kwargs):
        binding = original_prepare(*args, **kwargs)
        path = setup.workspace / binding[f"{which}_path"]
        path.chmod(0o600)
        path.write_bytes(b"changed before native dispatch")
        return binding

    monkeypatch.setattr(blender, "prepare_source", prepare)
    result, directory = setup.execute()
    assert result["status"] == "failed"
    assert "changed before native dispatch" in result["summary"]
    assert setup.calls == []
    assert (directory / "source-binding-failure.json").is_file()


def test_source_change_during_copy_rejected_and_copied_evidence_preserved(setup, monkeypatch):
    chunks = source_binding._chunks
    changed = False

    def corrupt(stream):
        nonlocal changed
        for chunk in chunks(stream):
            yield chunk
            if not changed:
                changed = True
                setup.source.write_bytes(b"source changed while copying")

    monkeypatch.setattr(source_binding, "_chunks", corrupt)
    result, directory = setup.execute()
    assert result["status"] == "failed"
    assert "changed while copying" in result["summary"]
    assert setup.calls == []
    assert (directory / "source_snapshot" / setup.source.name).is_file()


@pytest.mark.parametrize("which", ["source", "snapshot"])
def test_post_host_source_and_snapshot_checks_are_separate(setup, monkeypatch, which):
    def host(executable, job, directory, label, timeout):
        result = setup.host(executable, job, directory, label, timeout)
        if label == "execute":
            path = setup.source if which == "source" else Path(job["input_blend"])
            path.chmod(0o600)
            path.write_bytes(b"host-side mutation")
        return result

    monkeypatch.setattr(blender, "_launch", host)
    result, _ = setup.execute()
    assert result["status"] == "executed_unverified"
    binding = result["observations"]["source_binding"]
    other = "snapshot" if which == "source" else "source"
    assert binding[f"{which}_unchanged"] is False
    assert binding[f"{other}_unchanged"] is True
    assert binding[f"{which}_sha256_after_action"] == sha(b"host-side mutation")


def test_partial_snapshot_failure_preserves_files_and_never_launches_or_reuses(setup, monkeypatch):
    def interrupted(stream):
        yield stream.read(8)
        raise OSError("synthetic I/O failure includes private /not/published/path")

    monkeypatch.setattr(source_binding, "_chunks", interrupted)
    result, directory = setup.execute()
    partial = directory / "source_snapshot" / setup.source.name
    assert result["status"] == "failed" and setup.calls == []
    assert partial.read_bytes() == setup.source.read_bytes()[:8]
    assert "private" not in result["summary"]
    assert result["observations"]["source_binding"]["phase"] == "rejected_before_native"
    before = partial.read_bytes()
    retry, _ = setup.execute(directory=directory)
    assert retry["status"] == "failed" and setup.calls == []
    assert partial.read_bytes() == before


def test_snapshot_flush_failure_cannot_launch(setup, monkeypatch):
    def failed_sync(_):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(source_binding.os, "fsync", failed_sync)
    result, directory = setup.execute()
    assert result["status"] == "failed" and setup.calls == []
    assert (directory / "source_snapshot" / setup.source.name).is_file()


def test_source_size_bound_checked_before_launch(setup, monkeypatch):
    monkeypatch.setattr(source_binding, "MAX_SOURCE_BYTES", 8)
    result, _ = setup.execute()
    assert result["status"] == "failed" and setup.calls == []
    assert "512 MiB" in result["summary"]


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "../original scene.blend",
        ".studio/internal.blend",
        "a/../../original scene.blend",
        "a\\scene.blend",
    ],
)
def test_traversal_and_internal_inputs_rejected(setup, relative):
    result, _ = setup.execute("inspect", {"input_blend": relative})
    assert result["status"] == "failed" and setup.calls == []


def test_internal_symlink_and_symlink_parent_rejected(setup):
    (setup.workspace / "link.blend").symlink_to(setup.source)
    (setup.workspace / "link_dir").symlink_to(setup.workspace, target_is_directory=True)
    for relative in ("link.blend", "link_dir/" + setup.source.name):
        result, _ = setup.execute("inspect", {"input_blend": relative})
        assert result["status"] == "failed" and setup.calls == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX source descriptor guard")
def test_fifo_source_rejected_without_blocking(setup):
    os.mkfifo(setup.workspace / "fifo.blend")
    result, _ = setup.execute("inspect", {"input_blend": "fifo.blend"})
    assert result["status"] == "failed" and setup.calls == []


def test_snapshot_alone_does_not_verify_new_scene_action(setup, monkeypatch):
    def no_output(*args):
        result = setup.host(*args)
        result["outputs"] = []
        return result

    monkeypatch.setattr(blender, "_launch", no_output)
    result, _ = setup.execute()
    assert result["status"] == "executed_unverified"
    assert not next(
        check["passed"]
        for check in result["observations"]["checks"]
        if check["name"] == "new_native_scene_saved"
    )
    snapshot = result["observations"]["source_binding"]["snapshot_path"]
    assert (
        next(item["kind"] for item in result["artifacts"] if item["path"] == snapshot) == "metadata"
    )


def test_host_cannot_claim_source_snapshot_is_new_native_output(setup, monkeypatch):
    def aliases_source(executable, job, directory, label, timeout):
        result = setup.host(executable, job, directory, label, timeout)
        result["outputs"] = [{"kind": "blend", "path": job["input_blend"]}]
        return result

    monkeypatch.setattr(blender, "_launch", aliases_source)
    result, _ = setup.execute()
    assert result["status"] == "failed"
    assert "aliases source" in result["summary"]
    assert len(setup.calls) == 1


def test_native_exception_retains_post_attempt_source_evidence(setup, monkeypatch):
    def failed(*args):
        raise RuntimeError("synthetic host failed")

    monkeypatch.setattr(blender, "_launch", failed)
    result, directory = setup.execute()
    assert result["status"] == "failed"
    assert result["observations"]["source_binding"]["snapshot_unchanged"] is True
    assert (directory / "source-binding-check.json").is_file()


def test_resource_gate_failure_preserves_structured_evidence(setup, monkeypatch):
    report = {"supported": False, "unsupported_counts": {"unpacked_or_unsupported_images": 1}}

    def failed(*args):
        raise blender.SourceResourceError("Requested action was not executed.", report)

    monkeypatch.setattr(blender, "_launch", failed)
    result, _ = setup.execute()
    assert result["status"] == "failed"
    assert result["observations"]["source_resources"] == report


def test_synthetic_common_resource_gate_distinguishes_packed_and_external():
    packed = SimpleNamespace(source="FILE", packed_file=object())
    unpacked = SimpleNamespace(source="FILE", packed_file=None, filepath="/private/location.png")
    bpy = SimpleNamespace(data=SimpleNamespace(images=[packed]))
    assert inspect_resources(bpy)["supported"] is True
    bpy.data.images.append(unpacked)
    evidence = inspect_resources(bpy)
    assert evidence["supported"] is False
    assert evidence["packed_counts"] == {"images": 1}
    assert evidence["unsupported_counts"] == {"unpacked_or_unsupported_images": 1}
    assert "/private" not in json.dumps(evidence)


def test_synthetic_resource_gate_covers_linked_cache_font_and_media_resources():
    data = SimpleNamespace(
        fonts=[SimpleNamespace(filepath="<builtin>"), SimpleNamespace(filepath="//font.ttf")],
        libraries=[object()],
        volumes=[object()],
        sounds=[SimpleNamespace(packed_file=object())],
        objects=[SimpleNamespace(modifiers=[SimpleNamespace(type="MESH_CACHE")])],
        scenes=[
            SimpleNamespace(
                sequence_editor=SimpleNamespace(strips_all=[SimpleNamespace(type="MOVIE")])
            )
        ],
    )
    evidence = inspect_resources(SimpleNamespace(data=data))
    assert evidence["supported"] is False
    assert evidence["packed_counts"] == {"sounds": 1}
    assert evidence["unsupported_counts"] == {
        "external_sequencer_strips": 1,
        "libraries": 1,
        "mesh_cache_modifiers": 1,
        "unpacked_fonts": 1,
        "volumes": 1,
    }
