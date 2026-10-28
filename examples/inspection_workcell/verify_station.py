"""Independent native readback: no imports from the construction script."""
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

out = Path(globals()['STUDIO_OUTPUT_DIR'])
scene = bpy.context.scene
graph = bpy.context.evaluated_depsgraph_get()
station = [o for o in scene.objects if o.name.startswith('InspectionV1_') and o.type == 'MESH']
if not station:
    raise ValueError('No saved inspection station')


def bounds(objects):
    corners = []
    for o in objects:
        evaluated = o.evaluated_get(graph)
        corners.extend(evaluated.matrix_world @ Vector(c) for c in evaluated.bound_box)
    if not all(math.isfinite(v) for p in corners for v in p):
        raise ValueError('Non-finite evaluated bounds')
    return [[min(p[i] for p in corners) for i in range(3)],
            [max(p[i] for p in corners) for i in range(3)]]


sb = bounds(station)
robot = bounds([bpy.data.objects['Cell_Table_Top']])
bench = bounds([bpy.data.objects['Bench_Top']])
clearances = {'robot_table_x_gap_m': robot[0][0] - sb[1][0],
              'electronics_bench_y_gap_m': bench[0][1] - sb[1][1]}
triangles = 0
for obj in station:
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        triangles += len(mesh.loop_triangles)
    finally:
        evaluated.to_mesh_clear()
required = ['InspectionRing', 'CameraHousing', 'IlluminatorCrossBracket', 'FixtureClamp',
            'ControlCabinet', 'EmergencyButton', 'LevellingFoot', 'CableChainLink']
checks = {
    'all_required_assemblies_present': all(any(o.name.startswith('InspectionV1_' + name) for o in station) for name in required),
    'positive_dimensions': all(all(v > 0 for v in o.dimensions) for o in station),
    'all_tagged': all(o.get('studio_asset') is True for o in station),
    'all_materials_resolve': all(o.material_slots and all(s.material for s in o.material_slots) for o in station),
    'clearance_to_two_table_envelopes_at_least_1m': all(v >= 1 for v in clearances.values()),
    'floor_contact_within_1mm': abs(sb[0][2] - 0.015) <= 0.001,
    'added_triangle_budget_under_100k': triangles < 100000,
}
result = {'schema': 'studio-inspection-readback/v1', 'station_meshes': len(station),
          'evaluated_placed_triangles': triangles, 'evaluated_bounds_m': sb,
          'clearances': clearances, 'checks': checks, 'passed': all(checks.values()),
          'limits': 'AABB separations from two named table envelopes only; not full collision, accessibility, robot safety, optical or structural verification.'}
(out / 'independent-readback.json').write_text(json.dumps(result, indent=2) + '\n')
if not result['passed']:
    raise ValueError('Readback requirements failed')
print(json.dumps(result))
