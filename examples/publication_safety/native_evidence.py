"""Run inside Blender through the real bridge adapter; write private observations."""

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).parent))
from fingerprint_scene import compare_records, fingerprint
from sanitize_scene import sanitize

output = Path(globals()["STUDIO_OUTPUT_DIR"])
mode = globals().get("STUDIO_PARAMETERS", {}).get("mode", "read")
if mode not in {"read", "sanitize"}:
    raise ValueError("Unsupported publication evidence mode")
before, records = fingerprint(bpy)
original_records = records
(output / "fingerprint-before-details.json").write_text(
    json.dumps(records, sort_keys=True, allow_nan=False)
)
report = {"mode": mode, "before": before}
if mode == "sanitize":
    report["sanitization"] = sanitize(bpy)
    after, records = fingerprint(bpy)
    (output / "fingerprint-after-details.json").write_text(
        json.dumps(records, sort_keys=True, allow_nan=False)
    )
    report["after"] = after
    report["comparison"] = compare_records(original_records, records)
    report["unchanged_in_memory"] = report["comparison"]["passed"]
    (output / "scene-fingerprint.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    if not report["comparison"]["passed"]:
        raise ValueError("Native sanitization changed a measured scene property")
(output / "scene-fingerprint.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
