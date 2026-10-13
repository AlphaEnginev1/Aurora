"""Authoritative NVIDIA Build catalog reader for Free Endpoint eligibility.

NVIDIA's OpenAI-compatible /v1/models catalog does not expose the Free Endpoint
label. Aurora therefore uses NVIDIA's official Build catalog as the free-tier
eligibility source and intersects it with models actually served by the API.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
import time
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


class NVIDIAFreeCatalogError(RuntimeError):
    """Raised when the official NVIDIA Free Endpoint catalog cannot be resolved."""


class _CatalogNode:
    def __init__(self, tag: str, href: str | None = None) -> None:
        self.tag = tag
        self.href = href
        self.text: list[str] = []
        # Links are attached to the smallest enclosing card, not every open HTML
        # element. Keeping anchor text lets us distinguish a model title from
        # unrelated secondary links inside the card.
        self.links: list[tuple[str, str]] = []
        self.has_free_model_descendant = False


class _FreeEndpointHTMLParser(HTMLParser):
    _VOID_TAGS = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_CatalogNode] = []
        self._model_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        href = next(
            (str(value) for key, value in attrs if key == "href" and value),
            None,
        )
        # HTML void elements never have end tags. Pushing them onto a manual
        # stack corrupts card boundaries on NVIDIA's long, React-rendered page.
        if tag.lower() in self._VOID_TAGS:
            if href:
                for parent in self._stack:
                    parent.links.append((href, ""))
            return
        self._stack.append(_CatalogNode(tag.lower(), href))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        for node in self._stack:
            node.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        index = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == normalized_tag:
                index = i
                break
        if index is None:
            return

        while len(self._stack) > index:
            node = self._stack.pop()
            text = " ".join(node.text).lower()
            raw_model_links = _model_links_from_links(node.links)
            free_model_container = "free endpoint" in text and bool(raw_model_links)

            # Only count a card when no smaller descendant already owned a free
            # model. This prevents a grid/list ancestor (containing free and paid
            # cards) from accidentally promoting every model in that ancestor.
            if free_model_container and not node.has_free_model_descendant:
                self._model_ids.update(_select_model_ids(raw_model_links))

            # A Free Endpoint badge and the title link are siblings in the real
            # catalog. Preserve links upward until their nearest card/container
            # has enough context to associate them.
            if node.tag == "a" and node.href:
                label = " ".join(node.text).strip()
                for parent in self._stack:
                    parent.links.append((node.href, label))

            contains_free_card = (
                free_model_container or node.has_free_model_descendant
            )
            if contains_free_card:
                for parent in self._stack:
                    parent.has_free_model_descendant = True

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._model_ids))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _model_links_from_links(links: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for href, label in links:
        model_id = _model_id_from_href(href)
        if not model_id:
            continue
        item = (model_id, label.strip())
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _select_model_ids(links: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Select the model title link, avoiding unrelated links in a Free card."""
    unique_ids = {model_id for model_id, _label in links}
    title_matches = {
        model_id
        for model_id, label in links
        if label and _slug(label) == _slug(model_id.rsplit("/", 1)[-1])
    }
    if title_matches:
        return tuple(sorted(title_matches))
    # Older/simple card markup may contain a single model link but no useful
    # anchor text. Accept only that unambiguous case; otherwise fail closed.
    if len(unique_ids) == 1:
        return tuple(sorted(unique_ids))
    return ()


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
    if parts[0].lower() in {"explore", "login", "settings", "docs", "api"}:
        return None
    return "/".join(parts)


class NVIDIAFreeEndpointCatalogProvider:
    """Read the official NVIDIA catalog and return model IDs labelled Free Endpoint."""

    def __init__(
        self,
        *,
        url: str = "https://build.nvidia.com/models?pageSize=96",
        timeout_seconds: float = 20.0,
        gateway=None,
        max_attempts: int = 3,
    ) -> None:
        self.url = str(url).strip()
        self.timeout_seconds = float(timeout_seconds)
        self.gateway = gateway
        self.max_attempts = int(max_attempts)
        if not self.url:
            raise NVIDIAFreeCatalogError("NVIDIA free catalog URL is empty")
        if self.timeout_seconds <= 0:
            raise NVIDIAFreeCatalogError("NVIDIA free catalog timeout must be positive")
        if self.max_attempts < 1 or self.max_attempts > 5:
            raise NVIDIAFreeCatalogError("NVIDIA free catalog max_attempts must be between 1 and 5")

    def _read_html(self) -> str:
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Aurora-NVIDIA-Catalog/1.0",
        }
        request = Request(self.url, headers=headers, method="GET")
        if self.gateway is not None:
            response = self.gateway.request_with_metadata(
                self.url,
                method="GET",
                headers=headers,
            )
            return response.body.decode("utf-8")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read().decode("utf-8")

    def discover(self) -> tuple[str, ...]:
        last_failure = "NVIDIA Free Endpoint catalog returned no model IDs"
        for attempt in range(self.max_attempts):
            try:
                html = self._read_html()
            except Exception as exc:
                # Do not include response bodies, request headers, or credentials
                # in diagnostics; this public catalog request has no API key.
                last_failure = f"NVIDIA Free Endpoint catalog transport failure: {type(exc).__name__}"
            else:
                parser = _FreeEndpointHTMLParser()
                try:
                    parser.feed(html)
                    parser.close()
                except Exception as exc:
                    last_failure = f"NVIDIA Free Endpoint catalog parse failure: {type(exc).__name__}"
                else:
                    if parser.model_ids:
                        return parser.model_ids
                    last_failure = "NVIDIA Free Endpoint catalog contained no model IDs"

            if attempt + 1 < self.max_attempts:
                # The public catalog can return a transient, incomplete page.
                # Retry it briefly; never substitute a stale/static allowlist.
                time.sleep(0.25 * (attempt + 1))

        raise NVIDIAFreeCatalogError(
            f"NVIDIA Free Endpoint catalog discovery failed after {self.max_attempts} attempts: {last_failure}"
        )


__all__ = [
    "NVIDIAFreeCatalogError",
    "NVIDIAFreeEndpointCatalogProvider",
]
