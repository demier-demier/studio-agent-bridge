"""Synthetic tree/boundary tests; native codec and importer evidence is separate."""

import array
import copy
import hashlib
import importlib.util
import json
import struct
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "fbx_publication_helper",
    Path(__file__).resolve().parents[1] / "examples/publication_safety/sanitize_fbx.py",
)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
Node = namedtuple("Node", "id props props_type elems")


def node(name, props=(), types="", children=()):
    return Node(name, list(props), bytearray(types.encode()), list(children))


def field(name, value):
    return node(b"P", (name, b"KString", b"", b"", value), "SSSSS")


def original_tree():
    texture = b"/home/test-person/local/Paint.png"
    return node(
        b"",
        children=[
            node(
                b"FBXHeaderExtension",
                children=[
                    node(
                        b"SceneInfo",
                        children=[
                            node(
                                b"Properties70",
                                children=[
                                    field(
                                        b"Original|ApplicationNativeFile",
                                        b"/home/test-person/source.blend",
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
            ),
            node(
                b"Objects",
                children=[
                    node(
                        b"Texture",
                        children=[
                            node(b"FileName", [texture], "S"),
                            node(b"RelativeFilename", [texture], "S"),
                        ],
                    ),
                    node(
                        b"Video",
                        children=[
                            node(b"Properties70", children=[field(b"Path", texture)]),
                            node(b"Filename", [texture], "S"),
                            node(b"RelativeFilename", [texture], "S"),
                        ],
                    ),
                    node(
                        b"Geometry",
                        children=[
                            node(
                                b"Vertices", [array.array("d", [0.0, -0.0, 1.23456789012345])], "d"
                            ),
                            node(b"PolygonVertexIndex", [array.array("i", [0, 1, -3])], "i"),
                        ],
                    ),
                ],
            ),
            # Same field labels elsewhere are deliberately outside the exact allowlist.
            node(b"Custom", children=[field(b"Original|ApplicationNativeFile", b"keep")]),
        ],
    )


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "clean"
    (root / "assets/meshes").mkdir(parents=True)
    (root / "assets/textures").mkdir()
    texture = root / "assets/textures/content-hash.png"
    texture.write_bytes(b"synthetic texture fixture")
    record = {
        "path": texture.relative_to(root).as_posix(),
        "name": "Paint",
        "bytes": texture.stat().st_size,
        "sha256": hashlib.sha256(texture.read_bytes()).hexdigest(),
    }
    manifest = root / "environment-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "environment-manifest/v1",
                "materials": [{"textures": {"baseColor": record}}],
            }
        )
    )
    source = tmp_path / "source.fbx"
    source.write_bytes(helper.MAGIC + struct.pack("<I", 7400) + b"synthetic source")
    return root, manifest, source, root / "assets/meshes/output.fbx"


def test_exact_path_rewrite_keeps_numeric_arrays_and_unrelated_fields(bundle):
    root, manifest, _, destination = bundle
    _, aliases = helper.texture_index(root, manifest)
    source = original_tree()
    before = helper.tree_fingerprint(source)
    rewritten, changes, counts = helper._rewrite_tree(source, destination, aliases)
    after = helper.tree_fingerprint(rewritten)
    assert sum(counts.values()) == len(changes) == 6
    assert before["arrays"] == after["arrays"] == 2
    assert before["arrays_sha256"] == after["arrays_sha256"]
    assert before["sha256"] != after["sha256"]
    assert (
        helper.tree_fingerprint(source, changes)["sha256"]
        == helper.tree_fingerprint(rewritten, changes)["sha256"]
    )
    assert helper.tree_fingerprint(source) == before  # No mutation of the source tree.
    assert rewritten.elems[-1] == source.elems[-1]
    assert set(changes.values()) == {b"scene-day.blend", b"../textures/content-hash.png"}


@pytest.mark.parametrize("replacement", [0.0, 1e-15])
def test_full_tree_fingerprint_detects_exact_array_values_including_signed_zero(replacement):
    source = original_tree()
    changed = copy.deepcopy(source)
    changed.elems[1].elems[2].elems[0].props[0][1] = replacement
    assert helper.tree_fingerprint(source)["sha256"] != helper.tree_fingerprint(changed)["sha256"]
    assert (
        helper.tree_fingerprint(source)["arrays_sha256"]
        != helper.tree_fingerprint(changed)["arrays_sha256"]
    )


def test_full_tree_fingerprint_includes_order_and_property_types():
    source = original_tree()
    reordered = source._replace(elems=list(reversed(source.elems)))
    assert helper.tree_fingerprint(source)["sha256"] != helper.tree_fingerprint(reordered)["sha256"]
    assert (
        helper.tree_fingerprint(node(b"x", [1.0], "F"))["sha256"]
        != helper.tree_fingerprint(node(b"x", [1.0], "D"))["sha256"]
    )


def test_unknown_texture_basename_fails_without_reading_old_path(bundle):
    root, manifest, _, destination = bundle
    _, aliases = helper.texture_index(root, manifest)
    source = original_tree()
    source.elems[1].elems[0].elems[0].props[0] = b"/home/test-person/missing.png"
    with pytest.raises(ValueError, match="unambiguous packaged texture"):
        helper._rewrite_tree(source, destination, aliases)
    assert not destination.exists()


@pytest.mark.parametrize("problem", ["changed", "missing", "symlink", "ambiguous"])
def test_texture_manifest_rejects_changed_missing_escaped_or_ambiguous_targets(bundle, problem):
    root, manifest, _, _ = bundle
    value = json.loads(manifest.read_text())
    texture = root / "assets/textures/content-hash.png"
    if problem == "changed":
        texture.write_bytes(b"different bytes")
    elif problem == "missing":
        texture.unlink()
    elif problem == "symlink":
        outside = root.parent / "outside.png"
        outside.write_bytes(texture.read_bytes())
        texture.unlink()
        texture.symlink_to(outside)
    else:
        second = root / "assets/textures/other.png"
        second.write_bytes(texture.read_bytes())
        record = dict(
            value["materials"][0]["textures"]["baseColor"], path="assets/textures/other.png"
        )
        value["materials"].append({"textures": {"baseColor": record}})
        manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        helper.texture_index(root, manifest)


def invoke(bundle, **kwargs):
    root, manifest, source, destination = bundle
    return helper.sanitize_fbx(
        source,
        destination,
        bundle_root=root,
        manifest_path=manifest,
        module_directory=root / "unused-codec",
        forbidden_fragments=["test-person"],
        **kwargs,
    )


def test_existing_destination_and_source_survive_without_loading_codec(bundle, monkeypatch):
    _, _, source, destination = bundle
    destination.write_bytes(b"existing output")
    original = source.read_bytes()
    monkeypatch.setattr(helper, "load_codecs", lambda *_: pytest.fail("Codec must not load"))
    with pytest.raises(FileExistsError):
        invoke(bundle)
    assert destination.read_bytes() == b"existing output"
    assert source.read_bytes() == original


@pytest.mark.parametrize("failure", ["wrong-array", "privacy", "writer", "racing-output"])
def test_post_write_failures_preserve_original_and_never_publish_unchecked_bytes(
    bundle, monkeypatch, failure
):
    root, manifest, source, destination = bundle
    source_tree = original_tree()
    _, aliases = helper.texture_index(root, manifest)
    expected, _, _ = helper._rewrite_tree(source_tree, destination, aliases)
    changed = copy.deepcopy(expected)
    changed.elems[1].elems[2].elems[0].props[0][0] = 9.0
    original = source.read_bytes()

    def parse(path):
        return (
            source_tree
            if Path(path) == source
            else changed
            if failure == "wrong-array"
            else expected
        ), 7400

    def write(path, tree, version):
        if failure == "writer":
            Path(path).write_bytes(b"partial write")
            raise OSError("Synthetic writer failure")
        Path(path).write_bytes(
            helper.MAGIC
            + struct.pack("<I", version)
            + (
                b"t\0e\0s\0t\0-\0p\0e\0r\0s\0o\0n"
                if failure == "privacy"
                else b"clean synthetic output"
            )
        )
        if failure == "racing-output":
            destination.write_bytes(b"concurrent output")

    parser, encoder = SimpleNamespace(parse=parse), SimpleNamespace(write=write)
    monkeypatch.setattr(helper, "load_codecs", lambda *_: (parser, encoder, {}))
    monkeypatch.setattr(helper, "_encoded_tree", lambda tree, _: tree)
    with pytest.raises((ValueError, OSError)):
        invoke(bundle)
    assert source.read_bytes() == original
    if failure == "racing-output":
        assert destination.read_bytes() == b"concurrent output"
    else:
        assert not destination.exists()
    assert not list(destination.parent.glob(".fbx-publication-*"))


def test_destination_symlink_parent_is_rejected_before_codec(bundle, monkeypatch):
    root, manifest, source, destination = bundle
    link = root / "alias"
    link.symlink_to(destination.parent, target_is_directory=True)
    monkeypatch.setattr(helper, "load_codecs", lambda *_: pytest.fail("Codec must not load"))
    with pytest.raises(ValueError, match="symbolic links"):
        helper.sanitize_fbx(
            source,
            link / "new.fbx",
            bundle_root=root,
            manifest_path=manifest,
            module_directory=root / "unused",
            forbidden_fragments=["test-person"],
        )


@pytest.mark.parametrize("relative", [False, True])
def test_destination_escape_is_rejected_before_codec(bundle, monkeypatch, relative):
    root, manifest, source, _ = bundle
    destination = Path("../outside.fbx") if relative else root / "../outside.fbx"
    monkeypatch.setattr(helper, "load_codecs", lambda *_: pytest.fail("Codec must not load"))
    with pytest.raises(ValueError, match="inside the clean bundle"):
        helper.sanitize_fbx(
            source,
            destination,
            bundle_root=root,
            manifest_path=manifest,
            module_directory=root / "unused",
            forbidden_fragments=["test-person"],
        )
    assert not (root.parent / "outside.fbx").exists()


def test_unlisted_private_field_is_not_silently_rewritten(bundle):
    root, manifest, _, destination = bundle
    _, aliases = helper.texture_index(root, manifest)
    original = original_tree()
    original.elems[-1].elems[0].props[4] = b"/home/test-person/another.blend"
    rewritten, changes, _ = helper._rewrite_tree(original, destination, aliases)
    assert len(changes) == 6
    untouched = rewritten.elems[-1].elems[0].props[4]
    assert untouched == original.elems[-1].elems[0].props[4]
    with pytest.raises(ValueError, match="private-data rule"):
        helper._scan_output(untouched, ["test-person"])
