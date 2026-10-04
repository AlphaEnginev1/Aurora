from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from aurora.workbench.local_computer import LocalComputerGateway
from aurora.workbench.mcp import MCPClientBridge, MCPRegistry


class GatewayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b'{"ok":true,"ready":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = b'{"ok":true,"results":[{"action":"screenshot","mime_type":"image/png","data":"ZmFrZQ=="}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def test_local_computer_gateway_health_and_actions():
    server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        gateway = LocalComputerGateway(url=f"http://127.0.0.1:{server.server_port}")
        assert gateway.health()["ready"] is True
        result = gateway.execute([{"action": "screenshot"}], mission_id="m1", actor="director")
        assert result["results"][0]["mime_type"] == "image/png"
    finally:
        server.shutdown()


def test_mcp_registry_rejects_duplicate_servers(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(
        '{"version":1,"servers":['
        '{"server_id":"dup","enabled":true,"transport":"streamable-http","url":"http://127.0.0.1:1/mcp"},'
        '{"server_id":"dup","enabled":true,"transport":"streamable-http","url":"http://127.0.0.1:2/mcp"}]}',
        encoding="utf-8",
    )
    try:
        MCPRegistry(str(path))
    except Exception as exc:
        assert "duplicate MCP server" in str(exc)
    else:
        raise AssertionError("duplicate MCP servers must fail closed")


def test_mcp_bridge_uses_real_v2_client_in_process(tmp_path: Path):
    from mcp import Client
    from mcp.server import MCPServer

    server = MCPServer("Aurora Test MCP")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    registry_path = tmp_path / "mcp.json"
    registry_path.write_text(
        '{"version":1,"servers":[{"server_id":"test","enabled":true,"transport":"stdio","command":"python","args":[]}]}',
        encoding="utf-8",
    )
    bridge = MCPClientBridge(MCPRegistry(str(registry_path)))

    @asynccontextmanager
    async def in_process(_spec):
        async with Client(server) as client:
            yield client

    bridge._client = in_process
    tools = bridge.list_tools("test")
    assert [tool["name"] for tool in tools] == ["add"]
    result = bridge.call_tool("test", "add", {"a": 2, "b": 3})
    assert result["is_error"] is False
    assert result["structured_content"] == {"result": 5}
