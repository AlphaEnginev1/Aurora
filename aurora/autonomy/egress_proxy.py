from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass

@dataclass(frozen=True)
class EgressProxyPolicy:
    allowed_hosts: frozenset[str]
    allowed_ports: frozenset[int] = frozenset({443})
    connect_timeout: float = 10.0
    max_header_bytes: int = 16384

    def authorize(self, authority: str) -> tuple[str, int]:
        if len(authority) > 255:
            raise PermissionError("authority too long")
        host, sep, port_text = authority.rpartition(":")
        if not sep or not host or not port_text.isdigit():
            raise PermissionError("CONNECT requires host:port")
        port = int(port_text)
        if port not in self.allowed_ports:
            raise PermissionError("destination port is not allowed")
        host = host.rstrip(".").lower()
        try:
            socket.inet_aton(host)
            raise PermissionError("IP-literal destinations are not allowed")
        except OSError:
            pass
        if host not in self.allowed_hosts:
            raise PermissionError("destination host is not allowed")
        return host, port

class EgressProxy:
    def __init__(self, policy: EgressProxyPolicy):
        self.policy = policy

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if len(line) > self.policy.max_header_bytes or not line:
                raise PermissionError("invalid proxy request")
            parts = line.decode("ascii", errors="strict").strip().split()
            if len(parts) != 3 or parts[0].upper() != "CONNECT":
                raise PermissionError("only HTTPS CONNECT is supported")
            host, port = self.policy.authorize(parts[1])
            while True:
                header = await reader.readline()
                if not header or len(header) > self.policy.max_header_bytes:
                    raise PermissionError("invalid proxy headers")
                if header in (b"\r\n", b"\n"):
                    break
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), self.policy.connect_timeout
            )
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(
                self._pipe(reader, remote_writer),
                self._pipe(remote_reader, writer),
            )
        except (PermissionError, UnicodeError, asyncio.TimeoutError, OSError):
            try:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except OSError:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _pipe(self, reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            try:
                writer.close()
            except OSError:
                pass

    async def serve(self, host: str, port: int):
        return await asyncio.start_server(self.handle, host, port)
