from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from aurora.workbench.artifacts import ArtifactInspector
from aurora.workbench.session import WorkSessionStore


def test_artifact_inspector_text_and_metadata(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("Aurora\nUniversal Work", encoding="utf-8")
    inspector = ArtifactInspector()

    descriptor = inspector.inspect(path)
    assert descriptor.filename == "notes.txt"
    assert descriptor.media_type == "text/plain"
    assert inspector.extract_text(path) == "Aurora\nUniversal Work"


def test_artifact_inspector_extracts_docx(tmp_path: Path):
    path = tmp_path / "brief.docx"
    xml = (
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        "<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Aurora</w:t></w:r></w:p></w:body></w:document>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    assert ArtifactInspector().extract_text(path) == "Hello\nAurora"


def test_artifact_inspector_extracts_xlsx_strings(tmp_path: Path):
    path = tmp_path / "data.xlsx"
    shared = (
        "<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
        "<si><t>Project</t></si><si><t>Aurora</t></si></sst>"
    )
    sheet = (
        "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
        "<sheetData><row><c t=\"s\"><v>0</v></c><c t=\"s\"><v>1</v></c></row></sheetData></worksheet>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    assert ArtifactInspector().extract_text(path) == "Project\tAurora"


def test_work_session_store_survives_reload(tmp_path: Path):
    path = tmp_path / "sessions.db"
    store = WorkSessionStore(path)
    created = store.create_or_get(
        work_id="work-1", mission_id="mission-1", actor="director",
        environment="local", workspace_root=str(tmp_path),
    )
    store.record_capability("work-1", "workbench:file:read")

    store.record_artifact("work-1", "artifact-1")
    store.record_receipt(
        "work-1",
        "receipt-1",
        correlation_id="corr-1",
        payload={"status": "succeeded", "capability": "workbench:file:read"},
    )
    store.update_tool_health("work-1", "workbench:file:read", "healthy")
    store.set_approval_state("work-1", "approved")

    reloaded = WorkSessionStore(path).get("work-1")
    assert reloaded.work_id == created.work_id
    assert reloaded.mission_id == "mission-1"
    assert reloaded.workspace_roots == (str(tmp_path),)
    assert reloaded.capability_ids == ("workbench:file:read",)
    assert reloaded.artifact_ids == ("artifact-1",)
    assert reloaded.execution_receipts == ("receipt-1",)
    assert reloaded.correlation_ids == ("corr-1",)
    assert reloaded.tool_health["workbench:file:read"] == "healthy"
    assert reloaded.approval_state == "approved"
    assert store.get_receipts("work-1")[0]["payload"]["status"] == "succeeded"
