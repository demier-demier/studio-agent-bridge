"""Synthetic adapter boundary checks; no native Unreal process is launched here."""

import hashlib
import json
from pathlib import Path

import pytest

from studio_agent_bridge.adapters import unreal
from studio_agent_bridge.engine import Engine


@pytest.fixture
def setup(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    engine = tmp_path / "installation" / "Engine"
    executable = engine / "Binaries" / "Mac" / "UnrealEditor-Cmd"
    executable.parent.mkdir(parents=True)
    executable.write_text("synthetic executable fixture; never run")
    executable.chmod(0o700)
    version_file = engine / "Build" / "Build.version"
    version_file.parent.mkdir()
    version = {"MajorVersion": 5, "MinorVersion": 7, "PatchVersion": 4, "Changelist": 51494982}
    version_file.write_text(json.dumps(version))
    monkeypatch.setattr(unreal.sys, "platform", "darwin")

    def forbid_native(*args, **kwargs):
        pytest.fail("Synthetic preflight check attempted a native subprocess")

    monkeypatch.setattr(unreal.subprocess, "Popen", forbid_native)
    config = {"unreal_executable": str(executable)}
    return workspace, config, version_file, version


def fingerprint(workspace):
    return {
        path.relative_to(workspace).as_posix(): (
            path.is_dir(),
            path.stat().st_mode,
            None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in workspace.rglob("*")
    }


@pytest.mark.parametrize("explicit_mode", [False, True])
@pytest.mark.parametrize("existing_project", [False, True])
def test_known_crash_rejected_before_adapter_writes_or_native_launch(
    setup, monkeypatch, explicit_mode, existing_project
):
    workspace, config, _, version = setup
    if existing_project:
        project = unreal._project(workspace, {}, config, True)
        asset = project.parent / "Content" / "Keep.uasset"
        asset.write_bytes(b"existing native evidence must remain untouched")
    parameters = {"environment_manifest": "not-even-read.json", "editor_session": False}
    if explicit_mode:
        parameters["rendering"] = "structure"
    before = fingerprint(workspace)

    def forbidden(*args, **kwargs):
        pytest.fail("Known crash preflight reached project, manifest, lock or native work")

    for name in ("_project", "validate_manifest", "_lock", "_run"):
        monkeypatch.setattr(unreal, name, forbidden)
    result = unreal.execute(
        workspace,
        {"action": "build_scene", "parameters": parameters},
        workspace / "new-run",
        config,
    )
    assert result["status"] == "blocked"
    assert result["artifacts"] == []
    assert result["observations"]["native_launch_attempted"] is False
    assert "rendering=gpu" in result["summary"]
    guard = result["observations"]["preflight"]
    assert guard["code"] == "unreal_macos_5_7_4_environment_nullrhi"
    assert guard["decision"] == "blocked"
    assert guard["editor_session"] is True  # Caller cannot bypass a derived mode.
    assert guard["rendering_mode"] == "structure"
    assert guard["required_parameters"] == {"rendering": "gpu"}
    assert guard["engine"] == version and guard["platform"] == "darwin"
    assert guard["basis"] == "observed_native_failure"
    assert str(workspace) not in json.dumps(result)
    assert fingerprint(workspace) == before
    assert not (workspace / "new-run").exists()
    assert parameters.get("rendering", "structure") == "structure"


def bundle(workspace):
    mesh = workspace / "mesh.fbx"
    mesh.write_bytes(b"synthetic file, not actual geometry")
    manifest = {
        "schemaVersion": "environment-manifest/v1",
        "units": {"length": "meter"},
        "coordinateSystem": {"up": "Z", "handedness": "right"},
        "assets": [
            {
                "id": "mesh_cube",
                "exportFile": mesh.name,
                "fileSha256": hashlib.sha256(mesh.read_bytes()).hexdigest(),
                "boundsLocal": {"min": [0, 0, 0], "max": [1, 1, 1]},
            }
        ],
        "objects": [
            {
                "id": "object_cube",
                "assetId": "mesh_cube",
                "transform": {
                    "translationMeters": [0, 0, 0],
                    "rotationQuaternionWxyz": [1, 0, 0, 0],
                    "scale": [1, 1, 1],
                },
            }
        ],
    }
    path = workspace / "environment-manifest.json"
    path.write_text(json.dumps(manifest))
    return path


@pytest.mark.parametrize(
    "platform,release,action,environment,rendering",
    [
        ("darwin", (5, 7, 4), "build_scene", True, "gpu"),
        ("darwin", (5, 7, 4), "import_asset", True, "structure"),
        ("darwin", (5, 7, 4), "inspect", True, "structure"),
        ("darwin", (5, 7, 4), "build_scene", False, "structure"),
        ("darwin", (5, 7, 4), "create_project", False, "structure"),
        ("darwin", (5, 7, 3), "build_scene", True, "structure"),
        ("darwin", (5, 7, 5), "build_scene", True, "structure"),
        ("darwin", (5, 8, 0), "build_scene", True, "structure"),
        ("darwin", None, "build_scene", True, "structure"),
        ("linux", (5, 7, 4), "build_scene", True, "structure"),
        ("win32", (5, 7, 4), "build_scene", True, "structure"),
    ],
)
def test_other_combinations_keep_their_requested_launch_path_without_claiming_success(
    setup, monkeypatch, platform, release, action, environment, rendering
):
    workspace, config, version_file, version = setup
    if release is None:
        version_file.unlink()
    else:
        version.update(zip(("MajorVersion", "MinorVersion", "PatchVersion"), release))
        version_file.write_text(json.dumps(version))
    monkeypatch.setattr(unreal.sys, "platform", platform)
    unreal._project(workspace, {}, config, True)
    parameters = {"rendering": rendering}
    if environment:
        parameters["environment_manifest"] = bundle(workspace).name
    observed = []

    def synthetic_run(command, cwd, env, log, timeout):
        payload = json.loads(Path(env["STUDIO_AGENT_UNREAL_REQUEST"]).read_text())
        observed.append((command, payload))
        log.write_text("Synthetic process boundary only. No native evidence was produced.")
        return 0, False

    monkeypatch.setattr(unreal, "_run", synthetic_run)
    result = unreal.execute(
        workspace, {"action": action, "parameters": parameters}, workspace / "run", config
    )
    assert len(observed) == 1
    command, payload = observed[0]
    assert payload["editor_session"] is environment
    assert ("-nullrhi" in command) is (rendering == "structure")
    assert any(arg.startswith("-ExecutePythonScript=") for arg in command) is environment
    assert ("-run=pythonscript" in command) is (not environment)
    assert result["status"] == "failed"  # Passing preflight is not native verification.
    assert "preflight" not in result["observations"]


def test_unavailable_executable_keeps_existing_unavailable_diagnostic(setup):
    workspace, _, _, _ = setup
    unreal._project(workspace, {}, {}, True)
    result = unreal.execute(
        workspace,
        {"action": "build_scene", "parameters": {"environment_manifest": "unread.json"}},
        workspace / "run",
        {"unreal_executable": str(workspace / "missing-executable")},
    )
    assert result["status"] == "blocked"
    assert "unavailable" in result["summary"]
    assert "preflight" not in result["observations"]


def test_explicit_gpu_still_validates_manifest_before_launch(setup, monkeypatch):
    workspace, config, _, _ = setup
    unreal._project(workspace, {}, config, True)
    manifest = bundle(workspace)
    (workspace / "mesh.fbx").write_bytes(b"changed bytes")

    def forbidden(*args, **kwargs):
        pytest.fail("Tampered manifest reached process boundary")

    monkeypatch.setattr(unreal, "_run", forbidden)
    result = unreal.execute(
        workspace,
        {
            "action": "build_scene",
            "parameters": {"environment_manifest": manifest.name, "rendering": "gpu"},
        },
        workspace / "run",
        config,
    )
    assert result["status"] == "failed"
    assert "hash mismatch" in result["summary"]


def test_engine_records_blocked_attempt_but_adapter_creates_no_project(setup, tmp_path):
    workspace, config, _, _ = setup
    engine = Engine(workspace, config, adapters={"unreal": unreal}, lock_dir=tmp_path / "locks")
    job = engine.create_job(
        "blocked-native", "unreal", "Exercise the observed mode guard without launching Unreal."
    )
    result = engine.execute_action(
        job["id"], "build_scene", {"environment_manifest": "unread.json"}, job["revision"]
    )
    assert result["status"] == "blocked"
    assert result["revision"] == job["revision"] + 2
    assert result["result"]["observations"]["native_launch_attempted"] is False
    assert result["result"]["observations"]["preflight"]["decision"] == "blocked"
    assert not (workspace / "unreal").exists()
    assert list((workspace / result["run_dir"]).iterdir()) == []
    assert engine.inspect_job(job["id"])["status"] == "blocked"
    assert engine.list_artifacts(job["id"])["artifacts"] == []
