# Unreal editor automation

The Unreal adapter launches a native Unreal editor process. Legacy
operations use the PythonScript commandlet; manifest-driven environments use the
full editor through `-ExecutePythonScript`, because native material tools require
Slate on the tested 5.7.4 build. On macOS, environment operations select the
installed `UnrealEditor.app` executable next to `UnrealEditor-Cmd` when available.
It does not expose an HTTP
listener or enable Remote Execution. Only generated projects inside the configured
workspace are accepted. Existing unmarked projects are refused.

The interface is `probe(config)` and `execute(workspace, request, run_dir, config)`.
The MCP server resolves trusted configuration and records the run. A discovered
binary is not an execution test. Each native run writes structured measured
evidence; an exit code of zero alone is never called verified.

## Operations

| Action | Parameters | Native work |
| --- | --- | --- |
| `create_project` | `name` (default `StudioAgentLab`) | Creates a fresh `.uproject`, enables PythonScriptPlugin, EditorScriptingUtilities and SequencerScripting, launches the engine and saves a native bootstrap map. |
| `import_asset` | `source`: workspace-relative FBX; optional `destination`, `expected_size_cm` | Uses `AssetImportTask` and explicit static-mesh FBX options; saves and reloads real assets, counts triangles/LODs/material slots and measures bounds in centimeters. |
| `build_scene` | optional `name`, `asset_paths`, `sequence` | Creates a level containing imported static meshes, stage, material graphs, three lights, a cine camera and a keyed camera transform sequence. Saves and reopens the map and checks required actors. |
| `inspect` | optional `level_path`, `sequence_path` | Loads saved native packages in a new editor process, records actor and mesh properties, and optionally verifies persisted camera tracks and camera-cut binding resolution. |
| `run_script` | `script_path`; optional `expected_artifacts` | Runs trusted Python against the full installed `unreal` API. Requires `allow_scripts=True`. Reports execution as unverified because arbitrary custom intent cannot be established from file existence. |

Destination packages are constrained to `/Game/StudioAgentBridge/...`. The
`unreal_project` configuration may select another adapter-created `.uproject`
within the workspace. Operations acquire a process lock for their project.
Existing `level_path`, `sequence_path` and `asset_paths` accept either package
paths such as `/Game/StudioAgentBridge/Sequences/Move` or the full object reference
returned by native inspection, `/Game/StudioAgentBridge/Sequences/Move.Move`.
The suffix must exactly match the package's final name; subobject references,
extra suffixes and paths outside the generated namespace are refused. Creation
destinations still require a package path without an object suffix.
Custom Python is **not a sandbox**: trusted code can access everything permitted
to the native editor process. Do not enable it for untrusted documents or users.

## Blender handoff

Use the Blender adapter's FBX output as `source`. The source scene uses meters;
the exporter encodes FBX units and the importer uses `convert_scene=True`,
`convert_scene_unit=True`, `import_uniform_scale=1.0` and absolute mesh vertices.
When supplied, `expected_size_cm` checks combined mesh bounds with a 3% tolerance
(minimum 0.5 cm). Axis conversion can permute dimensions, so this size check
compares sorted bounds; it does not certify forward direction or handedness.

The legacy FBX pipeline is explicitly selected in this **generated project's**
configuration so `FbxImportUI` settings are respected. Parts remain separate
static mesh assets. Material slots are imported; Blender shader nodes,
constraints and procedural modifiers remain in the editable `.blend` source.
Unreal materials are editable native graphs, but equivalent rendered appearance
between engines is not assumed.

The Blender exporter enables face smoothing groups explicitly. An actual 5.7.4
headless import without those groups built geometry but then crashed in Epic's
FBX warning logger while trying to open a Slate message window. Exporting the
smoothing groups resolved that observed failure. Other importer warnings can
still trigger native engine problems; logs and failed status remain visible.

## Complete environment import

The Blender `export_environment` action supplies an `environment-manifest/v1`
bundle containing evaluated local FBX meshes, image textures and explicit world
transforms. Use `build_scene` with `environment_manifest`, `name` and a **fresh**
`destination` under `/Game/StudioAgentBridge/Environments/`. `import_asset` accepts
the same manifest to create assets without a level; `inspect` reads an existing
level and its resources without recreating them.
If `build_scene` supplies `level_path`, it must remain inside the fresh
destination, and an existing map is refused before any asset import.

