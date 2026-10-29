# Source-bound inspection workcell: utilities and native transfer

A chat-directed addition to the existing industrial lab, executed with the real Blender adapter. The connected client authored the modelling script; the bridge provided source-hash checks, durable job state, native execution and saved-file evidence. There is no autonomous inference in the server.

The original visual pilot below is retained. The follow-up adds six editable utility routes and independently measures their actual endpoints and attachment geometry. See the [interactive workcell demonstration](https://bernardo-demier-ai-lab.fancy-fly-9200.chatgpt.site/studio-agent-bridge/inspection-workcell/) for the current revision, separate native Blender/Unreal views and publication-checked downloads.

## Before and after

Same camera, lighting, resolution and Cycles settings. PNG metadata was removed without changing RGB pixels.

| Reviewed baseline | Added station |
| --- | --- |
| ![Original lab](before.png) | ![Optical inspection station](after.png) |

The station contains individually named frame members, slots, brackets, levelling feet, fasteners, fixture sockets and clamps, an original ring workpiece, dual camera housings and lenses, illuminator mounts, cable-chain links, a cabinet with ventilation, an HMI prop and signage. Repeated cylindrical parts share native mesh data. Existing packed materials are reused.

The first render exposed illuminators without a complete connection to the gantry. The second revision adds two cross-brackets. Both native attempts remain preserved locally; the images above show the corrected version.

## Reproduce

From the repository root, with the documented development environment and Blender installed:

```bash
.venv/bin/python examples/inspection_workcell/run_example.py --output /path/to/new-empty-run
```

This executes trusted Python with Blender's user privileges. It does not download assets, run an LLM, or call a paid API. Use a new empty folder for every run; the example refuses to replace prior evidence. Its source is the exact reviewed `industrial-lab-native-v2.zip` scene already in this repository. A changed scene hash is rejected before preparation. Never substitute a newly computed hash without reviewing a different input.

The runner performs three distinct native actions: baseline render, additive revision and fresh-process readback. It saves a local `state.json` with job IDs, action records, native artifact hashes and output paths. Input bytes remain unchanged. The `verify_station.py` script can be dispatched in a further `run_script` action with the newly saved scene's reviewed hash for independent geometry readback; it imports no construction code.

Raw native output/logs may contain local paths. They are not approved public downloads: run the separate [publication-safety workflow](../publication_safety/) before distributing native files.

## Observed evidence and limits

See [observed-native-proof.json](observed-native-proof.json) for measured counts, hashes, correction history and validation scope. The original 1,571 mesh records retain the same measured positions, base vertices, polygon indices and material-slot assignments across baseline, revision and fresh reopen. The addition has 249 mesh objects. These checks do not establish complete Blender-file equivalence, modifier invariance, appearance equivalence or physical performance.

The optical devices, control panel and workpiece are visual geometry. There is no camera sensor model, optical inspection algorithm, robot controller, kinematics or electrical/safety certification. The fixture sockets are surface details, not subtractive machining features. Human visual acceptance is pending. These pilot checks describe the earlier revision. The utility revision has its own [observed evidence](observed-utilities-proof.json); do not attribute earlier lab measurements or renderer equivalence to it.

## Utility revision and transfer

The addition contains two camera harnesses, two illuminator leads, an HMI connection and a supply lead terminating at an existing laboratory socket. Glands, strain relief, collars, clips, a cabinet inlet and floor cover make the routes visible. Six native `POLY` splines retain editable centreline points. Existing meshes, materials and cameras are retained; two presentation cameras are copied for close and wide views.

The independent `verify_utilities.py` imports no builder code. It reads world-space curve endpoints, evaluated gland geometry and named existing host bounds from a freshly opened file. It requires endpoint-to-gland-centre distance ≤1 mm, host-face AABB contact ≤2 mm, metre-scale scene units, nonempty resolved material slots and valid polygon material indices. A deliberately displaced endpoint in a separate copy must fail specifically on its measured ~0.1 m gap; an arbitrary process failure does not satisfy that experiment.

Architecture and acceptance sequence:

1. `run_utilities.py` pins a reviewed pilot hash, snapshots it, builds only additive geometry and compares 1,820 pre-existing mesh records after fresh reopening.
2. A separate negative copy displaces one endpoint. Its readback must reject the injected defect while the accepted native file remains unchanged.
3. Close and wide native views are rendered with an explicit CPU or Metal selection.
4. `run_transfer.py` binds the exact accepted state, scripts, native artifact and fresh readback. It sanitizes a separate editable copy, reopens it, and compares explicit current-frame scene categories, allowing at most `1e-6` residual in evaluated floating-point fields.
5. `export_utilities.py` converts only the six cable curves in the exchange snapshot. It verifies their identities in GLB and the FBX manifest. Original editable splines remain in the upstream native file.
6. FBX metadata is sanitized with the installed Blender codec. Typed trees must match the expected path-only rewrite; the manifest may change only file hashes and byte counts. A fresh Unreal GPU project imports this exact manifest and is reopened for native captures.

The observed run and exact source/target hashes are in [observed-utilities-proof.json](observed-utilities-proof.json). The first utility readback rejected evaluated Blender Material IDs compared directly against originals. Resolving `Material.original` corrected that false failure while retaining missing-reference and index checks. The first transfer stopped on a non-normalized output path. The second reached native FBX export but Blender exited with signal 11 while writing an export log line; its crash report and partial output were retained. The accepted transfer uses a separate fresh directory. Failed native attempts are retained privately and summarized in the public record. The final Unreal actions all verified, but an initial PNG trailer check rejected zero-only padding after `IEND`. A separate postflight review checked chunk CRCs, dimensions, complete pixel decoding, source integrity and both target readbacks without replaying native actions. The original failed state remains preserved; the accepted review has its own hash. Public PNGs were re-encoded with identical RGB pixels and without the padding.

### Reproduce the utility revision

Start with the reviewed pilot produced by `run_example.py`, or inspect the published native package. Utility construction requires the pilot without an existing `InspectionV2_` addition. Review the actual source file and its SHA-256, then run:

```bash
.venv/bin/python examples/inspection_workcell/run_utilities.py \
  --source /path/to/reviewed-pilot.blend \
  --source-sha256 REVIEWED_LOWERCASE_SHA256 \
  --output /path/to/new-utility-run \
  --device CPU

.venv/bin/python examples/inspection_workcell/run_transfer.py \
  --source-run /path/to/new-utility-run \
  --output /path/to/new-transfer-run
```

Use `--device METAL` only with a compatible Blender device; explicit Metal requests fail rather than silently switching to CPU. Transfer defaults target the tested macOS Blender 5.2 installation. Override `--blender`, `--unreal`, `--blender-python` and `--fbx-modules` for another installation. Other host versions/platforms need their own native validation. Both runners refuse existing output directories. They retain failures and do not resume or replay automatically. Native scripts run with your user privileges; these gates are not a sandbox.

The downloaded `.blend` preserves six cable curves. GLB and FBX contain static surface geometry. Raw logs, the local operational registry and Unreal project directories are not distributed. Publishing a new native archive requires the [separate publication gates](../publication_safety/), including scans of decompressed native bytes and every exact archive member.

### Limits and next acceptance

Endpoint alignment and AABB contact do not prove exact surface mating, collision clearance, electrical continuity or a safe cable bend radius. Supports and covers are visual geometry; no robot motion, sensor model, physical simulation or electrical operation is introduced. Simple Unreal collision is a coarse import feature, not validated navigation. Images show each native renderer; material and lighting equivalence is not asserted. Browser lighting is separate again.

Human visual acceptance and engineering review remain pending. Next improvements should follow review of these close/wide views and test actual spatial clearance or a bounded scene edit, preserving this published revision.

## Provenance and security boundary

All added geometry, labels and assembly design are original synthetic examples under the repository's MIT license. No external meshes or proprietary parts were used. Source pinning and output-directory isolation reduce accidental overwrites and stale edits; trusted script execution is not a sandbox, and mesh counts do not certify safety.
