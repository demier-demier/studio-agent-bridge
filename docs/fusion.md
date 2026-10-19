# Autodesk Fusion adapter

The adapter connects to Fusion's built-in local MCP at `http://127.0.0.1:27182/mcp`. Open Fusion and enable its local server in Preferences → General → API. It discovers the native execution interface and uses `fusion_mcp_execute` with a Python `run(context)` entry point. It never uses the unrelated Fusion cloud data service.

## Actions and parameters

| Action | Parameters | Behavior |
| --- | --- | --- |
| `create_demo` | Optional `dimensions` mapping parameter names to unit expressions; `formats` | Creates a new original parametric enclosure document |
| `inspect` | `input_design` (`.f3d`) | Imports the archive into a temporary document and measures geometry/history |
| `edit_parameters` | `input_design`, `parameters` mapping names to expressions | Edits a new work copy, recomputes, exports and reopens the archive |
| `export_asset` | `input_design`, `formats`: `f3d`, `step`, `stl` | Produces exports from a new work copy |
| `run_script` | `script_path`, optional `input_design`, `formats` | Runs trusted native Python in a new document/work copy |
| `api_documentation` | `search_pattern`; optional `category` | Retrieves version-specific API documentation from the running host |

All input files are workspace relative. Operations create/import their own document, close only that work copy after export and restore the previously active document. They do not silently select an existing user's active model as an editing target. Source F3D files are imported into new unsaved documents rather than overwritten. A failed native mutation is not automatically retried.

Supported demo dimensions: `base_width`, `base_depth`, `plate_thickness`, `edge_margin`, `wall_thickness`, `housing_height`, `lid_thickness`, `mount_diameter`, `boss_outer`, `boss_bore`, `lens_diameter`, `lens_length`. Use consistent, physically valid values. Fusion evaluates unit expressions and reports invalid construction; arbitrary parameter combinations are not a product configuration solver.

## Native Python

Copy `examples/fusion/edit_lens.py` into the workspace. With trusted scripts enabled, use `run_script` on a previous F3D artifact. Scripts must define:

```python
def build(app, design, run_dir):
    dimension = design.userParameters.itemByName("lens_length")
    dimension.expression = "24 mm"
    if not design.computeAll():
        raise RuntimeError("Recompute failed")
```

The script can import `adsk.core`, `adsk.fusion` and other installed Fusion API modules. This is the extension route for sketches, constraints, extrusions, revolves, lofts, sweeps, patterns, assemblies and further exposed API capabilities. Only the bundled operations and the reported native tests are verified here. Query `api_documentation` before using version-dependent methods.

The script executes as native Python with the user's privileges; workspace path validation does not sandbox arbitrary code or its side effects. The adapter enforces an operator-side opt-in. Code cannot enable its own opt-in through action parameters.

## Geometry and archive checks

The original enclosure contains a mounting base, hollow housing, removable cover with aligned fastener holes and optical aperture, four hollow standoffs and lens barrel. The native file retains 12 user parameters, 17 fully constrained sketches and 58 timeline entries. All 8 bodies are solids with positive volumes.

Default dimensions are 160 × 100 × 70 mm. Changing `base_width` to `180 mm` and `housing_height` to `55 mm` updates dependent geometry to 180 × 100 × 80 mm. Checks inspect individual body bounds, volumes, face counts, feature health, timeline count, dimensions and sketch constraint state.

After each F3D export, the runner imports the exported archive into another document and compares seven properties: body count, timeline count, healthy features, parameter expressions, positive solid geometry, bounds and sketch constraints. Only a matching archive can make a write action verified. A plain `inspect` action verifies the reopened input archive. STEP/STL-only exports are marked `executed_unverified` until their target geometry is inspected separately; these formats do not carry the Fusion feature timeline.

The preview is a native viewport capture. It is useful for visual review, but the geometric evidence is the native inspection, not the image. This demonstration does not establish manufacturing tolerances, sealing, material strength, CAM, drawing completeness or print readiness.

## Transport and failure behavior

The adapter accepts only loopback HTTP endpoints, negotiates a native MCP session and does not retry tool calls. A network timeout may leave a host-side operation running; it raises to the job engine, which retains an unresolved-action marker. Inspect the host and reconcile before another action. `fusion_timeout_seconds` can be supplied to the adapter configuration; the default is 180 seconds and the maximum is 600.

Version exercised: Fusion 2705.1.30. Changes to native APIs require another application-backed run.

## Primary references

- [Autodesk Fusion MCP overview](https://help.autodesk.com/view/fusion360/ENU/?guid=FMCP-OVERVIEW)
- [Fusion API reference](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/WhatsNew.htm)
- [Creating a Fusion script or add-in](https://help.autodesk.com/view/fusion360/ENU/?guid=GUID-9701BBA7-EC0E-4016-A9C8-964AA4838954)

The running native MCP API-documentation tool was also consulted for the actual installed signatures.