| Parameter | Purpose |
| --- | --- |
| `environment_manifest` | Workspace-relative manifest. All FBX/image paths must stay in its bundle, without symlinks, and match SHA-256. |
| `destination` | Native package namespace. Construction refuses an existing namespace to preserve edits. |
| `simple_collision` | Default `true`: creates and measures one box proxy per unique mesh. This is not exact collision or navigation. |
| `level_path` | Saved native map to reopen during `inspect`. |
| `rendering` | `structure` uses NullRHI; `gpu` activates the platform renderer. Both environment modes select the full editor. On macOS with detected Unreal 5.7.4, manifest-driven `build_scene` requires explicit `gpu`; the known failing NullRHI combination is blocked before launch. |
| `render_output` | New workspace PNG path. Requires `gpu`. Existing files are refused. |
| `camera_name` | Exported camera label to render; default is the first native cine camera. |
| `render_size` | `[width,height]`, each 64–2048; default `[1280,720]`. |
| `render_exposure` | Manual exposure compensation in EV, default `-7`, bounded to −16…16. It is recorded in the image evidence. |

For example, after creating a project and exporting the Blender environment:

```json
{
  "environment_manifest": "exports/lab/environment-manifest.json",
  "name": "IndustrialLab",
  "destination": "/Game/StudioAgentBridge/Environments/IndustrialLab",
  "simple_collision": true,
  "rendering": "gpu",
  "render_output": "renders/lab-overview.png",
  "camera_name": "Camera_Overview",
  "render_exposure": -7
}
```

Pass this as `build_scene` parameters through MCP or the CLI. Set
`STUDIO_UNREAL_PROJECT` to the adapter-created `.uproject` when selecting a
non-default project; the job name alone does not select a project. A subsequent
`inspect` must provide the same manifest/destination and the saved `level_path`.
Use a different `render_output` for each inspection.

### Preflight for the observed macOS NullRHI failure

On the tested Unreal 5.7.4 macOS installation, full-editor environment construction
with `rendering: "structure"` crashed during actor placement after saving imported
assets. The adapter now blocks that exact action/platform/version combination
before native launch or project writes. Omitting `rendering` selects `structure`
and is blocked too. Select `"rendering": "gpu"` explicitly to construct this
environment; the bridge never switches renderers or retries automatically.

`editor_session` is derived from a nonempty `environment_manifest`; it is not a
client override. Ordinary commandlet operations and manifest-only `import_asset`
or `inspect` retain their existing behavior. Other versions/platforms are outside
this measured compatibility rule, not certified compatible. The Engine still
records a blocked action and advances the job revision. See the
[preflight design and evidence](unreal-preflight.md) for the distinction between
ledger writes, project preservation and native execution.

Meshes preserve shared asset references with individually editable actors.
The importer measures local bounds against the manifest, including axis signs
and asymmetric extents, with a 0.05 cm tolerance. It reflects Blender Y and
converts meters to centimeters; quaternion mapping follows the same reflection.
Each saved actor is checked against its world translation, quaternion, scale
and expected mesh. Source parenting is flattened into world transforms and
category folders. Shear greater than `1e-5` is refused.

Native material graphs include base color, roughness, metallic, alpha, emission,
two-sided settings and supported image textures. Scalar/color inputs and mesh
material assignments are reread after reopening. Normal maps use green-channel
inversion for Blender OpenGL to Unreal DirectX; source normal strength scales
tangent X/Y followed by normalization. This is a declared translation policy,
not a claim of pixel parity. Procedural Blender graphs still require baking.

Lights and cine cameras are recreated and checked after reopening. Local light
energy maps at 100 lumens per source watt, and sun energy at 1000 lux per unit;
these are artistic transfer factors, not a photometric calibration. Image
capture uses a selected camera, waits for shader/resource compilation and 120
full editor frames, then exports a GPU SceneCapture2D image. The adapter keeps
the Python editor session alive until capture and lets the Python executor close
it on the following tick after clearing its keep-alive flag.
The PNG still needs visual review: file existence alone is not appearance proof.

Native diagnostics retained during development distinguish three failures from
the completed pipeline: a missing Metal toolchain prevented rendering; a
commandlet-only material creation crashed because Slate was unavailable; and a
commandlet GPU capture exported a black PNG even after compilation completed.
These were not accepted as successful environment renders. Full editor frames
and manual exposure are necessary for the demonstrated image workflow.

