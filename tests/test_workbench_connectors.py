from pathlib import Path

from aurora.autonomy.internet import InternetResponse
from aurora.workbench.connectors import ConnectorClient, ConnectorError, ConnectorRegistry


class FakeGateway:
    def __init__(self):
        self.calls = []

    def request_with_metadata(self, url, *, method="GET", headers=None, body=None):
        self.calls.append((url, method, headers or {}, body))
        return InternetResponse(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=b'{"result":"ok"}',
        )


def test_connector_registry_and_client_use_scoped_credentials(tmp_path: Path, monkeypatch):
    path = tmp_path / "connectors.json"
    path.write_text(
        '{"version":1,"connectors":[{"connector_id":"demo","connector_type":"http-api",'
        '"base_url":"https://example.com/api/","token_env":"DEMO_TOKEN","capabilities":["read","write"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEMO_TOKEN", "secret")
    gateway = FakeGateway()
    client = ConnectorClient(ConnectorRegistry(str(path)), gateway)

    result = client.request("demo", path="status", method="POST", capability="write", payload={"ok": True})

    assert result == {"result": "ok"}
    assert gateway.calls[0][0] == "https://example.com/api/status"
    assert gateway.calls[0][1] == "POST"
    assert gateway.calls[0][2]["Authorization"] == "Bearer secret"


def test_connector_client_blocks_host_escape(tmp_path: Path):
    path = tmp_path / "connectors.json"
    path.write_text(
        '{"version":1,"connectors":[{"connector_id":"demo","connector_type":"http-api",'
        '"base_url":"https://example.com/api/","capabilities":["read"]}]}',
        encoding="utf-8",
    )
    client = ConnectorClient(ConnectorRegistry(str(path)), FakeGateway())

    try:
        client.request("demo", path="https://evil.example/x")
    except ConnectorError as exc:
        assert "escaped" in str(exc)
    else:
        raise AssertionError("connector must block host escape")


def test_connector_registry_rejects_invalid_base_url(tmp_path: Path):
    path = tmp_path / "connectors.json"
    path.write_text(
        '{"version":1,"connectors":[{"connector_id":"bad","connector_type":"http-api","base_url":"ftp://example.com"}]}',
        encoding="utf-8",
    )
    try:
        ConnectorRegistry(str(path))
    except ConnectorError as exc:
        assert "invalid connector base_url" in str(exc)
    else:
        raise AssertionError("invalid connector URL must fail closed")
