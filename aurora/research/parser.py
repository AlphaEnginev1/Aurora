"""Deterministic source parsing and conservative claim extraction.

Parsing produces normalized readable text. Claim extraction only identifies candidate
claims; it never changes evidence state or treats extraction confidence as truth.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from .models import ClaimRecord, SourceRecord


class SourceParsingError(ValueError):
    """Raised when a retrieved source cannot be safely parsed."""


@dataclass(frozen=True)
class ParsedSource:
    source_id: str
    content_sha256: str
    text: str
    content_type: str


class _VisibleHtmlParser(HTMLParser):
    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "td", "th", "tr", "ul",
    }
    _IGNORED_TAGS = {"script", "style", "template", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
        if self._ignored_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag in self._IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.parts.append(data)


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    return text.strip()


def parse_source(source: SourceRecord) -> ParsedSource:
    content_type = (source.content_type or "text/plain").split(";", 1)[0].strip().lower()
    if content_type in {"text/plain", "text/csv", "application/json", "application/xml"}:
        text = _normalize_text(source.content_text)
    elif content_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleHtmlParser()
        try:
            parser.feed(source.content_text)
            parser.close()
        except Exception as exc:
            raise SourceParsingError("HTML source could not be parsed safely") from exc
        text = _normalize_text("".join(parser.parts))
    else:
        raise SourceParsingError(f"unsupported research source content type: {content_type}")

    if not text:
        raise SourceParsingError("research source contains no parseable text")
    return ParsedSource(
        source_id=source.source_id,
        content_sha256=source.content_sha256,
        text=text,
        content_type=content_type,
    )


_SENTENCE_END = re.compile(r"[.!?](?:["'’”’\)\]]+)?(?=\s|$)")
_WHITESPACE = re.compile(r"\s+")


def _candidate_spans(text: str):
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        candidate = text[start:end].strip()
        leading = len(text[start:end]) - len(text[start:end].lstrip())
        span_start = start + leading
        span_end = span_start + len(candidate)
        if candidate:
            yield span_start, span_end, candidate
        start = end
    tail = text[start:].strip()
    if tail:
        leading = len(text[start:]) - len(text[start:].lstrip())
        span_start = start + leading
        yield span_start, span_start + len(tail), tail


def extract_claims(parsed: ParsedSource) -> tuple[ClaimRecord, ...]:
    claims: list[ClaimRecord] = []
    for start, end, candidate in _candidate_spans(parsed.text):
        normalized = _WHITESPACE.sub(" ", candidate).strip()
        if len(normalized) < 20 or len(normalized) > 2000:
            continue
        if normalized.startswith(("#", "•")):
            continue
        extraction_confidence = 0.9 if _SENTENCE_END.search(normalized) else 0.75
        claim_id = "clm-" + hashlib.sha256(
            f"{parsed.source_id}|{parsed.content_sha256}|{start}|{end}|{normalized}".encode("utf-8")
        ).hexdigest()[:32]
        claims.append(
            ClaimRecord(
                claim_id=claim_id,
                source_id=parsed.source_id,
                source_content_sha256=parsed.content_sha256,
                claim=normalized,
                start_offset=start,
                end_offset=end,
                extraction_confidence=extraction_confidence,
            )
        )
    return tuple(claims)


__all__ = ["ParsedSource", "SourceParsingError", "parse_source", "extract_claims"]
