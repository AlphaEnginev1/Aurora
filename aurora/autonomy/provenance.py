"""Package integrity and provenance verification primitives."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProvenanceError(RuntimeError):
    """Raised when an acquired artifact cannot be verified."""


@dataclass(frozen=True)
class PackageProvenance:
    name: str
    version: str | None
    source: str
    artifact_sha256: str | None = None
    install_root: str | None = None
    record_verified: bool = False
    provenance_url: str | None = None
    metadata: dict[str, Any] | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_expected_sha256(path: str | Path, expected: str | None) -> str:
    actual = sha256_file(path)
    if expected and actual.lower() != expected.lower():
        raise ProvenanceError(
            f"sha256 mismatch for {path}: expected {expected}, got {actual}"
        )
    return actual


def _record_hash(path: Path, algorithm: str, expected: str) -> bool:
    if algorithm != "sha256":
        raise ProvenanceError(f"unsupported RECORD hash algorithm: {algorithm}")
    actual = sha256_file(path)
    encoded = base64.urlsafe_b64encode(bytes.fromhex(actual)).decode().rstrip("=")
    return encoded == expected


def verify_record(install_root: str | Path, record_path: str | Path) -> int:
    """Verify hashes in an installed distribution RECORD file."""
    root = Path(install_root).resolve()
    record = Path(record_path).resolve()
    if not record.is_file():
        raise ProvenanceError(f"missing RECORD file: {record}")
    try:
        record.relative_to(root)
    except ValueError as exc:
        raise ProvenanceError(f"RECORD is outside managed install root: {record}") from exc

    # RECORD paths are relative to the directory containing the .dist-info
    # directory, not necessarily the top-level installation root. Absolute
    # paths are accepted only when they remain inside Aurora's managed root.
    base = record.parent.parent
    checked = 0
    with record.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if len(row) != 3:
                raise ProvenanceError(f"invalid RECORD row in {record}: {row!r}")
            path_text, digest, _size = row
            if not digest:
                continue
            hash_text = digest
            try:
                target = (
                    Path(path_text).resolve()
                    if Path(path_text).is_absolute()
                    else (base / path_text).resolve()
                )
                target.relative_to(root)
            except (OSError, ValueError) as exc:
                raise ProvenanceError(
                    f"RECORD path escapes managed install root: {path_text}"
                ) from exc
            try:
                algorithm, expected = hash_text.split("=", 1)
            except ValueError as exc:
                raise ProvenanceError(
                    f"invalid RECORD digest: {hash_text}"
                ) from exc
            if not target.is_file() or not _record_hash(target, algorithm, expected):
                raise ProvenanceError(f"RECORD verification failed: {path_text}")
            checked += 1
    return checked


def load_pip_report(path: str | Path) -> PackageProvenance:
    """Extract immutable download metadata from pip's installation report."""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    installs = report.get("install")
    if not isinstance(installs, list) or len(installs) != 1:
        raise ProvenanceError("pip report must contain exactly one install entry")
    item = installs[0]
    metadata = item.get("metadata") or {}
    name = metadata.get("name")
    version = metadata.get("version")
    download_info = item.get("download_info") or {}
    archive_info = download_info.get("archive_info") or {}
    hashes = archive_info.get("hashes") or {}
    artifact_sha256 = hashes.get("sha256")
    if not name or not version:
        raise ProvenanceError("pip report is missing package name/version")
    return PackageProvenance(
        name=name,
        version=version,
        source="pypi",
        artifact_sha256=artifact_sha256,
        provenance_url=download_info.get("url"),
        metadata={
            "requested": bool(item.get("requested")),
            "requires_python": metadata.get("requires_python"),
        },
    )
