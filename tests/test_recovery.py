"""Synthetic durability fixtures only: no CAD, editor, or geometry execution."""

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager

import pytest

from studio_agent_bridge.engine import Engine, ReconciliationRequired, RevisionConflict
from studio_agent_bridge.workspace import WorkspaceError, atomic_json, canonical


class SyntheticAdapter:
    def __init__(self):
        self.calls = 0

    def execute(self, workspace, request, run_dir, config):
        self.calls += 1
        (run_dir / "synthetic-effect.txt").write_text("SYNTHETIC EFFECT; NO NATIVE APPLICATION")
        return {
            "status": "executed_unverified",
            "summary": "Synthetic test adapter executed once; no native verification.",
            "artifacts": [],
            "observations": {"synthetic": True},
            "limitations": ["No native application was called."],
        }


@pytest.fixture
def setup(tmp_path):
    adapter = SyntheticAdapter()
    engine = Engine(
        tmp_path / "workspace", {}, adapters={"blender": adapter}, lock_dir=tmp_path / "locks"
    )
    job = engine.create_job("recovery", "blender", "Exercise synthetic interruption recovery.")
    return engine, job, adapter


def reconcile(engine, job_id):
    current = engine.inspect_job(job_id)
    return engine.reconcile_job(
        job_id,
        "Inspected synthetic evidence and independently confirmed the fixture process is stopped.",
        True,
        current["revision"],
    )


def marker_for(engine):
    return engine.lock_dir / "blender.active.json"


def test_intent_is_complete_and_durable_before_run_directory_allocation(setup, monkeypatch):
    engine, job, adapter = setup
    original = engine.workspace.run_dir

    def allocate(*args):
        marker = json.loads(marker_for(engine).read_text())
        assert marker["schema_version"] == 1
        assert marker["job_id"] == job["id"]
        assert marker["expected_revision"] == 1
        assert engine.inspect_job(job["id"])["status"] == "running"
        assert not engine.workspace.resolve(marker["run_dir"]).exists()
        return original(*args)

    monkeypatch.setattr(engine.workspace, "run_dir", allocate)
    state = engine.execute_action(job["id"], "create_demo", {}, 1)
    assert state["status"] == "executed_unverified"
    assert adapter.calls == 1
    assert not marker_for(engine).exists()
    assert not engine.recovery_report(job["id"])["reconciliation"]["required"]


def test_exception_after_intent_before_event_remains_reconcilable(setup, monkeypatch):
    engine, job, adapter = setup
    original = engine._publish_intent

    def fail_after_intent(marker, intent):
        original(marker, intent)
        raise RuntimeError("Synthetic failure after durable intent")

    monkeypatch.setattr(engine, "_publish_intent", fail_after_intent)
    state = engine.execute_action(job["id"], "create_demo", {}, 1)
    assert state["status"] == "needs_review"
    assert state["result"]["observations"]["native_dispatch_attempted"] is False
    report = engine.recovery_report(job["id"])
    assert report["host_marker"]["ownership"] == "this_job"
    assert report["reconciliation"]["allowed"] is True
    assert report["event_state"]["preparation_failed"] is True
    assert report["run_directories"][0]["state"] == "missing"
    with pytest.raises(ReconciliationRequired):
        engine.execute_action(job["id"], "create_demo", {}, state["revision"])
    assert adapter.calls == 0
    reconcile(engine, job["id"])
    assert not engine.recovery_report(job["id"])["reconciliation"]["required"]


