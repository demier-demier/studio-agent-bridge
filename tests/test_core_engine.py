"""Boundary tests use explicit fake adapters, never masquerading as native CAD evidence."""

import json
import sqlite3
import subprocess
import sys

import pytest

from studio_agent_bridge.engine import (
    Engine,
    IntegrityError,
    ReconciliationRequired,
    RevisionConflict,
)
from studio_agent_bridge.models import plan_workflow
from studio_agent_bridge.workspace import (
    HostBusy,
    WorkspaceError,
    file_digest,
    host_lock,
)


class FixtureAdapter:
    calls = 0

    @staticmethod
    def probe(config):
        return {"available": True, "fixture": True}

    def execute(self, workspace, request, run_dir, config):
        self.calls += 1
        assert not list(run_dir.iterdir())
        fixture = run_dir / "fixture.bin"
        fixture.write_bytes(b"TEST FIXTURE, NOT A CAD MODEL")
        report = run_dir / "verification.json"
        report.write_text(json.dumps({"fixture": True, "measured_bytes": fixture.stat().st_size}))
        return {
            "status": "verified",
            "summary": "Core fixture completed.",
            "artifacts": [
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "kind": kind,
                    "sha256": file_digest(path),
                    "bytes": path.stat().st_size,
                }
                for path, kind in [(fixture, "fixture"), (report, "verification")]
            ],
            "observations": {"fixture": True},
            "limitations": ["A test double; no host execution."],
        }


@pytest.fixture
def setup(tmp_path):
    adapter = FixtureAdapter()
    engine = Engine(
        tmp_path / "workspace", {}, adapters={"blender": adapter}, lock_dir=tmp_path / "locks"
    )
    job = engine.create_job(
        "fixture", "blender", "Test durable orchestration using a fixture adapter."
    )
    return engine, job, adapter


def test_job_survives_reopen_and_preserves_all_action_artifacts(setup):
    engine, job, adapter = setup
    first = engine.execute_action(job["id"], "create_demo", {}, 1)
    assert first["status"] == "verified"
    assert first["revision"] == 3
    second = engine.execute_action(job["id"], "create_demo", {}, 3)
    assert second["revision"] == 5
    reopened = Engine(
        engine.workspace.root, {}, adapters={"blender": adapter}, lock_dir=engine.lock_dir
    )
    assert reopened.inspect_job(job["id"])["ledger"]["events"] == 5
    assert len(reopened.list_artifacts(job["id"])["artifacts"]) == 4


def test_stale_revision_never_calls_native_adapter(setup):
    engine, job, adapter = setup
    engine.execute_action(job["id"], "create_demo", {}, 1)
    with pytest.raises(RevisionConflict):
        engine.execute_action(job["id"], "create_demo", {}, 1)
    assert adapter.calls == 1


@pytest.mark.parametrize("change", ["snapshot", "event", "truncate"])
def test_modified_ledger_is_detected(setup, change):
    engine, job, _ = setup
    engine.execute_action(job["id"], "create_demo", {}, 1)
    with sqlite3.connect(engine.database) as db:
        if change == "snapshot":
            db.execute("UPDATE jobs SET snapshot='{}'")
        elif change == "event":
            body = json.loads(db.execute("SELECT body FROM events WHERE revision=1").fetchone()[0])
            body["state"]["goal"] = "Tampered goal"
            db.execute("UPDATE events SET body=? WHERE revision=1", (json.dumps(body),))
        else:
            db.execute("DELETE FROM events WHERE revision=3")
    with pytest.raises(IntegrityError):
        engine.inspect_job(job["id"])


def test_modified_artifact_rechecked_at_read(setup):
    engine, job, _ = setup
    result = engine.execute_action(job["id"], "create_demo", {}, 1)
    path = engine.workspace.resolve(result["result"]["artifacts"][0]["path"])
    path.write_text("changed after execution")
    assert engine.list_artifacts(job["id"])["artifacts"][0]["integrity"] == "missing_or_modified"


@pytest.mark.parametrize(
    "path", ["../escape.py", "/tmp/escape.py", ".studio/jobs.sqlite", "a/../../escape", "a\\b"]
)
def test_declared_path_escape_rejected_before_dispatch(setup, path):
    engine, job, adapter = setup
    with pytest.raises(WorkspaceError):
        engine.execute_action(job["id"], "inspect", {"input_blend": path}, 1)
    assert adapter.calls == 0


def test_environment_path_is_host_specific_and_rejected_before_dispatch(tmp_path):
    adapter = FixtureAdapter()
    engine = Engine(
        tmp_path / "workspace",
        {},
        adapters={"unreal": adapter, "blender": adapter},
        lock_dir=tmp_path / "locks",
    )
    unreal_job = engine.create_job("environment", "unreal", "Test environment manifest boundaries")
    for value in ("../private.json", "/tmp/private.json", True):
        with pytest.raises(WorkspaceError):
            engine.execute_action(
                unreal_job["id"], "build_scene", {"environment_manifest": value}, 1
            )
    assert adapter.calls == 0
    blender_job = engine.create_job("export", "blender", "Test the Blender export toggle")
    result = engine.execute_action(
        blender_job["id"], "export_asset", {"environment_manifest": True}, 1
    )
    assert result["status"] == "verified"
    assert adapter.calls == 1


def test_symlink_input_and_symlink_parent_rejected(setup, tmp_path):
    engine, job, adapter = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.blend").write_text("outside")
    (engine.workspace.root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError):
        engine.execute_action(job["id"], "inspect", {"input_blend": "linked/file.blend"}, 1)
    assert adapter.calls == 0


