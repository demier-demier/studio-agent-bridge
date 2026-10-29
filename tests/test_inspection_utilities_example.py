"""Stop changed inputs and preserve evidence before trusted native execution."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / "examples/inspection_workcell/run_utilities.py"
spec = importlib.util.spec_from_file_location("inspection_utilities_example", PATH)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def test_changed_source_creates_no_run_state(tmp_path):
    source = tmp_path / "scene.blend"
    source.write_bytes(b"changed source")
    output = tmp_path / "attempt"
    with pytest.raises(ValueError, match="reviewed SHA-256"):
        example.prepare(output, source, "0" * 64)
    assert not output.exists()
    assert source.read_bytes() == b"changed source"


def test_existing_evidence_and_empty_directories_are_preserved(tmp_path):
    output = tmp_path / "attempt"
    output.mkdir()
    with pytest.raises(ValueError, match="new output"):
        example.prepare(output, tmp_path / "missing.blend", "0" * 64)
    artifact = output / "native.blend"
    artifact.write_bytes(b"previous evidence")
    with pytest.raises(ValueError, match="new output"):
        example.prepare(output, tmp_path / "missing.blend", "0" * 64)
    assert artifact.read_bytes() == b"previous evidence"


def test_symlink_source_is_not_followed(tmp_path):
    source = tmp_path / "original.blend"
    source.write_bytes(b"source")
    link = tmp_path / "link.blend"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        example.prepare(tmp_path / "run", link, hashlib.sha256(b"source").hexdigest())
    assert not (tmp_path / "run").exists()


def test_input_limit_precedes_output_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(example, "MAX_SOURCE_BYTES", 4)
    source = tmp_path / "source.blend"
    source.write_bytes(b"12345")
    with pytest.raises(ValueError, match="bounded"):
        example.prepare(tmp_path / "run", source, hashlib.sha256(b"12345").hexdigest())
    assert not (tmp_path / "run").exists()


def test_snapshot_copies_exact_reviewed_bytes(tmp_path):
    source = tmp_path / "source.blend"
    source.write_bytes(b"reviewed scene")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    workspace = example.prepare(tmp_path / "run", source, sha)
    assert (workspace / "source/inspection-pilot.blend").read_bytes() == source.read_bytes()
    assert all((workspace / "scripts" / p).read_bytes() == PATH.with_name(p).read_bytes()
               for p in example.SCRIPTS)


@pytest.mark.parametrize("change", ["count", "fields", "routes"])
def test_readback_cannot_hide_changed_existing_geometry_or_broken_routes(change):
    baseline = {"original_mesh_count": 1820, "original_fields_sha256": "original"}
    fresh = {**baseline, "passed": True}
    if change == "count":
        fresh["original_mesh_count"] -= 1
    elif change == "fields":
        fresh["original_fields_sha256"] = "changed"
    else:
        fresh["passed"] = False
    with pytest.raises(ValueError):
        example.compare_readbacks(baseline, fresh)


@pytest.mark.parametrize("gap,errors,expected", [(0.1, [], True), (0.0, [], False), (0.1, ["unrelated failure"], False)])
def test_negative_check_requires_the_measured_injected_defect(gap, errors, expected):
    fresh = {"original_mesh_count": 1820, "original_fields_sha256": "same"}
    failed = {**fresh, "passed": False, "errors": errors,
              "measurements": {"routes": {"Camera_L": {"attachments": {"end": {
                  "checks": {"endpoint_to_gland_centre_within_1mm": False},
                  "endpoint_to_gland_centre_delta_m": gap}}}}}}
    if expected:
        assert example.check_disconnected_readback(fresh, failed)["measured_gap_m"] == gap
    else:
        with pytest.raises(ValueError, match="injected endpoint"):
            example.check_disconnected_readback(fresh, failed)
