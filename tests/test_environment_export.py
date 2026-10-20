"""Portable checks for the interchange contract; native geometry is tested separately."""

import hashlib
import json
import struct
from copy import deepcopy
from types import SimpleNamespace

import pytest

from studio_agent_bridge.adapters.blender import _environment_evidence
from studio_agent_bridge.host_scripts.blender import environment_export as export


class Object:
    def __init__(self, name, kind="MESH", parent=None, **tags):
        self.name, self.type, self.parent, self.tags = name, kind, parent, tags

    def get(self, key):
        return self.tags.get(key)


def fake_bpy(objects, collections=None):
    return SimpleNamespace(
        context=SimpleNamespace(scene=SimpleNamespace(objects=objects)),
        data=SimpleNamespace(collections=collections or {}),
    )


def test_environment_selection_precedes_legacy_and_excludes_presentation_by_default():
    rover, room, light = Object("rover"), Object("room"), Object("key", "LIGHT")
    bpy = fake_bpy(
        [rover, room, light],
        {
            "InspectionRover": SimpleNamespace(all_objects=[rover]),
            "StudioEnvironment": SimpleNamespace(all_objects=[room, light]),
            "PresentationRig": SimpleNamespace(all_objects=[light]),
        },
    )
    objects, selection = export.export_selection(bpy)
    assert objects == [room]
    assert selection["collection"] == "StudioEnvironment"
    assert export.export_selection(bpy, include_presentation=True)[0] == [light, room]
    assert export.export_selection(bpy, "InspectionRover")[0] == [rover]


def test_presentation_opt_out_is_respected_when_rig_is_added():
    room = Object("room")
    light = Object("key", "LIGHT", studio_export=False)
    bpy = fake_bpy(
        [room, light],
        {
            "StudioEnvironment": SimpleNamespace(all_objects=[room]),
            "PresentationRig": SimpleNamespace(all_objects=[light]),
        },
    )
    assert export.export_selection(bpy, include_presentation=True)[0] == [room]


def test_missing_explicit_collection_is_not_silently_exported_as_whole_scene():
    with pytest.raises(ValueError, match="not found"):
        export.export_selection(fake_bpy([Object("unrelated")]), "missing")


def test_tag_selection_retains_ancestors_not_unrelated_siblings_and_respects_opt_out():
    root = Object("root", "EMPTY")
    mesh = Object("mesh", parent=root, studio_asset=True)
    other = Object("other", parent=root)
    hidden = Object("hidden", studio_asset=True, studio_export=False)
    objects, selection = export.export_selection(fake_bpy([root, mesh, other, hidden]))
    assert objects == [mesh, root]
    assert selection["strategy"] == "studio_asset-tag"


def test_legacy_rover_selection_remains_compatible():
    rover, floor = Object("rover"), Object("floor")
    bpy = fake_bpy([rover, floor], {"InspectionRover": SimpleNamespace(all_objects=[rover])})
    assert export.export_selection(bpy)[0] == [rover]


def test_bounds_preserve_asymmetric_extents_and_reject_nan():
    assert export.bounds_from_points([[-2, 1, 3], [4, 8, 9]]) == {
        "min": [-2, 1, 3],
        "max": [4, 8, 9],
        "dimensions": [6, 7, 6],
    }
    with pytest.raises(ValueError, match="non-finite"):
        export.bounds_from_points([[float("nan"), 0, 0]])


class Matrix(list):
    def decompose(self):
        return ([2, -3, 4], [1, 0, 0, 0], [1, 2, 3])


def test_translation_converts_to_meters_without_scaling_rotation_matrix():
    value = Matrix([[1, 0, 0, 2], [0, 2, 0, -3], [0, 0, 3, 4], [0, 0, 0, 1]])
    transform = export.matrix_transform(value, 0.01)
    assert transform["translationMeters"] == [0.02, -0.03, 0.04]
    assert transform["matrixWorld"] == [
        [1, 0, 0, 0.02],
        [0, 2, 0, -0.03],
        [0, 0, 3, 0.04],
        [0, 0, 0, 1],
    ]
    assert value[0][3] == 2  # serialization must not mutate native matrices
    for unit in [0, -1, float("inf")]:
        with pytest.raises(ValueError):
            export.matrix_transform(value, unit)