def test_symlink_state_database_rejected(tmp_path):
    root = tmp_path / "workspace"
    (root / ".studio").mkdir(parents=True)
    outside = tmp_path / "outside.sqlite"
    outside.write_text("not sqlite")
    (root / ".studio" / "jobs.sqlite").symlink_to(outside)
    with pytest.raises(WorkspaceError):
        Engine(root, {})


def test_script_opt_in_is_server_side(setup):
    engine, job, adapter = setup
    with pytest.raises(PermissionError):
        engine.execute_action(
            job["id"], "run_script", {"script_path": "trusted.py", "allow_scripts": True}, 1
        )
    assert adapter.calls == 0


def test_nan_and_oversized_request_rejected(setup):
    engine, job, adapter = setup
    for parameters in ({"value": float("nan")}, {"value": "x" * 256001}):
        with pytest.raises(ValueError):
            engine.execute_action(job["id"], "create_demo", parameters, 1)
    assert adapter.calls == 0


@pytest.mark.parametrize("missing", ["artifacts", "observations", "evidence"])
def test_unsubstantiated_verified_result_is_downgraded(setup, missing):
    engine, job, adapter = setup
    original = adapter.execute

    def execute(*args):
        result = original(*args)
        if missing == "evidence":
            result["artifacts"] = result["artifacts"][:1]
        else:
            result[missing] = [] if missing == "artifacts" else {}
        return result

    adapter.execute = execute
    result = engine.execute_action(job["id"], "create_demo", {}, 1)
    assert result["status"] == "executed_unverified"
    assert "No verified claim accepted" in result["result"]["limitations"][-1]


@pytest.mark.parametrize("tamper", ["hash", "outside", "empty_evidence"])
def test_invalid_result_requires_review_not_automatic_retry(setup, tamper):
    engine, job, adapter = setup
    original = adapter.execute

    def execute(*args):
        result = original(*args)
        if tamper == "hash":
            result["artifacts"][0]["sha256"] = "0" * 64
        elif tamper == "outside":
            result["artifacts"][0]["path"] = "../outside"
        else:
            report = engine.workspace.resolve(result["artifacts"][1]["path"])
            report.write_text("{}")
            result["artifacts"][1].update(sha256=file_digest(report), bytes=2)
        return result

    adapter.execute = execute
    result = engine.execute_action(job["id"], "create_demo", {}, 1)
    assert result["status"] == "needs_review"
    with pytest.raises(ReconciliationRequired):
        engine.execute_action(job["id"], "create_demo", {}, result["revision"])
    assert adapter.calls == 1


def test_host_lock_excludes_another_process_and_releases_on_exit(tmp_path):
    code = """
from pathlib import Path
import sys, time
from studio_agent_bridge.workspace import host_lock
with host_lock('blender', Path(sys.argv[1])):
    print('locked', flush=True)
    time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path / "locks")], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(HostBusy):
            with host_lock("blender", tmp_path / "locks"):
                pytest.fail("Concurrent same-host execution was allowed")
        with host_lock("fusion", tmp_path / "locks"):
            pass
    finally:
        child.terminate()
        child.wait(timeout=5)
    with host_lock("blender", tmp_path / "locks"):
        pass


def test_process_crash_preserves_uncertain_outcome_and_blocks_other_workspace(tmp_path):
    root = tmp_path / "workspace"
    locks = tmp_path / "locks"
    engine = Engine(root, {}, lock_dir=locks)
    job = engine.create_job("crash", "blender", "Simulate an interrupted external side effect.")
    code = """
from pathlib import Path
import os, sys
from studio_agent_bridge.engine import Engine
class Crash:
    def execute(self, workspace, request, run_dir, config):
        (run_dir / 'external-effect.txt').write_text('committed before process termination')
        os._exit(17)
engine = Engine(Path(sys.argv[1]), {}, adapters={'blender': Crash()}, lock_dir=Path(sys.argv[2]))
engine.execute_action(sys.argv[3], 'create_demo', {}, 1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), str(locks), job["id"]], timeout=10
    )
    assert result.returncode == 17
    after = engine.inspect_job(job["id"])
    assert after["status"] == "running"
    assert (root / after["run_dir"] / "external-effect.txt").is_file()
    second = Engine(tmp_path / "other", {}, adapters={"blender": FixtureAdapter()}, lock_dir=locks)
    other = second.create_job(
        "other", "blender", "A separate workspace must respect unresolved native execution."
    )
    with pytest.raises(ReconciliationRequired):
        second.execute_action(other["id"], "create_demo", {}, 1)
    with pytest.raises(ValueError):
        engine.reconcile_job(
            job["id"], "I checked the owned synthetic effect file.", False, after["revision"]
        )
    reconciled = engine.reconcile_job(
        job["id"],
        "Inspected the synthetic effect file and confirmed the terminated child process is no longer running.",
        True,
        after["revision"],
    )
    assert reconciled["status"] == "ready"
    assert reconciled["revision"] == 3
    assert second.execute_action(other["id"], "create_demo", {}, 1)["status"] == "verified"


def test_role_plan_includes_interop_and_client_intelligence():
    plan = plan_workflow(
        "Create an editable bracket and visualize it in a real-time scene.",
        ["fusion", "blender", "unreal"],
    )
    assert len(plan["handoffs"]) == 2
    assert [role["role"] for role in plan["roles"]] == [
        "planner",
        "native_modeler",
        "geometry_qa",
        "interop",
        "reviewer",
    ]
    assert "MCP client" in plan["intelligence"]


@pytest.mark.parametrize("hosts", [[], ["fusion", "fusion"], ["unknown"]])
def test_invalid_plan_hosts(hosts):
    with pytest.raises(ValueError):
        plan_workflow("Goal", hosts)
