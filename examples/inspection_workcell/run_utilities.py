"""Continue a reviewed inspection pilot without rebuilding or overwriting it.

Use a separately reviewed source SHA-256 and a new output directory. Executes
trusted Blender Python through the real bridge; scripts are not sandboxed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))
from studio_agent_bridge.engine import Engine  # noqa: E402

SCRIPTS = ("complete_utilities.py", "verify_utilities.py", "disconnect_route.py")
OWNER = "inspection-utilities-run/v1"
MAX_SOURCE_BYTES = 32 * 1024 * 1024


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root: Path, source: Path, expected_sha256: str):
    """Verify the bytes we will use before creating any run state."""
    if root.exists() or root.is_symlink():
        raise ValueError("Use a new output directory; prior evidence is never replaced")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected source SHA-256 must be a reviewed lowercase digest")
    if source.is_symlink() or not source.is_file():
        raise ValueError("Source must be a regular native file, not a symlink")
    with source.open("rb") as stream:
        payload = stream.read(MAX_SOURCE_BYTES + 1)
    if len(payload) > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds the bounded native scene size")
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("Source does not match its reviewed SHA-256")
    scripts = {name: Path(__file__).with_name(name).read_bytes() for name in SCRIPTS}
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    (workspace / "source").mkdir(parents=True)
    (workspace / "scripts").mkdir()
    (workspace / "source/inspection-pilot.blend").write_bytes(payload)
    for name, content in scripts.items():
        (workspace / "scripts" / name).write_bytes(content)
    return workspace


def output_native(result):
    return next(
        a for a in result["result"]["artifacts"]
        if a["kind"] == "blend" and "/source_snapshot/" not in a["path"]
    )


def compare_readbacks(baseline, fresh):
    if not fresh["passed"]:
        raise ValueError("Saved utility geometry did not pass independent readback")
    for field in ("original_mesh_count", "original_fields_sha256"):
        if baseline[field] != fresh[field]:
            raise ValueError("Existing measured mesh fields changed")
    return {
        "existing_measured_mesh_fields_equal": True,
        "utility_readback_passed": True,
        "limits": "Explicit mesh fields and utility geometry only; not complete scene equivalence, collision clearance or electrical/robot safety verification.",
    }


def check_disconnected_readback(fresh, failure):
    """Require the intended measured defect, not merely any failed native action."""
    endpoint = failure["measurements"]["routes"]["Camera_L"]["attachments"]["end"]
    if (failure["passed"] is not False
            or endpoint["checks"]["endpoint_to_gland_centre_within_1mm"] is not False
            or not 0.099 <= endpoint["endpoint_to_gland_centre_delta_m"] <= 0.101
            or failure["errors"]
            or any(fresh[key] != failure[key]
                   for key in ("original_mesh_count", "original_fields_sha256"))):
        raise ValueError("Negative readback did not isolate the injected endpoint defect")
    return {"rejected_endpoint": "Camera_L.end", "measured_gap_m": endpoint["endpoint_to_gland_centre_delta_m"]}


def run(root, source, expected_sha256, *, render=True, device="CPU"):
    workspace = prepare(root, source, expected_sha256)
    engine = Engine(workspace, {"allow_scripts": True, "blender_timeout_seconds": 900})
    job = engine.create_job(
        "inspection-utilities", "blender",
        "Add source-bound utility routes, independently reopen and render close/wide views",
    )
    state = {
        "owner": OWNER, "job_id": job["id"], "source_sha256": expected_sha256,
        "script_hashes": {name: digest(workspace / "scripts" / name) for name in SCRIPTS},
        "actions": {}, "status": "prepared",
    }

    def save():
        temporary = root / "state.json.tmp"
        temporary.write_text(json.dumps(state, indent=2, allow_nan=False) + "\n")
        temporary.replace(root / "state.json")

    def action(label, script, native_path, sha, parameters, expected_status="verified"):
        if label in state["actions"]:
            raise ValueError("Refusing to replay an existing action label")
        state["status"] = "running-" + label
        save()
        result = engine.execute_action(job["id"], "run_script", {
            "script_path": "scripts/" + script, "input_blend": native_path,
            "expected_source_sha256": sha, "script_parameters": parameters,
            "render": False,
        }, engine.inspect_job(job["id"])["revision"])
        state["actions"][label] = result
        save()
        print(label, result["status"], flush=True)
        if result["status"] != expected_status:
            state["status"] = "failed-" + label
            save()
            raise RuntimeError("Native action failed; inspect retained evidence before a new attempt")
        return result

    def readback(result):
        return json.loads((workspace / result["run_dir"] / "utility-readback.json").read_text())

    try:
        save()
        baseline = action("baseline", "verify_utilities.py", "source/inspection-pilot.blend",
                          expected_sha256, {"mode": "baseline"})
        built = action("build", "complete_utilities.py", "source/inspection-pilot.blend",
                       expected_sha256, {"mode": "build", "render": False})
        native = output_native(built)
        fresh = action("fresh-readback", "verify_utilities.py", native["path"],
                       native["sha256"], {"mode": "inspect"})
        state["readbacks"] = {"baseline": readback(baseline), "fresh": readback(fresh)}
        state["comparison"] = compare_readbacks(**state["readbacks"])
        state["native"] = native
        disconnected = action("disconnected-copy", "disconnect_route.py", native["path"],
                              native["sha256"], {})
        broken = output_native(disconnected)
        rejected = action("reject-disconnected-copy", "verify_utilities.py", broken["path"],
                          broken["sha256"], {"mode": "inspect"}, expected_status="failed")
        failure_report = readback(rejected)
        injected_defect = check_disconnected_readback(state["readbacks"]["fresh"], failure_report)
        state["negative_check"] = {
            "kind": "move_one_curve_endpoint_0_1m_in_separate_copy",
            **injected_defect,
            "reported_status": rejected["status"], "readback": failure_report,
            "original_native_unchanged": digest(workspace / native["path"]) == native["sha256"],
        }
        if not state["negative_check"]["original_native_unchanged"]:
            raise ValueError("Negative check changed the accepted native source")
        if render:
            for view in ("close", "wide"):
                action("render-" + view, "complete_utilities.py", native["path"],
                       native["sha256"], {"mode": "render", "view": view, "device": device})
        if digest(source) != expected_sha256 or digest(workspace / "source/inspection-pilot.blend") != expected_sha256:
            raise ValueError("Input source changed during execution")
        if any(digest(workspace / "scripts" / n) != s for n, s in state["script_hashes"].items()):
            raise ValueError("Execution script changed during run")
        state.update(status="native-verified", original_source_unchanged=True,
                     unreal_transfer="not_run", human_visual_acceptance="pending")
        save()
        return state
    except Exception as exc:
        if not state["status"].startswith("failed-"):
            state["status"] = "failed-validation"
        state["error"] = {"type": type(exc).__name__, "message": str(exc)}
        save()
        raise



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--device", choices=("CPU", "METAL"), default="CPU")
    args = parser.parse_args()
    run(args.output.absolute(), args.source.absolute(), args.source_sha256,
        render=not args.skip_render, device=args.device)
