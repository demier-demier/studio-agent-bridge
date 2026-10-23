"""Exercise the public exporter against the actual engine with synthetic effects."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recovery_example", ROOT / "examples/recovery/generate.py"
)
assert SPEC and SPEC.loader
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


@pytest.fixture(scope="module")
def observed():
    return example.observe_recovery()


def test_every_exported_check_is_observed_and_passes(observed):
    assert {case["id"] for case in observed["cases"]} == {
        "clean-action", "effect-then-crash", "intent-window", "unknown-marker"
    }
    assert all(case["checks"] for case in observed["cases"])
    assert all(value is True for case in observed["cases"] for value in case["checks"].values())
    assert observed["provenance"]["native_apps_used"] is False
    assert observed["provenance"]["cloud_used"] is False


def test_crashed_effect_survives_reconcile_and_new_action(observed):
    case = next(case for case in observed["cases"] if case["id"] == "effect-then-crash")
    interrupted, blocked, inspected, reconciled, fresh = case["steps"][1:]
    assert [step["adapter_calls"] for step in (interrupted, blocked, inspected, reconciled, fresh)] == [1, 1, 1, 1, 2]
    assert interrupted["effects"] == reconciled["effects"]
    assert len(fresh["effects"]) == 2
    assert interrupted["effects"][0] in fresh["effects"]
    assert blocked["status"] == "blocked"


def test_pre_dispatch_crash_never_claims_adapter_execution(observed):
    case = next(case for case in observed["cases"] if case["id"] == "intent-window")
    crashed = case["steps"][1]
    assert crashed["revision"] == 1
    assert crashed["report"]["status"] == "ready"
    assert crashed["adapter_calls"] == 0
    assert crashed["effects"] == []
    assert crashed["report"]["reconciliation"]["required"] is True


def test_unknown_owner_cannot_be_acknowledged_by_current_job(observed):
    case = next(case for case in observed["cases"] if case["id"] == "unknown-marker")
    for step in case["steps"]:
        assert step["adapter_calls"] == 0
        assert step["revision"] == 1
        assert step["report"]["host_marker"]["ownership"] == "unknown"
        assert step["report"]["reconciliation"]["allowed"] is False


def test_public_evidence_has_no_machine_paths_or_native_verification_claim(observed):
    encoded = json.dumps(observed)
    for forbidden in (str(ROOT), "/Users/", "/home/", "studio-synthetic-recovery-"):
        assert forbidden not in encoded
    for case in observed["cases"]:
        for step in case["steps"]:
            assert step["report"]["native_host_idle"] == "unknown"
            assert step["report"]["automatic_replay"] is False
            for effect in step["effects"]:
                assert not Path(effect["path"]).is_absolute()
                assert effect["bytes"] > 0
                assert len(effect["sha256"]) == 64
