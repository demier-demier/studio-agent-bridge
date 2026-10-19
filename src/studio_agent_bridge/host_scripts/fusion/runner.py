"""Executed inside Autodesk Fusion. BRIDGE_JOB is injected by the local adapter."""

import json
import pathlib

import adsk.core
import adsk.fusion


def point(x=0, y=0, z=0):
    return adsk.core.Point3D.create(x, y, z)


def value(expression):
    return adsk.core.ValueInput.createByString(expression)


def inspect_design(design):
    root = design.rootComponent
    components, bodies, sketches, health = [], [], [], []
    for component in design.allComponents:
        components.append(component.name)
        for body in component.bRepBodies:
            box = body.boundingBox
            bodies.append(
                {
                    "name": body.name,
                    "component": component.name,
                    "solid": body.isSolid,
                    "volume_cm3": body.volume,
                    "faces": body.faces.count,
                    "bounds_mm": [
                        [p.x * 10, p.y * 10, p.z * 10] for p in [box.minPoint, box.maxPoint]
                    ],
                }
            )
        for sketch in component.sketches:
            sketches.append(
                {
                    "name": sketch.name,
                    "component": component.name,
                    "fully_constrained": sketch.isFullyConstrained,
                    "dimensions": sketch.sketchDimensions.count,
                }
            )
    for i in range(design.timeline.count):
        item = design.timeline.item(i)
        if item.healthState != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
            health.append({"index": i, "name": item.name, "message": item.errorOrWarningMessage})
    box = root.boundingBox
    return {
        "host_version": adsk.core.Application.get().version,
        "design_type": "parametric"
        if design.designType == adsk.fusion.DesignTypes.ParametricDesignType
        else "direct",
        "components": components,
        "body_count": len(bodies),
        "bodies": bodies,
        "sketches": sketches,
        "timeline_count": design.timeline.count,
        "unhealthy_features": health,
        "bounds_mm": [
            [min(b["bounds_mm"][0][i] for b in bodies) for i in range(3)],
            [max(b["bounds_mm"][1][i] for b in bodies) for i in range(3)],
        ]
        if bodies
        else None,
        "parameters": {
            p.name: {"expression": p.expression, "value_cm": p.value} for p in design.userParameters
        },
    }


