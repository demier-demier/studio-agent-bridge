# Blender native adapter

The adapter drives the installed Blender executable through its Python API. A client agent can create a scene, inspect it, write a native Python revision, export geometry, and render the result. Each operation starts a fresh background process with automatic file scripts disabled. Saved files are reopened in another Blender process before a result is marked verified.

There is no model or LLM inside this adapter. The MCP client supplies the reasoning and, when authorized, native modeling scripts. `create_demo` is a reproducible original scene generator used to exercise the pipeline; it is not evidence that arbitrary engineering requirements can be solved automatically.

## Actions

| Action | Required parameters | Result |
| --- | --- | --- |
| `create_demo` | None | Editable inspection rover, GLB, FBX, PNG and verification report |
| `inspect` | `input_blend` | Measured object, material, modifier and dimension data; source hash checked |
| `import_asset` | `asset_path` | Imports GLB/glTF or FBX into a fresh scene or optional `input_blend`; saves a new native file |
| `export_asset` | `input_blend` | New native copy, requested `export_formats` (`glb`, `fbx`), environment manifest, optional unique local mesh assets; separate-process import verification |
| `render` | `input_blend` | New native copy and PNG using the active camera and bounded `render_settings` |
| `run_script` | `script_path`; optional `input_blend` | Executes an explicitly trusted Python file, saves a new scene, and optionally renders it |

All file parameters are relative to the configured workspace. Paths that resolve outside it are rejected. Every operation uses a fresh, empty output directory. The original input file is hashed before and after execution; unexpected changes prevent a verified result.

`export_formats` defaults to `["glb", "fbx"]`. `render` defaults to true for `create_demo` and `render`, and false otherwise. `script_parameters` is passed as `STUDIO_PARAMETERS` to native scripts; `STUDIO_OUTPUT_DIR` is also provided. Scripts can use all installed `bpy` APIs, including geometry nodes, modifiers, constraints, animation, materials, and rendering. Those facilities are not all exercised by the bundled example.

## Reusable environment export

`export_asset` can transfer a scene assembled from repeated meshes and groups rather than treating the scene as one combined mesh. It keeps the editable `.blend`, emits whole-scene GLB/FBX previews, and writes `environment-manifest/v1`. With `split_assets: true`, it also exports **one local-space FBX per unique evaluated geometry/material combination**. Every placed object references that shared asset and carries its own world transform.

```json
{
  "input_blend": "scenes/industrial_lab.blend",
  "export_collection": "StudioEnvironment",
  "include_presentation": true,
  "environment_manifest": true,
  "split_assets": true,
  "export_formats": ["glb", "fbx"],
  "max_assets": 256,
  "max_objects": 5000
}
```

Parameters:

| Parameter | Default and meaning |
| --- | --- |
| `export_collection` | Optional exact collection name, including nested objects. A missing explicit collection fails; it never falls back to the entire scene. |
| `include_presentation` | `false`. When true, also includes cameras/lights from `PresentationRig`, or scene cameras/lights when that collection is absent. |
| `environment_manifest` | `true` for `export_asset`; `false` for the legacy `create_demo`. Describes static evaluated geometry, assets, instances, groups, materials, rig and bounds. |
| `split_assets` | `false`. Requires `environment_manifest`; when true, writes per-asset FBX files under `assets/meshes/`. Required for the manifest-based Unreal reconstruction. |
| `max_assets` | `256`, accepted range 1–256 unique evaluated mesh/material combinations. Repeated objects sharing equal geometry and slots reuse an asset. |
| `max_objects` | `5000`, accepted range 1–5000 evaluated mesh instances. |
| `render_settings` | Optional `{ "engine": "CYCLES", "resolution": [1920,1080], "samples": 32 }`. Engines: `CYCLES` (CPU) or `BLENDER_EEVEE`; dimensions 64–3840 each, at most 8,294,400 pixels; samples 1–256. Defaults remain Cycles CPU, 1200×900, 32 samples. |

Automatic selection order is `StudioEnvironment`, objects tagged `studio_asset=True` and their ancestors, the legacy `InspectionRover` collection, then the scene. `studio_export=False` explicitly excludes an object or image. Objects retain `studio_category` and collection membership. Convert exportable text/curves to meshes, keeping editable source geometry in another collection; unsupported selected types fail rather than disappearing silently.

The manifest preserves original names, mesh-data names, geometry hashes, material slots, shared-asset references, parent/group information, source modifiers, visibility, local asset bounds, world object bounds and aggregate counts. Local and world distances are **meters**. `matrixWorld` contains row-major arrays for column-vector multiplication, with translation already in meters. Quaternions use `[w,x,y,z]`. Coordinates remain Blender right-handed, Z-up; the Unreal consumer applies the handedness and centimeter conversion. The local FBX object is a temporary copy at the origin with identity transform; its mesh contains evaluated modifiers. The source object and input `.blend` are not transformed or rewritten.

