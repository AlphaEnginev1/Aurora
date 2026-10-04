"""Policy-controlled HTTPS Internet access for Aurora acquisition and research."""
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlparse
import urllib.error
import urllib.request

class InternetPolicyError(RuntimeError):
    """Raised when a network request violates Aurora policy."""

@dataclass(frozen=True)
class InternetPolicy:
    protocols: frozenset[str]
    allowed_hosts: frozenset[str]

    @classmethod
    def from_file(cls, path: str | Path) -> "InternetPolicy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            frozenset(data.get("protocols", [])),
            frozenset(data.get("allowed_hosts", [])),
        )

@dataclass(frozen=True)
class RedirectHop:
    source_url: str
    target_url: str
    status_code: int

@dataclass(frozen=True)
class InternetResponse:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    body: bytes
    redirects: tuple[RedirectHop, ...] = ()

class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, gateway: "InternetGateway") -> None:
        self.gateway = gateway
        self.hops: list[RedirectHop] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.gateway.authorize(newurl)
        self.hops.append(RedirectHop(req.full_url, newurl, code))
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class InternetGateway:
    """Small deny-by-default HTTPS gateway for controlled outbound access."""

    def __init__(self, policy: InternetPolicy, timeout: float = 30.0):
        self.policy = policy
        self.timeout = timeout

    def authorize(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in self.policy.protocols:
            raise InternetPolicyError(f"protocol is not allowed: {parsed.scheme}")
        if not parsed.hostname or parsed.hostname not in self.policy.allowed_hosts:
            raise InternetPolicyError(f"host is not allowed: {parsed.hostname}")
        if parsed.username or parsed.password:
            raise InternetPolicyError("embedded URL credentials are not allowed")

    def request_with_metadata(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> InternetResponse:
        self.authorize(url)
        handler = _PolicyRedirectHandler(self)
        opener = urllib.request.build_opener(handler)
        request_headers = {"User-Agent": "Aurora/0.1"}
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            url,
            method=method,
            headers=request_headers,
            data=body,
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                final_url = response.url
                self.authorize(final_url)
                return InternetResponse(
                    requested_url=url,
                    final_url=final_url,
                    status_code=response.status,
                    content_type=response.headers.get_content_type(),
                    body=response.read(),
                    redirects=tuple(handler.hops),
                )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise InternetPolicyError(f"network request failed: {exc}") from exc

    def get_with_metadata(self, url: str) -> InternetResponse:
        return self.request_with_metadata(url)

    def get_json(self, url: str, *, headers: dict[str, str] | None = None):
        response = self.request_with_metadata(url, headers=headers)
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InternetPolicyError("response was not valid JSON") from exc

    def get(self, url: str) -> bytes:
        """Return response bytes while preserving the legacy gateway API."""
        return self.get_with_metadata(url).body
