"""Synthetic byte fixtures exercise publication gates, not Blender semantics."""

import gzip
import hashlib
import importlib.util
import json
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

from studio_agent_bridge import publication as safety

HEADER = b"BLENDER-v502"
PRIVATE = ["sample_account"]


def scene(payload=b"safe synthetic bytes"):
    return HEADER + payload


def scan(data):
    return safety.scan_blend(data, forbidden_fragments=PRIVATE)


@pytest.mark.parametrize(
    "payload",
    [
        b"/Users/example/Documents/source.blend",
        b"/home/another/model.blend",
        b"C:\\Users\\example\\source.blend",
        b"/private/var/folders/owned/input.blend",
        b"prefix\0\0SAMPLE_ACCOUNT\0padding",
        b"/Users/sam\0\0ple_acc\0ount/path",
        "sample_account".encode("utf-16le"),
        b"stale buffer sample_account after a shorter renamed path",
    ],
)
def test_plain_and_gzip_detect_private_bytes_including_stale_padding(payload):
    for data in (scene(payload), gzip.compress(scene(payload))):
        report = scan(data)
        assert report["passed"] is False
        assert report["decoded"]["findings"]
        public = json.dumps(report)
        assert "sample_account" not in public and "/Users/" not in public


def test_matches_cross_stream_chunk_boundaries(monkeypatch):
    monkeypatch.setattr(safety, "CHUNK_BYTES", 7)
    for data in (
        scene(b"xx/Users/user/file"),
        scene(b"xxsample_accountyy"),
        scene(b"sam\0ple_acc\0ount"),
    ):
        assert scan(data)["passed"] is False


def test_plain_and_gzip_safe_bytes_report_observed_hashes():
    raw = scene()
    for encoded, kind in ((raw, "plain"), (gzip.compress(raw), "gzip")):
        report = scan(encoded)
        assert report["passed"] is True and report["format"] == kind
        assert report["raw"]["sha256"] == hashlib.sha256(encoded).hexdigest()
        assert report["decoded"]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert report["decoded"]["bytes"] == len(raw)


def test_current_blender_format_01_header_is_supported_strictly():
    raw = b"BLENDER17-01v0502" + b"REND synthetic bytes"
    for encoded in (raw, gzip.compress(raw)):
        report = scan(encoded)
        assert report["passed"] is True
        assert report["header_format"] == "versioned-17"
        assert report["decoded"]["sha256"] == hashlib.sha256(raw).hexdigest()
    for header in (
        b"BLENDER16-01v0502",
        b"BLENDER17-02v0502",
        b"BLENDER17-01V0502",
        b"BLENDER17-01v050",
        b"BLENDER17-01vtext",
    ):
        with pytest.raises(safety.PublicationError, match="recognized Blender header"):
            scan(header + b"REND")


@pytest.mark.parametrize(
    "data",
    [
        b"unknown compressed bytes",
        b"BLENDER",
        b"BLENDER?x502body",
        b"\x1f\x8b\x08",
        gzip.compress(scene())[:-4],
        gzip.compress(b"not a Blender header"),
    ],
)
def test_unknown_truncated_or_invalid_streams_fail_closed(data):
    with pytest.raises(safety.PublicationError):
        scan(data)


def test_limits_apply_to_plain_compressed_and_decoded_bytes(monkeypatch):
    monkeypatch.setattr(safety, "MAX_MEMBER_BYTES", 25)
    with pytest.raises(safety.PublicationError, match="member exceeds"):
        scan(scene(b"x" * 30))
    monkeypatch.setattr(safety, "MAX_MEMBER_BYTES", 1000)
    monkeypatch.setattr(safety, "MAX_DECODED_BYTES", 30)
    with pytest.raises(safety.PublicationError, match="Decoded"):
        scan(gzip.compress(scene(b"x" * 100)))


@pytest.mark.skipif(
    shutil.which("zstd") is None, reason="zstd CLI required for real compressed fixture"
)
def test_real_zstd_stream_is_scanned_after_decompression_and_truncation_rejected():
    source = scene(b"embedded sample_account buffer")
    compressed = subprocess.run(
        [shutil.which("zstd"), "-q", "-c"], input=source, capture_output=True, check=True
    ).stdout
    report = scan(compressed)
    assert report["format"] == "zstd" and report["passed"] is False
    assert report["decoded"]["sha256"] == hashlib.sha256(source).hexdigest()
    with pytest.raises(safety.PublicationError, match="zstd stream"):
        scan(compressed[:-3])


