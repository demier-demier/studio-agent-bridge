"""Public requests, bounded identifiers, and the deterministic role handoff plan."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Host = Literal["fusion", "blender", "unreal"]
Action = Literal[
    "create_demo",
    "inspect",
    "import_asset",
    "export_asset",
    "render",
    "run_script",
    "create_project",
    "build_scene",
    "edit_parameters",
    "api_documentation",
]
ResultStatus = Literal["verified", "executed_unverified", "blocked", "failed"]

ACTIONS: dict[str, list[str]] = {
    "fusion": [
        "create_demo",
        "inspect",
        "edit_parameters",
        "run_script",
        "export_asset",
        "api_documentation",
    ],
    "blender": ["create_demo", "inspect", "import_asset", "export_asset", "render", "run_script"],
    "unreal": ["create_project", "import_asset", "build_scene", "inspect", "run_script"],
}

# Keys describe filesystem paths, never Unreal /Game object references.
PATH_KEYS = {
    "script_path",
    "input_blend",
    "input_design",
    "asset_path",
    "source",
    "input_path",
    "output_path",
    "render_output",
}
# Blender's environment_manifest is an export toggle; Unreal's is an input filename.
HOST_PATH_KEYS = {"unreal": {"environment_manifest"}}
PATH_LIST_KEYS = {"expected_artifacts"}


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Action
    parameters: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=2000)
    kind: str = Field(min_length=1, max_length=80)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0)


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: ResultStatus
    summary: str = Field(min_length=1, max_length=10000)
    artifacts: list[Artifact] = Field(default_factory=list, max_length=2000)
    observations: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


ROLE_PROFILES = [
    {
        "role": "planner",
        "responsibility": "Resolve dimensions, units, native editability, deliverables and acceptance checks before modeling.",
    },
    {
        "role": "native_modeler",
        "responsibility": "Build named native features or scene objects, save the editable artifact, and preserve parameter intent.",
    },
    {
        "role": "geometry_qa",
        "responsibility": "Inspect actual geometry, feature health, transforms, counts and dimensions; reopen the saved artifact and test a meaningful edit.",
    },
    {
        "role": "interop",
        "responsibility": "Export and import using explicit units, axes and format; measure the target geometry and document lost semantics.",
    },
    {
        "role": "reviewer",
        "responsibility": "Compare evidence with the acceptance checks. Separate verified, executed-unverified, blocked and failed outcomes. Do not approve based on a screenshot alone.",
    },
]


def plan_workflow(goal: str, hosts: list[str]) -> dict:
    if not goal.strip() or len(goal) > 8000:
        raise ValueError("Goal must contain 1–8000 characters.")
    if not hosts or len(hosts) > 3 or len(set(hosts)) != len(hosts):
        raise ValueError("Choose one to three distinct hosts in execution order.")
    if any(host not in ACTIONS for host in hosts):
        raise ValueError("Unknown host.")
    return {
        "goal": goal,
        "hosts": hosts,
        "roles": ROLE_PROFILES,
        "actions": {host: ACTIONS[host] for host in hosts},
        "handoffs": [
            {
                "from": left,
                "to": right,
                "required": [
                    "artifact SHA-256",
                    "units",
                    "axis convention",
                    "target measurements",
                    "known semantic losses",
                ],
            }
            for left, right in zip(hosts, hosts[1:])
        ],
        "intelligence": "The MCP client supplies the language model and dispatches roles. This server supplies tools, persistence and verification gates; it does not create autonomous agents or infer CAD geometry from prose.",
    }
