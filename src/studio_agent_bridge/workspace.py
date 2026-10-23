"""Local workspace boundaries and cooperative native-host process locks.

These checks protect declared paths. Trusted host Python remains arbitrary code;
this module is deliberately not described as an operating-system sandbox.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class WorkspaceError(ValueError):
    pass


class HostBusy(RuntimeError):
    pass


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sync_directory(path: Path) -> None:
    """Persist a directory entry update; callers already hold the cooperative host lock."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: dict) -> None:
    """Publish a complete intent under the host lock, never truncate an active marker.

    A failed unpublished temporary file is deliberately preserved for diagnosis.
    No native dispatch is permitted until this function has returned successfully.
    The lock coordinates bridge processes; it does not constrain unrelated same-user code.
    """
    payload = canonical(value).encode("utf-8")
    if len(payload) > 16_384:
        raise WorkspaceError("Host intent exceeds its bounded metadata limit.")
    if path.exists() or path.is_symlink():
        raise WorkspaceError("An existing host intent must not be overwritten.")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    # The exclusive host lock makes this absence check and rename cooperative-atomic.
    if path.exists() or path.is_symlink():
        raise WorkspaceError("A host intent appeared before publication; preserved temporary file.")
    os.rename(temporary, path)
    sync_directory(path.parent)


def read_bounded_json(path: Path) -> dict:
    """Read a regular, non-symlink intent without trusting malformed ownership fields."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16_384:
            raise WorkspaceError("Host intent must be a bounded regular file.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(16_385)
        if len(payload) > 16_384:
            raise WorkspaceError("Host intent exceeds its bounded metadata limit.")
    finally:
        os.close(descriptor)

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WorkspaceError("Duplicate intent fields are not accepted.")
            result[key] = value
        return result

    value = json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=unique_fields)
    if not isinstance(value, dict):
        raise WorkspaceError("Host intent must be an object.")
    return value


class Workspace:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = self.resolve(".studio", allow_internal=True)
        self.state.mkdir(mode=0o700, exist_ok=True)
        self.resolve("jobs").mkdir(exist_ok=True)

    def resolve(self, value: str, *, allow_internal: bool = False) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise WorkspaceError("Path must be a nonempty workspace-relative POSIX path.")
        path = Path(value)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            raise WorkspaceError("Absolute paths and parent traversal are not allowed.")
        if not allow_internal and path.parts and path.parts[0] == ".studio":
            raise WorkspaceError("The state directory is reserved.")
        target = self.root / path
        # Reject every existing symlink, including a link that currently points inside.
        current = self.root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise WorkspaceError("Symlinks are not accepted in declared workspace paths.")
        resolved = target.resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError("Path escapes the workspace.")
        return target

    def relative(self, path: Path) -> str:
        value = path.relative_to(self.root).as_posix()
        self.resolve(value)
        return value

    def run_path(self, job_id: str, revision: int, action: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,100}", job_id):
            raise WorkspaceError("Invalid job identifier.")
        if not re.fullmatch(r"[a-z_]{1,80}", action):
            raise WorkspaceError("Invalid action identifier.")
        if type(revision) is not int or not 1 <= revision <= 1_000_000_000:
            raise WorkspaceError("Invalid run revision.")
        return self.resolve(f"jobs/{job_id}/runs/{revision:06d}-{action}")

    def run_dir(self, job_id: str, revision: int, action: str) -> Path:
        target = self.run_path(job_id, revision, action)
        target.mkdir(parents=True, exist_ok=False)
        # Preserve the run directory entry before an adapter can write native evidence.
        sync_directory(target.parent)
        sync_directory(target.parent.parent)
        sync_directory(target.parent.parent.parent)
        return target


@contextmanager
def host_lock(host: str, lock_dir: Path | None = None) -> Iterator[None]:
    """Exclusive across this user's bridge processes, including different workspaces.

    The lock cannot stop manual application actions or unrelated automation.
    It is released by the OS if the orchestrator dies; that is not evidence that
    an external editor/subprocess has stopped. Interrupted jobs require review.
    """
    if host not in {"fusion", "blender", "unreal"}:
        raise ValueError("Unknown host.")
    directory = lock_dir or Path.home() / ".cache" / "studio-agent-bridge" / "locks"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise WorkspaceError("Lock directory must be owned by this user and not a symlink.")
    path = directory / f"{host}.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise WorkspaceError("Lock must be a regular file.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HostBusy(f"{host} is already in use by another bridge process.") from exc
        yield
    finally:
        os.close(descriptor)
