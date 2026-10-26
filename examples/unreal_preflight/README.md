# Unreal environment preflight: native reproduction

This example records a real adapter rejection before launching an unsafe native environment build, followed by a separately requested GPU build and fresh inspection. It targets the observed **Unreal 5.7.4 on macOS** failure: an environment `build_scene` uses a full editor session, and `rendering: "structure"` adds NullRHI. The previous recorded run hit a viewport hit-proxy assertion during actor placement.

The guard applies to that combination. It does not disable ordinary commandlet actions or silently substitute GPU rendering. The failed request still has an engine lifecycle and ledger revisions.

This is a new run of the **original public lab transfer**. Its manifest contains 1,571 mesh instances and 84 asset entries. It is separate from the previous edited-cart scene, which had 82 shared mesh assets. The original scene is authored demonstration content; execution uses the real installed Unreal Editor and the real `Engine`, with no simulated adapter.

The checked-in proof records the October 25 input package. Current reproduction uses `industrial-lab-native-v2.zip`; portable FBX path metadata and its manifest hashes differ. The geometry-preservation checks and fresh transfer of that publication are recorded separately in [publication_safety](../publication_safety/). Historical proof hashes are not relabelled as v2 results.

## Reproduce

Use the repository's installed Python environment and Unreal 5.7.4 on macOS. Choose a **new empty private work directory**. The `prepare` step extracts only transfer files from the repository's public native ZIP, checking every selected member against its packaged SHA-256 and byte count. It does not extract or execute scene-construction scripts.

```bash
.venv/bin/python examples/unreal_preflight/run_example.py \
  --work-root ../studio-unreal-preflight-work --stage prepare

.venv/bin/python examples/unreal_preflight/run_example.py \
  --work-root ../studio-unreal-preflight-work --stage reject
```

The rejection stage performs three real engine actions:

1. Request a structure-mode environment build before any project exists. Verify the guard returns `blocked`, no subprocess starts, and no project is created.
2. Create a new adapter-owned project using the normal commandlet action.
3. Repeat the unsafe request against that project. Verify no subprocess starts and its complete tree fingerprint remains unchanged.

After reviewing those results, request the GPU build explicitly:

```bash
.venv/bin/python examples/unreal_preflight/run_example.py \
  --work-root ../studio-unreal-preflight-work --stage gpu

.venv/bin/python examples/unreal_preflight/run_example.py \
  --work-root ../studio-unreal-preflight-work --stage summarize
```

The GPU stage builds into a fresh destination in the newly owned project, then starts a separate native inspection process that reopens the saved map. No action retries or changes rendering mode automatically. A failure stops the stage and leaves its native evidence intact.

Use `--unreal /path/to/UnrealEditor-Cmd` when automatic discovery does not find the intended installation. The example refuses other host/version combinations for this specific reproduction. Run native stages sequentially; do not start a second editor operation on the workspace concurrently.

The final command writes `examples/unreal_preflight/observed-native-proof.json`. Use `--output` to select another location. Raw requests, process logs, native verification files, project assets, ledger, and audit records remain inside the private work root. The generator refuses to overwrite a prepared workspace or repeat a recorded action label.

## Evidence and limits

The public JSON records adapter preflight details, actual launch counts, revisions, project fingerprints, source/implementation hashes, and measured target counts. Launch counts come from Python's actual `subprocess.Popen` audit events; the adapter and native process behavior are not replaced. Project fingerprints include directory and file names, file contents, and modification timestamps around each request. Rejected requests still create engine-owned lifecycle evidence outside the native project.

Fresh native inspection checks mesh actors, shared mesh references, transforms, mesh bounds, materials, lights, and cameras against the public transfer manifest. GPU mode is necessary for this environment build on the tested installation, but **no Unreal image is captured or visually validated**. This run does not establish appearance parity, gameplay, simulation, navigation, or GPU performance.

The Oct24 native failure and subsequent successful GPU attempt remain separate historical evidence in `examples/source_binding/observed-native-proof.json`. This example demonstrates the new preflight; it does not rerun the known crash.
