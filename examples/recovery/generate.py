#!/usr/bin/env python3
"""Observe recovery on the real engine using explicitly synthetic host adapters.

This example never starts a native application. Every workspace and host-lock
directory is created inside one temporary directory and removed after recording.
The engine, durable ledger, host intent, interruption, and restart are real.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from studio_agent_bridge.engine import Engine, ReconciliationRequired  # noqa: E402
from studio_agent_bridge.workspace import WorkspaceError, file_digest  # noqa: E402

SCHEMA_VERSION = "1.0"
EFFECT_EXIT_CODE = 73
INTENT_EXIT_CODE = 74
HOST = "blender"
EFFECT_CONTENT = b"SYNTHETIC RECOVERY EFFECT. This is not native application evidence.\n"


def _durable_write(path: Path, content: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class SyntheticAdapter:
    """Only writes owned fixture files. It never imports or invokes a host adapter."""

    def __init__(self, case_root: Path, *, interrupt_after_effect: bool = False):
        self.case_root = case_root
        self.interrupt_after_effect = interrupt_after_effect

    @staticmethod
    def probe(config: dict) -> dict:
        return {"available": True, "synthetic": True, "native_application_started": False}

    def execute(self, workspace: Path, request: dict, run_dir: Path, config: dict) -> dict:
        invocation = {
            "synthetic": True,
            "action": request["action"],
            "run_dir": run_dir.relative_to(workspace).as_posix(),
        }
        with (self.case_root / "adapter-calls.jsonl").open("a", encoding="utf-8") as audit:
            audit.write(json.dumps(invocation, sort_keys=True) + "\n")
            audit.flush()
            os.fsync(audit.fileno())
        effect = run_dir / "synthetic-effect.txt"
        _durable_write(effect, EFFECT_CONTENT)
        if self.interrupt_after_effect:
            # Bypass Python finally/exception handlers, like an abrupt worker exit.
            os._exit(EFFECT_EXIT_CODE)
        evidence = run_dir / "synthetic-observations.json"
        measured = {
            "synthetic": True,
            "effect_bytes": effect.stat().st_size,
            "effect_sha256": file_digest(effect),
            "native_application_started": False,
        }
        _durable_write(evidence, (json.dumps(measured, sort_keys=True) + "\n").encode())
        return {
            "status": "executed_unverified",
            "summary": "Synthetic adapter completed an owned file effect; no native action ran.",
            "artifacts": [
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "kind": kind,
                    "sha256": file_digest(path),
                    "bytes": path.stat().st_size,
                }
                for path, kind in [(effect, "synthetic_effect"), (evidence, "evidence")]
            ],
            "observations": measured,
            "limitations": [
                "Synthetic host adapter; this does not establish native geometry or editor behavior."
            ],
        }


class InterruptAfterIntentEngine(Engine):
    """Inject a crash after the engine's atomic intent publication has returned."""

    def _publish_intent(self, marker: Path, intent: dict) -> None:
        super()._publish_intent(marker, intent)
        os._exit(INTENT_EXIT_CODE)


def _engine(case_root: Path, *, interrupt_after_effect: bool = False) -> Engine:
    return Engine(
        case_root / "workspace",
        {},
        adapters={HOST: SyntheticAdapter(case_root, interrupt_after_effect=interrupt_after_effect)},
        lock_dir=case_root / "locks",
    )


def _new_case(parent: Path, name: str) -> tuple[Path, Engine, dict]:
    case_root = parent / name
    case_root.mkdir()
    engine = _engine(case_root)
    job = engine.create_job(name, HOST, "Observe durable recovery with a synthetic host adapter.")
    return case_root, engine, job


def _calls(case_root: Path) -> int:
    path = case_root / "adapter-calls.jsonl"
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line])


def _effects(case_root: Path) -> list[dict]:
    workspace = case_root / "workspace"
    return [
        {
            "path": path.relative_to(workspace).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": file_digest(path),
        }
        for path in sorted(workspace.glob("jobs/*/runs/*/synthetic-effect.txt"))
    ]


