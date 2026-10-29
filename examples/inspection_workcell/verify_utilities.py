"""Independent native utility readback; never imports the construction script.

Run in Blender with STUDIO_PARAMETERS={'mode': 'baseline' or 'inspect'} and
STUDIO_OUTPUT_DIR. Measurements use metres. This script observes geometry and
named material references, not electrical functionality or collision safety.
"""
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

PREFIX = 'InspectionV2_'
PRESENTATION_CAMERAS = {PREFIX + 'Camera_Close', PREFIX + 'Camera_Wide'}
ENDPOINT_TOLERANCE = 0.001
CONTACT_TOLERANCE = 0.002
BOUNDS_TOLERANCE = 0.001
OUT = Path(globals()['STUDIO_OUTPUT_DIR'])
MODE = globals().get('STUDIO_PARAMETERS', {}).get('mode', 'inspect')
if MODE not in {'baseline', 'inspect'}:
    raise ValueError('Utility readback mode must be baseline or inspect')

# These are existing source meshes, not metadata-defined replacement hosts.
CABINET = 'InspectionV1_ControlCabinet'
ROUTES = {
    'Camera_L': (('InspectionV1_SensorConnector', '+Z'), (CABINET, '+Y')),
    'Camera_R': (('InspectionV1_SensorConnector.001', '+Z'), (CABINET, '+Y')),
    'Illuminator_L': (('InspectionV1_Illuminator', '+Y'), (CABINET, '+Y')),
    'Illuminator_R': (('InspectionV1_Illuminator.001', '+Y'), (CABINET, '+Y')),
    'HMI': (('InspectionV1_HMIConsole', '+X'), (CABINET, '+X')),
    'PowerSupply': ((CABINET, '+X'), ('Bench_PowerSocket.003', '-Y')),
}
scene = bpy.context.scene
bpy.context.view_layer.update()
graph = bpy.context.evaluated_depsgraph_get()
original = sorted((o for o in scene.objects
                   if o.type == 'MESH' and not o.name.startswith(PREFIX)),
                  key=lambda o: o.name)
added = sorted((o for o in scene.objects if o.name.startswith(PREFIX)),
               key=lambda o: o.name)
presentation = [o for o in added if o.name in PRESENTATION_CAMERAS]
utility_geometry = [o for o in added if o.name not in PRESENTATION_CAMERAS]
geometry_cache = {}
errors = []


def finite(values):
    return all(math.isfinite(float(v)) for v in values)