Geometry deduplication covers evaluated positions, triangles/material indices, corner normals, UV layers, vertex colors and material slot names. Camera records include focal length, sensor dimensions and clipping distances; light records include type, color, native energy and shape. Blender camera/light forward is local `-Z` and up is local `+Y`; target camera orientation requires an additional basis conversion. Native light energy is not asserted to be a calibrated Unreal intensity.

Whole-scene GLB export also uses temporary evaluated copies, with materials assigned on each unique mesh. This avoids a measured Blender glTF export failure where linked meshes with object material overrides retained geometry but lost eight assigned materials. The exporter restores source node names in the GLB JSON, removes all temporary native objects and meshes, and restores selection. A `*.glb.checks.json` report verifies the binary container, selected mesh-instance count, assigned materials **per object**, and embedded image/buffer resources. The portable structural reader accepts GLBs up to 256 MB. These checks are an additional gate before native reimport and visual review.

### Materials, images and provenance

The exporter reads an active, directly connected Principled BSDF surface. It records base color, metallic, roughness, alpha and emission values, plus directly linked image textures and Normal Map image inputs. Referenced images are copied from packed bytes or existing files, with content hashes and color-space labels. Missing declared images, unsupported image formats or files over 128 MB fail export. Procedural/mixed shaders require baking; unsupported shader or mapping nodes are explicitly listed as limitations rather than being called equivalent materials.

Set `studio_provenance` on authored objects and images, for example `original-procedural`, and retain any separate license/source records for third-party assets. The exporter records that declared provenance; it does not infer ownership or verify license rights. Public manifests omit absolute source texture paths.

The adapter rechecks manifest structure, asset/material references, count consistency and every referenced asset/texture hash. References must resolve inside the output run directory, including after symlink resolution. These checks provide bundle integrity, not visual equivalence or a publisher signature. An Unreal import must independently reopen and measure the resulting assets and map. Whole-scene GLB/FBX roundtrips continue to check mesh count and exact evaluated bounds; they do not certify all shader features.

The industrial lab case exercised this pipeline in Blender 5.2: 1,571 placed mesh objects, 17 materials, 18 embedded image maps, four cameras and nine lights. The corrected 2,479,564-byte GLB passed every object-to-material comparison. A separate native GLB import and `.blend` reopen retained 1,571 meshes, 17 materials and bounds of approximately 12.4 × 9.4 × 4.52 meters. The final manifest contains 84 exact geometry/material signatures; the GLB has 82 mesh definitions after its own representation deduplication. These are asset-definition counts, not missing placed objects.

Exact signatures can change across native evaluations: the initial lab bundle had 83 signatures. Comparing the affected FBX numeric arrays found unchanged positions, topology, normals and material assignments, with one UV value differing in each of 12 objects by at most `5.96e-8`. The exporter deliberately retains exact UV data instead of rounding it to force a stable deduplication count.

### Current interchange limits

- Static evaluated snapshots do not transfer animation, drivers, simulation, CAD history or editable Blender modifiers to Unreal; those remain in `.blend`.
- Sheared transforms retain a full matrix and a measured `shearResidual`, but a TRS-only target must reject or bake them. Negative-scale and instancing behavior still require target-native verification.
- PBR scalar/image transfer does not reproduce arbitrary shader graphs, texture mapping nodes, volume shaders or all renderer-specific settings. UV/image baking is needed for those materials.
- Light intensity, exposure, color management, collision, lightmaps, Nanite, packaged runtime performance and GPU visual equivalence require separate target tests.

## Example workflow

Install the project as described in the root README. Create a job and use its returned ID:

```sh
studio-agent-bridge --workspace "$HOME/StudioAgentWorkspace" job create \
  --name inspection-rover --host blender \
  --goal "Build and inspect an editable modular rover visualization"

studio-agent-bridge --workspace "$HOME/StudioAgentWorkspace" run \
  --job JOB_ID --action create_demo --parameters '{}'
```

The JSON result contains the actual artifact paths. Use the returned `.blend` path for subsequent operations. Copy `examples/blender/raise_sensor_mast.py` into your workspace under `scripts/`, then explicitly enable trusted scripts:

```sh
studio-agent-bridge --workspace "$HOME/StudioAgentWorkspace" --allow-scripts run \
  --job JOB_ID --action run_script \
  --parameters '{"input_blend":"PATH_FROM_PREVIOUS_RESULT.blend","script_path":"scripts/raise_sensor_mast.py","script_parameters":{"extra_height_m":0.15},"render":true}'
```

