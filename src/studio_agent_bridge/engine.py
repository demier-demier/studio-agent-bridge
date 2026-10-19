"""Durable local job orchestration; native host adapters own geometric interpretation."""

import hashlib
import importlib
import io
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ACTIONS, PATH_KEYS, PATH_LIST_KEYS, ActionRequest, ActionResult, plan_workflow
from .workspace import Workspace, WorkspaceError, canonical, file_digest, host_lock

_STDOUT_LOCK = threading.Lock()


class RevisionConflict(RuntimeError):
    pass


class IntegrityError(RuntimeError):
    pass


class ReconciliationRequired(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict[str, Any]:
    """Read only documented environment variables; never collect ambient secrets."""
    mapping = {
        "blender_executable": "STUDIO_BLENDER_EXECUTABLE",
        "unreal_executable": "STUDIO_UNREAL_EXECUTABLE",
        "unreal_project": "STUDIO_UNREAL_PROJECT",
        "fusion_url": "STUDIO_FUSION_URL",
    }
    config = {key: os.environ[env] for key, env in mapping.items() if os.environ.get(env)}
    config["allow_scripts"] = os.environ.get("STUDIO_ALLOW_SCRIPTS") == "1"
    for key, env in {
        "blender_timeout_seconds": "STUDIO_BLENDER_TIMEOUT_SECONDS",
        "unreal_timeout_seconds": "STUDIO_UNREAL_TIMEOUT_SECONDS",
    }.items():
        if env in os.environ:
            config[key] = int(os.environ[env])
    return config


class Engine:
    def __init__(
        self,
        workspace: str | Path,
        config: dict | None = None,
        *,
        adapters=None,
        lock_dir: Path | None = None,
    ):
        self.workspace = Workspace(workspace)
        self.config = dict(config if config is not None else load_config())
        self.adapters = adapters or {}
        self.lock_dir = lock_dir or Path.home() / ".cache" / "studio-agent-bridge" / "locks"
        self.database = self.workspace.resolve(".studio/jobs.sqlite", allow_internal=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    snapshot TEXT NOT NULL,
                    head TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    revision INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    PRIMARY KEY(job_id, revision)
                );
            """)

    @contextmanager
    def _connect(self):
        # Resolve again to catch a replaced declared database symlink.
        self.workspace.resolve(".studio/jobs.sqlite", allow_internal=True)
        for suffix in ("-wal", "-shm", "-journal"):
            self.workspace.resolve(f".studio/jobs.sqlite{suffix}", allow_internal=True)
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _adapter(self, host: str):
        if host not in ACTIONS:
            raise ValueError("Unknown host.")
        return self.adapters.get(host) or importlib.import_module(
            f"studio_agent_bridge.adapters.{host}"
        )

    def _read(self, db, job_id: str) -> dict:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Job does not exist.")
        previous = "0" * 64
        last_state = None
        events = db.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY revision", (job_id,)
        ).fetchall()
        for revision, event in enumerate(events, start=1):
            body = json.loads(event["body"])
            digest = hashlib.sha256(event["body"].encode()).hexdigest()
            if (
                body["previous_hash"] != previous
                or digest != event["hash"]
                or body["revision"] != revision
                or event["revision"] != revision
                or body["job_id"] != job_id
            ):
                raise IntegrityError("Job event chain has been modified or truncated.")
            previous, last_state = digest, body["state"]
        state = json.loads(row["snapshot"])
        if (
            not events
            or previous != row["head"]
            or len(events) != row["revision"]
            or state != last_state
        ):
            raise IntegrityError("Job snapshot does not match its event chain.")
        return state

    def _append(self, job_id: str, event_type: str, patch: dict, expected_revision: int) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db, job_id)
            if state["revision"] != expected_revision:
                raise RevisionConflict("Job changed; inspect its latest revision before retrying.")
            previous = db.execute("SELECT head FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            state.update(patch)
            state["revision"] += 1
            state["updated_at"] = utc_now()
            body = canonical(
                {
                    "job_id": job_id,
                    "revision": state["revision"],
                    "type": event_type,
                    "at": state["updated_at"],
                    "previous_hash": previous,
                    "state": state,
                }
            )
            digest = hashlib.sha256(body.encode()).hexdigest()
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?)", (job_id, state["revision"], body, digest)
            )
            db.execute(
                "UPDATE jobs SET revision=?, snapshot=?, head=? WHERE id=?",
                (state["revision"], canonical(state), digest, job_id),
            )
            return state

    def create_job(self, name: str, host: str, goal: str) -> dict:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name):
            raise ValueError("Use a lowercase job name, 1–48 letters, digits and hyphens.")
        workflow = plan_workflow(goal, [host])
        now = utc_now()
        job_id = f"{name}-{uuid.uuid4().hex[:12]}"
        state = {
            "id": job_id,
            "name": name,
            "host": host,
            "goal": goal,
            "status": "ready",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "result": None,
            "last_action": None,
            "workflow": workflow,
        }
        body = canonical(
            {
                "job_id": job_id,
                "revision": 1,
                "type": "job_created",
                "at": now,
                "previous_hash": "0" * 64,
                "state": state,
            }
        )
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self._connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (job_id, 1, canonical(state), digest))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (job_id, 1, body, digest))
        return state

    def inspect_job(self, job_id: str) -> dict:
        with self._connect() as db:
            state = self._read(db, job_id)
            events = db.execute(
                "SELECT body, hash FROM events WHERE job_id=? ORDER BY revision", (job_id,)
            ).fetchall()
        return {
            **state,
            "ledger": {
                "valid": True,
                "events": len(events),
                "head": events[-1]["hash"],
                "meaning": "Sequence and snapshot consistency, not cryptographic authorship or geometric correctness.",
            },
            "event_types": [json.loads(event["body"])["type"] for event in events],
        }

    def list_artifacts(self, job_id: str) -> dict:
        with self._connect() as db:
            self._read(db, job_id)
            events = db.execute(
                "SELECT body FROM events WHERE job_id=? ORDER BY revision", (job_id,)
            ).fetchall()
        artifacts = {}
        for event in events:
            body = json.loads(event["body"])
            if body["type"] != "action_finished":
                continue
            for artifact in (body["state"].get("result") or {}).get("artifacts", []):
                artifacts[artifact["path"]] = artifact
        results = []
        for artifact in artifacts.values():
            try:
                path = self.workspace.resolve(artifact["path"])
                matches = (
                    path.is_file()
                    and path.stat().st_size == artifact["bytes"]
                    and file_digest(path) == artifact["sha256"]
                )
                results.append(
                    {**artifact, "integrity": "matches" if matches else "missing_or_modified"}
                )
            except (OSError, WorkspaceError):
                results.append({**artifact, "integrity": "missing_or_modified"})
        return {"job_id": job_id, "artifacts": results}

    def status(self) -> dict:
        hosts = {}
        for host in ACTIONS:
            try:
                hosts[host] = self._adapter(host).probe(self.config)
            except Exception as exc:
                hosts[host] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
            marker = self.lock_dir / f"{host}.active.json"
            hosts[host]["unresolved_action"] = marker.exists()
        with self._connect() as db:
            count = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
        return {
            "hosts": hosts,
            "jobs": count,
            "actions": ACTIONS,
            "scripts_enabled": self.config.get("allow_scripts", False),
            "trust": "Local trusted client. Script execution has the native application's user privileges; this is not a sandbox.",
        }

    def _validate_request(self, host: str, action: str, parameters: dict) -> dict:
        request = ActionRequest(action=action, parameters=parameters).model_dump()
        if action not in ACTIONS[host]:
            raise ValueError(f"Unsupported action for {host}.")
        if len(canonical(request).encode()) > 256_000:
            raise ValueError("Request exceeds the 256 KB limit; use a workspace script file.")
        if action in {"run_script", "execute_script"} and not self.config.get(
            "allow_scripts", False
        ):
            raise PermissionError(
                "Trusted Python scripts are disabled. Start the server with STUDIO_ALLOW_SCRIPTS=1 only for trusted clients."
            )
        for key, value in parameters.items():
            if key in PATH_KEYS:
                self.workspace.resolve(value)
            elif key in PATH_LIST_KEYS:
                if not isinstance(value, list) or len(value) > 2000:
                    raise ValueError(f"{key} must be a bounded list of workspace-relative paths.")
                for item in value:
                    self.workspace.resolve(item)
        project = self.config.get("unreal_project")
        if host == "unreal" and project:
            self.workspace.resolve(project)
        return request

    def _validate_result(self, raw: dict, action: str) -> dict:
        result = ActionResult.model_validate(raw).model_dump()
        seen = set()
        evidence = []
        native = []
        for artifact in result["artifacts"]:
            path = self.workspace.resolve(artifact["path"])
            if artifact["path"] in seen or not path.is_file():
                raise IntegrityError("Artifact is missing, duplicated, or is not a regular file.")
            seen.add(artifact["path"])
            if path.stat().st_size != artifact["bytes"] or file_digest(path) != artifact["sha256"]:
                raise IntegrityError("Artifact size or SHA-256 does not match the adapter result.")
            if artifact["kind"] in {"verification", "report", "evidence"}:
                if path.suffix != ".json" or path.stat().st_size > 16_000_000:
                    raise IntegrityError("Verification evidence must be bounded JSON.")
                value = json.loads(path.read_text())
                if not isinstance(value, dict) or not value:
                    raise IntegrityError(
                        "Verification evidence must contain measured observations."
                    )
                evidence.append(artifact)
            elif (
                artifact["kind"] not in {"log", "script", "request", "metadata"}
                and artifact["bytes"] > 0
            ):
                native.append(artifact)
        requires_native = action != "api_documentation"
        if result["status"] == "verified" and (
            not evidence or (requires_native and not native) or not result["observations"]
        ):
            result["status"] = "executed_unverified"
            result["limitations"].append(
                "Core verification gate: missing measured JSON evidence, a native/export artifact, or observations. No verified claim accepted."
            )
        return result

    def execute_action(
        self,
        job_id: str,
        action: str,
        parameters: dict | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        current = self.inspect_job(job_id)
        request = self._validate_request(current["host"], action, parameters or {})
        expected = current["revision"] if expected_revision is None else expected_revision
        host = current["host"]
        adapter = self._adapter(host)
        with host_lock(host, self.lock_dir):
            marker = self.lock_dir / f"{host}.active.json"
            if marker.exists() or marker.is_symlink():
                raise ReconciliationRequired(
                    "An earlier action has an unresolved external outcome. Inspect the native application and reconcile its job before continuing."
                )
            current = self.inspect_job(job_id)
            if current["revision"] != expected:
                raise RevisionConflict("Job changed; inspect its latest revision before retrying.")
            if current["status"] in {"running", "needs_review"}:
                raise ReconciliationRequired(
                    "This job requires explicit reconciliation; it will not be replayed automatically."
                )
            run_dir = self.workspace.run_dir(job_id, expected + 1, action)
            with marker.open("x") as stream:
                stream.write(
                    canonical(
                        {
                            "job_id": job_id,
                            "workspace": str(self.workspace.root),
                            "run_dir": self.workspace.relative(run_dir),
                            "started_at": utc_now(),
                        }
                    )
                )
            started = self._append(
                job_id,
                "action_started",
                {
                    "status": "running",
                    "last_action": request,
                    "result": None,
                    "run_dir": self.workspace.relative(run_dir),
                },
                expected,
            )
            captured = io.StringIO()
            try:
                # Protect MCP stdout even if an adapter accidentally prints.
                with _STDOUT_LOCK, redirect_stdout(captured):
                    raw = adapter.execute(self.workspace.root, request, run_dir, self.config)
                result = self._validate_result(raw, action)
                if captured.getvalue():
                    path = run_dir / "adapter-stdout.log"
                    path.write_text(captured.getvalue())
                    result["artifacts"].append(
                        {
                            "path": self.workspace.relative(path),
                            "kind": "log",
                            "sha256": file_digest(path),
                            "bytes": path.stat().st_size,
                        }
                    )
                finished = self._append(
                    job_id,
                    "action_finished",
                    {
                        "status": result["status"],
                        "result": result,
                        "verification_scope": "documentation_query"
                        if action == "api_documentation"
                        else "native_action",
                    },
                    started["revision"],
                )
                marker.unlink()
                return finished
            except Exception as exc:
                result = {
                    "status": "failed",
                    "summary": f"Orchestration error: {type(exc).__name__}: {exc}",
                    "artifacts": [],
                    "observations": {},
                    "limitations": [
                        "The native action may have changed external state. Inspect the host and reconcile before another action; no automatic retry occurred."
                    ],
                }
                self._append(
                    job_id,
                    "action_uncertain",
                    {"status": "needs_review", "result": result},
                    started["revision"],
                )
                return self.inspect_job(job_id)

    def reconcile_job(
        self, job_id: str, notes: str, host_is_idle: bool, expected_revision: int
    ) -> dict:
        """Record a trusted operator's inspection, never pretend to roll back native state."""
        if (
            host_is_idle is not True
            or not isinstance(notes, str)
            or not 20 <= len(notes.strip()) <= 8000
        ):
            raise ValueError(
                "Reconciliation requires explicit host-is-idle confirmation and 20–8000 characters describing the observed outcome."
            )
        current = self.inspect_job(job_id)
        with host_lock(current["host"], self.lock_dir):
            marker = self.lock_dir / f"{current['host']}.active.json"
            if marker.is_symlink():
                raise WorkspaceError("Unresolved action marker cannot be a symlink.")
            if marker.exists():
                value = json.loads(marker.read_text())
                if value["job_id"] != job_id or value["workspace"] != str(self.workspace.root):
                    raise ReconciliationRequired(
                        "The unresolved marker belongs to another job/workspace."
                    )
            elif current["status"] not in {"running", "needs_review"}:
                raise ValueError("This job has no unresolved action.")
            state = self._append(
                job_id,
                "operator_reconciled",
                {"status": "ready", "reconciliation": notes, "result": None},
                expected_revision,
            )
            marker.unlink(missing_ok=True)
            return state
