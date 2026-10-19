"""CLI emits one JSON value on stdout; stdio mode reserves stdout for MCP."""

import argparse
import json
import os
import sys
from pathlib import Path

from .engine import Engine, load_config
from .models import ACTIONS, plan_workflow


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Native CAD/3D tools for a trusted local agent client"
    )
    result.add_argument(
        "--workspace",
        default=os.environ.get("STUDIO_WORKSPACE", str(Path.home() / "StudioAgentWorkspace")),
    )
    result.add_argument(
        "--allow-scripts",
        action="store_true",
        help="Enable trusted arbitrary Python inside native apps; not sandboxed",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status", aliases=["health"])
    commands.add_parser("serve")
    plan = commands.add_parser("plan")
    plan.add_argument("--hosts", nargs="+", choices=list(ACTIONS), required=True)
    plan.add_argument("--goal", required=True)
    jobs = commands.add_parser("job").add_subparsers(dest="job_command", required=True)
    create = jobs.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--host", choices=list(ACTIONS), required=True)
    create.add_argument("--goal", required=True)
    for verb in ("inspect", "artifacts"):
        jobs.add_parser(verb).add_argument("--job", required=True)
    reconcile = jobs.add_parser("reconcile")
    reconcile.add_argument("--job", required=True)
    reconcile.add_argument("--notes", required=True)
    reconcile.add_argument("--confirm-host-idle", action="store_true", required=True)
    reconcile.add_argument("--revision", type=int, required=True)
    run = commands.add_parser("run")
    run.add_argument("--job", required=True)
    run.add_argument("--action", required=True)
    run.add_argument(
        "--parameters", default="{}", help="JSON object; declared file paths are workspace-relative"
    )
    run.add_argument("--revision", type=int)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_config()
        if args.allow_scripts:
            config["allow_scripts"] = True
        engine = Engine(args.workspace, config)
        if args.command == "serve":
            from .server import make_server

            make_server(engine).run(transport="stdio")
            return 0
        if args.command in {"status", "health"}:
            result = engine.status()
        elif args.command == "plan":
            result = plan_workflow(args.goal, args.hosts)
        elif args.command == "run":
            parameters = json.loads(args.parameters)
            if not isinstance(parameters, dict):
                raise ValueError("Parameters must be a JSON object.")
            result = engine.execute_action(args.job, args.action, parameters, args.revision)
        elif args.job_command == "create":
            result = engine.create_job(args.name, args.host, args.goal)
        elif args.job_command == "inspect":
            result = engine.inspect_job(args.job)
        elif args.job_command == "artifacts":
            result = engine.list_artifacts(args.job)
        else:
            result = engine.reconcile_job(
                args.job, args.notes, args.confirm_host_idle, args.revision
            )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") in {"failed", "needs_review", "blocked"} else 0
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
