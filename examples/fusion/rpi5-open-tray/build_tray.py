"""Original open fixture; factual board dimensions credited in dimensions.json.

Use Studio Agent Bridge run_script. With no input, creates the model. With this
example's F3D as input, independently inspects the persisted BRep. No third-party
board model or PDF is embedded. All public checks report drawing alignment only.
"""

import json
import math
from pathlib import Path

DEFAULTS_MM = {
    "board_length": 85.0,
    "board_width": 56.0,
    "hole_pitch_x": 58.0,
    "hole_pitch_y": 49.0,
    "hole_offset_x": 3.5,
    "hole_offset_y": 3.5,
    "board_bore": 2.7,
    "edge_margin": 8.0,
    "plate_thickness": 3.0,
    "board_clearance": 8.0,
    "standoff_outer": 6.0,
    "chassis_bore": 3.4,
    "relief_inset": 9.0,
}
SOURCE_PARAMETERS = set(DEFAULTS_MM) - {
    "edge_margin",
    "plate_thickness",
    "board_clearance",
    "standoff_outer",
    "chassis_bore",
    "relief_inset",
}


def expected_geometry(values):
    """Bounded configuration domain for this fixture, not a fit solver."""
    p = {**DEFAULTS_MM, **values}
    if any(not math.isfinite(v) or v <= 0 for v in p.values()):
        raise ValueError("All dimensions must be positive finite millimetres")
    if any(abs(p[k] - DEFAULTS_MM[k]) > 1e-7 for k in SOURCE_PARAMETERS):
        raise ValueError("Board reference dimensions must match the cited drawing")
    if not 6 <= p["edge_margin"] <= 25:
        raise ValueError("edge_margin supported range is 6 to 25 mm")
    if not 2 <= p["plate_thickness"] <= 8 or not 5 <= p["board_clearance"] <= 25:
        raise ValueError("plate thickness / board clearance outside prototype domain")
    if not 5 <= p["standoff_outer"] <= 7 or not 2.5 <= p["chassis_bore"] <= 4:
        raise ValueError("standoff / chassis bore outside prototype domain")
    if not 8 <= p["relief_inset"] <= 15:
        raise ValueError("relief_inset supported range is 8 to 15 mm")
    m = p["edge_margin"]
    length, width = p["board_length"] + 2 * m, p["board_width"] + 2 * m
    mounting = [
        [m + p["hole_offset_x"] + x, m + p["hole_offset_y"] + y]
        for x in (0, p["hole_pitch_x"])
        for y in (0, p["hole_pitch_y"])
    ]
    chassis = [[x, y] for x in (m / 2, length - m / 2) for y in (m / 2, width - m / 2)]
    return {
        "parameters_mm": p,
        "size_mm": [length, width, p["plate_thickness"] + p["board_clearance"]],
        "mount_centres_mm": mounting,
        "chassis_centres_mm": chassis,
    }


