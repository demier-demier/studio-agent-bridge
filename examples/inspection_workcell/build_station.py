"""Source-bound inspection station pilot; execute through a trusted bridge action.

Original visual assembly. No controller, optical simulation or safety certification.
Modes: baseline (matched camera only), build (one new assembly), inspect (readback).
"""
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

P = globals().get('STUDIO_PARAMETERS', {})
OUT = Path(globals()['STUDIO_OUTPUT_DIR'])
MODE = P.get('mode', 'build')
if MODE not in {'baseline', 'build', 'inspect'}:
    raise ValueError('Unknown mode')
scene = bpy.context.scene
assets = bpy.data.collections.get('StudioEnvironment')
if assets is None or scene.get('studio_scene') != 'Industrial AI Robotics Lab':
    raise ValueError('The reviewed original industrial lab is required')
if MODE == 'build' and any(o.name.startswith('InspectionV1_') for o in scene.objects):
    raise ValueError('An inspection station already exists; refusing duplicate construction')


def original_digest():
    records = []
    for o in sorted(assets.objects, key=lambda ob: ob.name):
        if o.name.startswith('InspectionV1_') or o.type != 'MESH':
            continue
        records.append([o.name, [list(row) for row in o.matrix_world],
                        [list(v.co) for v in o.data.vertices],
                        [list(p.vertices) for p in o.data.polygons],
                        [s.material.name if s.material else None for s in o.material_slots]])
    data = json.dumps(records, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest(), len(records)


before, original_count = original_digest()
origin = Vector((-3.8, 0.15, 0.0))
cache = {}


def finish(o, name, mat):
    o.name = 'InspectionV1_' + name
    for col in list(o.users_collection):
        col.objects.unlink(o)
    assets.objects.link(o)
    o['studio_asset'] = True
    o['studio_category'] = 'inspection_station'
    o['studio_provenance'] = 'original-procedural'
    o.data.materials.append(bpy.data.materials[mat])
    return o


def box(name, pos, dims, mat='Steel', edge=0.003):
    # Apply scale before bevel so edge widths stay in metres.
    bpy.ops.mesh.primitive_cube_add(size=1, location=origin + Vector(pos))
    o = bpy.context.object
    o.scale = dims
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    finish(o, name, mat)
    if edge:
        bevel = o.modifiers.new('Machined_edge', 'BEVEL')
        bevel.width, bevel.segments = edge, 3
    return o


def cyl(name, pos, radius, depth, mat='Steel', axis='Z', verts=32):
    key = (radius, depth, mat, verts)
    if key not in cache:
        bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=depth)
        o = bpy.context.object
        finish(o, name, mat)
        cache[key] = o.data
    else:
        o = bpy.data.objects.new('InspectionV1_' + name, cache[key])
        assets.objects.link(o)
        o['studio_asset'] = True
        o['studio_category'] = 'inspection_station'
        o['studio_provenance'] = 'original-procedural'
    o.location = origin + Vector(pos)
    o.rotation_euler = {'Z': (0, 0, 0), 'Y': (math.pi/2, 0, 0)}[axis]
    return o


def label(name, body, pos, size):
    curve = bpy.data.curves.new('InspectionLabel', 'FONT')
    curve.body, curve.size, curve.extrude = body, size, 0.0005
    curve.align_x = 'CENTER'
    o = bpy.data.objects.new(name, curve)
    assets.objects.link(o)
    o.location = origin + Vector(pos)
    o.rotation_euler = (math.pi/2, 0, 0)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True)
    bpy.ops.object.convert(target='MESH')
    finish(o, name, 'White_Type')


