"""Adapter boundary tests. These do not claim native engine validation."""

import hashlib
import json
from pathlib import Path

import pytest

from studio_agent_bridge.adapters import unreal


def project(tmp_path):
    return unreal._project(tmp_path, {}, {}, True)


def test_probe_missing_explicit_executable(tmp_path):
    result = unreal.probe({"unreal_executable": str(tmp_path / "absent")})
    assert result["available"] is False
    assert "run_script" in result["actions"]


def test_no_engine_creates_only_project_descriptor(tmp_path):
    result = unreal.execute(
        tmp_path,
        {"action": "create_project", "parameters": {}},
        tmp_path / "run",
        {"unreal_executable": str(tmp_path / "missing")},
    )
    assert result["status"] == "blocked"
    assert [artifact["kind"] for artifact in result["artifacts"]] == ["unreal-project"]
    descriptor = json.loads((tmp_path / result["artifacts"][0]["path"]).read_text())
    assert {plugin["Name"] for plugin in descriptor["Plugins"]} == set(unreal.PLUGIN_NAMES)


def test_existing_unmarked_project_is_refused(tmp_path):
    folder = tmp_path / "unreal/StudioAgentLab"
    folder.mkdir(parents=True)
    file = folder / "StudioAgentLab.uproject"
    file.write_text("{}")
    result = unreal.execute(tmp_path, {"action": "create_project"}, tmp_path / "run", {})
    assert result["status"] == "failed"
    assert file.read_text() == "{}"


def test_malformed_project_marker_is_refused(tmp_path):
    file = project(tmp_path)
    (file.parent / unreal.MARKER).write_text("[]")
    result = unreal.execute(tmp_path, {"action": "inspect"}, tmp_path / "run", {})
    assert result["status"] == "failed"
    assert "created by this adapter" in result["summary"]


@pytest.mark.parametrize("value", ["../outside", "/tmp/outside-studio-agent", "x/../../outside"])
def test_paths_cannot_escape_workspace(tmp_path, value):
    with pytest.raises(ValueError):
        unreal._contained(tmp_path, value)


def test_symlink_escape_is_refused(tmp_path):
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        unreal._contained(tmp_path, "escape/elsewhere")


@pytest.mark.parametrize(
    "value",
    [
        "/Engine/BasicShapes/Cube",
        "/Game/StudioAgentBridge/../Other",
        "/Game/StudioAgentBridge/asset;code",
        "/Game/Other/Asset",
    ],
)
def test_destination_is_scoped(value):
    with pytest.raises(ValueError):
        unreal._game_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "/Game/StudioAgentBridge/Sequences/CameraMove",
        "/Game/StudioAgentBridge/Sequences/CameraMove.CameraMove",
    ],
)
def test_existing_object_references_normalize_to_package(value):
    assert unreal._game_reference(value) == "/Game/StudioAgentBridge/Sequences/CameraMove"


@pytest.mark.parametrize(
    "value",
    [
        "/Game/StudioAgentBridge/Maps/Stage.Wrong",
        "/Game/StudioAgentBridge/Maps/Stage.Stage.extra",
        "/Game/StudioAgentBridge/Maps/Stage.Stage:PersistentLevel",
        "/Game/StudioAgentBridge/../Other.Other",
        "/Game/Other/Stage.Stage",
    ],
)
def test_invalid_existing_object_references_are_refused(value):
    with pytest.raises(ValueError):
        unreal._game_reference(value)


def test_creation_destination_does_not_accept_object_reference():
    with pytest.raises(ValueError):
        unreal._game_path("/Game/StudioAgentBridge/Imports/Asset.Asset")


@pytest.mark.parametrize(
    "action,parameters,expected",
    [
        (
            "inspect",
            {
                "level_path": "/Game/StudioAgentBridge/Maps/Stage.Stage",
                "sequence_path": "/Game/StudioAgentBridge/Sequences/Move.Move",
            },
            {
                "level_path": "/Game/StudioAgentBridge/Maps/Stage",
                "sequence_path": "/Game/StudioAgentBridge/Sequences/Move",
            },
        ),
        (
            "build_scene",
            {"asset_paths": ["/Game/StudioAgentBridge/Imports/Mesh.Mesh"]},
            {"asset_paths": ["/Game/StudioAgentBridge/Imports/Mesh"]},
        ),
    ],
)
def test_adapter_normalizes_returned_object_paths_before_native_launch(
    tmp_path, monkeypatch, action, parameters, expected
):
    project(tmp_path)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))
    observed = {}

    def capture_request(command, cwd, env, log, timeout):
        payload = json.loads(Path(env["STUDIO_AGENT_UNREAL_REQUEST"]).read_text())
        observed.update(payload["parameters"])
        log.write_text("unit test: captured native request without launching an engine")
        return 0, False

    monkeypatch.setattr(unreal, "_run", capture_request)
    unreal.execute(tmp_path, {"action": action, "parameters": parameters}, tmp_path / "run", {})
    for key, value in expected.items():
        assert observed[key] == value