def sample_manifest():
    return {
        "schemaVersion": "environment-manifest/v1",
        "units": {"length": "meter"},
        "assets": [
            {"id": "mesh_a", "vertices": 8, "triangles": 12, "exportFile": "assets/meshes/a.fbx"}
        ],
        "objects": [
            {
                "id": "obj_a",
                "assetId": "mesh_a",
                "parentId": "group_root",
                "transform": {
                    "matrixWorld": [[1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, 3], [0, 0, 0, 1]]
                },
            }
        ],
        "groups": [{"id": "group_root"}],
    }


def test_manifest_accepts_instancing_and_group_parent():
    manifest = sample_manifest()
    twin = deepcopy(manifest["objects"][0])
    twin["id"] = "obj_b"
    twin["transform"]["matrixWorld"][0][3] = 5
    manifest["objects"].append(twin)
    assert export.validate_manifest(manifest) is manifest


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m["objects"][0].update(assetId="missing"),
        lambda m: m["objects"][0].update(parentId="missing"),
        lambda m: m["objects"].append(deepcopy(m["objects"][0])),
        lambda m: m["assets"][0].update(exportFile="../escape.fbx"),
        lambda m: m["assets"][0].update(exportFile="/absolute.fbx"),
        lambda m: m["assets"][0].update(vertices=0),
        lambda m: m["objects"][0]["transform"].update(matrixWorld=[[1, 2]]),
        lambda m: m["objects"][0]["transform"]["matrixWorld"][0].__setitem__(0, float("nan")),
        lambda m: m["objects"][0]["transform"]["matrixWorld"][3].__setitem__(0, 1),
    ],
)
def test_manifest_rejects_incoherent_or_unsafe_interchange(mutation):
    manifest = sample_manifest()
    mutation(manifest)
    with pytest.raises(ValueError):
        export.validate_manifest(manifest)


def test_stable_ids_are_bounded_ascii_and_disambiguate_sanitized_names():
    assert export.identifier("obj", "Rack A") != export.identifier("obj", "Rack-A")
    assert len(export.identifier("obj", "a" * 200)) <= 101
    assert export.identifier("obj", "a/b") == export.identifier("obj", "a/b")


def test_geometry_dedup_includes_uv_and_material_identity():
    mesh = SimpleNamespace(
        vertices=[
            SimpleNamespace(co=[0, 0, 0]),
            SimpleNamespace(co=[1, 0, 0]),
            SimpleNamespace(co=[0, 1, 0]),
        ],
        loop_triangles=[SimpleNamespace(vertices=[0, 1, 2], material_index=0)],
        loops=[SimpleNamespace(vertex_index=i) for i in range(3)],
        corner_normals=[SimpleNamespace(vector=[0, 0, 1]) for _ in range(3)],
        uv_layers=[SimpleNamespace(name="UVMap", data=[SimpleNamespace(uv=[0, 0])])],
        color_attributes=[],
        calc_loop_triangles=lambda: None,
    )
    digest = export._geometry_digest(mesh, ["metal"])
    assert digest == export._geometry_digest(mesh, ["metal"])
    assert digest != export._geometry_digest(mesh, ["plastic"])
    mesh.uv_layers[0].data[0].uv = [1, 1]
    assert digest != export._geometry_digest(mesh, ["metal"])


def test_adapter_rechecks_asset_bytes_and_containment(tmp_path):
    manifest = sample_manifest()
    path = tmp_path / "assets" / "meshes" / "a.fbx"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"mock geometry, not native proof")
    manifest["assets"][0].update(
        fileSha256=hashlib.sha256(path.read_bytes()).hexdigest(), materialSlots=[]
    )
    manifest.update(
        materials=[],
        limitations=[],
        totals={"uniqueAssets": 1, "meshInstances": 1},
        boundsWorld={"min": [0, 0, 0], "max": [1, 1, 1], "dimensions": [1, 1, 1]},
    )
    manifest_path = tmp_path / "environment-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    evidence = _environment_evidence(tmp_path, manifest_path, True)
    assert all(evidence["checks"].values())
    path.write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        _environment_evidence(tmp_path, manifest_path, True)
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        _environment_evidence(tmp_path, manifest_path, True)


def test_adapter_rejects_texture_symlink_escape(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"mock texture")
    (bundle / "texture.png").symlink_to(outside)
    manifest = sample_manifest()
    manifest["assets"][0].update(exportFile=None, materialSlots=["mat_a"])
    manifest.update(
        materials=[
            {
                "id": "mat_a",
                "textures": {
                    "baseColor": {
                        "path": "texture.png",
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    }
                },
            }
        ],
        limitations=[],
        totals={"uniqueAssets": 1, "meshInstances": 1},
    )
    manifest_path = bundle / "environment-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes"):
        _environment_evidence(bundle, manifest_path, False)