def test_missing_zstd_never_counts_as_success(monkeypatch):
    monkeypatch.setattr(safety.shutil, "which", lambda _: None)
    with pytest.raises(safety.PublicationError, match="requires the zstd"):
        scan(b"\x28\xb5\x2f\xfdunsupported")


def test_generic_sanitizer_prefix_literals_are_not_real_paths():
    report = safety.scan_bytes(
        b"markers = ['/Users/', '/var/folders/', '/home/']", forbidden_fragments=PRIVATE
    )
    assert report["passed"] is True
    assert (
        safety.scan_bytes(b"value='/Users/person/scene'", forbidden_fragments=PRIVATE)["passed"]
        is False
    )


def test_random_binary_drive_prefix_is_not_treated_as_a_windows_path():
    binary = b"packed I:\\\xf8\x01noise s:/\xad\xa0more\0w:/7|\xda\xfa\0Z:\\fw+\xc1\x02"
    assert safety.scan_bytes(binary, forbidden_fragments=PRIVATE)["passed"] is True
    assert scan(scene(binary))["passed"] is True
    for path in (b"C:\\Projects\\scene.blend", b"D:/models/asset.fbx", b"C:\\Users\\person\\model"):
        report = safety.scan_bytes(path, forbidden_fragments=PRIVATE)
        assert report["passed"] is False
        assert any(item["rule"] == "windows-drive-path" for item in report["raw"]["findings"])


def test_complete_windows_paths_match_across_chunks_and_nul_padding(monkeypatch):
    monkeypatch.setattr(safety, "CHUNK_BYTES", 7)
    for encoded in (
        b"C:\\Projects\\scene.blend\0padding",
        b"D:/models/" + b"nested/" * 12 + b"asset.fbx",
        b"label='E:/private-project/scene.blend'",
        "C:/Projects/scene.blend".encode("utf-16le"),
        b"C:/Pro\0\0jects/scene.blend",
    ):
        report = safety.scan_bytes(encoded, forbidden_fragments=PRIVATE)
        assert report["passed"] is False
        assert any(item["rule"] == "windows-drive-path" for item in report["raw"]["findings"])
    # A chunk boundary is not EOF: the following binary byte invalidates the path.
    assert safety.scan_bytes(b"xxZ:/fw+\xc1\x02", forbidden_fragments=PRIVATE)["passed"] is True


@pytest.mark.parametrize(
    "name", ["transfer/mesh.fbx", "scripts/tool.py", "evidence.json", "image.png"]
)
def test_non_blend_members_are_scanned_without_a_script_exemption(tmp_path, name):
    output = tmp_path / "native-v2.zip"
    with pytest.raises(safety.PublicationError, match="member bytes"):
        safety.publish_native_archive(
            {"scene.blend": scene(), name: b"sample_account"},
            output,
            version=2,
            forbidden_fragments=PRIVATE,
        )
    assert not output.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    [
        "../scene.blend",
        "/scene.blend",
        "a//scene.blend",
        "a\\scene.blend",
        "C:scene.blend",
        "a/./scene.blend",
        "sample_account.blend",
    ],
)
def test_member_names_cannot_escape_or_leak(tmp_path, name):
    with pytest.raises(safety.PublicationError):
        safety.publish_native_archive(
            {name: scene()}, tmp_path / "native-v2.zip", version=2, forbidden_fragments=PRIVATE
        )


def test_existing_public_archive_and_sources_preserved_on_failure(tmp_path):
    original = tmp_path / "native-v2.zip"
    original.write_bytes(b"previous release must not be truncated")
    before = original.read_bytes()
    with pytest.raises(safety.PublicationError, match="already exists"):
        safety.publish_native_archive(
            {"scene.blend": scene(b"sample_account")},
            original,
            version=2,
            forbidden_fragments=PRIVATE,
        )
    assert original.read_bytes() == before
    with pytest.raises(safety.PublicationError):
        safety.publish_native_archive(
            {"scene.blend": scene(b"sample_account")},
            tmp_path / "native-v3.zip",
            version=3,
            forbidden_fragments=PRIVATE,
        )
    assert original.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [original.name]