def test_scripts_disabled_by_default(tmp_path):
    result = unreal.execute(
        tmp_path,
        {"action": "run_script", "parameters": {"script_path": "test.py"}},
        tmp_path / "run",
        {},
    )
    assert result["status"] == "failed"
    assert "disabled" in result["summary"]


def test_zero_exit_without_native_evidence_is_not_success(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))

    def zero_exit(command, cwd, env, log, timeout):
        log.write_text("Process ended normally but wrote no native proof")
        return 0, False

    monkeypatch.setattr(unreal, "_run", zero_exit)
    result = unreal.execute(tmp_path, {"action": "inspect"}, tmp_path / "run", {})
    assert result["status"] == "failed"


def test_native_failure_is_not_success(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))

    def failure(command, cwd, env, log, timeout):
        request = json.loads(Path(env["STUDIO_AGENT_UNREAL_REQUEST"]).read_text())
        Path(request["response"]).write_text(
            json.dumps({"success": False, "summary": "mesh empty"})
        )
        log.write_text("native script failed")
        return 0, False

    monkeypatch.setattr(unreal, "_run", failure)
    result = unreal.execute(tmp_path, {"action": "inspect"}, tmp_path / "run", {})
    assert result["status"] == "failed"


def test_late_helper_log_writes_do_not_invalidate_manifest(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))

    def finished_editor(command, cwd, env, log, timeout):
        request = json.loads(Path(env["STUDIO_AGENT_UNREAL_REQUEST"]).read_text())
        Path(request["response"]).write_text(
            json.dumps({"success": False, "summary": "synthetic response"})
        )
        log.write_bytes(b"editor output\n")
        return 0, False

    monkeypatch.setattr(unreal, "_run", finished_editor)
    run = tmp_path / "run"
    result = unreal.execute(tmp_path, {"action": "inspect"}, run, {})
    # Model the observed native helper writing to its inherited stdout after
    # execute() already returned the hash manifest to its caller.
    with (run / "unreal-process.log").open("ab") as writer:
        writer.write(b"late trace-server shutdown output\n")
    logs = [entry for entry in result["artifacts"] if entry["kind"] == "log"]
    assert len(logs) == 1
    artifact = logs[0]
    assert artifact["path"] == "run/unreal-process.snapshot.log"
    data = (tmp_path / artifact["path"]).read_bytes()
    assert data == b"editor output\n"
    assert artifact["bytes"] == len(data)
    assert artifact["sha256"] == hashlib.sha256(data).hexdigest()
    assert not any(entry["path"] == "run/unreal-process.log" for entry in result["artifacts"])


def test_native_artifact_escape_is_refused(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))

    def escape(command, cwd, env, log, timeout):
        request = json.loads(Path(env["STUDIO_AGENT_UNREAL_REQUEST"]).read_text())
        Path(request["response"]).write_text(
            json.dumps(
                {
                    "success": True,
                    "verification": {"passed": True},
                    "files": [{"path": "/etc/hosts", "kind": "asset"}],
                }
            )
        )
        log.write_text("synthetic unit-test response")
        return 0, False

    monkeypatch.setattr(unreal, "_run", escape)
    result = unreal.execute(tmp_path, {"action": "inspect"}, tmp_path / "run", {})
    assert result["status"] == "failed"
    assert "workspace" in result["summary"]


def test_timeout_is_blocked(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))

    def timeout(command, cwd, env, log, timeout):
        log.write_text("Initialization did not finish")
        return None, True

    monkeypatch.setattr(unreal, "_run", timeout)
    result = unreal.execute(tmp_path, {"action": "inspect"}, tmp_path / "run", {})
    assert result["status"] == "blocked"
    assert result["observations"]["timed_out"] is True


def test_existing_response_cannot_be_reused(tmp_path, monkeypatch):
    project(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    (run / "unreal-result.json").write_text('{"success":true}')
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))
    result = unreal.execute(tmp_path, {"action": "inspect"}, run, {})
    assert result["status"] == "failed"
    assert "old response" in result["summary"]


def test_project_file_lock_excludes_concurrent_edit(tmp_path):
    file = project(tmp_path)
    with unreal._lock(file):
        with pytest.raises(RuntimeError, match="Another adapter"):
            with unreal._lock(file):
                pass