def _safe(value: Any, case_root: Path) -> Any:
    """Redact temporary roots defensively; the engine report should already be relative."""
    if isinstance(value, dict):
        return {key: _safe(item, case_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item, case_root) for item in value]
    if isinstance(value, str):
        text = value.replace(str(case_root), "<isolated-case>")
        # Do not export an unexpected absolute path, even in an error message.
        if text.startswith("/") or "/Users/" in text or "/home/" in text:
            return "<absolute path omitted>"
        return text
    return value


def _step(
    engine: Engine,
    job_id: str,
    case_root: Path,
    title: str,
    detail: str,
    *,
    status: str | None = None,
) -> dict:
    report = engine.recovery_report(job_id)
    return {
        "title": title,
        "detail": detail,
        "status": status or report["status"],
        "revision": report["revision"],
        "adapter_calls": _calls(case_root),
        "effects": _effects(case_root),
        "report": _safe(report, case_root),
    }


def _blocked_retry(engine: Engine, job_id: str) -> str:
    revision = engine.inspect_job(job_id)["revision"]
    try:
        engine.execute_action(job_id, "create_demo", {}, revision)
    except ReconciliationRequired as exc:
        return type(exc).__name__
    raise AssertionError("The interrupted action was retried instead of being blocked.")


def _interrupt_child(case_root: Path, job_id: str, mode: str) -> int:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            mode,
            "--case-root",
            str(case_root),
            "--job-id",
            job_id,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    expected = EFFECT_EXIT_CODE if mode == "after-effect" else INTENT_EXIT_CODE
    if completed.returncode != expected:
        raise AssertionError(
            f"Synthetic child exit was {completed.returncode}; expected {expected}. "
            f"{_safe(completed.stderr[-2000:], case_root)}"
        )
    # subprocess.run has waited and reaped the child. In this example the
    # synthetic adapter is entirely in that process; no external host was spawned.
    return completed.returncode


def _clean_case(parent: Path) -> dict:
    root, engine, job = _new_case(parent, "clean-action")
    steps = [_step(engine, job["id"], root, "Job created", "A fresh engine job is ready; no adapter has run.")]
    result = engine.execute_action(job["id"], "create_demo", {}, job["revision"])
    steps.append(
        _step(
            engine,
            job["id"],
            root,
            "Synthetic action completed",
            "The real engine recorded start and finish events, validated the owned files, and cleared its intent.",
        )
    )
    reopened = _engine(root)
    before = reopened.inspect_job(job["id"])
    steps.append(
        _step(reopened, job["id"], root, "Restart and inspect", "A new Engine instance read the persisted ledger without dispatching another action.")
    )
    after = reopened.inspect_job(job["id"])
    report = steps[-1]["report"]
    return {
        "id": "clean-action",
        "title": "A completed action stays completed",
        "description": "The control case distinguishes a clean finish from an uncertain external outcome.",
        "steps": steps,
        "checks": {
            "one_adapter_dispatch": _calls(root) == 1,
            "one_owned_effect": len(_effects(root)) == 1,
            "completed_at_revision_three": result["revision"] == 3,
            "synthetic_result_not_claimed_native_verified": result["status"] == "executed_unverified",
            "intent_cleared": not (engine.lock_dir / f"{HOST}.active.json").exists(),
            "ledger_valid_after_restart": after["ledger"]["valid"] is True,
            "inspection_preserves_revision": before["revision"] == after["revision"],
            "no_reconciliation_needed": report["reconciliation"]["required"] is False,
            "no_automatic_replay": report["automatic_replay"] is False,
        },
        "limitations": ["Only the orchestration lifecycle and synthetic files are exercised."],
    }