def create_demo(app, params, doc):
    doc.name = "Studio Agent - Parametric Sensor Enclosure"
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    design.unitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
    root = design.rootComponent
    root.attributes.add("studio-agent-bridge", "owned", "original-demo-v1")
    defaults = {
        "base_width": "160 mm",
        "base_depth": "100 mm",
        "plate_thickness": "6 mm",
        "edge_margin": "12 mm",
        "wall_thickness": "3 mm",
        "housing_height": "45 mm",
        "lid_thickness": "3 mm",
        "mount_diameter": "5 mm",
        "boss_outer": "10 mm",
        "boss_bore": "3 mm",
        "lens_diameter": "28 mm",
        "lens_length": "16 mm",
    }
    for name, expression in defaults.items():
        design.userParameters.add(
            name, value(params.get(name, expression)), "mm", "Editable engineering dimension"
        )
    units = design.unitsManager

    def ev(expr):
        return units.evaluateExpression(expr, "cm")

    def component(name):
        c = root.occurrences.addNewComponent(adsk.core.Matrix3D.create()).component
        c.name = name
        return c

    def plane(c, height):
        if height == "0 mm":
            return c.xYConstructionPlane
        inp = c.constructionPlanes.createInput()
        inp.setByOffset(c.xYConstructionPlane, value(height))
        pl = c.constructionPlanes.add(inp)
        pl.name = "Elevation - " + height
        return pl

    def xy_constraints(sk, sp, xexpr, yexpr):
        # Pin the center/anchor to two driving dimensions, or the origin.
        x, y = ev(xexpr), ev(yexpr)
        ori = adsk.fusion.DimensionOrientations
        if abs(x) < 1e-8 and abs(y) < 1e-8:
            sk.geometricConstraints.addCoincident(sp, sk.originPoint)
        else:
            if abs(x) > 1e-8:
                sk.sketchDimensions.addDistanceDimension(
                    sk.originPoint, sp, ori.HorizontalDimensionOrientation, point(x / 2, -1)
                ).parameter.expression = xexpr
            else:
                sk.geometricConstraints.addVerticalPoints(sk.originPoint, sp)
            if abs(y) > 1e-8:
                sk.sketchDimensions.addDistanceDimension(
                    sk.originPoint, sp, ori.VerticalDimensionOrientation, point(-1, y / 2)
                ).parameter.expression = yexpr
            else:
                sk.geometricConstraints.addHorizontalPoints(sk.originPoint, sp)

    def rect(sk, x, y, w, h):
        lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(
            point(ev(x), ev(y)), point(ev(x) + ev(w), ev(y) + ev(h))
        )
        for i in (0, 2):
            sk.geometricConstraints.addHorizontal(lines.item(i))
        for i in (1, 3):
            sk.geometricConstraints.addVertical(lines.item(i))
        xy_constraints(sk, lines.item(0).startSketchPoint, x, y)
        ori = adsk.fusion.DimensionOrientations
        sk.sketchDimensions.addDistanceDimension(
            lines.item(0).startSketchPoint,
            lines.item(0).endSketchPoint,
            ori.HorizontalDimensionOrientation,
            point(ev(x) + ev(w) / 2, ev(y) - 1),
        ).parameter.expression = w
        sk.sketchDimensions.addDistanceDimension(
            lines.item(1).startSketchPoint,
            lines.item(1).endSketchPoint,
            ori.VerticalDimensionOrientation,
            point(ev(x) + ev(w) + 1, ev(y) + ev(h) / 2),
        ).parameter.expression = h

    def circle(sk, x, y, d, concentric=False):
        c = sk.sketchCurves.sketchCircles.addByCenterRadius(point(ev(x), ev(y)), ev(d) / 2)
        if not concentric:
            xy_constraints(sk, c.centerSketchPoint, x, y)
        else:
            sk.geometricConstraints.addConcentric(
                sk.sketchCurves.sketchCircles.item(sk.sketchCurves.sketchCircles.count - 2), c
            )
        sk.sketchDimensions.addDiameterDimension(
            c, point(ev(x) + ev(d), ev(y) + ev(d))
        ).parameter.expression = d

    def extrude(c, sk, d, name, operation=None, profile=None):
        if operation is None:
            operation = adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        ex = c.features.extrudeFeatures.addSimple(
            profile or sk.profiles.item(0), value(d), operation
        )
        ex.name = name
        if ex.bodies.count and operation == adsk.fusion.FeatureOperations.NewBodyFeatureOperation:
            ex.bodies.item(0).name = name
        sk.isVisible = False
        return ex

    def sketch(c, elevation, name):
        s = c.sketches.add(plane(c, elevation))
        s.name = name
        return s

    base = component("01 Mounting base")
    sk = sketch(base, "0 mm", "Base footprint - width and depth")
    rect(sk, "0 mm", "0 mm", "base_width", "base_depth")
    extrude(base, sk, "plate_thickness", "Base plate")
    for i, (x, y) in enumerate(
        [
            (x, y)
            for x in ("edge_margin/2", "base_width-edge_margin/2")
            for y in ("edge_margin/2", "base_depth-edge_margin/2")
        ]
    ):
        holes = sketch(base, "plate_thickness", "Mounting hole %d" % (i + 1))
        circle(holes, x, y, "mount_diameter")
        extrude(
            base,
            holes,
            "-plate_thickness",
            "Mounting hole cut %d" % (i + 1),
            adsk.fusion.FeatureOperations.CutFeatureOperation,
        )
    case = component("02 Hollow enclosure")
    sk = sketch(case, "plate_thickness", "Constrained enclosure walls")
    rect(sk, "edge_margin", "edge_margin", "base_width-2*edge_margin", "base_depth-2*edge_margin")
    rect(
        sk,
        "edge_margin+wall_thickness",
        "edge_margin+wall_thickness",
        "base_width-2*edge_margin-2*wall_thickness",
        "base_depth-2*edge_margin-2*wall_thickness",
    )
    ring = next(p for p in sk.profiles if p.profileLoops.count == 2)
    extrude(case, sk, "housing_height", "Housing walls", profile=ring)
    lid = component("03 Removable cover")
    sk = sketch(lid, "plate_thickness+housing_height", "Cover footprint")
    rect(sk, "edge_margin", "edge_margin", "base_width-2*edge_margin", "base_depth-2*edge_margin")
    extrude(lid, sk, "lid_thickness", "Cover plate")
    for i, (x, y) in enumerate(
        [
            (x, y)
            for x in (
                "edge_margin+wall_thickness+boss_outer/2",
                "base_width-edge_margin-wall_thickness-boss_outer/2",
            )
            for y in (
                "edge_margin+wall_thickness+boss_outer/2",
                "base_depth-edge_margin-wall_thickness-boss_outer/2",
            )
        ]
    ):
        boss = component("%02d Internal mounting boss" % (4 + i))
        sk = sketch(boss, "plate_thickness", "Boss concentric profile")
        circle(sk, x, y, "boss_outer")
        circle(sk, x, y, "boss_bore", True)
        ring = next(p for p in sk.profiles if p.profileLoops.count == 2)
        extrude(boss, sk, "housing_height", "Hollow standoff", profile=ring)
        hs = sketch(
            lid, "plate_thickness+housing_height+lid_thickness", "Cover screw hole %d" % (i + 1)
        )
        circle(hs, x, y, "boss_bore")
        extrude(
            lid,
            hs,
            "-lid_thickness",
            "Cover fastener hole %d" % (i + 1),
            adsk.fusion.FeatureOperations.CutFeatureOperation,
        )
    aperture = sketch(
        lid, "plate_thickness+housing_height+lid_thickness", "Optical aperture through cover"
    )
    circle(aperture, "base_width/2", "base_depth/2", "lens_diameter-2*wall_thickness")
    extrude(
        lid,
        aperture,
        "-lid_thickness",
        "Optical aperture cut",
        adsk.fusion.FeatureOperations.CutFeatureOperation,
    )
    lens = component("08 Sensor lens housing")
    sk = sketch(lens, "plate_thickness+housing_height+lid_thickness", "Lens barrel profile")
    circle(sk, "base_width/2", "base_depth/2", "lens_diameter")
    circle(sk, "base_width/2", "base_depth/2", "lens_diameter-2*wall_thickness", True)
    ring = next(p for p in sk.profiles if p.profileLoops.count == 2)
    extrude(lens, sk, "lens_length", "Optical barrel", profile=ring)
    design.computeAll()
    return doc, design


