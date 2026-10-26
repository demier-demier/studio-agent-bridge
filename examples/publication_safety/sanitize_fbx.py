"""Rewrite six known FBX path-metadata fields with Blender's own binary codec.

This is an offline helper for the authored publication fixture, not an untrusted
FBX parser or a universal privacy sanitizer. Blender's bundled codec is imported
from an explicitly selected installation; its implementation is not vendored.
Original path strings are never resolved or opened. Texture destinations come
only from a validated local bundle manifest.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import importlib
import json
import os
import re
import struct
import sys
import tempfile
import types
from collections import Counter
from pathlib import Path, PurePosixPath

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_PARSED_BYTES = 256 * 1024 * 1024
CODEC_FILES = ("parse_fbx.py", "encode_bin.py", "data_types.py", "fbx_utils_threading.py")
PROPERTY_METHODS = dict(
    zip(
        "BCZYIFDLRSilfdbc",
        (
            "add_bool",
            "add_char",
            "add_int8",
            "add_int16",
            "add_int32",
            "add_float32",
            "add_float64",
            "add_int64",
            "add_bytes",
            "add_string",
            "add_int32_array",
            "add_int64_array",
            "add_float32_array",
            "add_float64_array",
            "add_bool_array",
            "add_byte_array",
        ),
    )
)
SCALAR_FORMATS = dict(zip("BCZYIFDL", ("<?", "<c", "<b", "<h", "<i", "<f", "<d", "<q")))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_codecs(module_directory):
    """Load pure bundled modules without executing the add-on's bpy initializer."""
    directory = Path(module_directory).resolve(strict=True)
    fingerprints = {name: digest((directory / name).read_bytes()) for name in CODEC_FILES}
    identity = digest((str(directory) + json.dumps(fingerprints, sort_keys=True)).encode())[:20]
    package_name = "_studio_publication_fbx_" + identity
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(directory)]
        sys.modules[package_name] = package
    parser = importlib.import_module(package_name + ".parse_fbx")
    encoder = importlib.import_module(package_name + ".encode_bin")
    return parser, encoder, fingerprints


def _bundle_file(root, relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Bundle references must be relative POSIX paths")
    value = PurePosixPath(relative)
    if value.is_absolute() or not value.parts or any(x in {"..", "."} for x in value.parts):
        raise ValueError("Bundle reference escapes the clean transfer directory")
    candidate = root
    for part in value.parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError("Bundle references cannot traverse symbolic links")
    if not candidate.resolve().is_relative_to(root) or not candidate.is_file():
        raise ValueError("Bundle texture must exist inside the clean transfer directory")
    return candidate


def texture_index(bundle_root, manifest_path):
    """Map public aliases to actual, hash-checked texture files, never old paths."""
    root = Path(bundle_root).resolve(strict=True)
    manifest = Path(manifest_path)
    if manifest.is_symlink() or not manifest.resolve().is_relative_to(root):
        raise ValueError("Manifest must belong to the clean transfer directory")
    value = json.loads(manifest.read_text())
    if value.get("schemaVersion") != "environment-manifest/v1":
        raise ValueError("Expected a supported environment transfer manifest")
    aliases = {}
    for material in value.get("materials", []):
        for texture in material.get("textures", {}).values():
            target = _bundle_file(root, texture["path"])
            if not 0 < target.stat().st_size <= MAX_FILE_BYTES:
                raise ValueError("Packaged texture exceeds the fixture size bound")
            data = target.read_bytes()
            if len(data) != texture["bytes"] or digest(data) != texture["sha256"]:
                raise ValueError("Packaged texture does not match its manifest")
            name = texture.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,200}", name):
                raise ValueError("Texture name must be a bounded public basename")
            candidates = {target.name, name + target.suffix}
            for alias in candidates:
                if alias in aliases and aliases[alias] != target:
                    raise ValueError("Texture basename aliases are ambiguous")
                aliases[alias] = target
    return root, aliases


def _field_rule(path, node):
    """Return an exact node/field/property rule, never a substring selector."""
    if path == (b"FBXHeaderExtension", b"SceneInfo", b"Properties70", b"P"):
        if node.props and node.props[0] == b"Original|ApplicationNativeFile":
            return "application-native-file", 4
    if path in {
        (b"Objects", b"Texture", b"FileName"),
        (b"Objects", b"Texture", b"RelativeFilename"),
        (b"Objects", b"Video", b"Filename"),
        (b"Objects", b"Video", b"RelativeFilename"),
    }:
        return "/".join(x.decode() for x in path), 0
    if path == (b"Objects", b"Video", b"Properties70", b"P"):
        if node.props and node.props[0] == b"Path":
            return "video-path-property", 4
    return None