def test_allocation_exception_preserves_partial_evidence_without_native_replay(setup, monkeypatch):
    engine, job, adapter = setup
    original = engine.workspace.run_dir

    def partial(*args):
        run_dir = original(*args)
        (run_dir / "partial.txt").write_bytes(b"original synthetic preparation bytes")
        raise OSError("Synthetic allocation failure with /private/path that must not escape")

    monkeypatch.setattr(engine.workspace, "run_dir", partial)
    state = engine.execute_action(job["id"], "create_demo", {}, 1)
    assert state["status"] == "needs_review"
    assert "/private/path" not in json.dumps(state)
    original_path = engine.workspace.resolve(state["run_dir"]) / "partial.txt"
    assert original_path.read_bytes() == b"original synthetic preparation bytes"
    assert adapter.calls == 0
    assert engine.recovery_report(job["id"])["run_directories"][0]["has_entries"] is True
    ready = reconcile(engine, job["id"])
    monkeypatch.setattr(engine.workspace, "run_dir", original)
    fresh = engine.execute_action(job["id"], "create_demo", {}, ready["revision"])
    assert fresh["run_dir"] != state["run_dir"]
    assert original_path.read_bytes() == b"original synthetic preparation bytes"
    assert adapter.calls == 1


@pytest.mark.parametrize("point", ["intent", "allocation", "adapter"])
def test_hard_crash_restart_requires_explicit_reconciliation_and_never_replays(setup, point):
    engine, job, adapter = setup
    code = r"""
import os, sys
from pathlib import Path
from studio_agent_bridge.engine import Engine
class SyntheticCrashAdapter:
    def execute(self, workspace, request, run_dir, config):
        with (run_dir / 'effect.txt').open('wb') as stream:
            stream.write(b'SYNTHETIC EXTERNAL EFFECT')
            stream.flush(); os.fsync(stream.fileno())
        os._exit(73)
class CrashAfterIntent(Engine):
    def _publish_intent(self, marker, intent):
        super()._publish_intent(marker, intent)
        if sys.argv[4] == 'intent': os._exit(71)
e = CrashAfterIntent(Path(sys.argv[1]), {}, adapters={'blender': SyntheticCrashAdapter()}, lock_dir=Path(sys.argv[2]))
original = e.workspace.run_dir
def allocate(*args):
    directory = original(*args)
    if sys.argv[4] == 'allocation':
        (directory / 'partial.txt').write_bytes(b'SYNTHETIC PREPARATION')
        os._exit(72)
    return directory
e.workspace.run_dir = allocate
e.execute_action(sys.argv[3], 'create_demo', {}, 1)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(engine.workspace.root),
            str(engine.lock_dir),
            job["id"],
            point,
        ],
        timeout=10,
    )
    assert result.returncode == {"intent": 71, "allocation": 72, "adapter": 73}[point]
    reopened = Engine(
        engine.workspace.root, {}, adapters={"blender": adapter}, lock_dir=engine.lock_dir
    )
    report = reopened.recovery_report(job["id"])
    assert report["status"] == ("ready" if point == "intent" else "running")
    assert report["native_host_idle"] == "unknown"
    assert report["automatic_replay"] is False
    assert report["reconciliation"]["required"] and report["reconciliation"]["allowed"]
    assert str(engine.workspace.root) not in canonical(report)
    before = {
        path.relative_to(engine.workspace.root).as_posix(): path.read_bytes()
        for path in (engine.workspace.root / "jobs").rglob("*.txt")
    }
    with pytest.raises(ReconciliationRequired):
        reopened.execute_action(job["id"], "create_demo", {}, report["revision"])
    other = Engine(
        engine.workspace.root.parent / "other",
        {},
        adapters={"blender": adapter},
        lock_dir=engine.lock_dir,
    )
    other_job = other.create_job("other", "blender", "Another synthetic workspace")
    foreign = other.recovery_report(other_job["id"])
    assert foreign["host_marker"]["ownership"] == "other_workspace"
    assert foreign["host_marker"]["run_dir"] is None
    with pytest.raises(ReconciliationRequired):
        other.execute_action(other_job["id"], "create_demo", {}, 1)
    assert adapter.calls == 0
    with pytest.raises(ValueError):
        reopened.reconcile_job(
            job["id"], "Synthetic host still requires direct inspection.", False, report["revision"]
        )
    ready = reconcile(reopened, job["id"])
    fresh = reopened.execute_action(job["id"], "create_demo", {}, ready["revision"])
    assert adapter.calls == 1
    assert fresh["run_dir"] != report["host_marker"]["run_dir"]
    for relative, payload in before.items():
        assert (engine.workspace.root / relative).read_bytes() == payload


def test_legacy_orphan_directory_is_reported_and_acknowledged_without_overwrite(setup):
    engine, job, adapter = setup
    orphan = engine.workspace.run_dir(job["id"], 2, "create_demo")
    (orphan / "keep.txt").write_bytes(b"legacy orphan synthetic evidence")
    report = engine.recovery_report(job["id"])
    assert report["status"] == "ready"
    assert report["host_marker"]["state"] == "absent"
    assert report["reconciliation"]["allowed"] is True
    assert report["run_directories"][0]["provenance"] == "unreferenced_job_path"
    with pytest.raises(ReconciliationRequired):
        engine.execute_action(job["id"], "create_demo", {}, 1)
    assert adapter.calls == 0
    ready = reconcile(engine, job["id"])
    assert (orphan / "keep.txt").read_bytes() == b"legacy orphan synthetic evidence"
    result = engine.execute_action(job["id"], "create_demo", {}, ready["revision"])
    assert result["run_dir"] != orphan.relative_to(engine.workspace.root).as_posix()
    assert adapter.calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        b'{"job_id":',
        b"{}",
        b'{"workspace":"/private/path"}',
        b"\xff",
        b"[]",
        b"[" * 1100 + b"]" * 1100,
    ],
)
def test_malformed_marker_never_guesses_ownership_or_modifies_evidence(setup, payload):
    engine, job, adapter = setup
    engine.lock_dir.mkdir()
    marker = marker_for(engine)
    marker.write_bytes(payload)
    report = engine.recovery_report(job["id"])
    assert report["host_marker"]["state"] == "malformed"
    assert report["host_marker"]["ownership"] == "unknown"
    assert report["reconciliation"]["required"] is True
    assert report["reconciliation"]["allowed"] is False
    assert "/private/path" not in canonical(report)
    with pytest.raises(ReconciliationRequired):
        reconcile(engine, job["id"])
    with pytest.raises(ReconciliationRequired):
        engine.execute_action(job["id"], "create_demo", {}, 1)
    assert marker.read_bytes() == payload
    assert engine.inspect_job(job["id"])["revision"] == 1
    assert adapter.calls == 0


def test_legacy_owned_marker_before_action_event_can_be_reconciled(setup):
    engine, job, adapter = setup
    engine.lock_dir.mkdir()
    marker_for(engine).write_text(
        canonical(
            {
                "job_id": job["id"],
                "workspace": str(engine.workspace.root),
                "run_dir": f"jobs/{job['id']}/runs/000002-create_demo",
                "started_at": job["created_at"],
            }
        )
    )
    report = engine.recovery_report(job["id"])
    assert report["host_marker"]["protocol"] == "legacy"
    assert report["reconciliation"]["allowed"] is True
    assert reconcile(engine, job["id"])["status"] == "ready"
    assert adapter.calls == 0


def test_atomic_marker_failure_preserves_temp_and_never_allocates_or_dispatches(setup, monkeypatch):
    engine, job, adapter = setup

    def interrupted_rename(*args):
        raise OSError("Synthetic interruption before publication")

    monkeypatch.setattr("studio_agent_bridge.workspace.os.rename", interrupted_rename)
    with pytest.raises(WorkspaceError, match="No native dispatch"):
        engine.execute_action(job["id"], "create_demo", {}, 1)
    assert not marker_for(engine).exists()
    temporary = list(engine.lock_dir.glob(".blender.active.json.*.tmp"))
    assert len(temporary) == 1
    assert json.loads(temporary[0].read_text())["job_id"] == job["id"]
    assert not (engine.workspace.root / "jobs" / job["id"]).exists()
    assert engine.inspect_job(job["id"])["revision"] == 1
    assert adapter.calls == 0


def test_atomic_marker_fsync_precedes_rename_and_existing_marker_is_not_overwritten(
    tmp_path, monkeypatch
):
    marker = tmp_path / "blender.active.json"
    calls = []
    rename = os.rename
    fsync = os.fsync
    monkeypatch.setattr(
        "studio_agent_bridge.workspace.os.fsync", lambda fd: (calls.append("fsync"), fsync(fd))[1]
    )
    monkeypatch.setattr(
        "studio_agent_bridge.workspace.os.rename",
        lambda a, b: (calls.append("rename"), rename(a, b))[1],
    )
    atomic_json(marker, {"synthetic": True})
    assert calls == ["fsync", "rename", "fsync"]
    with pytest.raises(WorkspaceError, match="overwritten"):
        atomic_json(marker, {"changed": True})
    assert json.loads(marker.read_text()) == {"synthetic": True}


@pytest.mark.parametrize("operation", ["inspect", "artifacts", "recovery"])
def test_snapshot_reads_remain_consistent_when_another_connection_commits(
    setup, monkeypatch, operation
):
    engine, job, adapter = setup
    writer = Engine(
        engine.workspace.root, {}, adapters={"blender": adapter}, lock_dir=engine.lock_dir
    )
    connect = engine._connect
    injected = False

    class RowInterleaver:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            nonlocal injected
            row = self.cursor.fetchone()
            if not injected:
                injected = True
                writer._append(job["id"], "synthetic_concurrent_note", {"synthetic": True}, 1)
            return row

    class ConnectionProxy:
        def __init__(self, db):
            self.db = db

        @property
        def in_transaction(self):
            return self.db.in_transaction

        def execute(self, sql, *args):
            cursor = self.db.execute(sql, *args)
            return RowInterleaver(cursor) if sql.startswith("SELECT * FROM jobs") else cursor

    @contextmanager
    def hooked_connection():
        with connect() as db:
            yield ConnectionProxy(db)

    monkeypatch.setattr(engine, "_connect", hooked_connection)
    if operation == "inspect":
        result = engine.inspect_job(job["id"])
        assert result["revision"] == result["ledger"]["events"] == 1
    elif operation == "artifacts":
        assert engine.list_artifacts(job["id"])["artifacts"] == []
    else:
        result = engine.recovery_report(job["id"])
        assert result["revision"] == result["ledger"]["events"] == 1
    assert injected
    assert writer.inspect_job(job["id"])["revision"] == 2


def test_reconcile_stale_revision_and_foreign_same_workspace_marker_fail_closed(setup):
    engine, job, adapter = setup
    other = engine.create_job("other", "blender", "Synthetic other job")
    engine.lock_dir.mkdir()
    marker_for(engine).write_text(
        canonical(
            {
                "job_id": other["id"],
                "workspace": str(engine.workspace.root),
                "run_dir": f"jobs/{other['id']}/runs/000002-create_demo",
                "started_at": other["created_at"],
            }
        )
    )
    report = engine.recovery_report(job["id"])
    assert report["host_marker"]["ownership"] == "other_job"
    with pytest.raises(ReconciliationRequired):
        reconcile(engine, job["id"])
    with pytest.raises(RevisionConflict):
        engine.reconcile_job(other["id"], "Synthetic confirmed process no longer running.", True, 0)
    assert marker_for(engine).exists()
    assert adapter.calls == 0


def test_report_is_read_only_and_never_probes_native_host(setup):
    engine, job, adapter = setup
    before = engine.inspect_job(job["id"])
    report = engine.recovery_report(job["id"])
    assert report["native_host_idle"] == "unknown"
    assert engine.inspect_job(job["id"]) == before
    assert not engine.lock_dir.exists()
    assert adapter.calls == 0


def test_finished_native_evidence_survives_marker_cleanup_failure(setup, monkeypatch):
    engine, job, adapter = setup

    def fail_cleanup(marker):
        raise OSError("Synthetic cleanup interruption")

    monkeypatch.setattr(engine, "_clear_intent", fail_cleanup)
    result = engine.execute_action(job["id"], "create_demo", {}, 1)
    assert result["status"] == "executed_unverified"
    assert result["recovery_required"] is True
    assert engine.inspect_job(job["id"])["event_types"][-1] == "action_finished"
    assert engine.recovery_report(job["id"])["reconciliation"]["allowed"] is True
    assert adapter.calls == 1


def test_process_crash_mid_temporary_write_never_publishes_partial_marker(tmp_path):
    marker = tmp_path / "blender.active.json"
    code = r"""
