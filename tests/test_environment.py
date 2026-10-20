import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from studio_agent_bridge.environment import plan_environment


@pytest.mark.parametrize(
    "patch",
    [
        {"goal": "     "},
        {"size_m": [12, float("nan"), 4]},
        {"size_m": [12, float("inf"), 4]},
        {"size_m": [0, 9, 4]},
        {"zones": ["Racks", " racks "]},
        {"zones": [""]},
        {"triangle_budget": True},
        {"triangle_budget": 99_000_000},
        {"detail": "certified"},
        {"external_script": "/tmp/arbitrary.py"},
    ],
)
def test_reject_invalid_or_unbounded_design_intent(patch):
    with pytest.raises(ValidationError):
        plan_environment({"goal": "Create a complete robotics laboratory", **patch})


def test_scene_plan_keeps_native_and_visual_evidence_separate():
    result = plan_environment({"goal": "A detailed control room", "zones": ["Operations", "Server room"]})
    assert result["status"] == "planned"
    assert result["brief"]["zones"] == ["Operations", "Server room"]
    assert result["acceptance"]["native_integrity"]
    assert result["acceptance"]["visual_review"]
    assert "real Unreal visual review" in result["acceptance"]["transfer"]
    assert result["assumptions"] == []
    json.dumps(result, allow_nan=False)


def test_blender_only_brief_does_not_require_unreal():
    result = plan_environment({"goal": "An architectural interior", "target": "blender"})
    assert result["acceptance"]["transfer"] == []
    assert "interop" not in {stage["role"] for stage in result["stages"]}
    assert result["assumptions"]  # An unspecified layout is not silently claimed as known.


def test_cli_environment_plan_is_usable_without_launching_hosts(tmp_path):
    command = [
        sys.executable, "-m", "studio_agent_bridge", "--workspace", str(tmp_path),
        "environment-plan", "--brief", json.dumps({"goal": "A fully furnished industrial lab"}),
    ]
    output = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert output.returncode == 0, output.stderr
    assert json.loads(output.stdout)["brief"]["detail"] == "high"
