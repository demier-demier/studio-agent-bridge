# Local MCP and job orchestration

Studio Agent Bridge is a local tool server for a trusted MCP client. The client supplies the language model, planning, design judgment and optional subagents. The server executes bounded native actions, stores job state and checks artifacts. The role plan is a handoff contract; it does not spawn independent models or turn a written goal into geometry by itself.

The implementation uses the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), pinned to **2.3.0**. It uses `MCPServer` and stdio, with a real subprocess test using `Client` and `StdioServerParameters`. Dependencies are pinned in `requirements.lock.txt`; native applications are installed separately.

## Install and connect

Python 3.12 or newer is required. The process-lock implementation supports macOS and Linux. Native-host availability still depends on the application's supported platform and installation.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install --no-deps -e .
export STUDIO_WORKSPACE="$HOME/StudioAgentWorkspace"
.venv/bin/studio-agent-bridge status
.venv/bin/studio-agent-bridge serve
```

The last command reserves stdout for MCP JSON-RPC. Ordinary CLI commands print one JSON value. `status` also reports missing hosts without attempting to install them. A missing host is a capability limit, not permission to fabricate native output.

Configure an MCP client with the absolute executable and workspace paths appropriate to that computer. This is an example, not an automatic edit to the client's settings:

```json
{
  "mcpServers": {
    "studio-agent-bridge": {
      "command": "/absolute/path/to/studio-agent-bridge/.venv/bin/studio-agent-bridge",
      "args": ["--workspace", "/absolute/path/to/StudioAgentWorkspace", "serve"],
      "env": {
        "STUDIO_ALLOW_SCRIPTS": "0"
      }
    }
  }
}
```

The server does not change client settings, enable application plugins, obtain cloud credentials, or expose a network listener. The Fusion adapter connects to a separately enabled local native Fusion endpoint. Host API and installation details are covered in the host-specific documentation.

## Tools

| Tool | Purpose |
|---|---|
| `studio_status` | Probe installed hosts, list actions, report script privilege and unresolved native actions. |
| `studio_plan_workflow` | Return planner, native modeler, geometry QA, interop and reviewer responsibilities for one to three hosts. |
| `studio_plan_environment` | Validate an environment brief and return high-detail construction, visual review, transfer and revision requirements. This is a plan, not a generated scene. |
| `studio_create_job` | Persist one single-host job with a goal and an initial revision. |
| `studio_execute_action` | Dispatch one supported action with parameters and an expected job revision. |
| `studio_inspect_job` | Read current state and check event/snapshot consistency. |
| `studio_list_artifacts` | List artifacts from completed actions and recheck their current sizes and hashes. |
| `studio_recovery_report` | Inspect unresolved intent and run-directory provenance without editing state, launching a host or deciding that a host is idle. |
| `studio_reconcile_job` | Record a trusted operator's observed outcome after an uncertain interruption. |

Prompts: `studio_workflow`, `studio_environment` and `studio_review`. Resources: `studio://roles`, `studio://capabilities`, and `studio://jobs/{job_id}`. Standalone role prompts are in `prompts/`.

The environment planner accepts a structured `brief` containing `goal`, optional `zones`, `style`, `size_m`, `detail` (`high` or `hero`), `target`, `seed`, `triangle_budget`, `texture_resolution` and `reference_policy`. Defaults produce a high-detail Blender → Unreal plan with original assets and a bounded 2 million triangle / 2048 texture budget. Budgets are ceilings, not quality scores. No native application is launched by planning. Use `environment-plan --brief '{"goal":"A fully equipped robotics laboratory"}'` from the CLI.

After upgrading a running server, reconnect the MCP client to discover new tools and prompts. A fresh CLI process loads the current implementation immediately; existing native `run_script` operations remain usable for clients with an older tool list.

One job is bound to one host. For a cross-application workflow, the client creates one job per host and passes a workspace-relative exported artifact to the next. The plan names required handoff evidence: content hash, units, axis convention, target measurements and known semantic losses. A format conversion does not preserve a native CAD feature timeline automatically.

## CLI example

```sh
.venv/bin/studio-agent-bridge plan --hosts fusion blender unreal \
  --goal "Build an editable mechanical part and a measured real-time visualization."

.venv/bin/studio-agent-bridge job create \
  --name inspection-scene --host blender \
  --goal "Create and reopen the native demo scene, then verify exported geometry."

# Use the id and revision returned by job create.
.venv/bin/studio-agent-bridge run --job JOB_ID --revision 1 \
  --action create_demo --parameters '{}'
.venv/bin/studio-agent-bridge job inspect --job JOB_ID
.venv/bin/studio-agent-bridge job artifacts --job JOB_ID
```

MCP execution requires `expected_revision`. CLI `--revision` is optional for interactive use; the engine still checks the revision again after taking the host lock. A stale revision is rejected before native execution. An action increments the revision when it starts and again when it finishes. Result `verified` applies to that action's measured evidence, not automatically to every requirement in the job goal.

