# Recovering interrupted native actions

A tool timeout does not prove that a CAD editor or a Blender subprocess did nothing. A native application can save a file and outlive the bridge process that requested it. Studio Agent Bridge therefore preserves uncertain outcomes for inspection and never retries them automatically.

## The defects this milestone addresses

The earlier preparation order created the run directory before recording durable intent. A process exit in that window left a ready job and an existing directory at the next revision; retries failed and ordinary reconciliation had no unresolved marker to inspect. The old marker was also written directly into its final filename, permitting a partially written JSON file after process termination. Finally, multiple database reads could observe different revisions when a writer committed between queries, causing a false integrity failure.

The recovery protocol publishes a complete action intent before allocating the run directory and before native dispatch. The marker is written through a temporary file, flushed, atomically published and synchronized. Job snapshots and event-chain checks share a SQLite read transaction. Native execution still requires the existing per-host lock and current job revision.

## Diagnose without changing anything

From an MCP client:

```text
studio_recovery_report(job_id="JOB_ID")
```

Or from the CLI, using your dedicated modeling workspace:

```sh
.venv/bin/studio-agent-bridge --workspace "$HOME/StudioAgentWorkspace" \
  job recovery --job JOB_ID
```

The report includes the persisted job state, event-chain consistency, host-marker ownership, workspace-relative run directories and reconciliation requirements. It neither launches a native application nor edits its files. `native_host_idle` is `unknown`: the bridge lock is not evidence that an external process has stopped.

When ownership can be established, inspect the actual editor, child processes and saved outputs. Only after the previous work has stopped, record an explicit reconciliation with the current revision:

```sh
.venv/bin/studio-agent-bridge --workspace "$HOME/StudioAgentWorkspace" \
  job reconcile --job JOB_ID --revision CURRENT_REVISION --confirm-host-idle \
  --notes "Inspected the stopped process and its saved output; recorded the observed outcome before requesting a new action."
```

Reconciliation records the operator's account. It does not roll back, repair or replay the native action. A new action is a separate explicit request. Existing run directories and their files are preserved. Legacy job-scoped orphan directories can be acknowledged through this flow; unknown, unsafe or foreign marker ownership remains blocked instead of being assigned to the caller's job. More than 512 unrecognized entries in a job's run directory requires manual investigation; completed ledger history does not consume that limit.

## Reproduce the experiment

After installing the repository's pinned dependencies:

```sh
.venv/bin/python -m pytest -q -m "not integration"
.venv/bin/python examples/recovery/generate.py
```

The [generator](../examples/recovery/generate.py) uses real engine processes, SQLite transactions and isolated temporary workspaces. Its synthetic adapter records dispatches and writes clearly labeled fixture effects. It does not start Blender, Fusion or Unreal, and uses no model or paid service. A controller waits for the interrupted child to exit before recording its explicit host-idle assertion.

The generated report is available in [examples/recovery/observed-recovery.json](../examples/recovery/observed-recovery.json). The [online recovery lab](https://bernardo-demier-ai-lab.fancy-fly-9200.chatgpt.site/studio-agent-bridge/recovery/) explores those recorded steps; browser buttons do not execute the Python bridge or control a user's desktop. Re-running the generator produces new job IDs and timestamps, so expected behavior and assertions are reproducible, rather than byte-identical output.

## Threat model and limits

The intended operator and MCP client are trusted local users. This milestone addresses accidental process interruption, concurrent bridge reads/writes, lost preparation metadata and misleading recovery conclusions. Declared-path validation rejects traversal and symlinks; markers with unestablished ownership stay blocked.

It does not defend against a malicious same-user process rewriting the database, changing files during a filesystem check or bypassing the bridge. Trusted native Python remains privileged arbitrary code. The event hash chain provides consistency checks, not signed authorship. Marker state and SQLite are not a distributed transaction with the native editor.

Process-interruption tests do not establish survival of sudden power loss on every operating system or filesystem. The lock coordinates bridge clients only. Manual editor actions, renderer side effects, exact-once execution, automatic rollback, physical validity and arbitrary design completeness are outside the guarantee. Job revision checks also do not pin the expected contents of a manually changed source scene; source identity must still be reviewed separately.

## Architecture decisions and primary references

- Keep the existing SQLite ledger and host lock instead of introducing a queue or external service. The deployment remains local and stdio-based.
- Establish a single read snapshot for state plus event verification. SQLite documents this behavior for explicit read transactions in WAL mode: [Isolation in SQLite](https://www.sqlite.org/isolation.html).
- Publish the complete intent on the same filesystem before native work, using atomic replacement semantics and synchronization. Python documents [os.rename](https://docs.python.org/3/library/os.html#os.rename) and [os.fsync](https://docs.python.org/3/library/os.html#os.fsync). This does not turn a native application and SQLite into one transaction.
- Preserve uncertain artifacts and require explicit inspection. Missing metadata is not permission to replay a potentially completed edit.

All fixtures, traces and example code in this case are original public-portfolio material. The primary references describe platform behavior; no third-party model, client scene or private dataset is bundled.