def measure(design):
    """Read BRep cylinders, dimensions, voids and connectivity, not sketch intent."""
    import adsk.core
    import adsk.fusion

    p = {v.name: v.value * 10 for v in design.userParameters}
    expected = expected_geometry(p)
    bodies = list(design.rootComponent.bRepBodies)
    if len(bodies) != 1 or not bodies[0].isSolid or bodies[0].volume <= 0:
        raise AssertionError("Fixture must be one positive-volume connected solid")
    body = bodies[0]
    bounds = body.boundingBox
    size = [
        (getattr(bounds.maxPoint, a) - getattr(bounds.minPoint, a)) * 10 for a in ("x", "y", "z")
    ]
    cylinders = []
    for face in body.faces:
        cylinder = adsk.core.Cylinder.cast(face.geometry)
        if cylinder:
            cylinders.append(
                {
                    "centre_xy_mm": [cylinder.origin.x * 10, cylinder.origin.y * 10],
                    "diameter_mm": cylinder.radius * 20,
                    "z_bounds_mm": [
                        face.boundingBox.minPoint.z * 10,
                        face.boundingBox.maxPoint.z * 10,
                    ],
                    "vertical": abs(cylinder.axis.z) > 0.999999,
                }
            )

    def near(a, b):
        return abs(a - b) < 1e-5

    def check_bores(centres, diameter, height):
        matches = []
        for xy in centres:
            match = [
                c
                for c in cylinders
                if c["vertical"]
                and near(c["diameter_mm"], diameter)
                and all(near(a, b) for a, b in zip(c["centre_xy_mm"], xy))
                and near(c["z_bounds_mm"][0], 0)
                and near(c["z_bounds_mm"][1], height)
            ]
            if len(match) != 1:
                raise AssertionError("Expected one full-depth bore at " + str(xy))
            matches.extend(match)
        return matches

    mount_bores = check_bores(expected["mount_centres_mm"], p["board_bore"], size[2])
    chassis_bores = check_bores(
        expected["chassis_centres_mm"], p["chassis_bore"], p["plate_thickness"]
    )
    outside = adsk.fusion.PointContainment.PointOutsidePointContainment
    empty_points = [
        *expected["mount_centres_mm"],
        *expected["chassis_centres_mm"],
        [size[0] / 2, size[1] / 2],
    ]
    void_checks = [
        body.pointContainment(adsk.core.Point3D.create(x / 10, y / 10, p["plate_thickness"] / 20))
        == outside
        for x, y in empty_points
    ]
    healthy = all(
        design.timeline.item(i).healthState
        == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
        for i in range(design.timeline.count)
    )
    checks = {
        "one_connected_solid": len(bodies) == 1,
        "bounds_match_parameters": all(near(a, b) for a, b in zip(size, expected["size_mm"])),
        "four_mounting_bores_at_drawing_centres": len(mount_bores) == 4,
        "four_chassis_bores_through_plate": len(chassis_bores) == 4,
        "bores_and_central_relief_are_void": all(void_checks),
        "all_sketches_fully_constrained": all(
            s.isFullyConstrained for s in design.rootComponent.sketches
        ),
        "healthy_timeline": healthy,
    }
    if not all(checks.values()):
        raise AssertionError("Native acceptance failed: " + str(checks))
    return {
        "checks": checks,
        "size_mm": size,
        "parameters_mm": p,
        "mounting_bores": mount_bores,
        "chassis_bores": chassis_bores,
        "volume_mm3": body.volume * 1000,
        "timeline_count": design.timeline.count,
        "sketch_count": design.rootComponent.sketches.count,
        "source_scope": "Approximate official reference drawing; no physical board fit test",
    }


