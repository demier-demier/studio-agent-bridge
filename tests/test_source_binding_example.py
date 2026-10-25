"""Pure reproducer preconditions; these tests do not launch a native app."""

import copy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "source_binding_example",
    Path(__file__).resolve().parents[1] / "examples/source_binding/run_example.py",
)
assert SPEC and SPEC.loader
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


def preflight_record():
    return {
        "status": "blocked",
        "native_process_launches": 0,
        "result": {
            "result": {
                "observations": {
                    "native_launch_attempted": False,
                    "preflight": {
                        "decision": "blocked",
                        "code": "unreal_macos_5_7_4_environment_nullrhi",
                        "action": "build_scene",
                        "editor_session": True,
                        "rendering_mode": "structure",
                    },
                }
            }
        },
    }


def test_known_preflight_branch_never_claims_a_native_process_exited():
    record = preflight_record()
    original = copy.deepcopy(record)
    basis = example.explicit_gpu_retry_basis(record)
    assert basis["previous_attempt"] == "preflight-blocked"
    assert basis["previous_native_process_launches"] == 0
    assert basis["previous_native_process_exited"] is None
    assert basis["previous_returncode"] is None
    assert record == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "unrelated_block"),
        ("decision", "allowed"),
        ("action", "inspect"),
        ("editor_session", False),
        ("rendering_mode", "gpu"),
    ],
)
def test_unknown_or_inconsistent_preflight_does_not_allow_followup(field, value):
    record = preflight_record()
    record["result"]["result"]["observations"]["preflight"][field] = value
    with pytest.raises(ValueError, match="known preflight"):
        example.explicit_gpu_retry_basis(record)


@pytest.mark.parametrize("count", [None, False, 1, -1, "0"])
def test_preflight_requires_exact_zero_observed_native_launches(count):
    record = preflight_record()
    record["native_process_launches"] = count
    with pytest.raises(ValueError, match="zero observed native launches"):
        example.explicit_gpu_retry_basis(record)


@pytest.mark.parametrize(
    "field,value", [("native_launch_attempted", True), ("returncode", 1), ("timed_out", False)]
)
def test_preflight_cannot_mask_a_started_native_process(field, value):
    record = preflight_record()
    record["result"]["result"]["observations"][field] = value
    with pytest.raises(ValueError, match="known preflight"):
        example.explicit_gpu_retry_basis(record)


def test_historical_failure_branch_still_requires_recorded_process_exit():
    record = {
        "status": "failed",
        "result": {"result": {"observations": {"returncode": 1, "timed_out": False}}},
    }
    basis = example.explicit_gpu_retry_basis(record)
    assert basis == {
        "previous_attempt": "native-failure",
        "previous_native_process_exited": True,
        "previous_returncode": 1,
    }
    for observations in (
        {},
        {"returncode": None, "timed_out": True},
        {"returncode": 1, "timed_out": True},
    ):
        record["result"]["result"]["observations"] = observations
        with pytest.raises(ValueError, match="prior native process exited"):
            example.explicit_gpu_retry_basis(record)


@pytest.mark.parametrize("status", ["verified", "running", "executed_unverified"])
def test_ineligible_attempts_are_not_retried(status):
    record = preflight_record()
    record["status"] = status
    with pytest.raises(ValueError, match="recorded failed or known"):
        example.explicit_gpu_retry_basis(record)


def test_public_summary_keeps_preflight_separate_from_native_failure(tmp_path):
    record = preflight_record()
    state = {
        "actions": {"unreal-edited-scene": record},
        "unreal_status": "blocked-structure-attempt",
    }
    result = example.unreal_observations(tmp_path, state)
    attempt = result["attempts"][0]
    assert attempt["status"] == "blocked"
    assert attempt["preflight"]["code"] == "unreal_macos_5_7_4_environment_nullrhi"
    assert attempt["rendering_mode"] == "structure"
    assert attempt["native_process_launches"] == 0
    assert attempt["native_launch_attempted"] is False
    assert attempt["returncode"] is None and attempt["timed_out"] is None
    assert "failure" not in attempt
    assert "verification" not in attempt