def _interrupted_case(parent: Path, *, after_intent: bool) -> dict:
    case_id = "intent-window" if after_intent else "effect-then-crash"
    root, engine, job = _new_case(parent, case_id)
    steps = [_step(engine, job["id"], root, "Job created", "The job is ready at revision 1.")]
    exit_code = _interrupt_child(root, job["id"], "after-intent" if after_intent else "after-effect")
    reopened = _engine(root)
    interrupted = reopened.inspect_job(job["id"])
    original_effects = _effects(root)
    calls_at_crash = _calls(root)
    description = (
        "The child exited immediately after atomic host-intent publication, before action_started and adapter dispatch."
        if after_intent
        else "The child persisted a synthetic effect, then exited inside the adapter before the engine could record completion."
    )
    steps.append(
        _step(
            reopened,
            job["id"],
            root,
            "Abrupt child exit; engine restarted",
            f"{description} The parent waited for and reaped exit code {exit_code}.",
            status="interrupted",
        )
    )
    interrupted_report = steps[-1]["report"]
    before_retry = reopened.inspect_job(job["id"])
    exception_name = _blocked_retry(reopened, job["id"])
    after_retry = reopened.inspect_job(job["id"])
    calls_after_retry = _calls(root)
    steps.append(
        _step(
            reopened,
            job["id"],
            root,
            "Retry rejected",
            f"execute_action raised {exception_name}; the adapter count and ledger revision did not advance.",
            status="blocked",
        )
    )
    report_before = reopened.inspect_job(job["id"])
    steps.append(
        _step(
            reopened,
            job["id"],
            root,
            "Read the recovery report",
            "The report exposes owned intent and persisted evidence. It does not infer that a native host is idle or replay an action.",
        )
    )
    report_after = reopened.inspect_job(job["id"])
    notes = (
        "The parent waited for the deliberately terminated synthetic child and confirmed it was reaped. "
        "No native application or other process was spawned. Inspected the owned fixture evidence and "
        "acknowledged the uncertain action; this confirmation applies only to this synthetic process."
    )
    reconciled = reopened.reconcile_job(job["id"], notes, True, report_after["revision"])
    calls_after_reconciliation = _calls(root)
    preserved_after_reconciliation = _effects(root) == original_effects
    steps.append(
        _step(
            reopened,
            job["id"],
            root,
            "Explicit operator reconciliation",
            "After confirming the synthetic child had stopped, the caller supplied host_is_idle=True. The engine recorded reconciliation without dispatching the adapter or deleting prior evidence.",
        )
    )
    resumed = reopened.execute_action(job["id"], "create_demo", {}, reconciled["revision"])
    steps.append(
        _step(
            reopened,
            job["id"],
            root,
            "A new action is explicitly requested",
            "A separate execute_action call uses the reconciled revision and writes to a fresh run directory. This is a new action, not an automatic replay.",
        )
    )
    final_effects = _effects(root)
    expected_calls_at_crash = 0 if after_intent else 1
    expected_revision_at_crash = 1 if after_intent else 2
    final_paths = {item["path"] for item in final_effects}
    old_paths = {item["path"] for item in original_effects}
    return {
        "id": case_id,
        "title": "Intent survives the pre-dispatch crash window" if after_intent else "An effect survives a terminated worker",
        "description": description,
        "steps": steps,
        "checks": {
            "child_exit_observed": exit_code == (INTENT_EXIT_CODE if after_intent else EFFECT_EXIT_CODE),
            "expected_adapter_count_at_crash": calls_at_crash == expected_calls_at_crash,
            "expected_ledger_revision_at_crash": interrupted["revision"] == expected_revision_at_crash,
            "crash_status_preserved": interrupted["status"] == ("ready" if after_intent else "running"),
            "owned_marker_reported": interrupted_report["host_marker"]["ownership"] == "this_job",
            "reconciliation_required": interrupted_report["reconciliation"]["required"] is True,
            "owned_reconciliation_allowed": interrupted_report["reconciliation"]["allowed"] is True,
            "retry_did_not_dispatch": calls_after_retry == calls_at_crash,
            "retry_preserved_revision": before_retry["revision"] == after_retry["revision"],
            "inspection_preserved_revision": report_before["revision"] == report_after["revision"],
            "no_automatic_replay": interrupted_report["automatic_replay"] is False,
            "native_host_liveness_stays_unknown": interrupted_report["native_host_idle"] == "unknown",
            "operator_confirmation_explicit": interrupted_report["reconciliation"]["explicit_host_idle_required"] is True,
            "reconciliation_did_not_dispatch": calls_after_reconciliation == calls_at_crash,
            "reconciliation_preserved_effect_files": preserved_after_reconciliation,
            "new_action_is_separate_dispatch": _calls(root) == calls_at_crash + 1,
            "new_effect_path_is_distinct": len(final_paths - old_paths) == 1,
            "old_effects_remain_intact": all(item in final_effects for item in original_effects),
            "final_ledger_valid": reopened.inspect_job(job["id"])["ledger"]["valid"] is True,
            "new_action_completed": resumed["status"] == "executed_unverified",
        },
        "limitations": [
            "The injected process exit is real, but the adapter and its external effect are synthetic.",
            "The parent can confirm this synthetic child stopped because it waited for its exit; this does not demonstrate native application liveness detection.",
            "Reconciliation acknowledges uncertainty. It does not roll back the synthetic effect or prove a real-world action succeeded.",
        ],
    }