## Configuration

| Environment variable | Meaning |
|---|---|
| `STUDIO_WORKSPACE` | Root for native models, projects, logs, reports and `.studio/jobs.sqlite`. Default: `~/StudioAgentWorkspace`. Use a dedicated folder outside the source repository. |
| `STUDIO_BLENDER_EXECUTABLE` | Explicit Blender executable; otherwise the adapter probes its supported installation locations. |
| `STUDIO_UNREAL_EXECUTABLE` | Explicit Unreal editor executable. |
| `STUDIO_UNREAL_PROJECT` | Workspace-relative owned `.uproject` path. |
| `STUDIO_FUSION_URL` | Local Fusion MCP endpoint. The Fusion adapter enforces its local endpoint policy. |
| `STUDIO_BLENDER_TIMEOUT_SECONDS` | Blender process timeout; the adapter bounds accepted values. |
| `STUDIO_UNREAL_TIMEOUT_SECONDS` | Unreal process timeout; the adapter bounds accepted values. |
| `STUDIO_ALLOW_SCRIPTS` | Exactly `1` enables trusted native Python scripts. Default disabled. `--allow-scripts` is an equivalent explicit CLI opt-in. |

`run_script` deliberately exposes each application's Python API. It has the current user's application and filesystem privileges, may perform network operations, and is **not sandboxed**. Never enable it for an untrusted client, repository, document or script. Declared-path checks prevent accidental workspace traversal in tool parameters; they cannot contain arbitrary Python or defend against a malicious process running as the same user. Do not treat text embedded in a CAD model or scene as agent instructions.

## Persistence, concurrency and uncertain outcomes

Jobs and append-only events are committed in SQLite transactions. Each event contains a full job snapshot, monotonically increasing revision, previous hash and SHA-256 digest. Reads verify the chain and the latest snapshot. This detects accidental modification, deletion and divergence; it does **not** authenticate an author or resist an attacker who can rewrite the database and recompute hashes.

An OS file lock serializes a given host across bridge processes and workspaces for the current user. Locks live in `~/.cache/studio-agent-bridge/locks`. The lock does not stop manual edits or unrelated automation. A second native action is rejected while the first is running.

Before calling a native host, the server persists an unresolved-action marker. On a clean return it validates the result, commits the result and clears that marker. If the orchestrator crashes, an external transport raises, or result validation fails, the marker prevents automatic replay and further actions on that host. The native application could have committed work even if the bridge did not receive confirmation.

To recover, inspect the actual host/process and output files first. Then record what happened:

```sh
.venv/bin/studio-agent-bridge job inspect --job JOB_ID
.venv/bin/studio-agent-bridge job recovery --job JOB_ID
.venv/bin/studio-agent-bridge job reconcile --job JOB_ID --revision CURRENT_REVISION \
  --confirm-host-idle \
  --notes "Observed the previous process is stopped; inspected the output and identified the completed native changes."
```

This confirmation is a trusted operator assertion, not an independent idle detector or rollback. Reconciliation never retries an action. A new action must be explicitly requested. The server does not claim exactly-once native edits or crash-safe transactions spanning SQLite and CAD editors.

The [recovery guide](recovery.md) describes atomic action intent, interrupted preparation, preserved legacy run directories, consistent read transactions and the reproducible fault-injection lab. The report returns only workspace-relative paths for the selected job; marker ownership is never guessed when metadata is malformed or belongs to another workspace.

## Evidence gates

Every listed artifact must be a workspace-contained regular file with the claimed byte count and SHA-256. Declared paths reject absolute paths, parent traversal, reserved state paths and existing symlink components. Artifact reads recheck the path. This assumes a trusted local user; it is not a defense against concurrent malicious filesystem replacement.

For a native action to retain `verified`, it must have nonempty observations, a nonempty JSON evidence object and at least one nonempty native/export artifact. Otherwise the engine downgrades it to `executed_unverified`. Adapters perform the host-specific geometric checks. A hash proves file identity, not that a solid is correct or that a scene meets the written goal. `api_documentation` is separately scoped to a documentation query and does not require a native model.

`blocked` means a precondition prevented execution. `failed` means the adapter reported a known failure. `needs_review` is an orchestration state for an uncertain external outcome; it must be reconciled. Completed artifacts that are later changed are labeled `missing_or_modified` by `studio_list_artifacts`.

## Tests and limits

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

Core tests use explicit fake adapters to test storage, revision conflicts, process locks, crash markers, path boundaries and evidence gating. The MCP test launches a real stdio subprocess and calls tools through the official SDK. These tests do not establish a native modeling result. Native acceptance requires each installed application, saved editable files, reopen/inspection evidence and the target measurements recorded separately.

This bridge does not certify manufacturability, dimensions not checked by a host, finite-element results, production rendering, drawings, or arbitrary project completeness. Broader native APIs are available through trusted scripts; the client must select and verify the appropriate operations.
