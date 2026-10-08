"""Authoritative NVIDIA Build catalog reader for Free Endpoint eligibility.

NVIDIA's OpenAI-compatible /v1/models catalog does not expose the Free Endpoint
label. Aurora therefore uses the official build.nvidia.com model catalog as the
free-tier eligibility source and intersects it with models actually served by
the configured NVIDIA API endpoint.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
import json as _json


class NVIDIAFreeCatalogError(RuntimeError):
    """Raised when the official NVIDIA Free Endpoint catalog cannot be resolved."""


class _CatalogNode:
    def __init__(self, tag: str, href: str | None = None) -> None:
        self.tag = tag
        self.text: list[str] = []
        self.hrefs: list[str] = []
        if href:
            self.hrefs.append(href)


class _FreeEndpointHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_CatalogNode] = []
        self._model_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        href = next((str(value) for key, value in attrs if key == "href" and value), None)
        node = _CatalogNode(tag, href)
        if href:
            for parent in self._stack:
                parent.hrefs.append(href)
        self._stack.append(node)

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        for node in self._stack:
            node.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        index = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == tag:
                index = i
                break
        if index is None:
            return
        while len(self._stack) > index:
            node = self._stack.pop()
            self._collect(node)

    def _collect(self, node: _CatalogNode) -> None:
        text = " ".join(node.text).lower()
        if "free endpoint" not in text:
            return
        hrefs = []
        for href in node.hrefs:
            normalized = _model_id_from_href(href)
            if normalized and normalized not in hrefs:
                hrefs.append(normalized)
        if len(hrefs) == 1:
            self._model_ids.add(hrefs[0])

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._model_ids))


def _model_id_from_href(href: str) -> str | None:
    raw = str(href).strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    path = unquote(parsed.path).strip("/")
    if path.startswith("models/"):
        path = path.removeprefix("models/").strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        return None
    if parts[0] in {"explore", "login", "settings", "docs", "api"}:
        return None
    return "/".join(parts)


class NVIDIAFreeEndpointCatalogProvider:
    """Read the official NVIDIA catalog and return model IDs labelled Free Endpoint."""

    def __init__(
        self,
        *,
        url: str = "https://build.nvidia.com/models?pageSize=200",
        timeout_seconds: float = 20.0,
        gateway=None,
    ) -> None:
        self.url = str(url).strip()
        self.timeout_seconds = float(timeout_seconds)
        self.gateway = gateway
        if not self.url:
            raise NVIDIAFreeCatalogError("NVIDIA free catalog URL is empty")
        if self.timeout_seconds <= 0:
            raise NVIDIAFreeCatalogError("NVIDIA free catalog timeout must be positive")

    def discover(self) -> tuple[str, ...]:
        request = Request(
            self.url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Aurora-NVIDIA-Catalog/1.0",
            },
            method="GET",
        )
        try:
            if self.gateway is not None:
                response = self.gateway.request_with_metadata(
                    self.url,
                    method="GET",
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "User-Agent": "Aurora-NVIDIA-Catalog/1.0",
                    },
                )
                html = response.body.decode("utf-8")
            else:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    html = response.read().decode("utf-8")
        except Exception as exc:
            raise NVIDIAFreeCatalogError(
                f"NVIDIA Free Endpoint catalog transport failure: {exc}"
            ) from None
        parser = _FreeEndpointHTMLParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception as exc:
            raise NVIDIAFreeCatalogError(
                f"NVIDIA Free Endpoint catalog parse failure: {exc}"
            ) from None
        if not parser.model_ids:
            raise NVIDIAFreeCatalogError(
                "NVIDIA Free Endpoint catalog contained no model IDs"
            )
        return parser.model_ids


__all__ = [
    "NVIDIAFreeCatalogError",
    "NVIDIAFreeEndpointCatalogProvider",
]