def _malformed_case(parent: Path) -> dict:
    root, engine, job = _new_case(parent, "unknown-marker")
    engine.lock_dir.mkdir(parents=True, exist_ok=True)
    marker = engine.lock_dir / f"{HOST}.active.json"
    malformed = b'{"job_id": "unknown", "workspace": '
    _durable_write(marker, malformed)
    before = engine.inspect_job(job["id"])
    steps = [
        _step(
            engine,
            job["id"],
            root,
            "Malformed marker discovered",
            "A deliberately truncated synthetic marker is present. Its ownership cannot be established from valid protocol data.",
        )
    ]
    report = steps[-1]["report"]
    exception_name = _blocked_retry(engine, job["id"])
    steps.append(
        _step(engine, job["id"], root, "Action rejected", f"execute_action raised {exception_name}; no adapter ran.", status="blocked")
    )
    rejected = False
    try:
        engine.reconcile_job(
            job["id"],
            "Synthetic example only: the caller cannot establish ownership of this malformed marker.",
            True,
            before["revision"],
        )
    except (ReconciliationRequired, WorkspaceError, ValueError) as exc:
        rejected = True
        refusal = type(exc).__name__
    if not rejected:
        raise AssertionError("Reconciliation accepted a marker with unknown ownership.")
    steps.append(
        _step(
            engine,
            job["id"],
            root,
            "Reconciliation remains blocked",
            f"Even with explicit idle confirmation, reconcile_job raised {refusal}; the marker was not removed or assigned to this job.",
            status="blocked",
        )
    )
    after = engine.inspect_job(job["id"])
    return {
        "id": "unknown-marker",
        "title": "Unknown ownership does not become permission",
        "description": "A malformed host marker cannot be attributed to the current job, so the example leaves it unresolved.",
        "steps": steps,
        "checks": {
            "ownership_unknown": report["host_marker"]["ownership"] == "unknown",
            "reconciliation_required": report["reconciliation"]["required"] is True,
            "reconciliation_not_allowed": report["reconciliation"]["allowed"] is False,
            "explicit_reconciliation_rejected": rejected,
            "no_adapter_dispatched": _calls(root) == 0,
            "marker_bytes_preserved": marker.read_bytes() == malformed,
            "ledger_revision_preserved": before["revision"] == after["revision"],
            "no_owned_effect_created": not _effects(root),
            "no_automatic_replay": report["automatic_replay"] is False,
        },
        "limitations": [
            "The marker is intentionally malformed fixture data. The example does not infer its owner or propose automatic deletion.",
            "The isolated temporary directory is removed only after evidence collection ends; unresolved state remains blocked throughout the observed case.",
        ],
    }


