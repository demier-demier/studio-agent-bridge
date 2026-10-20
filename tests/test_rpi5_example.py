"""Fixture preconditions are testable without a proprietary native application."""

import importlib.util
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).parents[1] / "examples/fusion/rpi5-open-tray/build_tray.py"
spec = importlib.util.spec_from_file_location("rpi5_tray", EXAMPLE)
tray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tray)


@pytest.mark.parametrize(
    "overrides",
    [
        {"edge_margin": 0},
        {"edge_margin": 26},
        {"board_clearance": 4},
        {"plate_thickness": 1},
        {"standoff_outer": 8},
        {"chassis_bore": 9},
        {"relief_inset": 28},
        {"edge_margin": float("nan")},
        {"edge_margin": float("inf")},
        {"hole_pitch_x": 57},
    ],
)
def test_reject_unsupported_or_changed_source_dimensions(overrides):
    with pytest.raises(ValueError):
        tray.expected_geometry(overrides)


def test_enlarging_fixture_preserves_real_board_pattern():
    original = tray.expected_geometry({})
    edited = tray.expected_geometry({"edge_margin": 12, "board_clearance": 11})
    assert original["size_mm"] == [101, 72, 11]
    assert edited["size_mm"] == [109, 80, 14]
    for old, new in zip(original["mount_centres_mm"], edited["mount_centres_mm"]):
        assert new == [old[0] + 4, old[1] + 4]
    # Source-drawing pitch is independent of the fixture envelope.
    assert edited["mount_centres_mm"][2][0] - edited["mount_centres_mm"][0][0] == 58
    assert edited["mount_centres_mm"][1][1] - edited["mount_centres_mm"][0][1] == 49
