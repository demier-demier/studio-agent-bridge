"""Local native Fusion MCP bridge. Never retries model-mutating calls."""

from __future__ import annotations

import hashlib
import json
import pathlib
import socket
import urllib.error
import urllib.parse
import urllib.request

ACTIONS = (
    "create_demo",
    "inspect",
    "edit_parameters",
    "export_asset",
    "run_script",
    "api_documentation",
)
DEFAULT_URL = "http://127.0.0.1:27182/mcp"


def _endpoint(config):
    url = config.get("fusion_url", DEFAULT_URL)
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Fusion endpoint must be loopback HTTP without credentials")
    return url


class FusionClient:
    def __init__(self, config):
        self.url = _endpoint(config)
        self.session = None
        self.seq = 0
        self.timeout = min(max(float(config.get("fusion_timeout_seconds", 180)), 1), 600)
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "studio-agent-bridge", "version": "0.1.0"},
            },
        )
        self.rpc("notifications/initialized", {})

    def rpc(self, method, params):
        self.seq += 1
        data = {"jsonrpc": "2.0", "method": method, "params": params}
        if not method.startswith("notifications/"):
            data["id"] = self.seq
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["MCP-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            self.session = response.headers.get("MCP-Session-Id", self.session)
            raw = response.read().decode()
        if not raw:
            return {}
        if raw.lstrip().startswith("data:"):
            events = [
                json.loads(line[5:].strip())
                for line in raw.splitlines()
                if line.startswith("data:")
            ]
            result = next((event for event in events if event.get("id") == self.seq), {})
        else:
            result = json.loads(raw)
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result.get("result", {})

    def call(self, name, arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(str(result.get("content")))
        texts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                try:
                    data = json.loads(item["text"])
                    if isinstance(data, dict) and data.get("success") is False:
                        raise RuntimeError(str(data))
                    texts.append(data)
                except json.JSONDecodeError:
                    texts.append(item["text"])
        return texts

    def script(self, script, read_only=False):
        return self.call(
            "fusion_mcp_execute",
            {"featureType": "script", "object": {"script": script, "readOnly": read_only}},
        )


def probe(config: dict) -> dict:
    try:
        client = FusionClient({**config, "fusion_timeout_seconds": 3})
        tools = client.rpc("tools/list", {})
        names = [tool["name"] for tool in tools.get("tools", [])]
        available = "fusion_mcp_execute" in names
        return {
            "available": available,
            "endpoint": client.url,
            "tools": names,
            "actions": list(ACTIONS),
            "note": "Fusion must remain open with its local MCP server enabled.",
        }
    except Exception as exc:
        return {
            "available": False,
            "actions": list(ACTIONS),
            "reason": str(exc),
            "setup": "Open Fusion; enable Preferences > General > API > Fusion MCP Server (local).",
        }


def _path(workspace, value, suffix):
    p = pathlib.Path(value)
    if p.is_absolute():
        raise ValueError("Input paths must be workspace relative")
    p = (workspace / p).resolve()
    if not p.is_relative_to(workspace.resolve()) or not p.is_file() or p.suffix.lower() != suffix:
        raise ValueError("Input must be an existing workspace-contained " + suffix + " file")
    return str(p)


def _artifact(workspace, p, kind):
    return {
        "path": str(p.relative_to(workspace)),
        "kind": kind,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "bytes": p.stat().st_size,
    }


def execute(workspace: pathlib.Path, request: dict, run_dir: pathlib.Path, config: dict) -> dict:
    workspace = workspace.resolve()
    run_dir = run_dir.resolve()
    try:
        if not run_dir.is_relative_to(workspace):
            raise ValueError("Run directory escapes workspace")
        if run_dir.exists() and any(run_dir.iterdir()):
            raise ValueError("Run directory must be empty; stale evidence cannot be reused")
        run_dir.mkdir(parents=True, exist_ok=True)
        action = request["action"]
        params = dict(request.get("parameters", {}))
        if action not in ACTIONS:
            raise ValueError("Unsupported Fusion action: " + action)
        if action == "run_script" and not config.get("allow_scripts", False):
            return {
                "status": "blocked",
                "summary": "Trusted Python execution requires allow_scripts=true.",
                "artifacts": [],
                "observations": {},
                "limitations": [
                    "Scripts have the full privileges of Fusion; this is not a sandbox."
                ],
            }
        if action == "api_documentation":
            pattern = params.get("search_pattern")
            if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
                raise ValueError("search_pattern is required (max 200 characters)")
            result = FusionClient(config).call(
                "fusion_mcp_read",
                {
                    "queryType": "apiDocumentation",
                    "searchPattern": pattern,
                    "apiCategory": params.get("category", "all"),
                },
            )
            file = run_dir / "api-documentation.json"
            file.write_text(json.dumps({"query": pattern, "result": result}, indent=2))
            return {
                "status": "verified",
                "summary": "Native Fusion API documentation retrieved.",
                "artifacts": [_artifact(workspace, file, "evidence")],
                "observations": {"query": pattern, "result": result},
                "limitations": [],
            }
        if action in ("inspect", "edit_parameters", "export_asset") and not params.get(
            "input_design"
        ):
            raise ValueError("input_design is required")
        input_digest = None
        if params.get("input_design"):
            params["input_design"] = _path(workspace, params["input_design"], ".f3d")
            input_digest = hashlib.sha256(
                pathlib.Path(params["input_design"]).read_bytes()
            ).hexdigest()
        if action == "run_script":
            params["script_path"] = _path(workspace, params["script_path"], ".py")
        if action == "edit_parameters":
            changes = params.get("parameters")
            if (
                not isinstance(changes, dict)
                or not changes
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in changes.items())
            ):
                raise ValueError("parameters must map parameter names to unit expressions")
        if action == "create_demo":
            known = {
                "base_width",
                "base_depth",
                "plate_thickness",
                "edge_margin",
                "wall_thickness",
                "housing_height",
                "lid_thickness",
                "mount_diameter",
                "boss_outer",
                "boss_bore",
                "lens_diameter",
                "lens_length",
            }
            if (
                not isinstance(params.get("dimensions", {}), dict)
                or set(params.get("dimensions", {})) - known
            ):
                raise ValueError("Unknown demo dimension")
        if not isinstance(params.get("formats", ["f3d", "step"]), list) or set(
            params.get("formats", [])
        ) - {"f3d", "step", "stl"}:
            raise ValueError("formats must contain f3d, step or stl")
        job = {"action": action, "parameters": params, "run_dir": str(run_dir)}
        runner = pathlib.Path(__file__).parents[1] / "host_scripts" / "fusion" / "runner.py"
        code = "BRIDGE_JOB = " + repr(job) + "\n" + runner.read_text()
        (run_dir / "executed-script.py").write_text(code)
        result = FusionClient(config).script(code)
        if (
            input_digest is not None
            and hashlib.sha256(pathlib.Path(params["input_design"]).read_bytes()).hexdigest()
            != input_digest
        ):
            raise RuntimeError(
                "The declared source archive changed during execution; native side effects require review"
            )
        (run_dir / "fusion-response.json").write_text(json.dumps(result, indent=2))
        evidence_path = run_dir / "fusion-evidence.json"
        if not evidence_path.exists():
            raise RuntimeError("Fusion returned without native geometry evidence")
        evidence = json.loads(evidence_path.read_text())
        artifacts = []
        for p in run_dir.iterdir():
            if p.is_file():
                kind = (
                    "native-model"
                    if p.suffix in (".f3d", ".step", ".stl")
                    else (
                        "evidence"
                        if p.name == "fusion-evidence.json"
                        else (
                            "image"
                            if p.suffix == ".png"
                            else ("script" if p.suffix == ".py" else "metadata")
                        )
                    )
                )
                artifacts.append(_artifact(workspace, p, kind))
        if action == "inspect":
            artifacts.append(
                _artifact(workspace, pathlib.Path(params["input_design"]), "native-model")
            )
        healthy = not evidence["unhealthy_features"]
        solids = evidence["body_count"] > 0 and all(
            b["solid"] and b["volume_cm3"] > 0 for b in evidence["bodies"]
        )
        if action == "create_demo":
            healthy = (
                healthy
                and evidence["design_type"] == "parametric"
                and evidence["timeline_count"] > 0
            )
        persisted = action == "inspect" or evidence.get("archive_roundtrip", {}).get(
            "verified", False
        )
        status = "verified" if healthy and solids and persisted else "executed_unverified"
        return {
            "status": status,
            "summary": f"Fusion {action}: {evidence['body_count']} solid bodies; {evidence['timeline_count']} timeline entries.",
            "artifacts": artifacts,
            "observations": evidence,
            "limitations": [
                "STEP/STL exports do not retain Fusion feature history.",
                "Trusted Python is native code execution, not a sandbox.",
            ],
        }
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        raise RuntimeError(
            "Fusion transport outcome is uncertain; inspect the native host before retrying: "
            + str(exc)
        ) from exc
    except Exception as exc:
        return {
            "status": "failed",
            "summary": str(exc),
            "artifacts": [],
            "observations": {},
            "limitations": [
                "No automatic retry after a native mutation; inspect the host before retrying."
            ],
        }
