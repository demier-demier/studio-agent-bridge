"""Bounded preparation must reject unreviewed input before native execution."""
import importlib.util
import zipfile
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / 'examples/inspection_workcell/run_example.py'
spec = importlib.util.spec_from_file_location('workcell_example', PATH)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def test_changed_native_source_is_rejected_before_workspace_creation(tmp_path):
    archive = tmp_path / 'wrong.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('industrial-lab/scene-day.blend', b'changed source')
    root = tmp_path / 'run'
    with pytest.raises(ValueError, match='reviewed'):
        example.prepare(root, archive)
    assert not root.exists()


def test_previous_outputs_are_not_overwritten(tmp_path):
    root = tmp_path / 'run'
    root.mkdir()
    evidence = root / 'evidence.json'
    evidence.write_text('preserve me')
    with pytest.raises(ValueError, match='empty'):
        example.prepare(root, tmp_path / 'absent.zip')
    assert evidence.read_text() == 'preserve me'


def test_published_source_prepares_exact_bytes(tmp_path):
    archive = PATH.parents[2] / 'web/public/assets/environment/industrial-lab-native-v2.zip'
    workspace = example.prepare(tmp_path / 'new', archive)
    import hashlib
    assert hashlib.sha256((workspace / 'source/scene-day.blend').read_bytes()).hexdigest() == example.SOURCE_SHA256
    assert (workspace / 'scripts/build_station.py').read_bytes() == PATH.with_name('build_station.py').read_bytes()
