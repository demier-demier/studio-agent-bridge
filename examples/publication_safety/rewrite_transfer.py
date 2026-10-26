"""Run the official FBX codec with its bundled Python; preserve original members."""

import argparse
import hashlib
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from sanitize_fbx import sanitize_fbx


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--fbx-modules", type=Path, required=True)
    args = parser.parse_args()
    root = args.work_root.resolve()
    state = json.loads((root / "state.json").read_text())
    if (
        state.get("owner") != "studio-publication-safety-example/v1"
        or state["stage"] != "native-complete"
    ):
        raise ValueError("Complete native scenes in the owned workspace first")
    source = root / "workspace/source-transfer"
    clean = root / "workspace/clean-transfer"
    if source.exists() or clean.exists():
        raise FileExistsError("Transfer inputs and outputs require fresh directories")
    source.mkdir()
    clean.mkdir()
    with zipfile.ZipFile(root / "original.zip") as archive:
        manifest = json.loads(archive.read("industrial-lab/manifest.json"))
        for member in archive.namelist():
            if not member.startswith("industrial-lab/transfer/") or member.endswith("/"):
                continue
            relative = PurePosixPath(member.removeprefix("industrial-lab/transfer/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe transfer name")
            data = archive.read(member)
            if {"sha256": digest(data), "bytes": len(data)} != manifest[
                "transfer/" + relative.as_posix()
            ]:
                raise ValueError("Original transfer member failed archive manifest")
            for folder in [source] if relative.suffix.lower() == ".fbx" else [source, clean]:
                path = folder / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    manifest_path = clean / "environment-manifest.json"
    original_manifest = json.loads(manifest_path.read_text())
    changed = json.loads(manifest_path.read_text())
    reports = []
    rules = Counter()
    for asset in changed["assets"]:
        relative = asset["exportFile"]
        before = source / relative
        after = clean / relative
        after.parent.mkdir(parents=True, exist_ok=True)
        if (
            digest(before.read_bytes()) != asset["fileSha256"]
            or before.stat().st_size != asset["exportBytes"]
        ):
            raise ValueError("FBX source failed transfer manifest")
        report = sanitize_fbx(
            before,
            after,
            bundle_root=clean,
            manifest_path=manifest_path,
            module_directory=args.fbx_modules,
            forbidden_fragments=[Path.home().name],
        )
        report["member"] = "transfer/" + relative
        reports.append(report)
        rules.update(report["changes_by_rule"])
        asset["fileSha256"] = report["output_sha256"]
        asset["exportBytes"] = report["output_bytes"]
    # Exactly these two export-byte fields may differ in the transfer manifest.
    normalized = json.loads(json.dumps(changed))
    for before, after in zip(original_manifest["assets"], normalized["assets"]):
        after["fileSha256"] = before["fileSha256"]
        after["exportBytes"] = before["exportBytes"]
    if normalized != original_manifest:
        raise ValueError("Unexpected semantic manifest change")
    manifest_path.write_text(json.dumps(changed, indent=2) + "\n")
    summary = {
        "files": len(reports),
        "changed_fields": sum(item["changed_fields"] for item in reports),
        "changes_by_rule": dict(rules),
        "typed_comparison_passed": all(
            item["typed_tree_matches_exact_rewrite"] for item in reports
        ),
        "arrays_preserved": sum(item["arrays_preserved"] for item in reports),
        "source_manifest_sha256": digest((source / "environment-manifest.json").read_bytes()),
        "publication_manifest_sha256": digest(manifest_path.read_bytes()),
        "manifest_changes_only_file_hashes_and_byte_counts": True,
        "codec_modules_sha256": reports[0]["codec_modules_sha256"],
        "helper_sha256": digest(Path(__file__).with_name("sanitize_fbx.py").read_bytes()),
        "python_version": sys.version.split()[0],
        "records": reports,
    }
    (root / "fbx-proof.json").write_text(json.dumps(summary, indent=2) + "\n")
    state["fbx"] = summary
    state["stage"] = "transfer-complete"
    (root / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps({"files": len(reports), "changed_fields": summary["changed_fields"]}))


if __name__ == "__main__":
    main()
