"""Launch native Unreal Python automation in an owned, isolated project.

The adapter is an automation boundary, not a Python security sandbox. Trusted
``run_script`` input has the editor process's filesystem and network authority.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from ..host_scripts.unreal.environment_contract import guard_map_creation, validate_manifest

ACTIONS = {"create_project", "import_asset", "build_scene", "inspect", "run_script"}
PLUGIN_NAMES = ["PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting"]
MARKER = ".studio-agent-project.json"


def _contained(workspace: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else workspace / path).resolve()
    if not path.is_relative_to(workspace.resolve()):
        raise ValueError("Path must remain inside the configured workspace")
    return path


def _executable(config: dict) -> Path | None:
    explicit = config.get("unreal_executable")
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    for base in (Path("/Users/Shared/Epic Games"), Path("/Users/Shared/UnrealEngine")):
        for candidate in sorted(
            base.glob("UE_*/Engine/Binaries/Mac/UnrealEditor-Cmd"), reverse=True
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
    return None


def _version(executable: Path | None) -> dict:
    if executable is None:
        return {}
    for parent in executable.parents:
        version_file = parent / "Build" / "Build.version"
        if version_file.is_file():
            try:
                info = json.loads(version_file.read_text())
                return {
                    key: info[key]
                    for key in ("MajorVersion", "MinorVersion", "PatchVersion", "Changelist")
                }
            except (ValueError, KeyError, OSError):
                return {}
    return {}


def probe(config: dict) -> dict:
    executable = _executable(config)
    return {
        "available": executable is not None,
        "status": "available" if executable else "blocked",
        "executable": str(executable) if executable else None,
        "version": _version(executable),
        "actions": sorted(ACTIONS),
        "summary": "Native executable detected; execution still requires a successful editor run."
        if executable
        else "UnrealEditor-Cmd was not found.",
        "limitations": [
            "Filesystem discovery does not prove the editor starts.",
            "Python automation is editor-only; gameplay and arbitrary Blueprint graph authoring are not guaranteed.",
        ],
    }


def _artifact(workspace: Path, path: Path, kind: str) -> dict:
    path = _contained(workspace, path)
    if not path.is_file():
        raise ValueError("Reported artifact does not exist")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": path.relative_to(workspace).as_posix(),
        "kind": kind,
        "sha256": digest.hexdigest(),
        "bytes": path.stat().st_size,
    }


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
        raise ValueError(
            "Names must contain 1-64 ASCII letters, digits or underscores, beginning with a letter"
        )
    return value


def _game_path(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"/Game/StudioAgentBridge(?:/[A-Za-z][A-Za-z0-9_]*)+", value
    ):
        raise ValueError(
            "Asset destination must remain under /Game/StudioAgentBridge using simple names"
        )
    return value


def _game_reference(value: str) -> str:
    """Normalize an existing package or its exact top-level object reference."""
    if not isinstance(value, str):
        raise ValueError("Asset reference must be a string")
    parts = value.split(".")
    if len(parts) > 2 or (len(parts) == 2 and parts[1] != parts[0].rsplit("/", 1)[-1]):
        raise ValueError("Object-reference suffix must exactly match the final package name")
    return _game_path(parts[0])


def _project(workspace: Path, parameters: dict, config: dict, create: bool) -> Path:
    name = _name(parameters.get("name", "StudioAgentLab")) if create else "StudioAgentLab"
    project = _contained(workspace, config.get("unreal_project", f"unreal/{name}/{name}.uproject"))
    if project.suffix != ".uproject":
        raise ValueError("unreal_project must identify a .uproject")
    marker = project.parent / MARKER
    if project.exists():
        ownership = json.loads(marker.read_text()) if marker.is_file() else None
        if (
            not isinstance(ownership, dict)
            or ownership.get("project") != project.name
            or ownership.get("owner") != "studio-agent-bridge"
        ):
            raise ValueError("Only projects created by this adapter may be modified")
        return project
    if not create:
        raise ValueError("Create the project before running this action")
    if project.parent.exists() and any(project.parent.iterdir()):
        raise ValueError("New project directory must be empty")
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "EngineAssociation": "5.7",
                "Category": "Studio Agent Bridge",
                "Description": "Original synthetic editor automation workspace",
                "Plugins": [{"Name": name, "Enabled": True} for name in PLUGIN_NAMES],
            },
            indent=2,
        )
        + "\n"
    )
    marker.write_text(
        json.dumps({"schema": 1, "project": project.name, "owner": "studio-agent-bridge"}, indent=2)
        + "\n"
    )
    (project.parent / "Content").mkdir()
    (project.parent / "Config").mkdir()
    (project.parent / "Config" / "DefaultEngine.ini").write_text(
        "[ConsoleVariables]\nInterchange.FeatureFlags.Import.FBX=False\n\n"
        "[/Script/EngineSettings.GameMapsSettings]\nEditorStartupMap=/Engine/Maps/Templates/Template_Default\n\n"
        "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]\nbRemoteExecution=False\n"
    )
    return project


@contextlib.contextmanager
def _lock(project: Path):
    import fcntl

    with (project.parent / ".studio-agent.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another adapter operation is using this project") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _run(
    command: list[str], cwd: Path, env: dict, log: Path, timeout: float
) -> tuple[int | None, bool]:
    with log.open("wb") as stream:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return None, True


def execute(workspace: Path, request: dict, run_dir: Path, config: dict) -> dict:
    """Execute an operation, verifying native output separately from exit status."""
    workspace = Path(workspace).resolve()
    artifacts: list[dict] = []
    limitations = [
        "Native Python runs in the editor, not in a packaged game.",
        "NullRHI commandlet validation does not validate rendered appearance or GPU features.",
    ]
    try:
        action = request["action"]
        if action not in ACTIONS:
            raise ValueError("Unsupported Unreal action")
        if action == "run_script" and not config.get("allow_scripts", False):
            raise ValueError(
                "Trusted script execution is disabled; explicitly enable allow_scripts"
            )
        parameters = dict(request.get("parameters", {}))
        rendering = parameters.get("rendering", "structure")
        if rendering not in {"structure", "gpu"}:
            raise ValueError("rendering must be structure or gpu")
        if rendering == "gpu":
            limitations[1] = (
                "GPU rendering was requested; a saved image must be inspected before claiming appearance."
            )
        run_dir = _contained(workspace, run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        project = _project(workspace, parameters, config, action == "create_project")
        artifacts.append(_artifact(workspace, project, "unreal-project"))
        executable = _executable(config)
        if executable is None:
            return {
                "status": "blocked",
                "summary": "Project descriptor exists, but UnrealEditor-Cmd is unavailable; native assets were not created.",
                "artifacts": artifacts,
                "observations": {"probe": probe(config)},
                "limitations": limitations,
            }
        if parameters.get("environment_manifest"):
            source_manifest = _contained(workspace, parameters["environment_manifest"])
            validate_manifest(source_manifest)
            parameters["environment_manifest"] = str(source_manifest)
            parameters["destination"] = _game_path(
                parameters.get("destination", "/Game/StudioAgentBridge/Environments/Environment")
            )
            if action not in {"import_asset", "build_scene", "inspect"}:
                raise ValueError(
                    "Environment manifests apply to import_asset, build_scene or inspect"
                )
        if parameters.get("render_output"):
            output = _contained(workspace, parameters["render_output"])
            if output.suffix.lower() != ".png" or output.exists():
                raise ValueError("render_output must be a new workspace PNG path")
            if rendering != "gpu" or not parameters.get("environment_manifest"):
                raise ValueError("Environment image capture requires rendering=gpu and a manifest")
            size = parameters.get("render_size", [1280, 720])
            if (
                not isinstance(size, list)
                or len(size) != 2
                or any(
                    not isinstance(n, int) or isinstance(n, bool) or not 64 <= n <= 2048
                    for n in size
                )
            ):
                raise ValueError("render_size must contain two integers from 64 to 2048")
            parameters["render_output"] = str(output)
            exposure = parameters.get("render_exposure", -7)
            if (
                not isinstance(exposure, (int, float))
                or isinstance(exposure, bool)
                or not math.isfinite(exposure)
                or not -16 <= exposure <= 16
            ):
                raise ValueError("render_exposure must be finite EV compensation from -16 to 16")
        if action == "import_asset" and not parameters.get("environment_manifest"):
            source = _contained(workspace, parameters["source"])
            if source.suffix.lower() != ".fbx" or not source.is_file():
                raise ValueError("source must identify an existing workspace FBX file")
            parameters["source"] = str(source)
            parameters["destination"] = _game_path(
                parameters.get("destination", "/Game/StudioAgentBridge/Imports")
            )
        if action == "build_scene":
            parameters["name"] = _name(parameters.get("name", "InspectionStage"))
            paths = parameters.get("asset_paths", [])
            if not isinstance(paths, list) or len(paths) > 100:
                raise ValueError("asset_paths must be a list with at most 100 items")
            parameters["asset_paths"] = [_game_reference(path) for path in paths]
        if parameters.get("level_path"):
            parameters["level_path"] = _game_reference(parameters["level_path"])
        if parameters.get("sequence_path"):
            parameters["sequence_path"] = _game_reference(parameters["sequence_path"])
        if action == "build_scene" and parameters.get("environment_manifest"):
            level = parameters.get(
                "level_path", parameters["destination"] + "/Maps/" + parameters["name"]
            )
            native_map = project.parent / "Content" / (level.removeprefix("/Game/") + ".umap")
            guard_map_creation(parameters["destination"], level, native_map.exists())
        if action == "run_script":
            script = _contained(workspace, parameters["script_path"])
            if script.suffix != ".py" or not script.is_file():
                raise ValueError("script_path must identify an existing workspace Python file")
            parameters["script_path"] = str(script)
            parameters["expected_artifacts"] = [
                str(_contained(workspace, p)) for p in parameters.get("expected_artifacts", [])
            ]
            limitations.append(
                "Trusted client Python is not sandboxed; it can use the editor process's full local authority."
            )
        timeout = float(config.get("unreal_timeout_seconds", config.get("unreal_timeout", 240)))
        if not 1 <= timeout <= 1800:
            raise ValueError("unreal_timeout_seconds must be between 1 and 1800 seconds")
        response = run_dir / "unreal-result.json"
        if response.exists():
            raise ValueError("run_dir contains an old response; use a fresh run directory")
        payload = {
            "schema": 1,
            "action": action,
            "parameters": parameters,
            "response": str(response),
            "project": str(project),
            "workspace": str(workspace),
            "editor_session": bool(parameters.get("environment_manifest")),
        }
        request_file = run_dir / "unreal-request.json"
        request_file.write_text(json.dumps(payload, indent=2) + "\n")
        host_script = (
            Path(__file__).resolve().parents[1] / "host_scripts" / "unreal" / "dispatch.py"
        )
        editor_binary = executable.parent / "UnrealEditor.app/Contents/MacOS/UnrealEditor"
        launch_executable = (
            editor_binary if payload["editor_session"] and editor_binary.is_file() else executable
        )
        command = [
            str(launch_executable),
            str(project),
            *(
                [f'-ExecutePythonScript="{host_script}"', "-windowed", "-ResX=1280", "-ResY=720"]
                if payload["editor_session"]
                else ["-run=pythonscript", f'-script="{host_script}"']
            ),
            "-unattended",
            *(
                ["-nullrhi"]
                if rendering == "structure"
                else (
                    []
                    if payload["editor_session"]
                    else ["-AllowCommandletRendering", "-RenderOffscreen"]
                )
            ),
            "-nosplash",
            "-nop4",
            "-nosound",
            "-UTF8Output",
            "-stdout",
            "-FullStdOutLogOutput",
        ]
        env = dict(os.environ, STUDIO_AGENT_UNREAL_REQUEST=str(request_file))
        log = run_dir / "unreal-process.log"
        started = time.monotonic()
        with _lock(project):
            returncode, timed_out = _run(command, project.parent, env, log, timeout)
        # Native helper processes can inherit stdout and append after the editor
        # exits. Manifest a separate frozen snapshot, never that live log file.
        # Exclusive creation also refuses stale snapshots in a reused run_dir.
        snapshot = run_dir / "unreal-process.snapshot.log"
        with snapshot.open("xb") as output:
            output.write(log.read_bytes())
        artifacts.append(_artifact(workspace, snapshot, "log"))
        observations = {
            "returncode": returncode,
            "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "engine": _version(executable),
            "rendering_mode": rendering,
            "log_snapshot": "Captured when the editor process exited; later helper-process output remains in the unmanifested unreal-process.log.",
        }
        if not response.is_file():
            log_tail = snapshot.read_text(errors="replace")[-8000:]
            known_blockers = (
                "license",
                "xcode",
                "not found",
                "missing",
                "failed to initialize",
                "shader compiler",
            )
            status = (
                "blocked"
                if timed_out or any(word in log_tail.lower() for word in known_blockers)
                else "failed"
            )
            observations["log_tail"] = log_tail[-2500:]
            return {
                "status": status,
                "summary": "Unreal did not produce native verification evidence; inspect the process log.",
                "artifacts": artifacts,
                "observations": observations,
                "limitations": limitations,
            }
        native = json.loads(response.read_text())
        artifacts.append(_artifact(workspace, response, "verification"))
        observations["native"] = native
        for record in native.get("files", []):
            artifacts.append(
                _artifact(workspace, Path(record["path"]), record.get("kind", "unreal-asset"))
            )
        if native.get("success") is not True or returncode != 0 or timed_out:
            status = "failed"
        elif action == "run_script":
            status = "executed_unverified"
        elif native.get("verification", {}).get("passed") is True:
            status = "verified"
        else:
            status = "executed_unverified"
        return {
            "status": status,
            "summary": native.get("summary", "Native editor command completed."),
            "artifacts": artifacts,
            "observations": observations,
            "limitations": limitations + native.get("limitations", []),
        }
    except (KeyError, ValueError, TypeError, OSError, RuntimeError) as error:
        return {
            "status": "failed",
            "summary": str(error),
            "artifacts": artifacts,
            "observations": {},
            "limitations": limitations,
        }