if MODE == 'build':
    for x in (-0.72, 0.72):
        for y in (-0.42, 0.42):
            cyl('LevellingFoot', (x, y, 0.047), 0.055, 0.064, 'Rubber')
            cyl('FootStud', (x, y, 0.10), 0.013, 0.065)
            box('ExtrusionLeg', (x, y, 0.52), (0.06, 0.06, 0.84))
            for dx in (-0.018, 0.018):
                box('LegSlot', (x+dx, y-0.031, 0.52), (0.006, 0.003, 0.79), 'Graphite', 0)
            for z in (0.21, 0.89):
                box('CornerBracket', (x, y-0.035, z), (0.10, 0.012, 0.10), 'Graphite')
                for dx in (-0.027, 0.027):
                    cyl('SocketBolt', (x+dx, y-0.044, z), 0.008, 0.009, axis='Y', verts=6)
        for z in (0.20, 0.90):
            box('SideRail', (x, 0, z), (0.06, 0.84, 0.06))
    for y in (-0.42, 0.42):
        for z in (0.20, 0.90):
            box('CrossRail', (0, y, z), (1.44, 0.06, 0.06))
    box('LowerShelf', (0, 0, 0.238), (1.36, 0.75, 0.025), 'Graphite')
    box('TablePlate', (0, 0, 0.96), (1.60, 1.0, 0.06))
    for x in [i*0.12 for i in range(-5, 6)]:
        for y in (-0.30, -0.15, 0, 0.15, 0.30):
            cyl('FixtureSocket', (x, y, 0.992), 0.009, 0.004, 'Graphite', verts=16)
    for x in (-0.68, 0.68):
        box('GantryPost', (x, 0.36, 1.59), (0.08, 0.08, 1.26))
        box('GantrySlot', (x, 0.318, 1.59), (0.014, 0.005, 1.15), 'Graphite', 0)
        box('PostBasePlate', (x, 0.36, 1.012), (0.18, 0.20, 0.04))
        for dx in (-0.06, 0.06):
            for dy in (-0.06, 0.06):
                cyl('BaseBolt', (x+dx, 0.36+dy, 1.037), 0.009, 0.014, verts=6)
    box('GantryBeam', (0, 0.36, 2.18), (1.44, 0.10, 0.10))
    box('LinearGuide', (0, 0.296, 2.18), (1.20, 0.028, 0.035), 'Graphite')
    for x in (-0.42, 0.42):
        box('Carriage', (x, 0.265, 2.16), (0.18, 0.06, 0.13), 'Teal_Paint')
        box('CameraHousing', (x, 0.18, 2.07), (0.13, 0.20, 0.18), 'Graphite')
        cyl('LensBarrel', (x, 0.16, 1.94), 0.043, 0.09, 'Graphite')
        for z in (1.91, 1.94, 1.97):
            cyl('FocusRing', (x, 0.16, z), 0.048, 0.009)
        cyl('OpticalGlass', (x, 0.16, 1.893), 0.037, 0.006, 'Window_Tint')
        cyl('SensorConnector', (x, 0.20, 2.18), 0.015, 0.04)
    for x in (-0.55, 0.55):
        box('IlluminatorCrossBracket', (math.copysign(0.615, x), 0.36, 1.72),
            (0.18, 0.05, 0.04), 'Steel')
        box('IlluminatorMount', (x, 0.13, 1.72), (0.035, 0.50, 0.04), 'Graphite')
        box('Illuminator', (x, 0.13, 1.69), (0.045, 0.42, 0.018), 'Light_Diffuser')
    for i in range(25):
        x = -0.60 + i*0.05
        box('CableChainLink', (x, 0.43, 2.258), (0.040, 0.075, 0.037), 'Graphite', 0.004)
        cyl('ChainPin', (x, 0.39, 2.258), 0.006, 0.009, axis='Y', verts=12)
    for i in range(18):
        z = 1.06+i*0.06
        cyl('CableSleeve', (0.76, 0.39, z), 0.018, 0.062, 'Rubber', verts=16)
    box('ControlCabinet', (0.37, 0.02, 0.56), (0.55, 0.60, 0.56), 'Teal_Paint', 0.015)
    box('CabinetDoor', (0.37, -0.292, 0.56), (0.50, 0.025, 0.51), 'Graphite')
    for i in range(10):
        box('CabinetVent', (0.28, -0.307, 0.40+i*0.022), (0.21, 0.005, 0.007), 'Steel', 0)
    cyl('DoorLock', (0.55, -0.315, 0.62), 0.018, 0.012, axis='Y')
    box('HMIConsole', (0.54, -0.37, 1.07), (0.36, 0.18, 0.12), 'Graphite')
    box('HMIScreen', (0.49, -0.463, 1.09), (0.20, 0.006, 0.065), 'Screen', 0)
    cyl('EmergencyButton', (0.68, -0.47, 1.07), 0.025, 0.025, 'Red_Stop', 'Y')
    for x in (0.43, 0.49, 0.55):
        cyl('StatusLED', (x, -0.469, 1.05), 0.006, 0.004, 'Green_LED', 'Y', 16)
    box('FixtureBase', (-0.12, 0.02, 1.02), (0.66, 0.50, 0.05), 'Graphite')
    for x in (-0.38, 0.14):
        for y in (-0.17, 0.21):
            box('FixtureClamp', (x, y, 1.075), (0.10, 0.08, 0.06), 'Amber_Paint')
            cyl('ClampStud', (x, y, 1.12), 0.012, 0.055)
            cyl('ClampNut', (x, y, 1.135), 0.023, 0.02, verts=6)
    bpy.ops.mesh.primitive_torus_add(major_radius=0.18, minor_radius=0.032, major_segments=64,
                                   minor_segments=12, location=origin+Vector((-0.12, 0.02, 1.10)))
    finish(bpy.context.object, 'InspectionRing', 'Steel')
    label('StationLabel', 'OPTICAL INSPECTION  /  02', (0, -0.507, 0.95), 0.048)
    scene['inspection_workcell_revision'] = 1

bpy.context.view_layer.update()
after, _ = original_digest()
if after != before:
    raise ValueError('Original mesh fields changed during the additive revision')
new = [o for o in assets.objects if o.name.startswith('InspectionV1_')]
points = [o.matrix_world @ Vector(corner) for o in new for corner in o.bound_box]
bounds = {'min': [min(p[i] for p in points) for i in range(3)],
          'max': [max(p[i] for p in points) for i in range(3)]} if points else None
if points and not all(math.isfinite(v) for p in points for v in p):
    raise ValueError('Non-finite workcell geometry')
report = {'mode': MODE, 'original_mesh_count': original_count,
          'original_fields_sha256': after, 'original_fields_preserved': before == after,
          'station_meshes': len(new), 'station_bounds_m': bounds,
          'limits': 'Explicit mesh/transform/material fields only; not complete scene equivalence. No robot, optics or safety simulation.'}
(OUT/'workcell-evidence.json').write_text(json.dumps(report, indent=2)+'\n')

if P.get('render', False):
    rig = bpy.data.collections['PresentationRig']
    cam = bpy.data.objects.get('Camera_InspectionPilot')
    if cam is None:
        data = bpy.data.cameras.new('InspectionPilot')
        cam = bpy.data.objects.new('Camera_InspectionPilot', data)
        rig.objects.link(cam)
    cam.location = (-1.05, -3.05, 2.65)
    cam.rotation_euler = (Vector((-3.6, 0.25, 1.1))-cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.lens = 42
    scene.camera = cam
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 1100, 850
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.use_stamp = False
    scene.render.filepath = str(OUT/('workcell-'+MODE+'.png'))
    bpy.ops.render.render(write_still=True)
    scene.render.filepath = '//render.png'
print(json.dumps(report))
