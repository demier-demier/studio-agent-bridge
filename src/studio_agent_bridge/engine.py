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

from .models import (
    ACTIONS,
    HOST_PATH_KEYS,
    PATH_KEYS,
    PATH_LIST_KEYS,
    ActionRequest,
    ActionResult,
    plan_workflow,
)
from .workspace import (
    Workspace,
    WorkspaceError,
    atomic_json,
    canonical,
    file_digest,
    host_lock,
    read_bounded_json,
    sync_directory,
)

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
        db.execute("PRAGMA synchronous=FULL")
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
        # sqlite3's connection context does not begin a transaction for SELECT.
        # Keep snapshot, chain, and subsequent inspection queries on one WAL snapshot.
        if not db.in_transaction:
            db.execute("BEGIN")
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

    @staticmethod
    def _run_identity(value: object, job_id: str, host: str) -> tuple[int, str] | None:
        if not isinstance(value, str):
            return None
        match = re.fullmatch(
            rf"jobs/{re.escape(job_id)}/runs/([0-9]{{6,10}})-([a-z_]{{1,80}})", value
        )
        if not match or match[2] not in ACTIONS[host]:
            return None
        revision = int(match[1])
        if not 1 <= revision <= 1_000_000_000 or match[1] != f"{revision:06d}":
            return None
        return revision, match[2]

    def _validate_intent(self, value: dict, host: str) -> dict:
        """Validate the whole intent before using any ownership claim, including legacy markers."""
        legacy = {"job_id", "workspace", "run_dir", "started_at"}
        versioned = {
            "schema_version",
            "intent_id",
            "job_id",
            "workspace",
            "host",
            "action",
            "expected_revision",
            "run_dir",
            "request_sha256",
            "created_at",
        }
        is_legacy = set(value) == legacy
        if not is_legacy and (
            set(value) != versioned
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
        ):
            raise WorkspaceError("Malformed host intent schema; ownership is unknown.")
        job_id, workspace = value.get("job_id"), value.get("workspace")
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-z0-9-]{1,100}", job_id):
            raise WorkspaceError("Malformed host intent identifier; ownership is unknown.")
        if (
            not isinstance(workspace, str)
            or not 1 <= len(workspace) <= 4096
            or "\x00" in workspace
            or "\\" in workspace
            or not Path(workspace).is_absolute()
            or ".." in Path(workspace).parts
        ):
            raise WorkspaceError("Malformed host intent workspace; ownership is unknown.")
        run_identity = self._run_identity(value.get("run_dir"), job_id, host)
        if run_identity is None:
            raise WorkspaceError("Malformed host intent run binding; ownership is unknown.")
        timestamp = value.get("started_at" if is_legacy else "created_at")
        if not isinstance(timestamp, str) or len(timestamp) > 80:
            raise WorkspaceError("Malformed host intent timestamp; ownership is unknown.")
        datetime.fromisoformat(timestamp)
        if not is_legacy and (
            value["host"] != host
            or value["action"] != run_identity[1]
            or type(value["expected_revision"]) is not int
            or value["expected_revision"] + 1 != run_identity[0]
            or not isinstance(value["intent_id"], str)
            or re.fullmatch(r"[a-f0-9]{32}", value["intent_id"]) is None
            or not isinstance(value["request_sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", value["request_sha256"]) is None
        ):
            raise WorkspaceError("Malformed host intent action binding; ownership is unknown.")
        return {**value, "protocol": "legacy" if is_legacy else "v1"}

    def _marker_observation(self, host: str, job_id: str) -> tuple[dict, dict | None]:
        marker = self.lock_dir / f"{host}.active.json"
        observation = {
            "file": marker.name,
            "scope": "host_lock_directory",
            "state": "absent",
            "ownership": "none",
            "protocol": None,
            "run_dir": None,
        }
        if marker.is_symlink():
            return {**observation, "state": "unsafe", "ownership": "unknown"}, None
        if self.lock_dir.is_symlink():
            return {**observation, "state": "unsafe", "ownership": "unknown"}, None
        try:
            value = self._validate_intent(read_bounded_json(marker), host)
        except FileNotFoundError:
            return observation, None
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return {**observation, "state": "malformed", "ownership": "unknown"}, None
        ownership = "this_job"
        if value["workspace"] != str(self.workspace.root):
            ownership = "other_workspace"
        elif value["job_id"] != job_id:
            ownership = "other_job"
        return {
            **observation,
            "state": "valid",
            "ownership": ownership,
            "protocol": value["protocol"],
            "run_dir": value["run_dir"] if ownership == "this_job" else None,
        }, value

    def recovery_report(self, job_id: str) -> dict:
        """Read-only observations, never evidence that a native host is idle.

        Ledger reads share one SQLite snapshot. Filesystem observations can change
        independently; reconciliation always repeats them under the host lock.
        """
        with self._connect() as db:
            current = self._read(db, job_id)
            rows = db.execute(
                "SELECT body, hash FROM events WHERE job_id=? ORDER BY revision", (job_id,)
            ).fetchall()
        events = [json.loads(row["body"]) for row in rows]
        host = current["host"]
        marker, intent = self._marker_observation(host, job_id)
        runs: dict[str, dict] = {}
        conflicts = []
        acknowledged = set(current.get("reconciled_run_dirs", []))
        action_event = None

        def record_run(value, provenance, unresolved):
            identity = self._run_identity(value, job_id, host)
            if identity is None:
                conflicts.append("invalid_recorded_run_binding")
                return
            runs[value] = {
                "path": value,
                "revision": identity[0],
                "action": identity[1],
                "provenance": provenance,
                "unresolved": unresolved and value not in acknowledged,
            }

        for event in events:
            kind, state = event["type"], event["state"]
            if kind in {"action_started", "action_finished", "action_uncertain"}:
                action_event = event
                if state.get("run_dir"):
                    record_run(state["run_dir"], "ledger", kind != "action_finished")
            elif kind == "operator_reconciled":
                for path in state.get("reconciled_run_dirs", []):
                    acknowledged.add(path)
                if state.get("run_dir"):
                    acknowledged.add(state["run_dir"])
        for path in acknowledged:
            if path in runs:
                runs[path]["unresolved"] = False
        if marker["ownership"] == "this_job":
            path = intent["run_dir"]
            if path not in runs:
                record_run(path, "host_intent", True)
            marker["matches_ledger"] = bool(
                action_event
                and action_event["state"].get("run_dir") == path
                and (
                    intent["protocol"] == "legacy"
                    or action_event["state"].get("intent_id") == intent["intent_id"]
                )
            )
            if intent["protocol"] == "v1":
                if intent["expected_revision"] > current["revision"]:
                    conflicts.append("intent_revision_ahead_of_ledger")
                if action_event and action_event["state"].get("run_dir") == path:
                    if (
                        action_event["state"].get("intent_id") != intent["intent_id"]
                        or hashlib.sha256(
                            canonical(action_event["state"].get("last_action")).encode()
                        ).hexdigest()
                        != intent["request_sha256"]
                    ):
                        conflicts.append("intent_ledger_binding_conflict")
        try:
            parent = self.workspace.resolve(f"jobs/{job_id}/runs")
            if parent.exists():
                unknown_entries = 0
                with os.scandir(parent) as children:
                    for child in children:
                        relative = f"jobs/{job_id}/runs/{child.name}"
                        if relative in runs:
                            continue
                        if relative in acknowledged and self._run_identity(relative, job_id, host):
                            record_run(relative, "reconciled_job_path", False)
                            continue
                        # Bound unexplained evidence, not legitimate ledger history.
                        unknown_entries += 1
                        if unknown_entries > 512:
                            conflicts.append("run_directory_scan_limit")
                            break
                        if self._run_identity(relative, job_id, host):
                            record_run(relative, "unreferenced_job_path", True)
        except (OSError, WorkspaceError):
            conflicts.append("run_parent_unreadable_or_unsafe")
        for value in runs.values():
            try:
                path = self.workspace.resolve(value["path"])
                value["state"] = (
                    "directory"
                    if path.is_dir()
                    else "non_directory"
                    if path.exists()
                    else "missing"
                )
                value["has_entries"] = None
                if path.is_dir():
                    with os.scandir(path) as entries:
                        value["has_entries"] = next(entries, None) is not None
            except (OSError, WorkspaceError):
                value["state"], value["has_entries"] = "unsafe_or_unreadable", None
                conflicts.append("run_path_unreadable_or_unsafe")
        unresolved = any(run["unresolved"] for run in runs.values())
        required = (
            marker["state"] != "absent"
            or current["status"] in {"running", "needs_review"}
            or unresolved
            or bool(conflicts)
        )
        allowed = required and not conflicts and marker["ownership"] in {"none", "this_job"}
        if marker["ownership"] in {"other_job", "other_workspace"}:
            reason = "The host intent belongs to another job or workspace; reconcile its owner."
        elif marker["ownership"] == "unknown":
            reason = "The host marker is malformed or unsafe. Ownership is unknown; preserve it for manual investigation."
        elif conflicts:
            reason = "Recovery evidence conflicts or cannot be safely inspected; preserve it for manual investigation."
        elif required:
            reason = "Explicit operator inspection and host-is-idle confirmation are required. No action will be replayed."
        else:
            reason = "No unresolved bridge evidence was observed; this does not establish native host idleness."
        return {
            "schema_version": 1,
            "job_id": job_id,
            "host": host,
            "status": current["status"],
            "revision": current["revision"],
            "ledger": {"valid": True, "events": len(rows), "head": rows[-1]["hash"]},
            "host_marker": marker,
            "run_directories": sorted(runs.values(), key=lambda value: value["path"]),
            "event_state": {
                "last_type": events[-1]["type"],
                "action_event_type": action_event["type"] if action_event else None,
                "action_event_revision": action_event["revision"] if action_event else None,
                "preparation_failed": bool(
                    action_event
                    and action_event["state"].get("dispatch_phase") == "preparation_failed"
                ),
            },
            "conflicts": sorted(set(conflicts)),
            "reconciliation": {
                "required": required,
                "allowed": allowed,
                "reason": reason,
                "explicit_host_idle_required": True,
            },
            "native_host_idle": "unknown",
            "automatic_replay": False,
        }

    def _publish_intent(self, marker: Path, intent: dict) -> None:
        self._validate_intent(intent, intent["host"])
        atomic_json(marker, intent)

    @staticmethod
    def _clear_intent(marker: Path) -> None:
        marker.unlink(missing_ok=True)
        sync_directory(marker.parent)

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
            if key in PATH_KEYS or key in HOST_PATH_KEYS.get(host, set()):
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
            recovery = self.recovery_report(job_id)
            if recovery["reconciliation"]["required"]:
                raise ReconciliationRequired(recovery["reconciliation"]["reason"])
            run_path = self.workspace.run_path(job_id, expected + 1, action)
            relative_run = self.workspace.relative(run_path)
            intent = {
                "schema_version": 1,
                "intent_id": uuid.uuid4().hex,
                "job_id": job_id,
                "workspace": str(self.workspace.root),
                "host": host,
                "action": action,
                "expected_revision": expected,
                "run_dir": relative_run,
                "request_sha256": hashlib.sha256(canonical(request).encode()).hexdigest(),
                "created_at": utc_now(),
            }
            binding = {
                "last_action": request,
                "run_dir": relative_run,
                "intent_id": intent["intent_id"],
            }
            captured = io.StringIO()
            native_attempted = False
            try:
                # Cross-workspace guard is durable before creating any run directory.
                self._publish_intent(marker, intent)
                started = self._append(
                    job_id,
                    "action_started",
                    {**binding, "status": "running", "result": None, "dispatch_phase": "admitted"},
                    expected,
                )
                run_dir = self.workspace.run_dir(job_id, expected + 1, action)
                # Protect MCP stdout even if an adapter accidentally prints.
                with _STDOUT_LOCK, redirect_stdout(captured):
                    native_attempted = True
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
            except Exception as exc:
                observation, persisted_intent = self._marker_observation(host, job_id)
                if (
                    observation["ownership"] != "this_job"
                    or not persisted_intent
                    or persisted_intent.get("intent_id") != intent["intent_id"]
                ):
                    # Failure before publication cannot have reached a native adapter.
                    # Partial temp files and any unexpected marker remain untouched.
                    if observation["state"] == "absent" and not native_attempted:
                        raise WorkspaceError(
                            "Host intent could not be published. No native dispatch was attempted; unpublished temporary files were preserved."
                        ) from exc
                    raise ReconciliationRequired(
                        "Host intent publication did not complete. No automatic retry occurred; inspect recovery evidence and preserve temporary files."
                    ) from exc
                latest = self.inspect_job(job_id)
                if (
                    latest["event_types"][-1] == "action_finished"
                    and latest.get("intent_id") == intent["intent_id"]
                ):
                    return {**latest, "recovery_required": True}
                result = {
                    "status": "failed",
                    "summary": f"{'Orchestration' if native_attempted else 'Preparation'} error: {type(exc).__name__}. Inspect recovery evidence before another action.",
                    "artifacts": [],
                    "observations": {"native_dispatch_attempted": native_attempted},
                    "limitations": [
                        "The native action may have changed external state. Inspect the host and reconcile before another action; no automatic retry occurred."
                        if native_attempted
                        else "Preparation failed before calling the native adapter. Intent and any partial run directory were preserved; explicit reconciliation is required."
                    ],
                }
                self._append(
                    job_id,
                    "action_uncertain",
                    {
                        **binding,
                        "status": "needs_review",
                        "result": result,
                        "dispatch_phase": "native_uncertain"
                        if native_attempted
                        else "preparation_failed",
                    },
                    latest["revision"],
                )
                return self.inspect_job(job_id)
            try:
                self._clear_intent(marker)
            except OSError:
                # Native evidence is already committed. Do not overwrite it with a failed result.
                return {**finished, "recovery_required": True}
            return finished

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
            current = self.inspect_job(job_id)
            marker = self.lock_dir / f"{current['host']}.active.json"
            report = self.recovery_report(job_id)
            if report["revision"] != expected_revision:
                raise RevisionConflict("Job changed; inspect its latest revision before retrying.")
            if not report["reconciliation"]["required"]:
                raise ValueError("This job has no unresolved action.")
            if not report["reconciliation"]["allowed"]:
                raise ReconciliationRequired(report["reconciliation"]["reason"])
            acknowledged = set(current.get("reconciled_run_dirs", []))
            acknowledged.update(
                run["path"] for run in report["run_directories"] if run["unresolved"]
            )
            if report["host_marker"]["run_dir"]:
                acknowledged.add(report["host_marker"]["run_dir"])
            state = self._append(
                job_id,
                "operator_reconciled",
                {
                    "status": "ready",
                    "reconciliation": notes,
                    "result": None,
                    "reconciled_run_dirs": sorted(acknowledged),
                    "dispatch_phase": "operator_reconciled",
                },
                expected_revision,
            )
            self._clear_intent(marker)
            return state
