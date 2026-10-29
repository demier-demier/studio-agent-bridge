"""Regression checks for Blender evaluated-material versus original-ID readback."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "examples/inspection_workcell/verify_utilities.py"


class Material:
    def __init__(self, name, original=None):
        self.name = name
        self.original = self if original is None else original


class IdentityMatrix:
    def __matmul__(self, point):
        return point


@pytest.fixture
def readback():
    # Load only the independent helpers; do not emulate an entire native scene.
    source = ast.parse(SCRIPT.read_text())
    helpers = ast.Module(body=[node for node in source.body
                              if isinstance(node, ast.FunctionDef)
                              and node.name in {"finite", "material_reference", "geometry"}],
                         type_ignores=[])
    namespace = {"math": math, "graph": object(), "geometry_cache": {},
                 "bpy": SimpleNamespace(data=SimpleNamespace(materials={}))}
    exec(compile(helpers, str(SCRIPT), "exec"), namespace)
    return namespace


def test_evaluated_material_resolves_to_exact_original_id(readback):
    original = Material("Steel")
    evaluated = Material("Steel", original=original)
    readback["bpy"].data.materials["Steel"] = original
    assert evaluated is not original
    assert readback["material_reference"](SimpleNamespace(material=evaluated))["resolves"]


@pytest.mark.parametrize("failure", ["empty_slot", "unregistered", "different_id_same_name"])
def test_dangling_or_name_only_material_reference_is_rejected(readback, failure):
    original = Material("Steel")
    material = Material("Steel", original=original)
    if failure == "empty_slot":
        material = None
    elif failure == "different_id_same_name":
        readback["bpy"].data.materials["Steel"] = Material("Steel")
    result = readback["material_reference"](SimpleNamespace(material=material))
    assert not result["resolves"]


@pytest.mark.parametrize("slot_count, material_index, expected", [
    (1, 0, True), (0, 0, False), (1, -1, False), (1, 1, False), (1, 3, False),
])
def test_evaluated_polygon_material_indices_remain_bounded(
    readback, slot_count, material_index, expected,
):
    original = Material("Steel")
    readback["bpy"].data.materials["Steel"] = original
    mesh = SimpleNamespace(
        vertices=[SimpleNamespace(co=(0, 0, 0)), SimpleNamespace(co=(1, 1, 1))],
        polygons=[SimpleNamespace(material_index=material_index)],
        loop_triangles=[object()], calc_loop_triangles=lambda: None,
    )
    obj = SimpleNamespace(
        name="InspectionV2_Test", type="MESH", matrix_world=IdentityMatrix(),
        material_slots=[SimpleNamespace(material=Material("Steel", original))] * slot_count,
        to_mesh=lambda: mesh, to_mesh_clear=lambda: None,
    )
    obj.evaluated_get = lambda graph: obj
    result = readback["geometry"](obj)
    assert result["all_material_references_resolve"] is expected
    assert result["invalid_polygon_material_indices"] == ([] if expected else [material_index])
