"""Environment boundary tests; real FBX and GPU evidence is recorded separately."""

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from studio_agent_bridge.adapters import unreal
from studio_agent_bridge.host_scripts.unreal.environment_contract import (
    bounds_cm,
    compare_bounds,
    guard_map_creation,
    local_file,
    transform,
    validate_manifest,
)


def bundle(tmp_path):
    mesh = tmp_path / "mesh.fbx"
    mesh.write_bytes(b"synthetic boundary fixture, not a valid native FBX")
    manifest = {
        "schemaVersion": "environment-manifest/v1",
        "units": {"length": "meter"},
        "coordinateSystem": {"up": "Z", "handedness": "right"},
        "assets": [
            {
                "id": "mesh_cube",
                "exportFile": "mesh.fbx",
                "fileSha256": hashlib.sha256(mesh.read_bytes()).hexdigest(),
                "boundsLocal": {"min": [-1, -2, 0], "max": [3, 4, 1]},
            }
        ],
        "objects": [
            {
                "id": "object_cube",
                "assetId": "mesh_cube",
                "transform": {
                    "translationMeters": [1, 2, 3],
                    "rotationQuaternionWxyz": [1, 0, 0, 0],
                    "scale": [1, 2, 3],
                },
            }
        ],
    }
    path = tmp_path / "environment-manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


def test_contract_validates_hashes_and_shared_instances(tmp_path):
    path, data = bundle(tmp_path)
    second = copy.deepcopy(data["objects"][0])
    second["id"] = "second"
    data["objects"].append(second)
    path.write_text(json.dumps(data))
    assert len(validate_manifest(path)["objects"]) == 2
    (tmp_path / "mesh.fbx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_manifest(path)


def test_coordinate_mapping_preserves_asymmetric_bounds_and_reflects_rotation():
    mapped = transform(
        {
            "translationMeters": [1.5, -2.5, 3],
            "rotationQuaternionWxyz": [math.sqrt(0.5), 0, 0, math.sqrt(0.5)],
            "scale": [1, 2, 3],
        }
    )
    assert mapped["translationCm"] == [150, 250, 300]
    assert mapped["rotationQuaternionXyzw"] == [0, 0, -math.sqrt(0.5), math.sqrt(0.5)]
    bounds = {"min": [-1, -2, 0], "max": [3, 4, 1]}
    assert bounds_cm(bounds) == ([-100, -400, 0], [300, 200, 100])
    assert compare_bounds(bounds, [-100, -400, 0], [300, 200, 100])["passed"]
    with pytest.raises(ValueError, match="bounds mismatch"):
        compare_bounds(bounds, [-100, -200, 0], [300, 400, 100])


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda d: d["objects"][0].update(assetId="missing"), "referenced asset"),
        (lambda d: d["objects"][0].update(shearResidual=0.05), "Sheared"),
        (lambda d: d["objects"][0]["transform"].update(scale=[1, 0, 1]), "nonzero"),
        (
            lambda d: d["objects"][0]["transform"].update(translationMeters=[1, float("nan"), 1]),
            "finite",
        ),
        (lambda d: d["assets"][0].update(id="../escape"), "identifiers"),
        (lambda d: d["assets"].append(copy.deepcopy(d["assets"][0])), "Duplicate"),
    ],
)
def test_invalid_manifest_fails_closed(tmp_path, mutation, match):
    path, data = bundle(tmp_path)
    mutation(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=match):
        validate_manifest(path)


def test_bundle_paths_cannot_escape_even_through_symlinks(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside.fbx"
    outside.write_bytes(b"outside")
    (root / "escape.fbx").symlink_to(outside)
    for relative in ["../outside.fbx", "escape.fbx", str(outside)]:
        with pytest.raises(ValueError):
            local_file(root, relative)


@pytest.mark.parametrize(
    "rendering,expected,absent",
    [
        ("gpu", "-AllowCommandletRendering", "-nullrhi"),
        ("structure", "-nullrhi", "-AllowCommandletRendering"),
    ],
)
def test_adapter_selects_explicit_rendering_mode(
    tmp_path, monkeypatch, rendering, expected, absent
):
    unreal._project(tmp_path, {}, {}, True)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))
    observed = {}

    def capture(command, cwd, env, log, timeout):
        observed["command"] = command
        log.write_text("boundary test only")
        return 0, False

    monkeypatch.setattr(unreal, "_run", capture)
    unreal.execute(
        tmp_path,
        {"action": "inspect", "parameters": {"rendering": rendering}},
        tmp_path / "run",
        {},
    )
    assert expected in observed["command"]
    assert absent not in observed["command"]


