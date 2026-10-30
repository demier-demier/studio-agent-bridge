"""Create a local double-click launcher for a previously built Rust engine.

The default links to the measured run to avoid duplicating multi-GB caches.
Use --copy-run when intentionally creating a self-contained local bundle.
"""
import argparse
import os
import plistlib
import shutil
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--run", type=Path, required=True)
p.add_argument("--geometry", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--copy-run", action="store_true")
a = p.parse_args()
root = Path(__file__).resolve().parent
binary = root / "target/release/scenward-render"
if not binary.is_file() or not a.run.is_dir() or not a.geometry.is_file() or a.output.exists():
    p.error("Build the engine first and supply existing data and a new output path")
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
with (contents / "Info.plist").open("wb") as f:
    plistlib.dump({
        "CFBundleName": "Scenward Render", "CFBundleDisplayName": "Scenward Render",
        "CFBundleIdentifier": "ai.scenward.render", "CFBundleExecutable": "scenward-render",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0",
        "NSDocumentsFolderUsageDescription": "Read your selected local simulation cache for rendering.",
    }, f)
print(a.output.resolve())