import os, sys
from pathlib import Path
from contextlib import contextmanager
from studio_agent_bridge.workspace import atomic_json
original = os.fdopen
class PartialWrite:
    def __init__(self, stream): self.stream = stream
    def write(self, data):
        self.stream.write(data[:len(data) // 2])
        self.stream.flush()
        os.fsync(self.stream.fileno())
        os._exit(75)
@contextmanager
def partial(*args, **kwargs):
    with original(*args, **kwargs) as stream:
        yield PartialWrite(stream)
os.fdopen = partial
atomic_json(Path(sys.argv[1]), {'synthetic': True, 'note': 'partial temporary intent'})
"""
    result = subprocess.run([sys.executable, "-c", code, str(marker)], timeout=10)
    assert result.returncode == 75
    assert not marker.exists()
    temporary = list(tmp_path.glob(".blender.active.json.*.tmp"))
    assert len(temporary) == 1
    preserved = temporary[0].read_bytes()
    with pytest.raises(ValueError):
        json.loads(preserved)
    atomic_json(marker, {"synthetic": "a new explicit publication"})
    assert temporary[0].read_bytes() == preserved


def test_more_than_512_completed_ledger_runs_do_not_block_new_work(setup):
    """Bulk synthetic history avoids 1,026 repeated full-ledger writes in this regression."""
    engine, job, adapter = setup
    state = dict(job)
    with engine._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT head FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        for index in range(513):
            run_revision = 2 + index * 2
            run_dir = engine.workspace.run_path(job["id"], run_revision, "create_demo")
            run_dir.mkdir(parents=True)
            for event_type, status in [
                ("action_started", "running"),
                ("action_finished", "executed_unverified"),
            ]:
                state.update(
                    revision=state["revision"] + 1,
                    status=status,
                    run_dir=engine.workspace.relative(run_dir),
                    last_action={"action": "create_demo", "parameters": {}},
                    result=None,
                )
                body = canonical(
                    {
                        "job_id": job["id"],
                        "revision": state["revision"],
                        "type": event_type,
                        "at": state["updated_at"],
                        "previous_hash": previous,
                        "state": state,
                    }
                )
                previous = hashlib.sha256(body.encode()).hexdigest()
                db.execute(
                    "INSERT INTO events VALUES (?,?,?,?)",
                    (job["id"], state["revision"], body, previous),
                )
        db.execute(
            "UPDATE jobs SET revision=?, snapshot=?, head=? WHERE id=?",
            (state["revision"], canonical(state), previous, job["id"]),
        )
    report = engine.recovery_report(job["id"])
    assert len(report["run_directories"]) == 513
    assert report["conflicts"] == []
    assert report["reconciliation"]["required"] is False
    fresh = engine.execute_action(job["id"], "create_demo", {}, report["revision"])
    assert fresh["status"] == "executed_unverified"
    assert adapter.calls == 1
    assert len(engine.recovery_report(job["id"])["run_directories"]) == 514


def test_unrecognized_directory_entries_are_bounded_without_deletion(setup):
    engine, job, adapter = setup
    parent = engine.workspace.resolve(f"jobs/{job['id']}/runs")
    parent.mkdir(parents=True)
    for index in range(513):
        (parent / f"unrecognized-{index:04d}.txt").write_bytes(b"preserve synthetic bytes")
    report = engine.recovery_report(job["id"])
    assert "run_directory_scan_limit" in report["conflicts"]
    assert report["reconciliation"]["required"] is True
    assert report["reconciliation"]["allowed"] is False
    with pytest.raises(ReconciliationRequired):
        engine.execute_action(job["id"], "create_demo", {}, 1)
    assert len(list(parent.iterdir())) == 513
    assert all(path.read_bytes() == b"preserve synthetic bytes" for path in parent.iterdir())
    assert adapter.calls == 0