def build(app, design, run_dir):
    import adsk.core
    import adsk.fusion

    root = design.rootComponent
    if root.bRepBodies.count:
        if not root.attributes.itemByName("studio-agent-bridge", "rpi5-open-tray"):
            raise ValueError("Refusing to inspect an unrelated design")
        Path(run_dir, "tray-measurements.json").write_text(json.dumps(measure(design), indent=2))
        return
    expected_geometry({})
    app.activeDocument.name = "Raspberry Pi 5 - original open mounting tray"
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    design.unitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
    root.attributes.add("studio-agent-bridge", "rpi5-open-tray", "original-fixture-v1")
    point = adsk.core.Point3D.create
    value = adsk.core.ValueInput.createByString
    ori = adsk.fusion.DimensionOrientations
    ops = adsk.fusion.FeatureOperations
    for name, number in DEFAULTS_MM.items():
        provenance = (
            "Approximate board reference drawing"
            if name in SOURCE_PARAMETERS
            else "Original fixture design choice"
        )
        design.userParameters.add(name, value(f"{number} mm"), "mm", provenance)

    def ev(expr):
        return design.unitsManager.evaluateExpression(expr, "cm")

    def sketch(name, elevation="0 mm"):
        plane = root.xYConstructionPlane
        if elevation != "0 mm":
            inp = root.constructionPlanes.createInput()
            inp.setByOffset(plane, value(elevation))
            plane = root.constructionPlanes.add(inp)
        s = root.sketches.add(plane)
        s.name = name
        return s

    def position(s, sp, x, y):
        if ev(x) == 0 and ev(y) == 0:
            s.geometricConstraints.addCoincident(sp, s.originPoint)
        else:
            s.sketchDimensions.addDistanceDimension(
                s.originPoint, sp, ori.HorizontalDimensionOrientation, point(ev(x) / 2, -1, 0)
            ).parameter.expression = x
            s.sketchDimensions.addDistanceDimension(
                s.originPoint, sp, ori.VerticalDimensionOrientation, point(-1, ev(y) / 2, 0)
            ).parameter.expression = y

    def rectangle(s, x, y, w, h):
        lines = s.sketchCurves.sketchLines.addTwoPointRectangle(
            point(ev(x), ev(y), 0), point(ev(x) + ev(w), ev(y) + ev(h), 0)
        )
        for i in (0, 2):
            s.geometricConstraints.addHorizontal(lines.item(i))
        for i in (1, 3):
            s.geometricConstraints.addVertical(lines.item(i))
        position(s, lines.item(0).startSketchPoint, x, y)
        s.sketchDimensions.addDistanceDimension(
            lines.item(0).startSketchPoint,
            lines.item(0).endSketchPoint,
            ori.HorizontalDimensionOrientation,
            point(ev(x) + ev(w) / 2, ev(y) - 1, 0),
        ).parameter.expression = w
        s.sketchDimensions.addDistanceDimension(
            lines.item(1).startSketchPoint,
            lines.item(1).endSketchPoint,
            ori.VerticalDimensionOrientation,
            point(ev(x) + ev(w) + 1, ev(y) + ev(h) / 2, 0),
        ).parameter.expression = h

    def circle(s, x, y, diameter):
        c = s.sketchCurves.sketchCircles.addByCenterRadius(point(ev(x), ev(y), 0), ev(diameter) / 2)
        position(s, c.centerSketchPoint, x, y)
        s.sketchDimensions.addDiameterDimension(
            c, point(ev(x) + 1, ev(y) + 1, 0)
        ).parameter.expression = diameter

    def extrude(s, height, name, operation):
        e = root.features.extrudeFeatures.addSimple(s.profiles.item(0), value(height), operation)
        e.name = name
        s.isVisible = False
        return e

    base = sketch("01 Parametric mounting frame footprint")
    rectangle(base, "0 mm", "0 mm", "board_length+2*edge_margin", "board_width+2*edge_margin")
    e = extrude(base, "plate_thickness", "01 Frame plate", ops.NewBodyFeatureOperation)
    e.bodies.item(0).name = "Open board mounting tray"
    relief = sketch("02 Underside access relief", "plate_thickness")
    rectangle(
        relief,
        "edge_margin+relief_inset",
        "edge_margin+relief_inset",
        "board_length-2*relief_inset",
        "board_width-2*relief_inset",
    )
    extrude(relief, "-plate_thickness", "02 Open central relief", ops.CutFeatureOperation)
    for i, (x, y) in enumerate(
        [
            (x, y)
            for x in ("edge_margin+hole_offset_x", "edge_margin+hole_offset_x+hole_pitch_x")
            for y in ("edge_margin+hole_offset_y", "edge_margin+hole_offset_y+hole_pitch_y")
        ],
        1,
    ):
        boss = sketch(f"03 Standoff {i}", "plate_thickness")
        circle(boss, x, y, "standoff_outer")
        extrude(boss, "board_clearance", f"03 Joined standoff {i}", ops.JoinFeatureOperation)
        bore = sketch(f"04 Board mounting bore {i}", "plate_thickness+board_clearance")
        circle(bore, x, y, "board_bore")
        extrude(
            bore,
            "-(plate_thickness+board_clearance)",
            f"04 Full-depth mounting bore {i}",
            ops.CutFeatureOperation,
        )
    for i, (x, y) in enumerate(
        [
            (x, y)
            for x in ("edge_margin/2", "board_length+1.5*edge_margin")
            for y in ("edge_margin/2", "board_width+1.5*edge_margin")
        ],
        1,
    ):
        bore = sketch(f"05 Chassis bore {i}", "plate_thickness")
        circle(bore, x, y, "chassis_bore")
        extrude(bore, "-plate_thickness", f"05 Chassis mounting hole {i}", ops.CutFeatureOperation)
    outline = sketch("06 Board footprint reference only", "plate_thickness+board_clearance")
    rectangle(outline, "edge_margin", "edge_margin", "board_length", "board_width")
    outline.isVisible = False
    if not design.computeAll():
        raise RuntimeError("Native recompute failed")
    Path(run_dir, "tray-measurements.json").write_text(json.dumps(measure(design), indent=2))