def image_fixture(**overrides):
    props = {
        "name": "Original test texture",
        "source": "FILE",
        "filepath": "original.png",
        "file_format": "PNG",
        "packed_file": None,
        "library": None,
        "colorspace_settings": SimpleNamespace(name="sRGB"),
        "size": [1, 1],
        "get": lambda key: {"studio_provenance": "Original procedural fixture"}.get(key),
    }
    props.update(overrides)
    return SimpleNamespace(**props)


def test_missing_declared_texture_fails_instead_of_silently_omitting_it(tmp_path):
    image = image_fixture(filepath=str(tmp_path / "missing.png"))
    bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda path, **kwargs: path))
    with pytest.raises(ValueError, match="referenced image is missing"):
        export._copy_texture(bpy, image, tmp_path, set())
    assert not (tmp_path / "assets").exists()


def test_packed_texture_retains_exact_bytes_hash_color_space_and_provenance(tmp_path):
    # Byte preservation is tested here; native Blender tests establish image decodability.
    data = b"explicit synthetic packed-image fixture"
    image = image_fixture(packed_file=SimpleNamespace(data=data))
    texture = export._copy_texture(None, image, tmp_path, set())
    assert (tmp_path / texture["path"]).read_bytes() == data
    assert texture["sha256"] == hashlib.sha256(data).hexdigest()
    assert texture["sourceKind"] == "packed-image"
    assert texture["colorSpace"] == "sRGB"
    assert texture["provenance"] == "Original procedural fixture"
    assert "filepath" not in texture


def test_empty_or_dynamic_texture_requires_explicit_baking(tmp_path):
    with pytest.raises(ValueError, match="no image bytes"):
        export._copy_texture(
            None, image_fixture(packed_file=SimpleNamespace(data=b"")), tmp_path, set()
        )
    with pytest.raises(ValueError, match="bake MOVIE"):
        export._copy_texture(None, image_fixture(source="MOVIE"), tmp_path, set())
    limitations = set()
    assert (
        export._copy_texture(
            None,
            image_fixture(
                source="MOVIE", get=lambda key: False if key == "studio_export" else None
            ),
            tmp_path,
            limitations,
        )
        is None
    )
    assert limitations


def write_glb_fixture(path, document):
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    path.write_bytes(
        struct.pack("<4sIII4s", b"glTF", 2, len(payload) + 20, len(payload), b"JSON") + payload
    )


def test_glb_gate_detects_material_loss_with_unchanged_geometry_count(tmp_path):
    path = tmp_path / "scene.glb"
    document = {
        "asset": {"version": "2.0"},
        "nodes": [{"mesh": 0}, {"mesh": 0}],
        "meshes": [{"primitives": [{"material": 0}]}],
        "materials": [{"name": "Graphite"}],
        "images": [],
    }
    write_glb_fixture(path, document)
    with pytest.raises(ValueError, match="lost assigned materials: Warm_Panel"):
        export.validate_glb_export(path, 2, {"Graphite", "Warm_Panel"})
    document["materials"].append({"name": "Warm_Panel"})
    write_glb_fixture(path, document)
    result = export.validate_glb_export(path, 2, {"Graphite", "Warm_Panel"})
    assert result["meshDefinitions"] == 1
    assert result["meshInstances"] == 2
    assert result["assignedMaterialNamesPreserved"]
    with pytest.raises(ValueError, match="instance count differs"):
        export.validate_glb_export(path, 3, {"Graphite"})


def test_glb_gate_rejects_external_images_and_malformed_container(tmp_path):
    path = tmp_path / "scene.glb"
    write_glb_fixture(path, {"nodes": [], "images": [{"uri": "missing.png"}]})
    with pytest.raises(ValueError, match="must embed"):
        export.validate_glb_export(path, 0, set())
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="header"):
        export.validate_glb_export(path, 0, set())


def test_glb_gate_checks_material_assignments_not_only_material_presence(tmp_path):
    path = tmp_path / "scene.glb"
    write_glb_fixture(
        path,
        {
            "nodes": [{"name": "Wall", "mesh": 0}, {"name": "Rack", "mesh": 0}],
            "meshes": [{"primitives": [{"material": 0}]}],
            "materials": [{"name": "Graphite"}, {"name": "Warm_Panel"}],
        },
    )
    with pytest.raises(ValueError, match="per-object material assignments"):
        export.validate_glb_export(
            path, 2, {"Graphite", "Warm_Panel"}, {"Wall": {"Warm_Panel"}, "Rack": {"Graphite"}}
        )
