# Unreal editor automation

The Unreal adapter launches **UnrealEditor-Cmd** as a separate process using the
native PythonScript commandlet. It does not drive the desktop, expose an HTTP
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

The commandlet uses `-nullrhi` for structural work. It does **not** verify GPU
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
- [Epic: Remote Control](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-for-unreal-engine) describes an alternative HTTP/WebSocket integration. This implementation chooses a local commandlet and does not enable that server.

The local native headers and Python commandlet implementation were also inspected
against Unreal Engine 5.7.4. Integration outcomes are recorded separately from
the wrapper's unit tests; mocked process tests are not engine evidence.