def _rewrite_tree(root, destination, aliases):
    changes = {}
    counts = Counter()

    def rewrite(node, names=(), indexes=()):
        names = names + (node.id,) if node.id else names
        props = list(node.props)
        rule = _field_rule(names, node)
        if rule:
            name, index = rule
            if index >= len(props) or node.props_type[index] != ord("S"):
                raise ValueError("Allowlisted path field is not an FBX string")
            previous = props[index]
            if name == "application-native-file":
                replacement = b"scene-day.blend"
            else:
                if not isinstance(previous, bytes) or b"\0" in previous:
                    raise ValueError("Texture path metadata is not a plain string")
                # Only the basename is used; never resolve an original machine path.
                basename = previous.replace(b"\\", b"/").rsplit(b"/", 1)[-1].decode("utf-8")
                target = aliases.get(basename)
                if target is None:
                    raise ValueError("FBX texture has no unambiguous packaged texture")
                replacement = Path(os.path.relpath(target, destination.parent)).as_posix().encode()
            if previous != replacement:
                props[index] = replacement
                changes[(indexes, index)] = replacement
                counts[name] += 1
        children = [rewrite(child, names, indexes + (i,)) for i, child in enumerate(node.elems)]
        return node._replace(props=props, elems=children)

    return rewrite(root), changes, dict(sorted(counts.items()))


def _property_bytes(kind, value):
    letter = chr(kind)
    if letter in SCALAR_FORMATS:
        return struct.pack(SCALAR_FORMATS[letter], value)
    if letter in "RS":
        if not isinstance(value, bytes):
            raise ValueError("FBX string/binary value has an unexpected type")
        return value
    if letter in "ilfdbc" and isinstance(value, array.array):
        # Both parsed trees use the same codec/interpreter and native array byte order.
        return value.typecode.encode() + b"\0" + value.tobytes()
    raise ValueError("Unsupported FBX property type")


def tree_fingerprint(root, masked=()):
    """Hash ordered node identity and every typed property; never truncate repr."""
    full, arrays = hashlib.sha256(), hashlib.sha256()
    count = Counter()
    masked = set(masked)

    def put(target, value):
        target.update(struct.pack("<Q", len(value)))
        target.update(value)

    def visit(node, indexes=(), depth=0):
        count["nodes"] += 1
        if depth > 64 or count["nodes"] > 200_000 or len(node.props) != len(node.props_type):
            raise ValueError("Parsed FBX exceeds supported tree bounds")
        put(full, node.id)
        put(full, bytes(node.props_type))
        full.update(struct.pack("<Q", len(node.elems)))
        for index, (kind, value) in enumerate(zip(node.props_type, node.props)):
            raw = _property_bytes(kind, value)
            count["properties"] += 1
            count["property_bytes"] += len(raw)
            if count["properties"] > 1_000_000 or count["property_bytes"] > MAX_PARSED_BYTES:
                raise ValueError("Parsed FBX exceeds supported property bounds")
            put(full, b"" if (indexes, index) in masked else raw)
            if chr(kind) in "ilfdbc":
                count["arrays"] += 1
                put(arrays, json.dumps([indexes, index, kind]).encode())
                put(arrays, raw)
        for index, child in enumerate(node.elems):
            visit(child, indexes + (index,), depth + 1)

    visit(root)
    return {
        "sha256": full.hexdigest(),
        "arrays_sha256": arrays.hexdigest(),
        **{key: count[key] for key in ("nodes", "properties", "property_bytes", "arrays")},
    }


def _encoded_tree(node, encoder):
    result = encoder.FBXElem(node.id)
    for kind, value in zip(node.props_type, node.props):
        method = PROPERTY_METHODS.get(chr(kind))
        if method is None:
            raise ValueError("FBX encoder does not support this typed property")
        getattr(result, method)(value)
    result.elems.extend(_encoded_tree(child, encoder) for child in node.elems)
    return result


def _scan_output(data, forbidden_fragments):
    fragments = tuple(forbidden_fragments)
    if not fragments or any(not isinstance(v, str) or not 4 <= len(v) <= 2000 for v in fragments):
        raise ValueError("Provide bounded private identifiers in local-only configuration")
    patterns = [x.encode().lower() for x in fragments]
    patterns += [b"/users/", b"/home/", b"/var/folders/", b"/private/var/"]
    normalized = data.lower().replace(b"\\", b"/").replace(b"\0", b"")
    if any(marker in normalized for marker in patterns):
        raise ValueError("Rewritten FBX still matches a configured private-data rule")


