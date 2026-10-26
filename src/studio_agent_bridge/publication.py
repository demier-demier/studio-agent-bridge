"""Bounded byte-pattern publication gates, not a universal private-data detector.

Native reopen checks do not inspect stale file buffers. Scan the bytes actually
packaged, including decompressed .blend data and every other ZIP member. Private
fragments come from local configuration and are never echoed in scan reports.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import selectors
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath

CHUNK_BYTES = 65536
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_DECODED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 4096
DECODE_SECONDS = 30
PATH_PATTERNS = (
    ("home-path", re.compile(rb"/(?:users|home|root)/[a-z0-9_.~\x80-\xff-]")),
    (
        "temporary-path",
        re.compile(rb"/(?:private/var|var/folders|tmp|private/tmp)/[a-z0-9_.~\x80-\xff-]"),
    ),
    ("mounted-volume-path", re.compile(rb"/(?:volumes|mnt|media)/[a-z0-9_.~\x80-\xff-]")),
    # A four-byte drive prefix alone occurs by chance in compressed data. Match
    # a bounded printable path with a string terminator (EOF is fed explicitly).
    # This is a pattern policy, not recognition of every legal Windows filename.
    (
        "windows-drive-path",
        re.compile(rb"(?<![a-z0-9_])[a-z]:/[a-z0-9_. ~()+/@#$%&!=,-]{1,256}(?=[\x00\r\n\"'])"),
    ),
    ("file-uri", re.compile(rb"file:///[a-z0-9_.~\x80-\xff-]")),
)
SCAN_SCOPE = (
    "Configured byte patterns only; not a proof that every private field, custom encoding, "
    "encrypted value or nested archive is absent. Raw and NUL-elided case-insensitive "
    "ASCII views are scanned; decompression is supported only for recognized .blend formats."
)


class PublicationError(ValueError):
    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report


def _fragments(values: list[str]) -> list[bytes]:
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise PublicationError("Provide 1-64 private fragments in local publication configuration.")
    result = []
    for value in values:
        if (
            not isinstance(value, str)
            or not 4 <= len(value.encode("utf-8")) <= 128
            or "\x00" in value
        ):
            raise PublicationError(
                "Private fragments must be strings of 4-128 UTF-8 bytes without NUL."
            )
        result.append(value.encode("utf-8").lower().replace(b"\\", b"/"))
    return result


class _Scanner:
    def __init__(self, fragments: list[bytes]):
        self.fragments = fragments
        self.tails = {"raw": b"", "nul-elided": b""}
        self.offsets = {"raw": 0, "nul-elided": 0}
        self.findings = []
        self.seen = set()
        self.overlap = max(512, *(len(fragment) for fragment in fragments))

    def feed(self, data: bytes, *, final: bool = False):
        normalized = data.lower().replace(b"\\", b"/")
        for view, current in (
            ("raw", normalized),
            ("nul-elided", normalized.replace(b"\x00", b"")),
        ):
            prior = self.tails[view]
            joined = prior + current + (b"\x00" if final else b"")
            origin = self.offsets[view] - len(prior)
            matches = [
                (rule, match.start())
                for rule, pattern in PATH_PATTERNS
                for match in pattern.finditer(joined)
            ]
            for index, fragment in enumerate(self.fragments):
                start = joined.find(fragment)
                if start >= 0:
                    matches.append((f"private-fragment-{index + 1}", start))
            for rule, offset in matches:
                key = (rule, view)
                if key not in self.seen:
                    self.seen.add(key)
                    self.findings.append(
                        {"rule": rule, "view": view, "offset_in_view": origin + offset}
                    )
            self.offsets[view] += len(current)
            self.tails[view] = joined[-self.overlap :]


def _input_bytes(value: Path | bytes) -> bytes:
    if isinstance(value, bytes):
        data = value
    else:
        path = Path(value)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MEMBER_BYTES:
            raise PublicationError(
                "Publication input must be a bounded regular file, not a symlink."
            )
        with path.open("rb") as handle:
            data = handle.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise PublicationError("Publication member exceeds the configured byte bound.")
    return data


def _raw_report(data: bytes, fragments: list[bytes]) -> dict:
    scanner = _Scanner(fragments)
    for start in range(0, len(data), CHUNK_BYTES):
        scanner.feed(data[start : start + CHUNK_BYTES])
    scanner.feed(b"", final=True)
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "findings": scanner.findings,
    }


def scan_bytes(value: Path | bytes, *, forbidden_fragments: list[str]) -> dict:
    """Scan arbitrary raw bytes, without claiming nested compression was decoded."""
    report = _raw_report(_input_bytes(value), _fragments(forbidden_fragments))
    return {
        "schema": "publication-byte-scan/v1",
        "passed": not report["findings"],
        "raw": report,
        "scope": SCAN_SCOPE,
    }


def _zstd_chunks(data: bytes):
    executable = shutil.which("zstd")
    if executable is None or os.name != "posix":
        raise PublicationError("Bounded zstd decoding requires the zstd CLI on a POSIX host.")
    with tempfile.TemporaryDirectory(prefix="studio-publication-zstd-") as temporary:
        source = Path(temporary) / "input.zst"
        source.write_bytes(data)
        process = subprocess.Popen(
            [executable, "--decompress", "--stdout", "--no-progress", "-M512MB", str(source)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + DECODE_SECONDS
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PublicationError("zstd decoding exceeded the time bound.")
                    if not selector.select(min(remaining, 0.25)):
                        continue
                    chunk = os.read(process.stdout.fileno(), CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk
            if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                raise PublicationError("zstd stream is corrupt, truncated or unsupported.")
        except subprocess.TimeoutExpired as error:
            raise PublicationError("zstd decoding exceeded the time bound.") from error
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def _decoded_chunks(data: bytes, format_name: str):
    if format_name == "zstd":
        yield from _zstd_chunks(data)
    elif format_name == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
            while chunk := handle.read(CHUNK_BYTES):
                yield chunk
    else:
        for start in range(0, len(data), CHUNK_BYTES):
            yield data[start : start + CHUNK_BYTES]


def scan_blend(value: Path | bytes, *, forbidden_fragments: list[str]) -> dict:
    """Inspect actual decoded .blend bytes; malformed/unknown streams fail closed."""
    data = _input_bytes(value)
    fragments = _fragments(forbidden_fragments)
    if data.startswith(b"BLENDER"):
        format_name = "plain"
    elif data.startswith(b"\x1f\x8b"):
        format_name = "gzip"
    elif data.startswith(b"\x28\xb5\x2f\xfd"):
        format_name = "zstd"
    else:
        raise PublicationError(
            "Unrecognized .blend encoding; publication cannot certify an uninspected member."
        )
    raw = _raw_report(data, fragments)
    scanner, digest, total, header = _Scanner(fragments), hashlib.sha256(), 0, b""
    chunks = _decoded_chunks(data, format_name)
    try:
        for chunk in chunks:
            total += len(chunk)
            if total > MAX_DECODED_BYTES:
                raise PublicationError("Decoded .blend exceeded the configured byte bound.")
            header = (header + chunk)[:17] if len(header) < 17 else header
            digest.update(chunk)
            scanner.feed(chunk)
    except (EOFError, OSError, zlib.error) as error:
        raise PublicationError("Compressed .blend is corrupt or truncated.") from error
    finally:
        chunks.close()
    scanner.feed(b"", final=True)
    # Blender's official _blendfile_header.py defines legacy 12-byte headers
    # and format-01 17-byte headers. Unknown lengths/formats fail closed.
    # https://github.com/blender/blender/blob/main/scripts/modules/_blendfile_header.py
    if re.fullmatch(rb"BLENDER[_-][vV][0-9]{3}", header[:12]):
        header_format = "legacy-12"
    elif re.fullmatch(rb"BLENDER17-01v[0-9]{4}", header):
        header_format = "versioned-17"
    else:
        raise PublicationError("Decoded bytes lack a recognized Blender header.")
    decoded = {"bytes": total, "sha256": digest.hexdigest(), "findings": scanner.findings}
    return {
        "schema": "publication-blend-scan/v1",
        "format": format_name,
        "header_format": header_format,
        "passed": not (raw["findings"] or decoded["findings"]),
        "raw": raw,
        "decoded": decoded,
        "scope": SCAN_SCOPE,
    }


def _member_name(name: str):
    if (
        not isinstance(name, str)
        or len(name.encode("utf-8")) > 512
        or "\\" in name
        or "\x00" in name
        or ":" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise PublicationError("Archive member names must be bounded POSIX paths.")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != name:
        raise PublicationError("Archive member names must be canonical relative paths.")


def validate_payloads(payloads: dict[str, bytes], *, forbidden_fragments: list[str]) -> dict:
    """All members and names are scanned. Only .blend receives format-aware decoding."""
    _fragments(forbidden_fragments)
    forbidden_fragments = list(forbidden_fragments)
    if not isinstance(payloads, dict) or not 1 <= len(payloads) <= MAX_MEMBERS:
        raise PublicationError("Archive has an unsupported number of members.")
    total, reports = 0, {}
    for name, data in sorted(payloads.items()):
        _member_name(name)
        if not isinstance(data, bytes):
            raise PublicationError("Archive payloads must be immutable bytes.")
        total += len(data)
        if total > MAX_ARCHIVE_BYTES:
            raise PublicationError("Archive payload total exceeds the byte bound.")
        name_report = scan_bytes(name.encode(), forbidden_fragments=forbidden_fragments)
        if not name_report["passed"]:
            raise PublicationError("Archive member name failed publication policy.", name_report)
        report = (
            scan_blend(data, forbidden_fragments=forbidden_fragments)
            if name.lower().endswith(".blend")
            else scan_bytes(data, forbidden_fragments=forbidden_fragments)
        )
        if not report["passed"]:
            # The exception never echoes a potentially private member name or matching bytes.
            raise PublicationError("Archive member bytes failed publication policy.", report)
        reports[name] = report
    return {
        "schema": "publication-archive-scan/v1",
        "passed": True,
        "member_count": len(reports),
        "payload_bytes": total,
        "members": reports,
        "scope": SCAN_SCOPE,
    }


def _sync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def publish_native_archive(
    payloads: dict[str, bytes], output: Path, *, version: int, forbidden_fragments: list[str]
) -> dict:
    """Validate, stage and exclusively publish one deterministic versioned ZIP.

    Hard-link publication is atomic and fails if the destination already exists.
    Earlier archives and all caller-owned source bytes are always preserved.
    """
    if not isinstance(payloads, dict):
        raise PublicationError("Archive payloads must be a mapping of immutable bytes.")
    # Retain no mutable caller-owned mapping or policy list across scan and write.
    payloads = dict(payloads)
    _fragments(forbidden_fragments)
    forbidden_fragments = list(forbidden_fragments)
    output = Path(output)
    if type(version) is not int or version < 2 or not output.name.endswith(f"-v{version}.zip"):
        raise PublicationError(
            "Choose an explicit integer version >=2 and a new *-v<VERSION>.zip filename."
        )
    if output.exists() or output.is_symlink():
        raise PublicationError("Publication destination already exists; select a new version.")
    report = validate_payloads(payloads, forbidden_fragments=forbidden_fragments)
    if not output.parent.is_dir():
        raise PublicationError("Publication output parent must already exist.")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".studio-publication-", suffix=".zip", dir=output.parent
    )
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(payloads.items()):
                    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, data)
            handle.flush()
            os.fsync(handle.fileno())
        with zipfile.ZipFile(staged) as archive:
            if (
                archive.comment
                or archive.testzip() is not None
                or archive.namelist() != sorted(payloads)
            ):
                raise PublicationError("Staged archive failed integrity or metadata validation.")
            for info in archive.infolist():
                if info.comment or info.extra or archive.read(info) != payloads[info.filename]:
                    raise PublicationError(
                        "Staged archive bytes or metadata changed during packaging."
                    )
        archive_sha = _file_sha256(staged)
        archive_size = staged.stat().st_size
        os.link(staged, output, follow_symlinks=False)
        _sync_directory(output.parent)
        return {"version": version, "bytes": archive_size, "sha256": archive_sha, "scan": report}
    finally:
        staged.unlink(missing_ok=True)


def scan_archive(path: Path | bytes, *, forbidden_fragments: list[str]) -> dict:
    """Audit conventional contiguous ZIP store/deflate records without extraction.

    Encryption, ZIP64, descriptors and unexplained prefix/trailer/gap bytes are
    unsupported and rejected, rather than silently omitting opaque metadata.
    """
    _fragments(forbidden_fragments)
    forbidden_fragments = list(forbidden_fragments)
    data = _input_bytes(path)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= MAX_MEMBERS or len({info.filename for info in infos}) != len(
            infos
        ):
            raise PublicationError("Archive member count or duplicate names are invalid.")
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise PublicationError("Archive expands beyond the payload byte bound.")
        metadata = archive.comment + b"".join(info.comment + info.extra for info in infos)
        next_offset, central_size = 0, 0
        for info in sorted(infos, key=lambda item: item.header_offset):
            if info.orig_filename != info.filename:
                raise PublicationError("Archive contains an ambiguous original member name.")
            _member_name(info.orig_filename)
            if (
                info.header_offset != next_offset
                or info.flag_bits & ~0x800
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise PublicationError(
                    "Archive uses unsupported flags, compression or unexplained record gaps."
                )
            header = data[next_offset : next_offset + 30]
            if len(header) != 30:
                raise PublicationError("Truncated local archive header.")
            signature, _, flags, method, _, _, crc, packed, expanded, namesize, extrasize = (
                struct.unpack("<4s5H3L2H", header)
            )
            if signature != b"PK\x03\x04" or (flags, method, crc, packed, expanded) != (
                info.flag_bits,
                info.compress_type,
                info.CRC,
                info.compress_size,
                info.file_size,
            ):
                raise PublicationError("Local and central archive metadata disagree.")
            name_start = next_offset + 30
            name_bytes = data[name_start : name_start + namesize]
            encoding = "utf-8" if flags & 0x800 else "cp437"
            if name_bytes.decode(encoding) != info.orig_filename:
                raise PublicationError("Local and central archive names disagree.")
            metadata += name_bytes + data[name_start + namesize : name_start + namesize + extrasize]
            next_offset = name_start + namesize + extrasize + packed
            central_size += (
                46 + len(info.orig_filename.encode(encoding)) + len(info.extra) + len(info.comment)
            )
        end_offset = len(data) - len(archive.comment) - 22
        end = data[end_offset : end_offset + 22]
        if len(end) != 22:
            raise PublicationError("Truncated archive end record.")
        (
            signature,
            disk,
            directory_disk,
            disk_count,
            count,
            directory_size,
            directory_offset,
            comment_size,
        ) = struct.unpack("<4s4H2LH", end)
        if (
            signature != b"PK\x05\x06"
            or disk != 0
            or directory_disk != 0
            or disk_count != len(infos)
            or count != len(infos)
            or directory_offset != next_offset
            or directory_size != central_size
            or directory_offset + directory_size != end_offset
            or comment_size != len(archive.comment)
        ):
            raise PublicationError(
                "Archive has unsupported end records or uninspected trailing data."
            )
        metadata_report = scan_bytes(metadata, forbidden_fragments=forbidden_fragments)
        if not metadata_report["passed"]:
            raise PublicationError("Archive metadata failed publication policy.", metadata_report)
        payloads = {}
        for info in infos:
            if (
                info.file_size > MAX_MEMBER_BYTES
                or info.is_dir()
                or ((info.external_attr >> 16) & 0o170000) == 0o120000
            ):
                raise PublicationError("Archive member type or size is unsupported.")
            with archive.open(info) as handle:
                payloads[info.filename] = handle.read(MAX_MEMBER_BYTES + 1)
        return validate_payloads(payloads, forbidden_fragments=forbidden_fragments)


def publish_directory(staged: Path, output: Path, *, expected_files: dict[str, dict]) -> None:
    """Publish an already validated collection with an exclusive atomic rename.

    The caller supplies hashes of the exact scanned bytes. The private owned copy
    must match before rename; a racing destination is never replaced. This is not
    protection against a privileged same-user process altering hidden staging.
    Supported on macOS and Linux.
    """
    import ctypes
    import sys

    output = Path(output)
    expected = {}
    for name, record in dict(expected_files).items():
        _member_name(name)
        digest, size = record.get("sha256"), record.get("bytes")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or type(size) is not int
            or not 0 <= size <= MAX_ARCHIVE_BYTES
        ):
            raise PublicationError(
                "Directory publication requires validated file hashes and byte counts."
            )
        expected[name] = (digest, size)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise PublicationError("Choose a new publication directory with an existing parent.")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]

        def invoke(source, destination):
            return rename(source, destination, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]

        def invoke(source, destination):
            return rename(-100, source, -100, destination, 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise PublicationError("Atomic exclusive directory publication requires macOS or Linux.")
    rename.restype = ctypes.c_int
    if Path(staged).is_symlink() or any(path.is_symlink() for path in Path(staged).rglob("*")):
        raise PublicationError("Publication staging must not contain symlinks.")
    temporary = Path(tempfile.mkdtemp(prefix=".studio-publish-", dir=output.parent))
    try:
        shutil.copytree(staged, temporary, dirs_exist_ok=True)
        actual = {}
        for path in temporary.rglob("*"):
            if path.is_symlink():
                raise PublicationError("Publication staging must not contain symlinks.")
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
                actual[path.relative_to(temporary).as_posix()] = (
                    _file_sha256(path),
                    path.stat().st_size,
                )
        if actual != expected:
            raise PublicationError("Copied publication bytes differ from the scanned manifest.")
        for directory in sorted(
            (p for p in temporary.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            _sync_directory(directory)
        _sync_directory(temporary)
        if invoke(os.fsencode(temporary), os.fsencode(output)) != 0:
            raise PublicationError(
                "Exclusive publication rename failed; existing output was preserved."
            )
        _sync_directory(output.parent)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