def test_environment_adapter_validates_bundle_before_native_launch(tmp_path, monkeypatch):
    path, _ = bundle(tmp_path)
    unreal._project(tmp_path, {}, {}, True)
    (tmp_path / "mesh.fbx").write_bytes(b"changed after export")
    launched = []
    monkeypatch.setattr(unreal, "_run", lambda *args: launched.append(True))
    result = unreal.execute(
        tmp_path,
        {
            "action": "build_scene",
            "parameters": {"environment_manifest": str(path), "name": "Environment"},
        },
        tmp_path / "run",
        {},
    )
    assert result["status"] == "failed"
    assert "hash mismatch" in result["summary"]
    assert not launched


def test_render_output_never_overwrites_existing_file(tmp_path):
    path, _ = bundle(tmp_path)
    unreal._project(tmp_path, {}, {}, True)
    image = tmp_path / "keep.png"
    image.write_bytes(b"keep")
    result = unreal.execute(
        tmp_path,
        {
            "action": "inspect",
            "parameters": {
                "environment_manifest": str(path),
                "rendering": "gpu",
                "render_output": str(image),
            },
        },
        tmp_path / "run",
        {},
    )
    assert result["status"] == "failed"
    assert image.read_bytes() == b"keep"


def test_environment_uses_full_editor_for_native_material_tools(tmp_path, monkeypatch):
    path, _ = bundle(tmp_path)
    unreal._project(tmp_path, {}, {}, True)
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))
    observed = {}

    def capture(command, cwd, env, log, timeout):
        observed["command"] = command
        observed["request"] = json.loads(Path(env["STUDIO_AGENT_UNREAL_REQUEST"]).read_text())
        log.write_text("boundary test only; native full-editor validation is separate")
        return 0, False

    monkeypatch.setattr(unreal, "_run", capture)
    unreal.execute(
        tmp_path,
        {
            "action": "build_scene",
            "parameters": {"environment_manifest": str(path), "rendering": "gpu"},
        },
        tmp_path / "run",
        {},
    )
    assert observed["request"]["editor_session"]
    assert any(x.startswith("-ExecutePythonScript=") for x in observed["command"])
    assert "-run=pythonscript" not in observed["command"]
    assert "-nullrhi" not in observed["command"]


@pytest.mark.parametrize(
    "level,existing",
    [
        ("/Game/StudioAgentBridge/Maps/Preserve", False),
        ("/Game/StudioAgentBridge/Environments/Fresh/Maps/Preserve", True),
    ],
)
def test_map_creation_refuses_external_or_existing_map(level, existing):
    with pytest.raises(ValueError):
        guard_map_creation("/Game/StudioAgentBridge/Environments/Fresh", level, existing)


def test_build_scene_cannot_overwrite_existing_map_in_another_namespace(tmp_path, monkeypatch):
    path, _ = bundle(tmp_path)
    project = unreal._project(tmp_path, {}, {}, True)
    saved = project.parent / "Content/StudioAgentBridge/Maps/Preserve.umap"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"valuable map")
    launched = []
    monkeypatch.setattr(unreal, "_executable", lambda config: Path("/fake/UnrealEditor-Cmd"))
    monkeypatch.setattr(unreal, "_run", lambda *args: launched.append(True))
    result = unreal.execute(
        tmp_path,
        {
            "action": "build_scene",
            "parameters": {
                "environment_manifest": str(path),
                "destination": "/Game/StudioAgentBridge/Environments/Fresh",
                "level_path": "/Game/StudioAgentBridge/Maps/Preserve",
            },
        },
        tmp_path / "run",
        {},
    )
    assert result["status"] == "failed"
    assert "fresh destination" in result["summary"]
    assert saved.read_bytes() == b"valuable map"
    assert not launched