def test_valid_archive_deterministic_and_reread_without_modifying_members(tmp_path):
    payloads = {
        "scene.blend": gzip.compress(scene(), mtime=0),
        "transfer/mesh.fbx": b"synthetic geometry",
        "scripts/sanitize.py": b"markers=['/Users/']",
    }
    reports = []
    for folder in (tmp_path / "one", tmp_path / "two"):
        folder.mkdir()
        output = folder / "native-v2.zip"
        report = safety.publish_native_archive(
            payloads, output, version=2, forbidden_fragments=PRIVATE
        )
        reports.append(report)
        assert safety.scan_archive(output, forbidden_fragments=PRIVATE)["passed"] is True
        with zipfile.ZipFile(output) as archive:
            assert {name: archive.read(name) for name in archive.namelist()} == payloads
    assert reports[0] == reports[1]


def test_caller_payload_mutation_after_scan_cannot_change_published_bytes(tmp_path, monkeypatch):
    payloads = {"scene.blend": scene()}
    original = dict(payloads)
    validate = safety.validate_payloads

    def mutate_after_scan(snapshot, **kwargs):
        report = validate(snapshot, **kwargs)
        payloads["scene.blend"] = scene(b"sample_account")
        payloads["new-private.txt"] = b"sample_account"
        return report

    monkeypatch.setattr(safety, "validate_payloads", mutate_after_scan)
    output = tmp_path / "native-v2.zip"
    safety.publish_native_archive(payloads, output, version=2, forbidden_fragments=PRIVATE)
    with zipfile.ZipFile(output) as archive:
        assert {name: archive.read(name) for name in archive.namelist()} == original


def test_destination_created_during_staging_is_preserved(tmp_path, monkeypatch):
    output = tmp_path / "native-v2.zip"
    real_link = safety.os.link

    def race(source, destination, **kwargs):
        output.write_bytes(b"concurrent release")
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(safety.os, "link", race)
    with pytest.raises(FileExistsError):
        safety.publish_native_archive(
            {"scene.blend": scene()}, output, version=2, forbidden_fragments=PRIVATE
        )
    assert output.read_bytes() == b"concurrent release"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize(
    "metadata_kind", ["comment", "member-comment", "member-extra", "member-name"]
)
def test_existing_zip_metadata_is_inspected(tmp_path, metadata_kind):
    output = tmp_path / "input.zip"
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo(
            "sample_account.blend" if metadata_kind == "member-name" else "scene.blend"
        )
        if metadata_kind == "comment":
            archive.comment = b"sample_account"
        elif metadata_kind == "member-comment":
            info.comment = b"sample_account"
        elif metadata_kind == "member-extra":
            info.extra = b"\xfe\xca\x0e\x00sample_account"
        archive.writestr(info, scene())
    with pytest.raises(safety.PublicationError):
        safety.scan_archive(output, forbidden_fragments=PRIVATE)


def test_zip_nul_truncated_original_member_name_cannot_hide_private_bytes(tmp_path):
    output = tmp_path / "input.zip"
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("safe")
        info.filename = info.orig_filename = "safe\0sample_account.txt"
        archive.writestr(info, b"safe bytes")
    with pytest.raises(safety.PublicationError, match="ambiguous original member name"):
        safety.scan_archive(output, forbidden_fragments=PRIVATE)


@pytest.mark.parametrize("where", ["prefix", "suffix", "local-extra"])
def test_zip_opaque_metadata_is_not_skipped(tmp_path, where):
    output = tmp_path / "input.zip"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("scene.blend", scene())
    data = output.read_bytes()
    if where == "prefix":
        data = b"sample_account" + data
    elif where == "suffix":
        data += b"sample_account"
    else:
        # A valid local-only extra field, intentionally absent from the central header.
        marker = b"\xfe\xca\x0e\x00sample_account"
        name_size = struct.unpack_from("<H", data, 26)[0]
        insertion = 30 + name_size
        data = bytearray(data[:insertion] + marker + data[insertion:])
        struct.pack_into("<H", data, 28, len(marker))
        end = len(data) - 22
        offset = struct.unpack_from("<I", data, end + 16)[0]
        struct.pack_into("<I", data, end + 16, offset + len(marker))
    output.write_bytes(data)
    with pytest.raises(safety.PublicationError):
        safety.scan_archive(output, forbidden_fragments=PRIVATE)


