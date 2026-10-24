# Source-bound native scene revision

This example starts from the original detailed Industrial AI Robotics Lab packaged in the public repository. It rejects a stale expected SHA-256, then uses the accepted source to move the diagnostics cart by **+0.25 metres on X**. The source is preserved, and the edited scene is saved as a separate editable `.blend`.

The workflow uses the real `Engine` and supported native adapters. It does not substitute synthetic host behavior. The cart-editing scripts are the original public scripts extracted from the existing native package after checking their manifest hashes. A small original publication sanitizer overwrites native path properties before saving separate download copies. No new geometry is constructed.

## Reproduce

Requirements: the repository's Python environment, Blender 5.2 or newer, and the `zstd` command for the final decompressed-byte privacy scan. Unreal is optional for the separate static transfer stage. Native applications can take several minutes to render or import the scene.

From the repository root, choose a **new empty private directory**:

```bash
.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage prepare

.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage blender

# Optional: requires an installed Unreal Editor and creates a new owned project.
.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage unreal

.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage sanitize-downloads

.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage summarize
```

Use `--blender` or `--unreal` to specify an executable if automatic discovery does not locate it. The native stages keep their raw evidence, logs, SQLite ledger, source snapshots, rendered files, and owned project under the chosen work root. They do not modify the source ZIP or any existing native project. A failed native stage preserves its diagnostics; inspect them before preparing another fresh run. The script refuses to overwrite a prior prepared workspace or repeat an already recorded action.

The recorded Unreal 5.7.4 run hit a native viewport array-bounds assertion during actor placement in the first `rendering: "structure"` (NullRHI) attempt. That process exited with code 1. An **explicit** alternate GPU editor attempt then saved and independently reopened the scene successfully. Both outcomes appear in the JSON. The failed attempt's partial imported assets remain untouched.

After inspecting a matching failure, the following separate stage confirms that the earlier process exited, uses a new destination and map, and performs one GPU-mode build plus a fresh reopen. It is never invoked automatically and refuses an ambiguous or timed-out previous process:

```bash
.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage unreal-gpu-retry

.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage sanitize-downloads

.venv/bin/python examples/source_binding/run_example.py \
  --work-root ../studio-source-binding-work --stage summarize
```

The GPU editor run verifies 1,571 mesh actors referencing 82 shared mesh assets, 17 materials, nine lights, and four cameras. No Unreal image was captured; enabling its GPU renderer does not establish visual parity with Blender.

The summary command writes `examples/source_binding/observed-native-proof.json` and creates these sanitized outputs inside the private work root:

- `public-exports/before.png` and `public-exports/after.png`: matching Overview camera renders at 1200 × 834, Cycles, 24 samples. Text and EXIF chunks are removed while compressed pixel bytes are preserved.
- `public-exports/source-bound-cart-native.zip`: sanitized baseline and edited `.blend` copies, independently reopened by the adapter, their separate hashes, and a short README. Both scenes include their packed textures. Native path-property cleanup changes file bytes; the downloadable baseline is not byte-identical to the preserved input source. The generator scans both decompressed native files for private path prefixes and the current local account identifier, checks ZIP integrity, and enforces a size below 25 MB.

The publication sanitizer first writes a long benign value into native path fields, then their final relative values. This clears stale bytes beyond a string's terminator in fixed-size native buffers. A previous short-value-only cleanup left such a fragment; the tightened scan rejects that copy. No binary patching is used, and raw native observations remain preserved privately.

## What is measured

1. Every selected ZIP member matches its packaged byte count and SHA-256.
2. A stale expected hash produces a failed action with **zero Blender subprocess launches**. The engine still records the attempted action lifecycle and advances its ledger revision; this is not described as zero engine dispatch.
3. The correct hash binds the edit to a per-run source snapshot. Binding evidence records the expected, original, and snapshot hashes and whether the snapshot remained unchanged.
4. The existing revision script records all 28 cart object positions before and after the 0.25 m translation. The 1,571 mesh count remains unchanged.
5. A fresh Blender process loads the edited file. Running the same public script with zero displacement records the loaded cart positions before editing, and those positions must equal the saved edit's measured positions.
6. Separate native inspect and render actions verify the saved scene. The original source hash is checked again after the complete workflow.
7. When requested, the edited scene is exported as a static environment bundle, imported into a newly owned Unreal project, and reopened in another editor process. That stage measures target structure and transforms; it does not claim GPU-rendered appearance.

Subprocess counts come from Python's `subprocess.Popen` audit event. The audit records only the executable basename and phase; it does not replace adapter code, process output, or host results.

## Separate native resource regression check

The original lab uses packed images. A separate tiny authored test scene checks what happens with an unpacked image, without changing the lab or borrowing any external asset:

```bash
.venv/bin/python examples/source_binding/check_resources.py \
  --work-root ../studio-source-binding-resource-check
```

This creates one cube and a two-pixel-by-two-pixel image with native Blender. It runs the same requested edit against packed and unpacked variants, then writes `observed-resource-proof.json`. The packed scene must execute the edit; the unpacked scene must be rejected after opening the file and before the edit script writes its marker. That rejection includes one native process launch, unlike the stale-hash rejection which starts no Blender process. These are explicitly test-only scenes executed by the real adapter, not synthetic adapter results or part of the lab deliverable.

## Evidence boundaries

`observed-native-proof.json` records actual host versions, actions, lifecycle revisions, native launch counts, source-binding evidence, before/after measurements, input preservation, and public artifact hashes. Raw process logs remain private because native applications may include local paths. The exported JSON rejects private absolute paths, and the native download is scanned after decompression before packaging.

The source-binding mechanism captures a single packed, self-contained `.blend` file. It does not snapshot arbitrary external texture trees, linked libraries, or every dependency of trusted Python. Unpinned loading elsewhere in the adapter is outside this example's accepted-source guarantee.

This is a controlled revision of an original visualization scene. It does not establish autonomous interpretation of arbitrary modeling requests, physical accuracy, safe robot operation, or a certified building layout. Static transfer does not preserve native text editing, modifiers, or procedural relationships. Native geometry checks and a screenshot are separate evidence.

The recorded preservation checks cover all 28 cart positions, mesh counts, and the adapter's native reopen invariants. The original revision script does not rebuild geometry, but this example does not independently compare every non-cart transform or the complete mesh data.
