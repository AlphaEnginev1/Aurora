"""Artifact inspection and lightweight document text extraction."""
from __future__ import annotations

import hashlib
import mimetypes
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree


class ArtifactError(RuntimeError):
    """Raised when an artifact cannot be safely inspected or parsed."""


@dataclass(frozen=True)
class ArtifactDescriptor:
    path: str
    filename: str
    media_type: str
    size: int
    sha256: str
    extension: str


class ArtifactInspector:
    """Inspect common work artifacts without exposing credentials."""

    def inspect(self, path: str | Path) -> ArtifactDescriptor:
        source = Path(path).resolve()
        if not source.is_file():
            raise ArtifactError(f"artifact is not a file: {source}")
        return ArtifactDescriptor(
            path=str(source),
            filename=source.name,
            media_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            size=source.stat().st_size,
            sha256=self._sha256(source),
            extension=source.suffix.lower(),
        )

    def extract_text(self, path: str | Path, *, max_chars: int = 2_000_000) -> str:
        descriptor = self.inspect(path)
        source = Path(descriptor.path)
        ext = descriptor.extension
        if ext in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log", ".yaml", ".yml"}:
            return self._bounded(source.read_text(encoding="utf-8", errors="replace"), max_chars)
        if ext == ".docx":
            return self._bounded(self._docx_text(source), max_chars)
        if ext == ".xlsx":
            return self._bounded(self._xlsx_text(source), max_chars)
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except ImportError as exc:
                raise ArtifactError("PDF parser is unavailable; governed pypdf acquisition is required") from exc
            try:
                text = "\n".join((page.extract_text() or "") for page in PdfReader(str(source)).pages)
            except Exception as exc:
                raise ArtifactError(f"PDF extraction failed: {exc}") from exc
            return self._bounded(text, max_chars)
        raise ArtifactError(f"no text extractor is registered for {ext or \"unknown\"}")

    @staticmethod
    def _docx_text(source: Path) -> str:
        try:
            with zipfile.ZipFile(source) as archive:
                xml = archive.read("word/document.xml")
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise ArtifactError(f"DOCX container is invalid: {exc}") from exc
        root = ElementTree.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", namespace):
            paragraphs.append("".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)))
        return "\n".join(paragraphs)

    @staticmethod
    def _xlsx_text(source: Path) -> str:
        try:
            with zipfile.ZipFile(source) as archive:
                shared = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                    for item in root.findall(".//x:si", ns):
                        shared.append("".join(node.text or "" for node in item.findall(".//x:t", ns)))
                rows = []
                for name in sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")):
                    root = ElementTree.fromstring(archive.read(name))
                    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                    for row in root.findall(".//x:row", ns):
                        cells = []
                        for cell in row.findall("x:c", ns):
                            value = cell.find("x:v", ns)
                            text = "" if value is None else (value.text or "")
                            if cell.get("t") == "s" and text.isdigit() and int(text) < len(shared):
                                text = shared[int(text)]
                            cells.append(text)
                        if cells:
                            rows.append("\t".join(cells))
                return "\n".join(rows)
        except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise ArtifactError(f"XLSX container is invalid: {exc}") from exc

    @staticmethod
    def _bounded(text: str, max_chars: int) -> str:
        if max_chars < 1:
            raise ArtifactError("max_chars must be positive")
        return text[:max_chars]

    @staticmethod
    def _sha256(source: Path) -> str:
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = ["ArtifactDescriptor", "ArtifactError", "ArtifactInspector"]
