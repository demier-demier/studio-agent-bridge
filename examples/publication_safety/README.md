# Versioned native publication copies

This workflow removes known local path metadata from the original Industrial AI Laboratory download while preserving its raw input privately. It creates **`industrial-lab-native-v2.zip`**, a new archive with explicit source-to-publication hashes. Historical evidence remains historical; the package does not silently relabel old hashes as new observations.

The recorded case found account fragments in fixed-size Blender string buffers and local paths in FBX metadata. Shortening a visible path was insufficient. Native Blender properties are overwritten with a long benign value before assigning their final relative paths. FBX changes use Blender's official parser/writer and allow only six exact metadata field shapes.

## Reproduce

Requirements: the installed repository Python environment, Blender 5.2, its bundled Python/NumPy and FBX codec, Unreal Editor 5.7.4, and `zstd`. Native stages are local and run sequentially. No external account, API key, rendering service or model is used. Native applications may perform their own installed telemetry behavior.

From the repository root, choose a **new empty private directory**:

```bash
.venv/bin/python examples/publication_safety/run_example.py \
  --work-root ../studio-publication-work --stage prepare

.venv/bin/python examples/publication_safety/run_example.py \
  --work-root ../studio-publication-work --stage native

.venv/bin/python examples/publication_safety/run_example.py \
  --work-root ../studio-publication-work --stage transfer

.venv/bin/python examples/publication_safety/run_example.py \
  --work-root ../studio-publication-work --stage unreal

.venv/bin/python examples/publication_safety/run_example.py \
  --work-root ../studio-publication-work --stage publish
```

The default input is the current public **v2** package. Repeating the workflow on already-clean files is supported; it is not an exact rerun of the historical contaminated input. The 759 metadata changes and original hashes in the committed proof describe the **October 26, 2026** run. Exact original-case reproduction requires a separately retained archive passed explicitly:

```bash
.venv/bin/python examples/publication_safety/run_example.py \
  --work-root ../studio-publication-original-case --stage prepare \
  --source-archive /path/to/retained/industrial-lab-native.zip
```

That historical input has SHA-256 `461fbaa85c1bda7e6a7bd71dece05bfa5c3b48b11db0b1883481c9b815f15485`. Its known private bytes are why it is not the current download.

Use `--blender` and `--unreal` for alternate executables. The transfer worker uses the default installed Blender 5.2 bundled Python and codec paths on macOS; override them with `--blender-python` and `--fbx-modules` when needed. Raw logs, observations, source archives, original FBX files, owned native project and failed attempts remain private under the selected work root. A recorded action or output is never overwritten. An explicit `--attempt N` on the native stage preserves separate script copies if a measurement compatibility failure needs correction.

## What each stage proves

1. **Prepare:** preserve the exact raw ZIP as `original.zip`; validate every content member against the complete original manifest, including scripts and historical evidence. The manifest itself is covered by the preserved archive hash. Extract the day/night inputs only after hash verification.
2. **Native:** run the real Engine with pinned source snapshots. Observe scene content, sanitize native path fields, save a distinct file, then load it in a separate process and measure again. Both raw scene inputs remain unchanged.
3. **Transfer:** validate and retain original transfer files. Rewrite six exact FBX metadata field forms through the bundled codec, encode and reparse, and compare the full ordered typed tree with only those fields allowed to change. Numeric arrays must match exactly. The transfer manifest changes only each FBX file's checksum and byte count.
4. **Unreal:** build the cleaned transfer in a new adapter-owned project and destination using explicit GPU mode, then reopen and inspect it in a separate process. The observed target has 1,571 mesh actors referencing 84 mesh assets, 17 materials, nine lights and four cameras. No image is captured.
5. **Publish:** rerun the final typed comparator against retained native observations. Take immutable byte snapshots and require scene hashes/sizes to match native observations, FBX bytes to match both the codec evidence and transfer manifest, and the transfer manifest to match the one reopened in Unreal. Reject missing or added transfer files and any change to preserved textures, GLB or historical members. Scan those same bytes and create the ZIP only in a newly created private output directory. The gate scans names and ZIP metadata too. The resulting archive is scanned again, and a complete manifest covers its actual members.

The standalone current native sanitizer is [sanitize_scene.py](sanitize_scene.py). It must operate on a separate work copy and be followed by native save/reopen and byte scans. The scripts bundled inside the archive remain byte-identical historical sources, including the older sanitizer; they are not a substitute for this publication workflow.

## Comparison scope

Per scene, the measurements cover 42 base mesh datablocks, 1,617 objects, evaluated geometry for 1,603 mesh/curve/text objects, 32 curve/text datablocks, 17 materials, image records including 18 packed textures, nine lights, four cameras, worlds and selected scene settings. The fields include mesh coordinates/topology/UVs/attributes/material indices, object matrices/hierarchy/material slots/modifier scalar settings, material node inputs and links, packed image bytes, light/camera properties, and current-frame evaluated instances.

Non-evaluated categories compare exact typed canonical values. Evaluated float components compare directly with an absolute **1e-6** bound in each field's native units. This accounts for observed bevel UV recomputation differences; raw measured maxima and raw evaluated hashes are reported. Rounded evaluated hashes are informative and do not decide acceptance, since values can straddle rounding boundaries.

The final stricter comparator was rerun on the retained raw observations after native execution. Its hash is separate from the frozen script hashes used inside Blender. Four earlier measurement attempts stopped without accepting an output; their concise failure history and source hashes remain in the proof.

This is stronger than a count-only check, but it is **not complete `.blend` invariance**. It does not inspect every possible custom property, font implementation, simulation state, animation frame, renderer setting or add-on behavior. Neither structural equality nor GPU editor execution proves visual parity, physical accuracy or runtime performance.

## Outputs and tests

The private `public-exports/` directory receives `industrial-lab-native-v2.zip` and `observed-native-proof.json`. The workflow never overwrites the committed historical proof; an operator copies a reviewed result explicitly when publishing. Use `--output-directory` to assemble a revised candidate in a new directory inside the same private work root, preserving an earlier candidate. Only these reviewed publication outputs are candidates for public copying; raw logs and workspaces are not.

Inside the ZIP, `publication.json` records the new native and FBX mappings and checks. `evidence.json`, the GLB, historical scripts and texture bytes remain unchanged. `manifest.json` describes the v2 members. The embedded publication record deliberately excludes the final archive hash to avoid a circular dependency; the external proof records it.

```bash
.venv/bin/python -m pytest tests/test_scene_publication.py tests/test_publication.py tests/test_fbx_publication.py -q
```

The byte gate checks configured account fragments and path patterns, including recognized decoded Blender formats. It is a bounded pattern detector, not a universal guarantee that all private information or encodings are absent. The native and FBX workflows are designed for this repository-authored fixture and are not hardened importers for arbitrary untrusted files.
