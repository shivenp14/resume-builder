"""Best-effort, review-required extraction of job descriptions from public pages.

This is deliberately a small scraper rather than a browser. It does not execute
JavaScript and reports a low-confidence result when a page needs client-side
rendering or does not expose enough readable job text.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from contextlib import aclosing
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 4
REQUEST_TIMEOUT_SECONDS = 8.0
MIN_DESCRIPTION_CHARS = 400


@dataclass(frozen=True)
class JobDescriptionResult:
    status: str  # "ok", "low_confidence", or "unavailable"
    text: str = ""
    title: str = ""
    source_url: str = ""
    reason: str = ""


class _TextExtractor(HTMLParser):
    """Collect text from likely job-content containers and the document body."""

    _SKIP = {"head", "title", "script", "style", "noscript", "svg", "nav", "footer", "header", "form", "button"}
    _BLOCK = {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2", "h3", "h4", "li", "main", "ol", "p", "section", "table", "td", "th", "tr", "ul"}
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _JOB_HINT = re.compile(r"job|description|responsibil|qualif|posting", re.I)
    _JOB_CONTENT_SIGNAL = re.compile(
        r"\b(responsibilit(?:y|ies)|qualifications?|requirements?|what you(?:'|’)ll do|"
        r"what we(?:'|’)re looking for|about the role|role overview|job description|"
        r"who you are|what you(?:'|’)ll need|skills and experience)\b",
        re.I,
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._stack: list[tuple[str, bool, bool]] = []  # tag, likely-content, skipped-subtree
        self._body: list[str] = []
        self._candidates: list[list[str]] = []
        self._candidate_has_signal: list[bool] = []
        self._active_candidates: list[int] = []
        self._json_ld: list[str] = []
        self._in_json_ld = False
        self.has_job_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and "ld+json" in attr.get("type", "").lower():
            self._in_json_ld = True
            self._json_ld.append("")
        hidden = attr.get("aria-hidden", "").lower() == "true" or "hidden" in attr or attr.get("style", "").replace(" ", "").lower().find("display:none") >= 0
        skipped = any(parent_skipped for _, _, parent_skipped in self._stack) or tag in self._SKIP or hidden
        hint = " ".join((attr.get("id", ""), attr.get("class", ""), attr.get("itemprop", "")))
        likely = not skipped and (tag in {"main", "article"} or bool(self._JOB_HINT.search(hint)))
        if tag in self._BLOCK:
            self._body.append("\n")
            for index in self._active_candidates:
                self._candidates[index].append("\n")
        if likely:
            candidate: list[str] = []
            self._candidates.append(candidate)
            self._candidate_has_signal.append(bool(self._JOB_HINT.search(hint)))
            self._active_candidates.append(len(self._candidates) - 1)
        if tag not in self._VOID:
            self._stack.append((tag, likely, skipped))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        # HTML can be malformed; unwind through the matching open tag.
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                popped = self._stack[index:]
                del self._stack[index:]
                for popped_tag, likely, _ in reversed(popped):
                    if popped_tag == "script":
                        self._in_json_ld = False
                    if likely and self._active_candidates:
                        self._active_candidates.pop()
                break
        if tag in self._BLOCK:
            self._body.append("\n")
            for index in self._active_candidates:
                self._candidates[index].append("\n")

    def handle_data(self, data: str) -> None:
        if any(tag == "title" for tag, _, _ in self._stack):
            self.title_parts.append(data)
        visible = not any(skipped for _, _, skipped in self._stack)
        if visible and any(tag in {"h1", "h2", "h3", "h4"} for tag, _, _ in self._stack) and self._JOB_CONTENT_SIGNAL.search(data):
            self.has_job_heading = True
            for index in self._active_candidates:
                self._candidate_has_signal[index] = True
        if self._in_json_ld and self._json_ld:
            self._json_ld[-1] += data
        if any(skipped for _, _, skipped in self._stack):
            return
        self._body.append(data)
        for index in self._active_candidates:
            self._candidates[index].append(data)


def _clean(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _jsonld_descriptions(chunks: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            kind = value.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            description = value.get("description")
            if any(str(k).lower() == "jobposting" for k in kinds) and isinstance(description, str):
                parser = _TextExtractor()
                parser.feed(description)
                found.append((_clean("\n".join("".join(c) for c in parser._candidates) or "".join(parser._body)), str(value.get("title", ""))))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    for chunk in chunks:
        try:
            visit(json.loads(chunk))
        except (json.JSONDecodeError, RecursionError):
            continue
    return found


def _extract(html: str) -> tuple[str, str, bool]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        # HTMLParser is tolerant; malformed input should still yield collected text.
        pass
    title = _clean(" ".join(parser.title_parts))
    structured = _jsonld_descriptions(parser._json_ld)
    if structured:
        text, structured_title = max(structured, key=lambda pair: len(pair[0]))
        if len(text) >= MIN_DESCRIPTION_CHARS:
            return text, structured_title or title, True
    candidates = [_clean("".join(candidate)) for candidate in parser._candidates]
    body = _clean("".join(parser._body))
    # Main/article alone is not evidence of a job page. Require a job-related
    # container hint or a visible job-content heading within that container.
    eligible = [
        text for text, has_signal in zip(candidates, parser._candidate_has_signal)
        if has_signal and len(text) >= MIN_DESCRIPTION_CHARS
    ]
    if eligible:
        return max(eligible, key=len), title, True
    return body, title, parser.has_job_heading


def _validate_public_url(url: str) -> tuple[str, list[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL") from exc
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("URL must be a public http or https address")
    if port is not None and port not in ({80} if scheme == "http" else {443}):
        raise ValueError("non-standard ports are not allowed")
    normalized_host = host.rstrip(".").lower()
    if normalized_host == "localhost" or normalized_host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("private hostnames are not allowed")
    try:
        ip = ipaddress.ip_address(normalized_host)
        addresses = [ip]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(normalized_host, port or (443 if scheme == "https" else 80), type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise ValueError("hostname could not be resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("private or non-public destination is not allowed")
    return normalized_host, addresses


def _pinned_request_url(url: str, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[httpx.URL, str, dict[str, str]]:
    """Use the validated IP as the socket destination while retaining HTTP host/TLS identity."""
    original = httpx.URL(url)
    pinned = original.copy_with(host=ip.compressed)
    authority_host = f"[{original.host}]" if ":" in original.host else original.host
    authority = authority_host if original.port is None else f"{authority_host}:{original.port}"
    extensions = {"sni_hostname": original.host} if original.scheme == "https" else {}
    return pinned, authority, extensions


async def fetch_job_description_async(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> JobDescriptionResult:
    """Fetch and extract visible job text without executing page code.

    Every redirect is checked again and automatic redirects are disabled. Each
    request connects to an IP address from the validated DNS answer; HTTP Host
    and TLS SNI/certificate verification retain the original hostname. ``timeout``
    is an absolute deadline for DNS resolution, redirects, and body streaming.
    """
    owns_client = client is None
    if client is not None and transport is not None:
        raise ValueError("pass either client or transport, not both")
    active_client = client or httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "ResumeBuilderJobDescriptionFetcher/1.0"},
    )

    async def fetch() -> JobDescriptionResult:
        current = url
        for hop in range(max_redirects + 1):
            try:
                _, addresses = await asyncio.to_thread(_validate_public_url, current)
            except ValueError as exc:
                return JobDescriptionResult("unavailable", source_url=current, reason=str(exc))
            try:
                pinned_url, authority, extensions = _pinned_request_url(current, addresses[0])
                request = active_client.build_request(
                    "GET", pinned_url, headers={"Host": authority}, timeout=timeout, extensions=extensions,
                )
                response = await active_client.send(request, stream=True, follow_redirects=False)
                async with aclosing(response):
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or hop == max_redirects:
                            return JobDescriptionResult("unavailable", source_url=current, reason="redirect limit reached")
                        current = urljoin(current, location)
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        return JobDescriptionResult("unavailable", source_url=current, reason=f"page returned HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").lower()
                    if "html" not in content_type and "text/plain" not in content_type:
                        return JobDescriptionResult("unavailable", source_url=current, reason="page is not HTML or plain text")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            return JobDescriptionResult("unavailable", source_url=current, reason="page exceeded response size limit")
                        chunks.append(chunk)
                    encoding = response.encoding or "utf-8"
                    html = b"".join(chunks).decode(encoding, errors="replace")
                    text, title, job_content_signal = _extract(html)
                    if len(text) < MIN_DESCRIPTION_CHARS or not job_content_signal:
                        return JobDescriptionResult("low_confidence", text=text, title=title, source_url=current, reason="page did not expose enough recognizable job description text")
                    return JobDescriptionResult("ok", text=text, title=title, source_url=current)
            except (httpx.HTTPError, OSError, UnicodeError) as exc:
                return JobDescriptionResult("unavailable", source_url=current, reason=f"fetch failed: {type(exc).__name__}")
        return JobDescriptionResult("unavailable", source_url=current, reason="redirect limit reached")

    try:
        return await asyncio.wait_for(fetch(), timeout=timeout)
    except asyncio.TimeoutError:
        return JobDescriptionResult("unavailable", source_url=url, reason=f"fetch exceeded absolute deadline of {timeout:g} seconds")
    finally:
        if owns_client:
            await active_client.aclose()


def fetch_job_description(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> JobDescriptionResult:
    """Synchronous wrapper; async callers should await ``fetch_job_description_async``."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fetch_job_description_async(
            url, transport=transport, timeout=timeout, max_bytes=max_bytes, max_redirects=max_redirects,
        ))
    raise RuntimeError("fetch_job_description cannot run inside an event loop; await fetch_job_description_async instead")
