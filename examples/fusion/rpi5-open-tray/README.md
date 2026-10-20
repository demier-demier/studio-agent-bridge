# A real board reference, an original editable fixture

This case designs an **open mounting tray for a Raspberry Pi 5 development bench** from the manufacturer's public mechanical drawing. A tray with four standoffs, through-bores, separate chassis holes and a central underside opening is a useful starting point for a robotics or edge-AI prototype.

![Actual Fusion geometry rendered in Blender](../../../docs/images/rpi5-open-tray.png)

**Verified against approximate drawing dimensions; physical fit has not been tested.** The source drawing explicitly limits its dimensions to reference use and does not show all board components. This is a prototype fixture, not a manufacturing release, a complete enclosure or a tested cooling solution.

## What was actually built and measured

| Property | Baseline | Edited variant |
| --- | --- | --- |
| Measured overall size | 101 × 72 × 11 mm | 109 × 80 × 14 mm |
| Base plate thickness | 3 mm | 3 mm |
| Board underside elevation above the base top | 8 mm | 11 mm |
| Margin around nominal board footprint | 8 mm | 12 mm |
| Four board mounting bores | Ø2.7 mm; 58 × 49 mm pattern | Same pattern, moved with the board origin |
| Fixture structure | 1 connected positive-volume solid | 1 connected positive-volume solid |
| Native editability | 13 parameters; 15 fully constrained sketches; 43 timeline entries | All retained and healthy |

The script reads **cylindrical BRep faces** after construction and again from each reopened F3D. It checks actual bore axes, diameters and full depths, point containment in all eight holes and the central relief, body bounds, sketch constraint states and feature health. These checks do not rely solely on parameter values or screenshots.

Both F3D files were independently reopened. The original hash remained unchanged during the edit. The Fusion STL was imported into Blender using an explicit millimetre-to-metre conversion, then the `.blend` was reopened in a fresh process. Measured tray bounds differed from CAD by less than **0.00001 mm** (floating-point comparison, **not a manufacturing tolerance**). A render uses that imported geometry; the board and its components are deliberately not approximated as a fake fit test.

[Machine-readable native evidence](observed-native-proof.json) · [Source dimensions and attribution](dimensions.json) · [Fusion viewport](../../../docs/images/rpi5-fusion-native.png)

## Reproduce through MCP

Use the root [installation guide](../../../README.md). Copy `build_tray.py` and `render_tray.py` to `scripts/rpi5/` in your configured modeling workspace. Fusion must be open with its local MCP server enabled. Native Python requires `STUDIO_ALLOW_SCRIPTS=1`.

1. Call `studio_status`, then `studio_create_job` with `host: "fusion"` and your acceptance brief.
2. Call `studio_execute_action` with the returned job ID/revision, `action: "run_script"`, and:

```json
{"script_path":"scripts/rpi5/build_tray.py","formats":["f3d","step","stl"]}
```

3. Use the returned F3D artifact path in `edit_parameters`:

```json
{
  "input_design":"PATH_RETURNED_BY_THE_PREVIOUS_ACTION.f3d",
  "parameters":{"edge_margin":"12 mm","board_clearance":"11 mm"},
  "formats":["f3d","step","stl"]
}
```

4. Run `build_tray.py` again with each F3D as `input_design` and `formats: ["f3d"]`. On an owned existing tray it performs measurement only. Compare `tray-measurements.json`, then inspect the job. Always use the latest returned revision; never automatically replay an interrupted native action.
5. For visualization, create a separate Blender job and execute `run_script`:

```json
{
  "script_path":"scripts/rpi5/render_tray.py",
  "script_parameters":{"stl_path":"BASELINE_STL_PATH_FROM_FUSION.stl"},
  "render":true
}
```

The renderer intentionally validates the **baseline** 101 × 72 × 11 mm fixture. Its scene preserves a mesh, camera, materials and lighting; parametric history remains in the F3D. The current Fusion adapter uses the generic `sensor-enclosure` export stem even for custom scripts; select artifacts by the returned paths, not their filename alone.

## Editable design choices and validation boundaries

The reference board dimensions are separate from original fixture choices. The source defines an 85 × 56 mm board, 58 × 49 mm mounting pattern, 3.5 mm X offset and Ø2.7 mm mounting bores. The Y edge offset is derived from the symmetric rows as `(56 - 49) / 2`. It must still be confirmed on a physical board.

`expected_geometry()` checks a bounded prototype parameter domain: margin 6–25 mm, plate 2–8 mm, board clearance 5–25 mm, standoff outside diameter 5–7 mm, chassis bore 2.5–4 mm and relief inset 8–15 mm. The two native configurations above were actually tested; the whole domain is not a native stress-test matrix. The case validator runs in `build_tray.py`; generic `edit_parameters` only verifies native geometry/history and cannot certify application-specific fit.

Before fabrication, inspect a physical board and its underside components, choose the actual fasteners, check connector/cable/HAT/cooler access, select material and process, and validate strength, tolerances and temperature. The open layout avoids inventing connector cutouts, but does not establish component clearance. STEP was exported but not reimported in this case. STL scale was checked in Blender; mesh topology and print preparation were not validated. Neither format retains the Fusion timeline.

## Source, attribution and originality

- Raspberry Pi Ltd, [Raspberry Pi 5 mechanical drawing](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf), accessed 19 October 2026; the PDF hash is recorded in `dimensions.json`.
- [Publisher licensing policy](https://www.raspberrypi.com/licensing/). The drawing has no explicit licence printed on its page. The public repository contains **no copy of the official PDF, diagram screenshot, board model or logo**. It cites factual dimensions and publishes an original fixture and original scripts.

Raspberry Pi identifies the intended board; no endorsement or affiliation is implied. The original code is covered by this repository's MIT licence. Source notices remain separate.
