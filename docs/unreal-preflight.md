# Unreal environment launch preflight

## Problem and decision

The [24 October source-bound revision](../examples/source_binding/observed-native-proof.json)
recorded a real Unreal 5.7.4 failure on macOS. A full-editor environment build with
NullRHI imported assets, then hit a viewport array-bounds assertion while placing
actors. A separate, explicitly selected GPU attempt in a fresh destination
completed and reopened successfully. The failed destination was preserved.

The adapter rejects the observed combination before launching Unreal:

- `action` is `build_scene`;
- a nonempty `environment_manifest` selects a full editor session;
- `rendering` is `structure`, including its default when omitted;
- the platform is macOS and the discovered engine version is 5.7.4.

The result is `blocked`, with machine-readable preflight evidence and an explicit
instruction to select `rendering: "gpu"`. There is no automatic renderer change,
replay, destination cleanup or claim that all NullRHI workflows fail. A
client-supplied `editor_session` flag cannot override the manifest-driven choice.

## Execution boundary

The launch preflight runs before the Unreal adapter creates a run directory,
creates or opens its owned project, validates the environment bundle, writes a
native request, acquires the project lock, or launches an editor. Consequently,
the known compatibility error takes priority over deeper manifest diagnostics.
If a client chooses GPU, the ordinary path, ownership, hash and fresh-destination
checks still apply.

The enclosing Engine has its own durable action lifecycle. A rejected request
still produces Engine evidence and advances the job revision. “No project
writes” does **not** mean no workspace or ledger writes. Clients must read the
new revision before their next request.

Unreal `import_asset` and `inspect`, legacy commandlet `build_scene`, other engine
versions and other platforms retain their existing paths. Unchanged behavior is
not a validation result for those combinations. An unreadable version file
cannot establish the exact known failure signature; normal execution remains
subject to native errors and the existing result checks.

## Acceptance and reproducibility

The [native regression case](../examples/unreal_preflight/) exercises the real
Engine and adapter with a fresh owned workspace. It measures native process
launches, fingerprints project files around rejected actions, then explicitly
builds the original public laboratory with GPU and inspects it in a separate
process. The public record contains only allowlisted observations; native logs
and machine paths remain private. See its README and dated JSON for measured
results and exact source hashes.

Adapter tests separately cover the known rejection, default rendering mode,
accepted GPU path, and the action/platform/version boundaries. Mocked tests are
not counted as native scene validation. The older source-binding proof remains
unchanged as historical evidence of the crash and subsequent successful GPU run.

On **25 October 2026**, all 18 checks in five real Engine actions passed. The two
rejected requests started zero subprocesses: one left the project absent and one
preserved all 14 existing project files and their tree fingerprint. The GPU build
and separate inspection each launched one native process, exited with code 0,
and returned verified structural evidence: 1,571 mesh actors, 84 shared mesh
assets, 17 materials, nine lights and four cameras. The maximum measured mesh
bounds residual was 0.0000715256 cm. This run captured no image.

## Threat model and limits

This is a deterministic compatibility guard against a known destructive failure
sequence. It reduces the risk of partially imported destinations for this
specific combination. It is not a crash predictor, security sandbox or
transactional rollback mechanism. Trusted Python and native applications retain
their existing privileges, and a user can run them outside this adapter.

The check trusts the local executable discovery, platform and engine version
metadata; it does not attest the engine binary. Filesystem races, unsupported
engine modifications, native plugin failures and GPU driver issues remain
outside this guard. Preserve failed destinations and reconcile their actual
state before a separate attempt. A successful GPU structure check does not
establish rendered appearance, performance, physical simulation or autonomous
interpretation of arbitrary modeling requests.
