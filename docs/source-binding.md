# Source-bound Blender revisions

An agent can inspect a scene, plan a change, and execute after another process has replaced the file. A job revision protects the job ledger; it does not identify the scene bytes the agent reviewed. `expected_source_sha256` binds a Blender request to those bytes.

## Contract

1. Inspect the scene and its current artifact record. Review the actual native content and retain its SHA-256.
2. Pass `input_blend` and that reviewed `expected_source_sha256`. It must be exactly 64 lowercase hexadecimal characters. Existing-scene `run_script` edits require it. Other actions that load `input_blend`, including `import_asset`, enforce it whenever provided.
3. The bridge streams a bounded copy into the new run's `source_snapshot/` directory, hashes the copied bytes, checks source stability, flushes the snapshot and makes it read-only. A wrong hash is rejected before Blender launches.
4. Blender opens that snapshot with embedded automatic scripts disabled. A pinned scene must be self-contained: known unsupported external file dependencies are rejected before the requested script executes. This resource check happens inside Blender, after native launch.
5. The action saves a separate output scene. The bridge reopens native outputs and checks the original source and preserved snapshot independently. A detected change prevents a verified result; evidence is retained.

A missing or malformed expected hash on a required edit is an error. Do not respond to a stale-hash failure by automatically computing a new expected hash: inspect the changed scene and reconsider the planned edit first. A failed preflight still becomes a recorded engine action with a new job revision. Use the returned current revision for the next request.

For compatibility, `inspect`, `render`, `export_asset` and `import_asset` may load an **unpinned** scene without an expected hash. These retain the original path and explicitly report their weaker mode. They do not get a preserved snapshot or an expected-version guarantee. `run_script` without any input scene remains available for original scene construction.

## Client request

The MCP client supplies the language-model reasoning and trusted edit script. The bridge performs deterministic validation and native execution; it does not infer a scene edit from arbitrary prose.

```json
{
  "job_id": "JOB_ID",
  "action": "run_script",
  "expected_revision": 3,
  "parameters": {
    "input_blend": "scenes/laboratory.blend",
    "expected_source_sha256": "REPLACE_WITH_THE_REVIEWED_64_CHARACTER_SHA256",
    "script_path": "scripts/revise_scene.py",
    "script_parameters": {"cart_offset_x_m": 0.25}
  }
}
```

Use the artifact hash from inspection, not the example placeholder. The output includes `observations.source_binding` and a `source-binding.json` metadata artifact. Snapshot inputs cannot satisfy the requirement for a newly saved native scene. Declared outputs must still pass native and engine acceptance checks.

## Architecture and decisions

Source binding is an adapter precondition, separate from the durable action-intent and job-revision machinery. Snapshot files and their metadata belong to the action's run directory; a failure preserves them for diagnosis. The source-copy bound is 512 MiB. The bridge validates workspace containment and rejects symlink-based pinned input paths.

Snapshot relocation changes the base for Blender-relative paths. Rather than silently resolving textures or linked libraries against a different directory, pinned mode requires supported external data to be packed and rejects known unsupported dependencies. The gate checks common built-in resource fields (images, fonts, sounds, libraries, caches and sequence media); it is not exhaustive dependency discovery for custom properties, add-ons, scripts, drivers or future Blender resource types. Only the `.blend` bytes are pinned. The original input is never repacked in place. Unpinned actions retain their original path so existing read/export workflows are not silently relocated.

Read-only file permissions prevent accidental writes by cooperative tools. They are **not operating-system immutability**: a privileged Python script running as the user can change permissions, write elsewhere or restore bytes after modifying them. The post-action checks detect persistent declared-input changes; they do not prove absence of all intermediate side effects.

## Threat model and limits

| Case | Behavior or boundary |
| --- | --- |
| Scene replaced after review | Expected hash mismatch rejects before Blender launch |
| Source changes while snapshot is copied | Source stability and copied-byte checks reject the action |
| Supplied hash on an import into an existing scene | Enforced just like other scene-consuming actions |
| Input snapshot offered as the only saved output | Does not establish a newly produced native scene |
| External file dependencies | Pinned mode rejects unsupported unpacked resources before executing the edit script; Blender has already launched |
| Source/snapshot changed during execution | Independent post-action comparisons prevent verification when detected |
| Concurrent manual editor work | Not serialized by bridge locks; hashes detect relevant saved-file changes, not unsaved UI edits |
| Malicious native files or trusted-script abuse | Not sandboxed; OS isolation and trusted inputs remain necessary |
| Semantic correctness | A matching hash proves identity, not that a change meets the brief |

No automatic retry, rollback, exactly-once native execution or competitive performance claim follows from this mechanism. It complements [durable intent and explicit recovery](recovery.md).

## Native acceptance case

The reproducible case uses only the original public robotics-laboratory scene and scripts in this repository. It exercises a stale source, a pinned cart revision, matching-camera renders, native reopening and a separate Blender–Unreal handoff. Observed results and application versions are published with the [executable case](../examples/source_binding/). The [browser evidence explorer](https://bernardo-demier-ai-lab.fancy-fly-9200.chatgpt.site/studio-agent-bridge/source-binding/) shows recorded native output; it does not launch desktop applications from the browser.

Acceptance requires: rejection before native launch for the wrong hash; correct snapshot identity; separate native output; original source preserved; measured intended movement; retained mesh count and scoped native reopen checks; actual transfer/reopen results reported separately from rendered appearance. Every unrelated object's transform and mesh contents were not exhaustively compared. The controlled script does not rebuild geometry. Failures and untested target features must remain visible in the evidence.

The 2026-10-24 run met the source-binding checks and preserved 1,571 mesh instances while moving 28 cart components by +0.25 m on X. The first Unreal 5.7.4 environment build using `editor_session=true` with `rendering=structure` failed in viewport hit-proxy handling. After confirming its process had exited, an explicit retry used `rendering=gpu` and a fresh target destination. That build and a separate native reopen passed structure, transform, material and bounds checks; no Unreal image was captured for this revision. This observed mode-specific failure remains in the report. Known successful older renders remain separate evidence.

Two additional original native fixtures check packed versus unpacked FILE images. The packed scene executes; the unpacked scene launches Blender once and is rejected before the requested script. The two cases exercise this image boundary, not every possible external resource. See `observed-resource-proof.json` in the executable case.

Publication copies of the baseline and edited scenes are re-saved through Blender to clear local path fields and stale string padding, then independently reopened and scanned. Their file hashes are recorded separately from the unchanged execution source. This metadata cleanup does not rewrite the original evidence or substitute the download hash for the source pin used in the experiment.

## Primary references

- [Blender packed data](https://docs.staging.blender.org/manual/en/latest/files/blend/packed_data.html): eligible resources can be embedded; not every external resource can be packed.
- [Blender relative paths](https://docs.staging.blender.org/manual/en/latest/files/blend/open_save.html): paths depend on the containing scene's location.
- [Blender 5.2 `blend_paths`](https://docs.blender.org/api/5.2/bpy.utils.html#bpy.utils.blend_paths): inspect external paths with version-appropriate packed-resource semantics.
- [Blender Python file-path notes](https://github.com/blender/blender/blob/main/doc/python_api/rst/info_gotchas_file_paths_and_encoding.rst): linked libraries introduce their own relative-path context.
