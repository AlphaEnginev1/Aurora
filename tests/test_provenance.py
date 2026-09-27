import base64
import csv
import hashlib
import json

import pytest

from aurora.autonomy.provenance import (
    ProvenanceError,
    load_pip_report,
    verify_expected_sha256,
    verify_record,
)


def _record_digest(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_verify_expected_sha256(tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"aurora")
    expected = hashlib.sha256(b"aurora").hexdigest()
    assert verify_expected_sha256(artifact, expected) == expected


def test_verify_expected_sha256_rejects_mismatch(tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"aurora")
    with pytest.raises(ProvenanceError, match="sha256 mismatch"):
        verify_expected_sha256(artifact, "0" * 64)


def test_verify_record(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    payload = root / "pkg.py"
    payload.write_bytes(b"hello")
    dist = root / "demo-1.0.dist-info"
    dist.mkdir()
    record = dist / "RECORD"
    with record.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pkg.py", _record_digest(b"hello"), "5"])
        writer.writerow(["demo-1.0.dist-info/RECORD", "", ""])
    assert verify_record(root, record) == 1


def test_verify_record_rejects_tampering(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    payload = root / "pkg.py"
    payload.write_bytes(b"hello")
    dist = root / "demo-1.0.dist-info"
    dist.mkdir()
    record = dist / "RECORD"
    record.write_text("pkg.py,sha256=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,5\n", encoding="utf-8")
    with pytest.raises(ProvenanceError, match="RECORD verification failed"):
        verify_record(root, record)


def test_load_pip_report(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "install": [
                    {
                        "requested": True,
                        "metadata": {
                            "name": "demo",
                            "version": "1.2.3",
                            "requires_python": ">=3.12",
                        },
                        "download_info": {
                            "url": "https://files.pythonhosted.org/demo.whl",
                            "archive_info": {"hashes": {"sha256": "abc123"}},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = load_pip_report(report)
    assert result.name == "demo"
    assert result.version == "1.2.3"
    assert result.artifact_sha256 == "abc123"
    assert result.provenance_url.endswith("demo.whl")


def test_verify_record_resolves_paths_from_dist_info_and_allows_managed_scripts(tmp_path):
    root = tmp_path / "prefix"
    site = root / "lib" / "python3.12" / "site-packages"
    bin_dir = root / "bin"
    site.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    payload = bin_dir / "normalizer"
    payload.write_bytes(b"script")
    dist = site / "demo-1.0.dist-info"
    dist.mkdir()
    record = dist / "RECORD"
    record.write_text(
        f"../../../../bin/normalizer,{_record_digest(b'script')},6\n",
        encoding="utf-8",
    )
    assert verify_record(root, record) == 1


def test_verify_record_rejects_path_escape(tmp_path):
    root = tmp_path / "prefix"
    site = root / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"not managed")
    dist = site / "demo-1.0.dist-info"
    dist.mkdir()
    record = dist / "RECORD"
    record.write_text(
        f"../../../../../outside,{_record_digest(b'not managed')},11\n",
        encoding="utf-8",
    )
    with pytest.raises(ProvenanceError, match="escapes managed install root"):
        verify_record(root, record)
