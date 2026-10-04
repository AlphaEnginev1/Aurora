from pathlib import Path
import ast

from aurora.workbench.local_computer import LocalComputerGateway
from aurora.workbench.mcp import MCPBridgeError, MCPRegistry


def test_mcp_registry_loads_disabled_server(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(
        '{"version":1,"servers":[{"server_id":"disabled","enabled":false,"transport":"streamable-http","url":"https://example.invalid/mcp","header_env":{}}]}',
        encoding="utf-8",
    )
    registry = MCPRegistry(str(path))
    try:
        registry.get("disabled")
    except MCPBridgeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("disabled MCP servers must fail closed")


def test_mcp_registry_rejects_invalid_transport(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(
        '{"version":1,"servers":[{"server_id":"bad","enabled":true,"transport":"ftp","url":"ftp://example.invalid"}]}',
        encoding="utf-8",
    )
    try:
        MCPRegistry(str(path))
    except MCPBridgeError as exc:
        assert "transport" in str(exc)
    else:
        raise AssertionError("invalid MCP transport must fail closed")


def test_local_gateway_client_has_safe_defaults(monkeypatch):
    monkeypatch.delenv("AURORA_LOCAL_GATEWAY_URL", raising=False)
    gateway = LocalComputerGateway()
    assert gateway.url == "http://127.0.0.1:8790"


def test_local_gateway_companion_has_no_syntax_errors():
    source = Path("scripts/local-workstation-gateway.py").read_text(encoding="utf-8")
    ast.parse(source)


def test_local_gateway_installer_has_param_block_first():
    source = Path("scripts/install-local-workstation-gateway.ps1").read_text(encoding="utf-8").lstrip()
    assert source.startswith("param(")
