"""Real subprocess stdio negotiation with the official MCP client SDK."""

import asyncio
import json
import os
import subprocess
import sys

from mcp import Client, StdioServerParameters


def test_stdio_tools_prompts_resources_and_call(tmp_path):
    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "studio_agent_bridge", "--workspace", str(tmp_path / "workspace"), "serve"],
            env={**os.environ, "STUDIO_ALLOW_SCRIPTS": "0"},
        )
        async with Client(parameters, read_timeout_seconds=15) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert {
                "studio_status",
                "studio_create_job",
                "studio_execute_action",
                "studio_inspect_job",
                "studio_list_artifacts",
                "studio_plan_workflow",
                "studio_reconcile_job",
                "studio_plan_environment",
            } <= names
            prompts = await client.list_prompts()
            assert {prompt.name for prompt in prompts.prompts} == {
                "studio_workflow",
                "studio_review",
                "studio_environment",
            }
            roles = await client.read_resource("studio://roles")
            assert "geometry_qa" in roles.contents[0].text
            environment = await client.call_tool(
                "studio_plan_environment",
                {"brief": {"goal": "Create an occupied robotics laboratory", "zones": ["Robotics", "Electronics"]}},
            )
            assert not environment.is_error
            assert environment.structured_content["status"] == "planned"
            assert environment.structured_content["brief"]["detail"] == "high"
            invalid = await client.call_tool(
                "studio_plan_environment",
                {"brief": {"goal": "An invalid environment", "size_m": [12, -1, 4]}},
            )
            assert invalid.is_error
            created = await client.call_tool(
                "studio_create_job",
                {
                    "name": "mcp-proof",
                    "host": "blender",
                    "goal": "Verify durable MCP control without launching any native host.",
                },
            )
            assert not created.is_error
            job = created.structured_content
            assert job["revision"] == 1
            inspected = await client.call_tool("studio_inspect_job", {"job_id": job["id"]})
            assert inspected.structured_content["ledger"]["valid"]
            denied = await client.call_tool(
                "studio_execute_action",
                {
                    "job_id": job["id"],
                    "action": "run_script",
                    "parameters": {"script_path": "untrusted.py"},
                    "expected_revision": 1,
                },
            )
            assert denied.is_error
            again = await client.call_tool("studio_inspect_job", {"job_id": job["id"]})
            assert again.structured_content["revision"] == 1
            resource = await client.read_resource(f"studio://jobs/{job['id']}")
            assert job["id"] in resource.contents[0].text

    asyncio.run(scenario())


def test_cli_json_errors_and_durable_job(tmp_path):
    base = [sys.executable, "-m", "studio_agent_bridge", "--workspace", str(tmp_path)]
    created = subprocess.run(
        base
        + [
            "job",
            "create",
            "--name",
            "cli-test",
            "--host",
            "blender",
            "--goal",
            "Exercise JSON CLI durability.",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert created.returncode == 0
    job = json.loads(created.stdout)
    inspected = subprocess.run(
        base + ["job", "inspect", "--job", job["id"]], capture_output=True, text=True, timeout=15
    )
    assert json.loads(inspected.stdout)["ledger"]["valid"]
    rejected = subprocess.run(
        base + ["run", "--job", job["id"], "--action", "inspect", "--parameters", "[]"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert rejected.returncode == 2
    assert json.loads(rejected.stdout)["error"] == "ValueError"
