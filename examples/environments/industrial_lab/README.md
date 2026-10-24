# Industrial AI robotics lab

An original, editable Blender environment built through the Studio Agent Bridge. This example demonstrates a complete interior scene and a measured static handoff to Unreal: modular architecture, a robot inspection cell, electronics bench, compute racks, utilities, furniture, original signage and PBR maps.

The scene is a visual design demonstration. It is not a certified building layout, electrical installation, robot controller or production robot cell. The robot's components are individually editable; there is no inverse-kinematics solver or validated robot motion. Window panels are opaque tinted visualization surfaces.

## Scene contract

- Interior design envelope: 12 × 9 × 4.2 metres. Complete walls, open sliding entrance and removable ceiling modules.
- `StudioEnvironment` contains exportable assets with `studio_asset`, `studio_category` and `studio_provenance` properties.
- `PresentationRig` holds four cameras and nine area lights.
- `SourceGeometry` retains editable text and cable curves outside the export selection. Evaluated mesh counterparts make these assets portable.
- Every mesh, texture map and typography layout was authored for this example. No downloaded models, company identities, employer assets or confidential data are included.
- Repeated parts use linked mesh data. Evaluated prototypes distinguish modifier results and material assignments during export.

Observed final geometry: 1,571 mesh instances; 84 evaluated FBX export prototypes; 26,304 unique triangles and 253,920 placed triangles; 17 materials; 18 packed original 256 × 256 base-color, roughness and normal maps. Aggregate bounds including the foundation are 12.4 × 9.4 × 4.52 m. These counts describe this saved example, not a general engine limit.

## Reproduce through the bridge

1. Copy `build_environment.py`, `revise_and_render.py` , `export_environment_bundle.py` and `sanitize_scene.py` into the configured workspace under `scripts/environments/`. Copy `src/studio_agent_bridge/host_scripts/blender/environment_export.py` alongside them. The helper is maintained by the core adapter; this example does not duplicate its source in the repository.
2. Call `studio_status`, then `studio_create_job` with host `blender`. Trusted scripts must already be enabled by the operator.
3. Call `studio_execute_action` using the returned job ID and current revision:

```json
{
  "action": "run_script",
  "parameters": {
    "script_path": "scripts/environments/build_environment.py",
    "script_parameters": {"lighting_theme": "day"},
    "render": false
  }
}
```

4. Use the returned native file as `input_blend` for `revise_and_render.py`, passing its reviewed artifact SHA-256 as `expected_source_sha256`. Existing-scene script actions require this hash and a packed, self-contained scene. `render_views` accepts `Overview`, `RobotDetail`, `BenchDetail` and `ComputeAisle`. Images are rendered in Cycles on CPU at 1440 × 1000, 48 samples. Render one or two cameras per action on slower machines rather than exceeding the configured host timeout.
5. Use the final native file as `input_blend` with its reviewed `expected_source_sha256` for `export_environment_bundle.py`. It produces `environment-manifest.json`, unique FBX files, copied texture maps and the complete `industrial-lab.glb`. Its saved `.blend` uses packed images and relative resource paths.
6. Run `sanitize_scene.py` against a work copy intended for sharing and scan the saved decompressed bytes for private paths. It cleans packed-image records and file-browser state as well as the render path.
7. Inspect the returned evidence, independently reopen the native file, and reimport the GLB. An Unreal import must then check its own actor transforms, mesh bounds, materials and render; Blender success alone is not Unreal evidence.

Each native action creates a new run directory. Never replace the input file. Capture its SHA-256 before and after revision.

## A concrete follow-up edit

Against an existing native file, run `revise_and_render.py` with:

```json
{
  "lighting_theme": "night",
  "cart_offset_x_m": 0.4,
  "camera": "Overview",
  "render_views": ["Overview"]
}
```

The script changes the existing lighting setup and translates the diagnostics cart as a group while retaining all meshes. `revision-evidence.json` records actual positions and lighting state before and after. The supported `day` preset restores daytime lights and emissive values. Applying an offset of `-0.4` to the edited file restores the cart position.

The bridge validates reopening and preserves the source hash. This controlled example demonstrates a conversational revision with native evidence; it is not a guarantee that arbitrary prose edits are correct automatically.

## Interchange boundaries

The manifest stores Blender coordinates in metres and explicit transforms. Unique FBX assets contain local geometry at identity. The Unreal adapter is responsible for handedness and centimetre conversion, material reconstruction and instance placement. Packed Principled base-color/roughness/normal images and scalar metallic/emission values are available; rendering appearance also depends on each engine's lighting, normal strength, exposure and color management. Native curves, text editing, bevel modifiers and hierarchy remain in the `.blend`; exported meshes are evaluated static snapshots.

Raw bridge reports and logs remain in the local workspace and can contain paths. Public evidence should be a curated summary. Strip PNG metadata without modifying pixels before sharing images; keep public artifact hashes distinct from native raw-output hashes.

## Recorded native validation

`observed-native-proof.json` contains the curated run summary and hashes. Blender 5.2.0 LTS independently reopened the final native day and night files. A fresh import of the corrected GLB preserved 1,571 mesh objects, 17 materials and aggregate bounds. The exporter checked every object's material assignment and embedded all 18 images. Its 82 GLB mesh definitions can differ from the 84 FBX prototype entries because interchange representations are deduplicated differently; all instances were retained.

The initial direct glTF export lost materials when linked meshes used object-level material overrides. That candidate was rejected. The final exporter builds temporary evaluated prototypes keyed by geometry and resolved material slots, restores node names, and checks assignments. The working native scene remains unchanged.

The demonstrated night revision moved all 28 cart objects by +0.4 m. Returning to day and moving -0.4 m restored lighting and emission exactly, with a maximum cart position difference of 2.39e-7 m. This is floating-point tolerance, not a physical accuracy claim.