The example changes the lower mast height and moves the upper sensors together. It preserves the original `.blend`, retains separate editable objects and modifiers, and records the measured new dimensions after reopening. It demonstrates a native revision beyond the fixed generator.

The host must have Blender installed. The adapter searches `PATH`, the standard macOS application location, and standard Windows Blender installation folders. Server configuration can set `blender_executable` explicitly. `blender_timeout_seconds` defaults to 180 and accepts 5–900 seconds; large scenes may need a larger value. A timeout kills the launched Blender process and reports failure rather than returning a successful model.

## Original demonstration asset

The procedural inspection rover includes chassis and service panels, four independent wheel assemblies, suspension members, individually named fasteners, rangefinders, stereo camera, lidar mast, a visual probe arm, antenna, lights, materials, camera and presentation floor. The `InspectionRover` collection is exported; `PresentationStudio` is retained only in the native scene.

Observed on Blender 5.2.0 LTS:

- 90 scene objects, including 84 asset meshes and one studio floor mesh.
- 84 editable bevel modifiers, 9 materials, and one keyed lidar-cover rotation.
- Asset dimensions approximately 1.740 × 1.352 × 1.3525 meters.
- Independent native reopening preserved object names, mesh count, modifiers and dimensions.
- GLB and FBX roundtrips preserved 84 mesh objects and dimensions within 2 mm.
- A trusted script raised the mast 0.15 m, changing the measured total height to 1.5025 m while the source file hash remained unchanged.
- Both original and revised PNGs were rendered and visually inspected during development.

These are observed properties of the demonstration, not a claim of manufacturing readiness. The rover is an editable visual assembly: it has no verified load cases, physical suspension simulation, engineering tolerances, watertight assembly guarantee, articulated robot controller, or production specification. Its small animation remains in the `.blend`; the default mesh exports intentionally exclude animation.

## Blender → Unreal interchange

Blender geometry is authored in meters (`METRIC`, unit scale 1). FBX export uses `-Z` forward, `Y` up, `FBX_SCALE_UNITS`, applied unit scale, evaluated mesh modifiers and **face smoothing groups**. GLB uses standard glTF Y-up output. Native `.blend` files retain their editable modifiers.

The Unreal adapter should import with scene and scene-unit conversion enabled. Expected aggregate dimensions are approximately 174 × 135.2 × 135.25 centimeters. Validate those values after import; do not infer correctness from file creation alone.

Face smoothing was added after a real headless Unreal import encountered a missing-smoothing warning that attempted to create a UI message window. Blender roundtrip validation confirms the corrected FBX geometry; Unreal's own report is the evidence for Unreal import. Blender success alone does not prove Unreal import, shader equivalence, collisions, Nanite behavior, lightmaps or packaged-game readiness.

## Verification and security boundaries

Native verification compares measured object names, mesh counts, modifier counts and overall dimensions after reopening. Export verification imports each requested format into a separate empty Blender process and compares asset mesh counts and aggregate dimensions. Reports include detailed per-object dimensions and materials for inspection. These checks detect missing geometry and scale errors; aggregate checks cannot establish that every vertex, material, rig or local transform is identical.

`run_script` is disabled unless the server starts with `--allow-scripts` or `STUDIO_ALLOW_SCRIPTS=1`. A request cannot enable its own privilege. Authorized Python runs with the OS user's permissions. `--disable-autoexec` and `open_mainfile(use_scripts=False)` disable embedded automatic scripts; they do not sandbox explicit scripts or Blender's native file parsers. Only open trusted native files/assets. Imported formats may resolve referenced resources. Use OS/container isolation for hostile input.

The adapter does not claim to undo arbitrary script side effects. Source-hash checks detect modifications to declared inputs after execution; they do not provide a general filesystem rollback. Logs and verification reports stay in the local workspace and may contain local paths.

Unit tests use mocks to check boundaries, failures and status handling. Native tests are opt-in and require Blender:

```sh
python -m unittest discover -s tests -p 'test_blender*.py' -v
STUDIO_BLENDER_TEST_WORKSPACE="$HOME/StudioAgentWorkspace" \
  python -m unittest discover -s tests -p 'test_blender_integration.py' -v
```

## Primary references

- [Blender command-line Python and automatic execution options](https://docs.blender.org/manual/en/latest/advanced/command_line/arguments.html)
- [Blender FBX and glTF export operators](https://docs.blender.org/api/current/bpy.ops.export_scene.html)
- [Blender import operators](https://docs.blender.org/api/current/bpy.ops.import_scene.html)
- [Blender data access API](https://docs.blender.org/api/current/bpy.data.html)

The implementation was exercised against the installed Blender 5.2.0 LTS API. Other versions require their own native integration run.
