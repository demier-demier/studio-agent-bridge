"""Download the CC0 music used in the published edit with a pinned checksum.

The ten-megabyte original track stays outside the source repository. No accounts,
keys or paid services are used. Existing output files are never overwritten.
"""

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def download(output):
    source = json.loads((ROOT / "sources.json").read_text())["sources"][1]
    expected = source["sha256"]["DST-MindStream.mp3"]
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a new output path; existing music is preserved")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="music-", suffix=".tmp", dir=output.parent)
    try:
        request = urllib.request.Request(
            source["download_url"], headers={"User-Agent": "Scenward-Solo12-Reproduction/1.0"}
        )
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as target, urllib.request.urlopen(request, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise ValueError("Music response exceeds the expected size bound")
                target.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("Music checksum mismatch; downloaded bytes were not accepted")
        # Exclusive creation preserves any file created while the request was running.
        with output.open("xb") as target, Path(temp_name).open("rb") as source_file:
            while chunk := source_file.read(1024 * 1024):
                target.write(chunk)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    print(
        json.dumps(
            {"file": output.name, "sha256": expected, "bytes": size, "license": "CC0 1.0"}, indent=2
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    download(parser.parse_args().output)
