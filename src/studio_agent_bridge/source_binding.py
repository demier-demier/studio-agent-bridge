"""Bounded, descriptor-based source pinning for trusted local native operations.

The copied .blend bytes are the binding. File permissions discourage accidental
writes; they are not OS immutability or a sandbox against privileged host scripts.
External resources are deliberately not copied or claimed to be pinned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

MAX_SOURCE_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
PIN_LIMITATIONS = [
    "Only the .blend file bytes are pinned; external relative resources are not copied. Pinned loads require packed or self-contained assets.",
    "Read-only permissions are an accidental-write guard, not OS immutability. Trusted scripts can change permissions or other files.",
    "Before/after digest checks are observations, not continuous attestation against a same-user process that changes and restores bytes.",
]


class SourceBindingError(ValueError):
    def __init__(self, message: str, evidence: dict | None = None):
        super().__init__(message)
        self.evidence = evidence


def expected_digest(action: str, parameters: dict) -> str | None:
    """Validate presence as well as value; null never means an optional pin."""
    supplied = "expected_source_sha256" in parameters
    value = parameters.get("expected_source_sha256")
    if supplied and (not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)):
        raise SourceBindingError(
            "expected_source_sha256 must contain exactly 64 lowercase hexadecimal characters."
        )
    if supplied and "input_blend" not in parameters:
        raise SourceBindingError("expected_source_sha256 requires input_blend.")
    if action == "run_script" and "input_blend" in parameters and not supplied:
        raise SourceBindingError("Editing an existing input_blend requires expected_source_sha256.")
    return value if supplied else None


def relative_input(workspace: Path, value: object, suffixes: set[str]) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2000
        or "\x00" in value
        or "\\" in value
    ):
        raise SourceBindingError("Input must be a bounded workspace-relative POSIX path.")
    relative = Path(value)
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[0] == ".studio"
    ):
        raise SourceBindingError(
            "Input traversal, absolute paths, and internal state paths are not accepted."
        )
    path = workspace
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise SourceBindingError("Input paths must not contain symlinks.")
    if path.suffix.lower() not in suffixes:
        raise SourceBindingError("Input file has an unsupported extension.")
    if not path.is_file():
        raise SourceBindingError("Input file is missing or is not regular.")
    return path


@contextmanager
def _open_source(workspace: Path, relative: str):
    """Open each directory component without following links; hold the final inode."""
    path = relative_input(workspace, relative, {".blend"})
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    source = None
    try:
        parts = path.relative_to(workspace).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        metadata = os.fstat(source)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_SOURCE_BYTES:
            raise SourceBindingError(
                "Source .blend must be a nonempty regular file no larger than 512 MiB."
            )
        with os.fdopen(source, "rb", closefd=False) as stream:
            yield stream, metadata
    finally:
        if source is not None:
            os.close(source)
        os.close(descriptor)


def _signature(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _chunks(stream):
    total = 0
    while chunk := stream.read(CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_SOURCE_BYTES:
            raise SourceBindingError("Source .blend exceeded the 512 MiB bound while reading.")
        yield chunk


def _digest_stream(stream) -> tuple[str, int]:
    digest, count = hashlib.sha256(), 0
    for chunk in _chunks(stream):
        digest.update(chunk)
        count += len(chunk)
    return digest.hexdigest(), count


def observe_source(workspace: Path, relative: str) -> tuple[str, int]:
    with _open_source(workspace, relative) as (stream, before):
        digest, count = _digest_stream(stream)
        after = os.fstat(stream.fileno())
        current = relative_input(workspace, relative, {".blend"}).stat(follow_symlinks=False)
        if (
            _signature(before) != _signature(after)
            or _signature(after) != _signature(current)
            or count != before.st_size
        ):
            raise SourceBindingError("Source changed while its bytes were being observed.")
        return digest, count


def _sync_dir(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_evidence(run_dir: Path, name: str, value: dict) -> Path:
    """Each evidence record is created once; earlier evidence is never overwritten."""
    path = run_dir / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(run_dir)
    return path


def prepare_source(workspace: Path, source: Path, run_dir: Path, expected: str | None) -> dict:
    relative = source.relative_to(workspace).as_posix()
    binding = {
        "schema_version": 1,
        "mode": "pinned_snapshot" if expected else "unpinned",
        "phase": "preparing",
        "source_path": relative,
        "snapshot_path": None,
        "expected_source_sha256": expected,
        "source_sha256": None,
        "snapshot_sha256": None,
        "bytes": None,
        "snapshot_sha256_after_action": None,
        "source_sha256_after_action": None,
        "snapshot_unchanged": None,
        "source_unchanged": None,
        "resources_scope": "blend_bytes_only" if expected else "original_direct_load_compatibility",
        "limitations": PIN_LIMITATIONS
        if expected
        else [
            "No expected source digest was supplied. Blender loads the original path directly; this compatibility mode does not bind a caller-approved source revision or snapshot.",
        ],
    }
    try:
        if expected is None:
            binding["source_sha256"], binding["bytes"] = observe_source(workspace, relative)
        else:
            snapshot_dir = run_dir / "source_snapshot"
            snapshot_dir.mkdir(mode=0o700, exist_ok=False)
            snapshot = snapshot_dir / source.name
            binding["snapshot_path"] = snapshot.relative_to(workspace).as_posix()
            with _open_source(workspace, relative) as (stream, before):
                descriptor = os.open(
                    snapshot, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(descriptor, "w+b") as output:
                    copied_hash, count = hashlib.sha256(), 0
                    for chunk in _chunks(stream):
                        output.write(chunk)
                        copied_hash.update(chunk)
                        count += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                    os.fchmod(output.fileno(), 0o444)
                    os.fsync(output.fileno())
                    output.seek(0)
                    saved_hash, saved_bytes = _digest_stream(output)
                _sync_dir(snapshot_dir)
                _sync_dir(run_dir)
                binding.update(
                    source_sha256=copied_hash.hexdigest(),
                    snapshot_sha256=saved_hash,
                    bytes=saved_bytes,
                )
                # A second source read and inode/metadata checks reject changes during copying.
                after_copy = os.fstat(stream.fileno())
                stream.seek(0)
                source_hash, source_bytes = _digest_stream(stream)
                after = os.fstat(stream.fileno())
                current = relative_input(workspace, relative, {".blend"}).stat(
                    follow_symlinks=False
                )
                if (
                    _signature(before) != _signature(after_copy)
                    or _signature(before) != _signature(after)
                    or _signature(after) != _signature(current)
                    or count != before.st_size
                    or saved_bytes != count
                    or source_bytes != count
                    or copied_hash.hexdigest() != saved_hash
                    or source_hash != saved_hash
                ):
                    raise SourceBindingError(
                        "Source changed while copying; native dispatch was rejected."
                    )
                if saved_hash != expected:
                    raise SourceBindingError(
                        "Expected source SHA-256 does not match copied .blend bytes; native dispatch was rejected."
                    )
        binding["phase"] = "prepared"
        write_evidence(run_dir, "source-binding.json", binding)
        return binding
    except (OSError, ValueError) as error:
        binding["phase"] = "rejected_before_native"
        binding["error"] = (
            str(error)
            if isinstance(error, SourceBindingError)
            else "Source snapshot preparation failed; partial owned files were preserved."
        )
        try:
            write_evidence(run_dir, "source-binding-failure.json", binding)
        except OSError:
            pass
        raise SourceBindingError(binding["error"], binding) from error


def check_source(workspace: Path, binding: dict, *, before_dispatch: bool = False) -> dict:
    observed = dict(binding)
    observed["phase"] = "checked_before_dispatch" if before_dispatch else "checked_after_action"
    for key in ("source", "snapshot"):
        if binding[f"{key}_path"] is None:
            continue
        try:
            digest, size = observe_source(workspace, binding[f"{key}_path"])
            unchanged = digest == binding[f"{key}_sha256"] and size == binding["bytes"]
        except (OSError, ValueError):
            digest, unchanged = None, False
        suffix = "before_dispatch" if before_dispatch else "after_action"
        observed[f"{key}_sha256_{suffix}"] = digest
        observed[f"{key}_unchanged"] = unchanged
    if before_dispatch and binding["mode"] == "pinned_snapshot":
        if observed["source_unchanged"] is not True:
            raise SourceBindingError("Original source changed before native dispatch.", observed)
        if observed["snapshot_unchanged"] is not True:
            raise SourceBindingError(
                "Preserved source snapshot changed before native dispatch.", observed
            )
    return observed
