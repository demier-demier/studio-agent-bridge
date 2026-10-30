"""Create a local double-click launcher for a previously built Rust engine.

The default links to the measured run to avoid duplicating multi-GB caches.
Use --copy-run when intentionally creating a self-contained local bundle.
"""

import argparse
import os
import plistlib
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--run", type=Path, required=True)
p.add_argument("--geometry", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--copy-run", action="store_true")
p.add_argument("--live-model", type=Path)
p.add_argument("--environment", type=Path)
p.add_argument("--mujoco-sdk", type=Path)
a = p.parse_args()
root = Path(__file__).resolve().parent
binary = root / "target/release/scenward-render"
if not binary.is_file() or not a.run.is_dir() or not a.geometry.is_file() or a.output.exists():
    p.error("Build the engine first and supply existing data and a new output path")
if a.live_model:
    if not a.environment or not a.mujoco_sdk:
        p.error("Live packaging requires --environment and --mujoco-sdk")
    meshdir = Path(ET.parse(a.live_model).getroot().find("compiler").get("meshdir", ""))
    if not str(meshdir) or meshdir.is_absolute() or ".." in meshdir.parts:
        p.error("Live model assets must use a portable relative meshdir")
contents = a.output / "Contents"
macos = contents / "MacOS"
resources = contents / "Resources"
macos.mkdir(parents=True)
resources.mkdir()
shutil.copy2(binary, macos / "scenward-render")
shutil.copy2(a.geometry, resources / "geometry.json")
if a.copy_run:
    shutil.copytree(a.run, resources / "run")
else:
    os.symlink(a.run.resolve(), resources / "run", target_is_directory=True)
if a.live_model:
    shutil.copy2(a.live_model, resources / "live-model.xml")
    shutil.copy2(a.environment, resources / "environment.json")
    shutil.copytree(a.live_model.parent / meshdir, resources / meshdir)
    framework = contents / "Frameworks/mujoco.framework/Versions/A"
    framework.mkdir(parents=True)
    shutil.copy2(a.mujoco_sdk / "libmujoco.3.3.7.dylib", framework / "libmujoco.3.3.7.dylib")
    licenses = a.mujoco_sdk.parent / "mujoco-3.3.7.dist-info/licenses"
    shutil.copytree(licenses, resources / "MuJoCo-licenses")
    shutil.copy2(
        root.parents[1] / "examples/solo12_granular/robot/LICENSE-BSD-3-Clause.txt",
        resources / "Solo12-LICENSE.txt",
    )
with (contents / "Info.plist").open("wb") as f:
    plistlib.dump(
        {
            "CFBundleName": "Scenward Live" if a.live_model else "Scenward Render",
            "CFBundleDisplayName": "Scenward Live" if a.live_model else "Scenward Render",
            "CFBundleIdentifier": "ai.scenward.live" if a.live_model else "ai.scenward.render",
            "CFBundleExecutable": "scenward-render",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSDocumentsFolderUsageDescription": "Read your selected local simulation cache for rendering.",
        },
        f,
    )
print(a.output.resolve())