def sanitize_fbx(
    source, destination, *, bundle_root, manifest_path, module_directory, forbidden_fragments
):
    """Create a distinct FBX only after precise structural and privacy checks."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Source must be a regular trusted FBX file")
    if not 27 <= source.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("Source must be bounded binary FBX")
    original = source.read_bytes()
    if not 27 <= len(original) <= MAX_FILE_BYTES or not original.startswith(MAGIC):
        raise ValueError("Source must be bounded binary FBX")
    version = struct.unpack_from("<I", original, len(MAGIC))[0]
    if version not in {7400, 7500}:
        raise ValueError("Only binary FBX 7400 and 7500 are supported by this helper")
    root, aliases = texture_index(bundle_root, manifest_path)
    if not destination.is_absolute():
        destination = destination.absolute()
    if (
        not destination.is_relative_to(root)
        or not destination.parent.is_dir()
        or not destination.resolve().is_relative_to(root)
    ):
        raise ValueError("Destination must have an existing parent inside the clean bundle")
    current = destination
    while current != root:
        if current.is_symlink():
            raise ValueError("Destination cannot traverse symbolic links")
        current = current.parent
    if os.path.lexists(destination) or destination.resolve() == source.resolve():
        raise FileExistsError("Publication helper never replaces a destination or its source")
    parser, encoder, codec_hashes = load_codecs(module_directory)
    original_tree, parsed_version = parser.parse(source)
    if parsed_version != version:
        raise ValueError("FBX header version changed during parsing")
    before = tree_fingerprint(original_tree)
    expected_tree, changes, counts = _rewrite_tree(original_tree, destination, aliases)
    expected = tree_fingerprint(expected_tree)
    original_masked = tree_fingerprint(original_tree, changes)
    handle, temporary_name = tempfile.mkstemp(
        prefix=".fbx-publication-", suffix=".tmp", dir=destination.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    published = False
    try:
        encoder.write(temporary, _encoded_tree(expected_tree, encoder), version)
        rewritten = temporary.read_bytes()
        if len(rewritten) > MAX_FILE_BYTES:
            raise ValueError("Rewritten FBX exceeds the supported size bound")
        actual_tree, actual_version = parser.parse(temporary)
        after = tree_fingerprint(actual_tree)
        if actual_version != version or after != expected:
            raise ValueError("FBX reparse changed a property outside the exact requested rewrite")
        after_masked = tree_fingerprint(actual_tree, changes)
        if (
            original_masked["sha256"] != after_masked["sha256"]
            or before["arrays_sha256"] != after["arrays_sha256"]
        ):
            raise ValueError("Non-path FBX data changed during metadata rewriting")
        _scan_output(rewritten, forbidden_fragments)
        if source.read_bytes() != original:
            raise ValueError("Source changed during metadata rewriting")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, destination)  # Atomic no-clobber publication on the same filesystem.
        published = True
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if published and destination.exists() and os.path.samefile(temporary, destination):
            destination.unlink()
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema_version": "fbx-publication-rewrite/v1",
        "fbx_version": version,
        "source_sha256": digest(original),
        "output_sha256": digest(rewritten),
        "source_bytes": len(original),
        "output_bytes": len(rewritten),
        "codec_modules_sha256": codec_hashes,
        "changed_fields": len(changes),
        "changes_by_rule": counts,
        "typed_tree_matches_exact_rewrite": True,
        "non_path_tree_sha256": original_masked["sha256"],
        "array_values_sha256": before["arrays_sha256"],
        "arrays_preserved": before["arrays"],
        "properties_checked": before["properties"],
        "nodes_checked": before["nodes"],
        "configured_private_rules_passed": True,
        "source_preserved": True,
        "limitations": [
            "Only six exact path-metadata field shapes may change. Numeric arrays and every other ordered typed property are compared after encoding and reparsing.",
            "This trusted-fixture helper uses Blender's official codec; it is not a hardened parser for arbitrary untrusted FBX input.",
            "Texture references map only to hash-checked manifest textures. Native target validation remains separate from typed-tree equivalence.",
            "Configured pattern detection is not proof that all possible private information is absent.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "source",
        "destination",
        "bundle-root",
        "manifest",
        "fbx-modules",
        "private-config",
        "report",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.path.lexists(args.report) or not args.report.parent.is_dir():
        raise FileExistsError("Report requires a fresh path in an existing directory")
    config = json.loads(args.private_config.read_text())
    report = sanitize_fbx(
        args.source,
        args.destination,
        bundle_root=args.bundle_root,
        manifest_path=args.manifest,
        module_directory=args.fbx_modules,
        forbidden_fragments=config["forbidden_fragments"],
    )
    with args.report.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {"changed_fields": report["changed_fields"], "output_sha256": report["output_sha256"]}
        )
    )


if __name__ == "__main__":
    main()