def test_publication_policy_requires_explicit_local_fragments(tmp_path):
    for fragments in (None, [], ["abc"], [False]):
        with pytest.raises(safety.PublicationError):
            safety.publish_native_archive(
                {"scene.blend": scene()},
                tmp_path / "native-v2.zip",
                version=2,
                forbidden_fragments=fragments,
            )


def test_exclusive_directory_publish_preserves_existing_and_handles_new_version(tmp_path):
    staged = tmp_path / "staging"
    staged.mkdir()
    (staged / "safe.txt").write_text("validated bytes")
    output = tmp_path / "v2"
    expected = {"safe.txt": {"sha256": hashlib.sha256(b"validated bytes").hexdigest(), "bytes": 15}}
    safety.publish_directory(staged, output, expected_files=expected)
    assert (output / "safe.txt").read_text() == "validated bytes"
    (staged / "safe.txt").write_text("replacement")
    with pytest.raises(safety.PublicationError):
        safety.publish_directory(staged, output, expected_files=expected)
    assert (output / "safe.txt").read_text() == "validated bytes"


def test_exclusive_directory_publish_preserves_a_racing_empty_destination(tmp_path, monkeypatch):
    staged = tmp_path / "staging"
    staged.mkdir()
    (staged / "safe.txt").write_text("validated bytes")
    output = tmp_path / "v2"
    real_copy = safety.shutil.copytree
    inode = []

    def race(*args, **kwargs):
        result = real_copy(*args, **kwargs)
        output.mkdir()
        inode.append(output.stat().st_ino)
        return result

    monkeypatch.setattr(safety.shutil, "copytree", race)
    expected = {"safe.txt": {"sha256": hashlib.sha256(b"validated bytes").hexdigest(), "bytes": 15}}
    with pytest.raises(safety.PublicationError, match="Exclusive publication"):
        safety.publish_directory(staged, output, expected_files=expected)
    assert output.stat().st_ino == inode[0]
    assert list(output.iterdir()) == []


def test_changed_staging_bytes_cannot_be_published_under_an_old_scan(tmp_path, monkeypatch):
    staged = tmp_path / "staging"
    staged.mkdir()
    (staged / "safe.txt").write_bytes(b"validated bytes")
    expected = {"safe.txt": {"sha256": hashlib.sha256(b"validated bytes").hexdigest(), "bytes": 15}}
    real_copy = safety.shutil.copytree

    def mutate_before_copy(*args, **kwargs):
        (staged / "safe.txt").write_bytes(b"sample_account")
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(safety.shutil, "copytree", mutate_before_copy)
    with pytest.raises(safety.PublicationError, match="differ from the scanned manifest"):
        safety.publish_directory(staged, tmp_path / "v2", expected_files=expected)
    assert not (tmp_path / "v2").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["staging"]


@pytest.mark.parametrize("failure", ["build-error", "private-sidecar"])
def test_packager_failure_never_touches_existing_public_version(tmp_path, monkeypatch, failure):
    spec = importlib.util.spec_from_file_location(
        "package_environment",
        Path(__file__).resolve().parents[1] / "web/scripts/package-environment.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = tmp_path / "v2"
    original.mkdir()
    (original / "old.zip").write_bytes(b"previous package")
    config = {
        "publication_version": 3,
        "publication_output": str(tmp_path / "v3"),
        "publication_forbidden_fragments": PRIVATE,
    }

    def invalid_build(config, staged):
        (staged / "evidence.json").write_bytes(b'{"unexpected": "sample_account"}')
        if failure == "build-error":
            raise safety.PublicationError("synthetic publication failure")
        return {}

    monkeypatch.setattr(module, "build_package", invalid_build)
    with pytest.raises(safety.PublicationError):
        module.package(config)
    assert not (tmp_path / "v3").exists()
    assert (original / "old.zip").read_bytes() == b"previous package"