def export_design(app, design, folder, formats):
    exported = []
    manager = design.exportManager
    for fmt in formats:
        path = str(folder / ("sensor-enclosure." + fmt))
        if fmt == "f3d":
            options = manager.createFusionArchiveExportOptions(path)
        elif fmt == "step":
            options = manager.createSTEPExportOptions(path)
        elif fmt == "stl":
            options = manager.createSTLExportOptions(design.rootComponent, path)
            options.sendToPrintUtility = False
        else:
            raise ValueError("Unsupported format: " + fmt)
        if not manager.execute(options):
            raise RuntimeError("Export failed: " + fmt)
        exported.append(path)
    camera = app.activeViewport.camera
    camera.viewOrientation = adsk.core.ViewOrientations.IsoTopRightViewOrientation
    camera.isFitView = True
    app.activeViewport.camera = camera
    app.activeViewport.refresh()
    screenshot = str(folder / "fusion-preview.png")
    if app.activeViewport.saveAsImageFile(screenshot, 1600, 1100):
        exported.append(screenshot)
    return exported


def verify_archive(app, archive, expected):
    original = app.activeDocument
    reopened = None
    try:
        reopened = app.importManager.importToNewDocument(
            app.importManager.createFusionArchiveImportOptions(str(archive))
        )
        if not reopened:
            raise RuntimeError("Exported F3D could not be reopened")
        actual = inspect_design(adsk.fusion.Design.cast(app.activeProduct))
        checks = {
            "body_count": actual["body_count"] == expected["body_count"],
            "timeline_count": actual["timeline_count"] == expected["timeline_count"],
            "healthy_features": not actual["unhealthy_features"],
            "parameter_expressions": {k: v["expression"] for k, v in actual["parameters"].items()}
            == {k: v["expression"] for k, v in expected["parameters"].items()},
            "solid_geometry": all(b["solid"] and b["volume_cm3"] > 0 for b in actual["bodies"]),
            "bounds": actual["bounds_mm"] is not None
            and expected["bounds_mm"] is not None
            and all(
                abs(actual["bounds_mm"][i][j] - expected["bounds_mm"][i][j]) < 1e-5
                for i in range(2)
                for j in range(3)
            ),
            "sketch_constraints": sorted(
                (s["name"], s["fully_constrained"]) for s in actual["sketches"]
            )
            == sorted((s["name"], s["fully_constrained"]) for s in expected["sketches"]),
        }
        return {
            "verified": all(checks.values()),
            "checks": checks,
            "observed_bounds_mm": actual["bounds_mm"],
        }
    finally:
        if reopened and reopened.isValid:
            reopened.close(False)
        if original and original.isValid:
            original.activate()