An additional shutdown defect was reproduced with a three-frame minimal script
in the same editor/project: calling `SystemLibrary.quit_editor()` inside a Slate
callback produced exit code 1 and a native Slate/ICU abort after writing the
report. Clearing `EditorPythonScripting.set_keep_python_script_alive(False)`
instead returned exit code 0. The latter lets the executor destroy its
notification before deferring shutdown. The adapter retains the nonzero-process
failure gate; it does not promote the failed runs because they happened to
produce files.

### Observed original industrial lab

On 20 October 2026, Unreal 5.7.4 reopened the final lab map in a fresh process,
returned **exit code 0**, verified its saved resources and exported a visually
reviewed 1920×1080 GPU image. The source was the final `000016-run_script`
environment bundle, manifest SHA-256
`856ee52312b3d8b38424bf58f09be554ee10797229ce3415e8b6f543547208d4`.

| Measurement | Native result |
| --- | --- |
| Mesh actors / unique mesh assets | 1,571 / 84 |
| Reused mesh references beyond the first instance | 1,487 |
| Materials / image textures | 17 / 18 |
| Reopened lights / cine cameras | 9 / 4 |
| Simple collision boxes | 84 |
| Largest local bounds residual | 0.0000716 cm, below the 0.05 cm tolerance |
| Native unique / instanced triangles | 26,280 / 253,404 |

The source has 26,304 unique and 253,920 instanced triangles. Native default
degenerate removal changes the counts on two mesh assets; the evidence retains
both measurements. Asset instancing, transforms, material inputs and bounds
passed independently of that count difference. This is a complete editable
environment handoff, with measured box collisions and GPU appearance. It does
not certify a navigation bake, gameplay, frame rate, physically calibrated
lighting or identical pixels between Blender and Unreal.

The saved map is
`/Game/StudioAgentBridge/Environments/IndustrialLabFinal/Maps/IndustrialLabFinal`
in the local `IndustrialLab.uproject`. The curated evidence file is named
`industrial-lab-final-public-evidence.json`; it includes the complete per-object
and per-resource verification without personal filesystem paths. Failed
diagnostic renders and shutdown runs are excluded from the final proof.

## What verification means

Native JSON is returned with `.uproject`, `.umap`, `.uasset` and process-log
artifacts; the adapter hashes files and the MCP layer rechecks those hashes.
The manifested log is `unreal-process.snapshot.log`, frozen when the editor
process exits. Native helper processes can continue appending to inherited stdout;
that later output stays in the separate, unmanifested `unreal-process.log`.
Its contents are useful diagnostics but are not treated as immutable evidence.
Scene verification reopens the saved map. Mesh verification measures the loaded
native mesh, not a screenshot or an invented triangle count. Local process logs
can contain machine paths and should not be published indiscriminately.

The world is saved **before** the sequence binds its camera. A fresh-process
check caught a real failure when a possessable was bound against an Untitled
world: its keys existed but its camera did not resolve after reopening. The
final implementation checks both the transform binding and camera-cut binding.
The generated project's startup map points to the successfully built level.

The legacy commandlet uses `-nullrhi` for structural work. It does **not** verify GPU
rendering, Lumen, Nanite, animation appearance, a packaged game or performance.
The camera sequence preset authors transform channels and a camera-cut track;
a Movie Render Queue job is not included. Additional capabilities can be authored
through a trusted script and must receive their own checks. Python is editor-only and is not a runtime game
language. Full Blueprint graph authoring is not claimed.

If startup, licensing, plugins or platform tooling block the native process,
the result is `blocked`/`failed` with the log rather than fabricated output. The
adapter does not accept license agreements, change Xcode settings or install
engine plugins outside its generated project.

## Official references

- [Epic: scripting the editor using Python](https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python?application_version=5.7)
- [Epic Python API: AssetImportTask](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AssetImportTask?application_version=5.7)
- [Epic Python API: FbxImportUI](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/FbxImportUI?application_version=5.7)
- [Epic Python API: EditorLoadingAndSavingUtils](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EditorLoadingAndSavingUtils?application_version=5.7)
- [Epic: Remote Control](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-for-unreal-engine) describes an alternative HTTP/WebSocket integration. This implementation launches a local editor process and does not enable that server.

The local native headers and Python commandlet implementation were also inspected
against Unreal Engine 5.7.4. Integration outcomes are recorded separately from
the wrapper's unit tests; mocked process tests are not engine evidence.
