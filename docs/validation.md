# Native acceptance record

The original record below was observed on macOS on **2026-10-19**. This record separates orchestration tests from application-backed results. Full machine logs and working files remain in the local workspace; these public reports contain original synthetic geometry measurements and artifact hashes.

## Follow-up: versioned native publication, 26 October 2026

The ordinary suite now has **347 passed, one opt-in Blender integration skipped**. The new tests exercise bounded archive scanning, exact scanned-to-published bytes, overwrite/race refusal, FBX metadata rewrite boundaries, and scene-comparison tolerances. Synthetic tests do not substitute for the separate native record below.

The [publication example](../examples/publication_safety/) creates separate day/night Blender copies, independently reopens them, and compares the declared current-frame model fields. Base geometry and scene fields compare exactly. Evaluated geometry uses a declared absolute tolerance of `1e-6`, with the observed residual published in the evidence; a rounded hash alone is not an acceptance test.

All 84 FBX files are parsed, changed only at 759 allowlisted path fields, encoded and parsed again. Every other ordered typed property compares equal, including 756 numerical arrays. The transfer manifest is updated to identify these exact new FBX bytes, and a new Unreal project is built and independently reopened from that manifest. The v2 package includes original-to-publication hash mappings. Original gallery renders, GLB, textures and historical execution evidence retain their original bytes.

This is a publication and static-transfer check. It does not establish new appearance, animation, navigation, robot control or performance results. Four early scene-measurement attempts were rejected; their limitations and corrected acceptance logic remain in the new record. The [publication threat model](publication-safety.md) describes supported encodings, configured-pattern limits and the trusted-fixture boundary of the FBX helper.

## Follow-up: Unreal launch preflight, 25 October 2026

That day's ordinary suite had **261 passed, one opt-in Blender integration skipped**; lint and wheel build passed locally. These tests include mocked adapter contracts and are separate from native application evidence.

The [new native regression case](../examples/unreal_preflight/) executed five real Engine actions with Unreal 5.7.4 on macOS. All 18 acceptance checks passed. Two known-risk NullRHI environment builds were blocked before any subprocess launch, preserving the absent or existing native project. Explicit GPU construction and fresh inspection each exited 0 and verified the original lab's 1,571 mesh actors, 84 mesh assets, 17 materials, nine lights and four cameras. No image was captured in this follow-up. The historical crash in the [October 24 source-bound revision](../examples/source_binding/observed-native-proof.json) remains unchanged.

The following sections retain their original dated measurements and limitations; their test count and NullRHI-only appearance boundary apply to the October 19 cases.

## Automated tests

**92 tests passed; one opt-in Blender native test was skipped in the ordinary suite.** Ruff passed across source, tests and examples. The Blender native integration was separately executed successfully against the installed application, along with the targeted native operations below. The project wheel was built and inspected for all host scripts and role prompt resources.

The ordinary suite covers real MCP stdio negotiation/tool calls, revision conflicts, event-chain consistency, source/artifact changes, path and symlink boundaries, script opt-in, stale evidence, concurrent native actions, interrupted subprocesses, unresolved outcomes, export failures, Unreal object-reference handoffs and stable log snapshots. Mocked adapter tests are not counted as application geometry tests.

## Fusion

![Fusion-native enclosure viewport](images/fusion-enclosure.png)

- Actual MCP stdio calls dispatched through the bridge to Fusion **2705.1.30**.
- Created **8 solid bodies**, **17 fully constrained sketches**, **12 user parameters** and **58 timeline entries**.
- Initial bounds: **160 × 100 × 70 mm**.
- Changed width and housing height through `edit_parameters`: **180 × 100 × 80 mm**.
- Executed a trusted native Python revision increasing lens length: **180 × 100 × 88 mm**.
- Every final F3D was reopened; body count, history count, health, parameter expressions, positive solids, bounds and constraints matched.
- Original archives were preserved; SHA-256 manifest checks passed. Version-specific API documentation was retrieved through MCP.

[Recorded Fusion measurements](../examples/fusion/native-verification.json)

## Blender

- Blender **5.2.0 LTS** created an editable rover and studio scene.
- **84 asset meshes**, 84 retained bevel modifiers, 9 materials and keyed lidar rotation. The studio floor adds one further mesh to the native scene.
- Asset bounds: approximately **1.740 × 1.352 × 1.3525 m**.
- Native reopen and separate GLB/FBX import processes checked counts and dimensions.
- A custom Python revision raised the mast **0.15 m**, changing height to **1.5025 m** while preserving the input hash.
- Original and revised images were rendered and visually inspected. Saved render settings were independently checked on reopening.
- The native scene was also inspected through the actual MCP server.

[Recorded Blender measurements](../examples/blender/observed-native-proof.json)

## Unreal and cross-application handoff

- Unreal **5.7.4** created a real native project and imported Blender's FBX.
- **84 StaticMesh assets**, **40,176 triangles**, measured bounds **174 × 135.200004 × 135.250009 cm**.
- The saved level contains **90 actors**, native materials, three lights and a cine camera.
- The **24 fps / 96 frame** sequence has 9 transform channels, 18 keys and a camera-cut track.
- A new editor process reopened the saved assets/map and resolved both camera bindings to the persisted camera actor.
- Actual MCP `inspect` succeeded. A subsequent artifact query verified the native files and frozen process-log snapshot against their hashes.

[Recorded Unreal measurements](../examples/unreal/native-verification.json)

## Failures caught during real execution

- Fusion sketch constraints were refined until all sketches were fully constrained rather than merely dimensioned.
- Unreal's FBX warning logger attempted to open a Slate window in headless mode when smoothing groups were absent. Explicit face-smoothing export resolved that import failure.
- A camera bound before the level was saved referred to an Untitled world and failed after restart. Saving the world before binding resolved it.
- Unreal helper processes continued appending to inherited stdout after editor exit. The manifested log is now a frozen snapshot; the changing diagnostic log is excluded from the manifest.
- Native Unreal object paths returned by inspection can now be passed back as input, with strict package/suffix validation.

## Second Fusion case: board mounting fixture

The [Raspberry Pi 5 open tray](../examples/fusion/rpi5-open-tray/) was created on 19 October 2026 from attributed numerical facts in the official approximate mechanical drawing. Default and revised F3D archives passed the seven archive checks, and an additional independent reopen measured actual BRep bore axes/depths and voids. Each contains one connected solid, 15 constrained sketches, 13 parameters and 43 healthy timeline entries. Bounds changed from 101 × 72 × 11 mm to 109 × 80 × 14 mm; the 58 × 49 mm board mounting pattern was preserved. The exported STL was imported into Blender in metres, dimension-checked and rendered. Native Blender reopen also succeeded.

This extends the native proof to a second CAD brief with real source dimensions. No physical board, fastener, cooler, print, material or production tolerance was tested. STEP was exported but not reimported. Public [evidence](../examples/fusion/rpi5-open-tray/observed-native-proof.json) and [source provenance](../examples/fusion/rpi5-open-tray/dimensions.json) preserve these boundaries.

## What these checks do not establish

These are original native examples, not a reliability benchmark for arbitrary briefs. The Fusion enclosure, board fixture and Blender rover have no engineering certification, load analysis or manufacturing release. Interchange checks do not prove identical shaders, every local transform or rig behavior. Unreal execution used NullRHI, so GPU rendering, Lumen/Nanite output, animation appearance, packaged gameplay and performance are unverified. The client must create acceptance checks for each new project and inspect its actual outputs.