def run(context):
    job = BRIDGE_JOB  # noqa: F821 - injected by the adapter
    app = adsk.core.Application.get()
    folder = pathlib.Path(job["run_dir"])
    action = job["action"]
    params = job["parameters"]
    previous = app.activeDocument
    doc = None
    try:
        if action == "create_demo":
            doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            doc, design = create_demo(app, params.get("dimensions", {}), doc)
        elif params.get("input_design"):
            doc = app.importManager.importToNewDocument(
                app.importManager.createFusionArchiveImportOptions(params["input_design"])
            )
            if not doc:
                raise RuntimeError("Archive could not be reopened")
            design = adsk.fusion.Design.cast(app.activeProduct)
        elif action == "run_script":
            doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            design = adsk.fusion.Design.cast(app.activeProduct)
        else:
            raise ValueError("input_design is required")
        if not design:
            raise RuntimeError("Document is not a Fusion design")
        before = inspect_design(design)
        if action == "edit_parameters":
            for name, expression in params["parameters"].items():
                p = design.userParameters.itemByName(name)
                if not p:
                    raise ValueError("Unknown user parameter: " + name)
                p.expression = expression
            if not design.computeAll():
                raise RuntimeError("Parameter recompute failed")
        if action == "run_script":
            scope = {
                "__name__": "studio_trusted_script",
                "app": app,
                "design": design,
                "run_dir": str(folder),
            }
            script_path = params["script_path"]
            exec(compile(pathlib.Path(script_path).read_text(), script_path, "exec"), scope)
            entry = scope.get("build")
            if not callable(entry):
                raise ValueError("Trusted script must define build(app, design, run_dir)")
            entry(app, design, str(folder))
            design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        observed = inspect_design(design)
        if action == "edit_parameters":
            observed["before_edit"] = before
        formats = params.get("formats", ["f3d", "step"]) if action != "inspect" else []
        exported = export_design(app, design, folder, formats) if formats else []
        observed["exported"] = exported
        if "f3d" in formats:
            observed["archive_roundtrip"] = verify_archive(
                app, folder / "sensor-enclosure.f3d", observed
            )
        (folder / "fusion-evidence.json").write_text(json.dumps(observed, indent=2))
        print(
            json.dumps(
                {
                    "evidence": str(folder / "fusion-evidence.json"),
                    "body_count": observed["body_count"],
                    "timeline_count": observed["timeline_count"],
                }
            )
        )
    finally:
        if doc and doc.isValid:
            doc.close(False)  # only this newly created/imported work copy
        if previous and previous.isValid:
            previous.activate()
