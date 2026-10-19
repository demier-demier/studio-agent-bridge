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

    def run_dir(self, job_id: str, revision: int, action: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,100}", job_id):
            raise WorkspaceError("Invalid job identifier.")
        if not re.fullmatch(r"[a-z_]{1,80}", action):
            raise WorkspaceError("Invalid action identifier.")
        target = self.resolve(f"jobs/{job_id}/runs/{revision:06d}-{action}")
        target.mkdir(parents=True, exist_ok=False)
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
