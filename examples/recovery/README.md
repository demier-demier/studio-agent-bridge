# Observed recovery with synthetic host adapters

This example exercises the real engine, SQLite event ledger, host-intent protocol, process interruption, restart, recovery inspection, and explicit reconciliation. The host adapters and their file effects are **synthetic**. It does not start a native application, contact a cloud service, or verify geometry.

Run from the repository root after installing the project dependencies:

```bash
.venv/bin/python examples/recovery/generate.py
```

The generator creates isolated temporary workspaces and lock directories, executes the scenarios, asserts every recorded check, and writes:

- `examples/recovery/observed-recovery.json`
- `web/public/assets/recovery.json`

Both files contain the same observed evidence. The export includes UTC generation time and hashes of the engine, workspace, and generator source. Temporary absolute paths and machine usernames are not exported. Job identifiers and timestamps vary between runs; results are reproducible as checked behavior rather than byte-identical snapshots.

To verify the behavior without updating the public snapshot:

```bash
.venv/bin/python examples/recovery/generate.py --check
```

## Scenarios

| Case | Actual interruption or action | Evidence checked |
| --- | --- | --- |
| Clean action | Synthetic adapter completes normally | One dispatch, persisted finish, clean restart, no recovery requirement |
| Effect then crash | Child exits abruptly inside the adapter after fsync of an owned effect file | Running state and effect survive; retry is blocked; explicit reconciliation preserves evidence; a separate requested action gets a new run directory |
| Intent window | Engine subclass exits immediately after the real atomic intent publisher returns | Intent survives while ledger remains ready; no adapter was dispatched; explicit recovery is required before another action |
| Unknown marker | A deliberately truncated marker is present | Ownership remains unknown; action and reconciliation stay blocked; marker bytes and ledger revision are preserved |

The two interrupted children call `os._exit`, bypassing Python exception handlers and cleanup. The parent waits for their exits. Adapter dispatch counts come from fsynced audit records, and effect evidence contains measured sizes and SHA-256 hashes. Report inspection and blocked retries are checked for unintended ledger changes.

The synthetic adapter runs entirely inside the child process and starts no other process. That is why this example can truthfully supply an operator's explicit `host_is_idle=True` confirmation after waiting for the child. It does **not** demonstrate that the engine can detect whether a real native application is idle. The engine's recovery report continues to describe native host idleness as `unknown`.

The adapter returns `executed_unverified` even after its fixture files pass integrity checks. No synthetic file is presented as verified native application output. Reconciliation records an acknowledgment; it does not reverse a side effect, establish native success, or automatically replay an action.

## Export contract

The top-level object contains `schema_version`, `generated_at`, `provenance`, `cases`, and `limitations`. Every case contains `id`, `title`, `description`, `steps`, `checks`, and `limitations`.

Each step records its `title`, `detail`, operation `status`, persisted `revision`, cumulative `adapter_calls`, observed `effects`, and the engine's read-only `report`. A step's `status` can describe an observed blocked or interrupted operation; `report.status` always carries the persisted engine status. Each effect has a workspace-relative `path`, measured `bytes`, and `sha256`.

The generator fails before writing a report if any check is false, if source files change during collection, or if the export contains a local absolute path. The committed snapshot is evidence from one observed execution of these scenarios, not an exhaustive crash-point proof, concurrency stress test, native-host integration test, or production durability guarantee.