def original_digest():
    """Exact named base mesh, matrix and material-slot fields only."""
    records = []
    for obj in original:
        records.append([
            obj.name,
            [list(row) for row in obj.matrix_world],
            [list(vertex.co) for vertex in obj.data.vertices],
            [[list(poly.vertices), poly.material_index] for poly in obj.data.polygons],
            [slot.material.name if slot.material else None for slot in obj.material_slots],
        ])
    payload = json.dumps(records, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def material_reference(slot):
    """Resolve evaluated Material IDs back to their original Blender datablock."""
    material = slot.material
    original_material = material.original if material is not None else None
    registered = (bpy.data.materials.get(original_material.name)
                  if original_material is not None else None)
    return {
        'evaluated_material_name': material.name if material is not None else None,
        'original_material_name': original_material.name if original_material is not None else None,
        'original_datablock_registered': registered is not None,
        'resolves': original_material is not None and registered == original_material,
    }


def geometry(obj):
    """AABB of actual evaluated world-space vertices, not object origins."""
    if obj.name in geometry_cache:
        return geometry_cache[obj.name]
    if obj.type not in {'MESH', 'CURVE'}:
        raise ValueError(f'{obj.name}: expected MESH or CURVE, got {obj.type}')
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        if mesh is None or not mesh.vertices or not mesh.polygons:
            raise ValueError(f'{obj.name}: no evaluated surface geometry')
        vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        if not all(finite(point) for point in vertices):
            raise ValueError(f'{obj.name}: non-finite evaluated vertex coordinates')
        low = [min(p[i] for p in vertices) for i in range(3)]
        high = [max(p[i] for p in vertices) for i in range(3)]
        dims = [high[i] - low[i] for i in range(3)]
        slots = list(evaluated.material_slots)
        material_names = [slot.material.name if slot.material else None for slot in slots]
        references = [material_reference(slot) for slot in slots]
        materials_resolve = bool(slots) and all(ref['resolves'] for ref in references)
        invalid_material_indices = sorted({p.material_index for p in mesh.polygons
                                           if not 0 <= p.material_index < len(slots)})
        mesh.calc_loop_triangles()
        result = {
            'bounds_m': [low, high],
            'centre_m': [(low[i] + high[i]) / 2 for i in range(3)],
            'dimensions_m': dims,
            'evaluated_vertices': len(mesh.vertices),
            'evaluated_polygons': len(mesh.polygons),
            'evaluated_triangles': len(mesh.loop_triangles),
            'positive_finite_dimensions': finite(dims) and all(v > 0 for v in dims),
            'material_names': material_names,
            'material_references': references,
            'invalid_polygon_material_indices': invalid_material_indices,
            'all_material_references_resolve': materials_resolve and not invalid_material_indices,
        }
        geometry_cache[obj.name] = result
        return result
    finally:
        evaluated.to_mesh_clear()


def union_bounds(objects):
    boxes = [geometry(obj)['bounds_m'] for obj in objects]
    if not boxes:
        raise ValueError('Cannot measure an empty bounds set')
    return [[min(box[0][i] for box in boxes) for i in range(3)],
            [max(box[1][i] for box in boxes) for i in range(3)]]


def point_in_envelope(point, floor_bounds, scene_bounds):
    return (finite(point)
            and all(floor_bounds[0][i] - BOUNDS_TOLERANCE <= point[i]
                    <= floor_bounds[1][i] + BOUNDS_TOLERANCE for i in (0, 1))
            and floor_bounds[1][2] - BOUNDS_TOLERANCE <= point[2]
            <= scene_bounds[1][2] + BOUNDS_TOLERANCE)


def read_attachment(route, end, point, expected_host):
    gland_name = f'{PREFIX}{route}_{end.title()}Gland'
    curve = scene.objects[f'{PREFIX}Route_{route}']
    referred_name = curve.get(f'utility_{end}_anchor')
    obj = scene.objects.get(gland_name)
    checks = {'curve_references_expected_gland': referred_name == gland_name,
              'gland_is_new_mesh_geometry': obj is not None and obj.type == 'MESH'}
    result = {'gland': gland_name, 'curve_anchor_reference': referred_name,
              'curve_endpoint_m': list(point), 'checks': checks}
    if not checks['gland_is_new_mesh_geometry']:
        result['passed'] = False
        return result
    gland = geometry(obj)
    host_name = obj.get('utility_attachment_target')
    face = obj.get('utility_attachment_face')
    host = scene.objects.get(host_name) if isinstance(host_name, str) else None
    host_is_existing = (host is not None and host.type == 'MESH'
                        and not host.name.startswith(PREFIX) and host != obj)
    checks['expected_existing_host_and_face'] = (
        host_is_existing and (host_name, face) == expected_host)
    delta = (Vector(gland['centre_m']) - point).length
    checks['endpoint_to_gland_centre_within_1mm'] = delta <= ENDPOINT_TOLERANCE
    checks['positive_finite_gland_dimensions'] = gland['positive_finite_dimensions']
    result.update({'gland_bounds_m': gland['bounds_m'],
                   'gland_geometric_centre_m': gland['centre_m'],
                   'endpoint_to_gland_centre_delta_m': delta,
                   'attachment_host': host_name, 'attachment_face': face})
    if not host_is_existing or face not in {'+X', '-X', '+Y', '-Y', '+Z', '-Z'}:
        checks['gland_contacts_existing_host_face_within_2mm'] = False
    else:
        host_geometry = geometry(host)
        hb, gb = host_geometry['bounds_m'], gland['bounds_m']
        axis, sign = 'XYZ'.index(face[1]), 1 if face[0] == '+' else -1
        host_face = hb[1 if sign > 0 else 0][axis]
        gland_face = gb[0 if sign > 0 else 1][axis]
        # Positive means a gap; negative means AABB penetration at this face.
        signed_gap = sign * (gland_face - host_face)
        centre_offset = sign * (gland['centre_m'][axis] - host_face)
        tangent_axes = [i for i in range(3) if i != axis]
        overlaps = { 'XYZ'[i]: min(gb[1][i], hb[1][i]) - max(gb[0][i], hb[0][i])
                     for i in tangent_axes }
        projected_centre_inside = all(
            hb[0][i] - CONTACT_TOLERANCE <= gland['centre_m'][i]
            <= hb[1][i] + CONTACT_TOLERANCE for i in tangent_axes)
        checks['gland_contacts_existing_host_face_within_2mm'] = (
            abs(signed_gap) <= CONTACT_TOLERANCE
            and centre_offset >= 0
            and all(value > 0 for value in overlaps.values())
            and projected_centre_inside)
        checks['positive_finite_host_dimensions'] = host_geometry['positive_finite_dimensions']
        result.update({'host_bounds_m': hb, 'signed_face_gap_m': signed_gap,
                       'gland_centre_outside_host_face_m': centre_offset,
                       'tangent_aabb_overlap_m': overlaps,
                       'gland_centre_projects_inside_host_face': projected_centre_inside})
    result['passed'] = all(checks.values())
    return result


def read_route(name, floor_bounds, scene_bounds):
    obj = scene.objects.get(f'{PREFIX}Route_{name}')
    checks = {'required_curve_present': obj is not None and obj.type == 'CURVE'}
    result = {'object': f'{PREFIX}Route_{name}', 'checks': checks}
    if not checks['required_curve_present']:
        result['passed'] = False
        return result
    evaluated = obj.evaluated_get(graph)
    splines = evaluated.data.splines
    checks['one_open_poly_spline'] = (len(splines) == 1 and splines[0].type == 'POLY'
                                     and not splines[0].use_cyclic_u)
    if not checks['one_open_poly_spline']:
        result['passed'] = False
        return result
    points = [evaluated.matrix_world @ Vector(p.co[:3]) for p in splines[0].points]
    checks['finite_control_points'] = len(points) >= 2 and all(finite(p) for p in points)
    if not checks['finite_control_points']:
        result['passed'] = False
        return result
    segments = [(right - left).length for left, right in zip(points, points[1:])]
    length = sum(segments)
    # Every route must be a meaningful, nondegenerate path in this metre-scale lab.
    checks['finite_meaningful_route_length'] = (
        math.isfinite(length) and 0.05 <= length <= 50
        and all(value > 1e-6 for value in segments))
    checks['control_points_within_floor_and_scene_envelope'] = all(
        point_in_envelope(p, floor_bounds, scene_bounds) for p in points)
    route_geometry = geometry(obj)
    checks['curve_has_positive_surface_dimensions'] = route_geometry['positive_finite_dimensions']
    attachments = {
        'start': read_attachment(name, 'start', points[0], ROUTES[name][0]),
        'end': read_attachment(name, 'end', points[-1], ROUTES[name][1]),
    }
    checks['both_geometric_attachments_pass'] = all(a['passed'] for a in attachments.values())
    result.update({'control_points_m': [list(p) for p in points],
                   'control_point_count': len(points), 'polyline_length_m': length,
                   'minimum_segment_length_m': min(segments),
                   'evaluated_bounds_m': route_geometry['bounds_m'],
                   'attachments': attachments, 'passed': all(checks.values())})
    return result


digest = None
try:
    digest = original_digest()
except (ValueError, TypeError, OverflowError) as exc:
    errors.append(f'Original field digest: {exc}')
counts = {
    'original_meshes': len(original),
    'original_lab_meshes': sum(not o.name.startswith('InspectionV1_') for o in original),
    'inspection_v1_meshes': sum(o.name.startswith('InspectionV1_') for o in original),
    'utility_objects': len(added),
    'utility_geometry_objects': len(utility_geometry),
    'utility_meshes': sum(o.type == 'MESH' for o in added),
    'utility_curves': sum(o.type == 'CURVE' for o in added),
    'presentation_cameras': sum(o.type == 'CAMERA' for o in presentation),
}
checks = {
    'unit_scale_is_one_metre': scene.unit_settings.scale_length == 1.0,
    'reviewed_lab_scene': scene.get('studio_scene') == 'Industrial AI Robotics Lab',
    '1820_existing_meshes_present': len(original) == 1820,
    '1571_original_lab_meshes_present': counts['original_lab_meshes'] == 1571,
    '249_existing_inspection_meshes_present': counts['inspection_v1_meshes'] == 249,
    'original_field_digest_finite': digest is not None,
}
measurements = {'routes': {}, 'unit_scale_length': scene.unit_settings.scale_length}
if MODE == 'baseline':
    checks['baseline_has_no_utility_addition'] = not added
else:
    floor_bounds = scene_bounds = None
    try:
        floor_bounds = union_bounds([o for o in original if o.name.startswith('FloorTile_')])
        scene_bounds = union_bounds(original)
        measurements.update({'floor_tile_bounds_m': floor_bounds,
                             'existing_mesh_scene_bounds_m': scene_bounds})
        checks['finite_existing_floor_and_scene_bounds'] = True
    except (ValueError, TypeError, RuntimeError) as exc:
        errors.append(f'Existing scene bounds: {exc}')
        checks['finite_existing_floor_and_scene_bounds'] = False
    expected_routes = {f'{PREFIX}Route_{name}' for name in ROUTES}
    observed_routes = {o.name for o in added if o.name.startswith(PREFIX + 'Route_')}
    checks['exactly_six_named_routes'] = (
        observed_routes == expected_routes and counts['utility_curves'] == 6)
    checks['exactly_two_named_presentation_cameras'] = (
        {o.name for o in presentation} == PRESENTATION_CAMERAS
        and all(o.type == 'CAMERA' for o in presentation))
    checks['presentation_camera_transforms_and_lenses_finite'] = bool(presentation) and all(
        o.type == 'CAMERA'
        and finite(value for row in o.matrix_world for value in row)
        and finite((o.data.lens, o.data.clip_start, o.data.clip_end))
        and o.data.lens > 0 and 0 < o.data.clip_start < o.data.clip_end
        for o in presentation)
    checks['all_utility_geometry_objects_are_mesh_or_curve'] = bool(utility_geometry) and all(
        obj.type in {'MESH', 'CURVE'} for obj in utility_geometry)
    valid_geometry = []
    invalid_geometry = []
    for obj in utility_geometry:
        try:
            valid_geometry.append((obj.name, geometry(obj)))
        except (ValueError, TypeError, RuntimeError) as exc:
            invalid_geometry.append(obj.name)
            errors.append(str(exc))
    checks['all_added_geometry_has_evaluated_surfaces'] = bool(utility_geometry) and not invalid_geometry
    checks['all_added_dimensions_positive_finite'] = bool(valid_geometry) and all(
        g['positive_finite_dimensions'] for _, g in valid_geometry) and not invalid_geometry
    checks['all_added_material_references_resolve'] = bool(valid_geometry) and all(
        g['all_material_references_resolve'] for _, g in valid_geometry) and not invalid_geometry
    triangles = sum(g['evaluated_triangles'] for _, g in valid_geometry)
    counts['utility_evaluated_triangles'] = triangles
    checks['added_triangle_budget_under_100k'] = 0 < triangles < 100000 and not invalid_geometry
    measurements['invalid_geometry_objects'] = invalid_geometry
    measurements['invalid_material_references'] = [
        {'object': name, 'material_references': g['material_references'],
         'invalid_polygon_material_indices': g['invalid_polygon_material_indices']}
        for name, g in valid_geometry if not g['all_material_references_resolve']]
    if floor_bounds is not None and scene_bounds is not None:
        for name in ROUTES:
            try:
                measurements['routes'][name] = read_route(name, floor_bounds, scene_bounds)
            except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as exc:
                errors.append(f'{name}: {exc}')
                measurements['routes'][name] = {'passed': False, 'error': str(exc)}
    checks['all_six_independent_route_measurements_pass'] = (
        len(measurements['routes']) == 6
        and all(route['passed'] for route in measurements['routes'].values()))

checks['no_readback_errors'] = not errors
result = {
    'schema': 'studio-inspection-utilities-readback/v1',
    'mode': MODE,
    'original_mesh_count': len(original),
    'original_fields_sha256': digest,
    'orig_digest': digest,
    'digest_scope': 'Scene MESH objects excluding InspectionV2_: sorted object name, '
                    'matrix_world, base vertex coordinates, polygon vertex indices and '
                    'material indices, and material-slot names. Excludes cameras, '
                    'modifiers, material contents and other scene state.',
    'counts': counts,
    'tolerances_m': {'endpoint_to_evaluated_gland_centre': ENDPOINT_TOLERANCE,
                     'gland_to_host_aabb_face': CONTACT_TOLERANCE,
                     'route_control_point_envelope': BOUNDS_TOLERANCE},
    'checks': checks,
    'passed': all(checks.values()),
    'measurements': measurements,
    'errors': errors,
    'limits': 'Native POLY endpoints and evaluated world-space vertex AABBs only. '
              'Host face contact is an AABB check with limited intentional overlap; '
              'it does not establish exact surface mating. Floor and scene bounds '
              'are broad envelopes, not free-space or collision tests. No electrical '
              'compliance, connectivity functionality, trajectory safety, robot '
              'clearance, optical, structural or full-scene equivalence verification.',
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'utility-readback.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
print(json.dumps(result, allow_nan=False))
if not result['passed']:
    failures = ', '.join(key for key, value in checks.items() if not value)
    raise ValueError(f'Independent utility readback failed: {failures}')