def _source_hashes() -> dict[str, str]:
    return {
        path: file_digest(REPOSITORY / path)
        for path in [
            "examples/recovery/generate.py",
            "src/studio_agent_bridge/engine.py",
            "src/studio_agent_bridge/workspace.py",
        ]
    }


def observe_recovery() -> dict:
    """Execute every case and return measured evidence, or fail on an unmet check."""
    if not hasattr(Engine, "recovery_report") or not hasattr(Engine, "_publish_intent"):
        raise RuntimeError("This example requires the engine recovery_report and atomic-intent protocol.")
    before_hashes = _source_hashes()
    with tempfile.TemporaryDirectory(prefix="studio-synthetic-recovery-") as temporary:
        parent = Path(temporary)
        cases = [
            _clean_case(parent),
            _interrupted_case(parent, after_intent=False),
            _interrupted_case(parent, after_intent=True),
            _malformed_case(parent),
        ]
        for case in cases:
            for name, passed in case["checks"].items():
                if passed is not True:
                    raise AssertionError(f"Observed recovery check failed: {case['id']}.{name}")
    if _source_hashes() != before_hashes:
        raise RuntimeError("Engine or example source changed during evidence collection; run again.")
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "kind": "observed-synthetic-adapter-execution",
            "generator": "examples/recovery/generate.py",
            "engine_source": "src/studio_agent_bridge/engine.py",
            "source_sha256": before_hashes,
            "python_version": ".".join(str(part) for part in sys.version_info[:3]),
            "native_apps_used": False,
            "cloud_used": False,
            "isolated_workspaces_and_lock_directories": True,
        },
        "cases": cases,
        "limitations": [
            "These are observed engine outcomes with explicitly synthetic host adapters, not native application or geometry verification.",
            "The exporter uses abrupt child-process exits and isolated temporary files; no native application, external service, or cloud model is invoked.",
            "The engine reports native host idleness as unknown. Operator reconciliation is an explicit assertion, not a liveness detector or rollback.",
            "The snapshot is one execution of these scenarios, not an exhaustive crash-point proof or a concurrency stress test.",
        ],
    }
    encoded = json.dumps(report, sort_keys=True, allow_nan=False)
    for forbidden in (str(REPOSITORY), "/Users/", "/home/", "studio-synthetic-recovery-"):
        if forbidden in encoded:
            raise AssertionError("Export would contain a local absolute path; no report was written.")
    return report


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        _durable_write(temporary, content.encode())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Execute and assert the cases without writing report files")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "examples/recovery/observed-recovery.json")
    parser.add_argument("--web-output", type=Path, default=REPOSITORY / "web/public/assets/recovery.json")
    parser.add_argument("--child", choices=("after-effect", "after-intent"), help=argparse.SUPPRESS)
    parser.add_argument("--case-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--job-id", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        if args.case_root is None or args.job_id is None:
            parser.error("internal child mode requires case root and job ID")
        if args.child == "after-effect":
            child_engine = _engine(args.case_root, interrupt_after_effect=True)
        else:
            child_engine = InterruptAfterIntentEngine(
                args.case_root / "workspace",
                {},
                adapters={HOST: SyntheticAdapter(args.case_root)},
                lock_dir=args.case_root / "locks",
            )
        child_engine.execute_action(args.job_id, "create_demo", {}, 1)
        raise AssertionError("The configured interruption point was not reached.")
    report = observe_recovery()
    if not args.check:
        _write_report(args.output, report)
        _write_report(args.web_output, report)
    checks = sum(len(case["checks"]) for case in report["cases"])
    print(f"Observed {len(report['cases'])} synthetic-adapter recovery cases; {checks} checks passed.")
    print("Native applications: not used. Automatic replay: not performed.")
    if not args.check:
        print("Wrote the recovery evidence JSON and matching web asset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
