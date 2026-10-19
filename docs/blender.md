# Blender native adapter

The adapter drives the installed Blender executable through its Python API. A client agent can create a scene, inspect it, write a native Python revision, export geometry, and render the result. Each operation starts a fresh background process with automatic file scripts disabled. Saved files are reopened in another Blender process before a result is marked verified.

There is no model or LLM inside this adapter. The MCP client supplies the reasoning and, when authorized, native modeling scripts. `create_demo` is a reproducible original scene generator used to exercise the pipeline; it is not evidence that arbitrary engineering requirements can be solved automatically.

## Actions

| Action | Required parameters | Result |
| --- | --- | --- |
| `create_demo` | None | Editable inspection rover, GLB, FBX, PNG and verification report |
| `inspect` | `input_blend` | Measured object, material, modifier and dimension data; source hash checked |
| `import_asset` | `asset_path` | Imports GLB/glTF or FBX into a fresh scene or optional `input_blend`; saves a new native file |
| `export_asset` | `input_blend` | New native copy and requested `export_formats` (`glb`, `fbx`); separate-process import verification |
| `render` | `input_blend` | New native copy and 1200 × 900 PNG using the active camera |
| `run_script` | `script_path`; optional `input_blend` | Executes an explicitly trusted Python file, saves a new scene, and optionally renders it |

All file parameters are relative to the configured workspace. Paths that resolve outside it are rejected. Every operation uses a fresh, empty output directory. The original input file is hashed before and after execution; unexpected changes prevent a verified result.

`export_formats` defaults to `["glb", "fbx"]`. `render` defaults to true for `create_demo` and `render`, and false otherwise. `script_parameters` is passed as `STUDIO_PARAMETERS` to native scripts; `STUDIO_OUTPUT_DIR` is also provided. Scripts can use all installed `bpy` APIs, including geometry nodes, modifiers, constraints, animation, materials, and rendering. Those facilities are not all exercised by the bundled example.

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
